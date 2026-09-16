"""
config.py
Configurazione, persistenza su disco e permessi — interamente per-server.

Ogni server (guild) ha la propria configurazione isolata: ruoli, canali, sezioni,
owner, tempi SLA, tipi candidatura, team candidatura,
branding, blacklist, opt-out notifiche. Nessuna impostazione è più cablata nel
codice o condivisa tra server.

I dati sono salvati in 4 file (nella cartella indicata da DATA_DIR, o accanto al
bot se DATA_DIR non è impostata):

    config_ticket.json     -> configurazione per-guild (ruoli, canali, sezioni, impostazioni)
    tickets_active.json    -> ticket attualmente aperti, per-guild
    tickets_history.json   -> storico dei ticket chiusi, per-guild
    staff_preferences.json -> sessioni candidatura in corso (indice cross-guild: i DM
                              non hanno contesto server, quindi serve un indice globale
                              con chiave "guild_id:user_id")

Scrittura su disco:
  * ATOMICA — si scrive su un file temporaneo e si fa os.replace(), quindi un crash
    a metà salvataggio non può corrompere la configurazione di tutti i server;
  * ASINCRONA — la parte lenta (I/O su disco/volume di rete) gira in un thread,
    quindi non blocca mai l'event loop e con essa tutti gli altri server;
  * DEBOUNCED — le scritture ravvicinate vengono accorpate in una sola (vedi
    SAVE_DEBOUNCE_SECONDS), e si riscrivono solo i file effettivamente modificati.

Permessi (tre livelli distinti):
  * bot operator — chi HOSTA il bot (variabile d'ambiente BOT_OWNER_IDS). Serve solo
    per la diagnostica: NON ha alcun potere sui ticket o sulla configurazione degli
    altri server;
  * amministratore del server — il proprietario Discord della guild, chi ha il permesso
    Administrator, o chi è stato aggiunto con /config owner add. Vale solo in quel server;
  * staff — amministratore del server, oppure chi ha il ruolo staff globale della guild
    o il ruolo staff di una sua sezione.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands

log = logging.getLogger("ticketbot.config")

# ---------------------------------------------------------------------------
# File .env (opzionale) — caricato prima di leggere qualunque variabile.
# Evita di scrivere token e ID dentro il codice sorgente: bastano due righe in un
# file .env accanto al bot (che va escluso da git / dalle condivisioni).
#
#     DISCORD_TOKEN=il-tuo-token
#     BOT_OWNER_IDS=123456789012345678
#
# Le variabili già presenti nell'ambiente hanno la precedenza sul file.
# ---------------------------------------------------------------------------
def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Impossibile leggere %s: %s", path.name, exc)
        return
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Percorsi dei file dati
# ---------------------------------------------------------------------------
BASE_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent))
BASE_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = BASE_DIR / "config_ticket.json"
ACTIVE_FILE = BASE_DIR / "tickets_active.json"
HISTORY_FILE = BASE_DIR / "tickets_history.json"
PREFS_FILE = BASE_DIR / "staff_preferences.json"

_FILE_PATHS = {
    "config": CONFIG_FILE,
    "active": ACTIVE_FILE,
    "history": HISTORY_FILE,
    "prefs": PREFS_FILE,
}
_ALL_FILES = tuple(_FILE_PATHS)

# Le scritture ravvicinate vengono accorpate in una sola dopo questo intervallo.
SAVE_DEBOUNCE_SECONDS = 2.0
# Dopo quanti giorni la configurazione di un server da cui il bot è stato rimosso
# viene eliminata (lo storico dei ticket chiusi viene comunque conservato).
LEFT_GUILD_RETENTION_DAYS = 30
# Dopo quanti giorni una candidatura iniziata in DM e mai completata è considerata
# abbandonata. Senza scadenza resterebbe per sempre nell'indice globale e
# bloccherebbe in eterno nuovi tentativi di quell'utente in quel server.
CANDIDATURE_SESSION_TTL_DAYS = 14
# Dopo quanti giorni si smette di tenere il riferimento al messaggio di riepilogo
# di una candidatura di cui nessuno ha mai deciso l'esito.
SUMMARY_REF_TTL_DAYS = 90
# Ogni quante ore girano le pulizie periodiche (vedi main.maintenance_loop). Su un
# bot sempre acceso non basta farle all'avvio: senza questo giro i file crescono
# senza limite e ogni salvataggio rallenta per TUTTI i server.
MAINTENANCE_INTERVAL_HOURS = 6


# ---------------------------------------------------------------------------
# ⚙️ Owner del BOT (chi lo hosta) — variabile d'ambiente BOT_OWNER_IDS
#
# Elenco di ID Discord separati da virgola, es:  BOT_OWNER_IDS=123456,7891011
# Serve SOLO per /config diagnostica. Non dà accesso ai ticket né alla
# configurazione dei server: ogni server nomina i propri amministratori con
# /config owner add (oltre al proprietario Discord e a chi ha Administrator).
# ---------------------------------------------------------------------------
def _parse_id_list(raw: str | None) -> set[int]:
    ids: set[int] = set()
    for chunk in (raw or "").replace(";", ",").replace(" ", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            ids.add(int(chunk))
    return ids


BOT_OWNER_ID = 1070724341928571050
BOT_OPERATOR_IDS: set[int] = _parse_id_list(os.getenv("BOT_OWNER_IDS")) | {BOT_OWNER_ID}

# ---------------------------------------------------------------------------
# Valori della vecchia versione single-server, usati UNA SOLA VOLTA dalla
# migrazione automatica per non perdere la configurazione già funzionante del
# server di origine. Non vengono più letti a runtime da nessuna parte.
# ---------------------------------------------------------------------------
MIGRATION_V2 = "v2-multiserver"

# ---------------------------------------------------------------------------
# Configurazione di default di OGNI server
# ---------------------------------------------------------------------------
DEFAULT_TEAMS = ["Staff", "Content Creator"]

DEFAULT_GUILD = {
    # --- canali e ruoli ---
    "transcript_channel": None,
    "staff_role": None,                # ruolo staff globale della guild
    "sla_channel": None,               # canale avvisi SLA (/slachannel)
    "log_ticket_staff_channel": None,  # canale di log del sistema ticket (/setlogticketstaff)

    # --- amministrazione DI QUESTO server (/config owner) ---
    "owner_ids": [],
    # Provenienza delle deleghe create esplicitamente con /config owner add.
    # Serve a distinguere una delega manuale da owner_ids ereditati da versioni
    # precedenti che inserivano automaticamente il proprietario Discord.
    "owner_grants": {},
    # Solo metadato di lifecycle: il proprietario Discord ottiene i permessi in
    # modo dinamico e non viene mai inserito in owner_ids.
    "observed_owner_id": None,

    # --- pannello, sezioni e ticket ---
    "sections": {},                    # key -> {label, emoji, description, category_id, staff_role_id, color}
    # Riferimento al pannello pubblicato + la sua personalizzazione, così ogni
    # server può mantenerlo allineato alle proprie sezioni senza ripubblicarlo.
    "panel": {"channel_id": None, "message_id": None,
              "title": None, "description": None, "color": None},
    "tickets": {},                     # str(channel_id) -> dati ticket (vedi tickets.py)
    "ticket_counter": 0,
    "blacklist": [],                   # user_id a cui è vietato aprire ticket

    # --- notifiche DM allo staff: opt-out PER SERVER (/config, pulsante in DM) ---
    "ticket_notify_optout": [],

    # --- automazioni configurabili (/config sla, /config claimtimeout, ...) ---
    "sla_seconds": 300,                # avviso se nessuno staff risponde entro questo tempo
    "claim_timeout_seconds": 120,      # anti-abbandono: claim rimosso se il claimer tace
    "inactivity_hours": 24,            # ore citate dal promemoria /risponditicket

    # --- candidature staff ---
    "staff_questions": [],                  # domande del questionario (/domandestaff)
    "candidature_staff_channel": None,      # canale destinazione (/candidaturestaffcanale)
    "candidature_summary_messages": {},     # str(user_id) -> {"channel_id","message_id","notice_message_id"}
    "candidature_types": {},                # key -> tipo candidatura per questa guild
    # Chiavi legacy mantenute in lettura per la migrazione dei dati esistenti.
    # Chiavi legacy mantenute solo per la migrazione dei dati esistenti.
    "staff_accepted_role_ids": [],
    "nick_format": None,
    "teams": list(DEFAULT_TEAMS),           # team proposti da /candidatura (/config team)

    # --- personalizzazione ---
    "branding": None,                  # footer di transcript/help; None = nome del server

    # --- ciclo di vita ---
    "left_at": None,                   # timestamp di rimozione del bot dal server
}

DEFAULT_PREFS = {
    # Indice cross-guild delle candidature in corso: i DM non hanno contesto
    # server, quindi la chiave è "guild_id:user_id" e non il solo user_id.
    "candidature_sessions": {},
    # str(user_id) -> guild_id scelta quando l'utente ha più candidature aperte
    # contemporaneamente in server diversi.
    "candidature_active": {},
    # Conversazioni avviate dallo staff Manager: guild_id:user_id -> messaggi.
    "candidature_dm_threads": {},
    # nomi delle migrazioni già eseguite
    "migrations": [],
}


def _clone(value):
    """Copia profonda di un valore JSON-serializzabile."""
    return json.loads(json.dumps(value))


def _load_json(path: Path, default_factory):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Impossibile leggere %s (%s): riparto dai valori di default.", path.name, exc)
            return default_factory()
        expected = default_factory()
        if not isinstance(loaded, type(expected)):
            log.error("Struttura non valida in %s: atteso %s, trovato %s; riparto dai valori di default.",
                      path.name, type(expected).__name__, type(loaded).__name__)
            return expected
        return loaded
    return default_factory()


def _atomic_write(path: Path, text: str) -> None:
    """Scrive su un file temporaneo nella stessa cartella e poi lo rinomina.
    os.replace è atomico: il file di destinazione o è quello vecchio integro o è
    quello nuovo completo, mai un ibrido troncato."""
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _write_payloads(payloads: dict[Path, str]) -> None:
    """Eseguita in un thread separato: solo I/O, nessun accesso alle strutture in memoria."""
    for path, text in payloads.items():
        _atomic_write(path, text)


class ConfigManager:
    """Carica/salva la configurazione e i dati del sistema ticket di tutti i server.

    Tutte le mutazioni avvengono sull'event loop; il salvataggio è debounced e la
    scrittura su disco viene delegata a un thread, così nessun server può bloccare
    gli altri."""

    def __init__(self):
        self.data: dict[str, dict] = {}   # str(guild_id) -> config + ticket attivi
        self.history: dict[str, list] = {}  # str(guild_id) -> ticket chiusi
        self.prefs: dict = {}               # indice candidature + migrazioni
        self._dirty: set[str] = set()
        self._flush_task: asyncio.Task | None = None
        self._flush_lock = asyncio.Lock()
        self.load()

    # -- caricamento --------------------------------------------------------

    def load(self) -> None:
        config_raw = _load_json(CONFIG_FILE, dict)
        active_raw = _load_json(ACTIVE_FILE, dict)
        self.history = _load_json(HISTORY_FILE, dict)
        self.prefs = _load_json(PREFS_FILE, dict)
        self.history = {
            str(gid): entries for gid, entries in self.history.items()
            if str(gid).isdigit() and isinstance(entries, list)
        }

        for key, value in DEFAULT_PREFS.items():
            if key not in self.prefs or not isinstance(self.prefs[key], type(value)):
                self.prefs[key] = _clone(value)

        self.data = {}
        for gid in set(config_raw) | set(active_raw):
            # Scarta lo pseudo-server "_global" della vecchia versione: le
            # preferenze non vivono più dentro l'indice delle guild.
            if not str(gid).isdigit():
                continue
            gconf = _clone(DEFAULT_GUILD)
            raw_gconf = config_raw.get(gid)
            if isinstance(raw_gconf, dict):
                gconf.update(raw_gconf)
            raw_tickets = active_raw.get(gid)
            gconf["tickets"] = raw_tickets if isinstance(raw_tickets, dict) else {}
            self._ensure_defaults(gconf)
            self._normalize_guild_data(gconf)
            self.data[str(gid)] = gconf

        self._normalize_prefs()

        self._migrate()
        log.info("Configurazione caricata: %d server, %d con storico.", len(self.data), len(self.history))

    @staticmethod
    def _ensure_defaults(gconf: dict) -> None:
        """Aggiunge le chiavi introdotte dalle versioni successive senza toccare i valori già presenti."""
        for key, value in DEFAULT_GUILD.items():
            if key not in gconf:
                gconf[key] = _clone(value)
            elif isinstance(value, (list, dict)) and not isinstance(gconf[key], type(value)):
                gconf[key] = _clone(value)

        # Il pannello è un dizionario a struttura fissa: se una versione precedente
        # ne ha salvato solo una parte, completiamo le chiavi mancanti. Altrimenti
        # un server aggiornato avrebbe un 'panel' più povero di uno nuovo e chi lo
        # legge per chiave (non con .get) andrebbe in errore solo per quel server.
        panel = gconf["panel"]
        for key, value in DEFAULT_GUILD["panel"].items():
            panel.setdefault(key, value)

    @staticmethod
    def _normalize_guild_data(gconf: dict) -> None:
        """Scarta solo strutture annidate non utilizzabili, senza mai fondere
        dati provenienti da una guild diversa."""
        for key in ("sections", "tickets", "owner_grants", "panel"):
            if not isinstance(gconf.get(key), dict):
                gconf[key] = _clone(DEFAULT_GUILD[key])
        gconf["sections"] = {k: v for k, v in gconf["sections"].items() if isinstance(v, dict)}
        gconf["tickets"] = {k: v for k, v in gconf["tickets"].items()
                             if str(k).isdigit() and isinstance(v, dict)}
        gconf["owner_ids"] = [int(v) for v in (gconf.get("owner_ids") or [])
                               if str(v).isdigit()]
        staff_role = gconf.get("staff_role")
        gconf["staff_role"] = int(staff_role) if str(staff_role).isdigit() else None
        gconf["owner_grants"] = {str(k): v for k, v in gconf["owner_grants"].items()
                                  if str(k).isdigit() and isinstance(v, dict)
                                  and v.get("source") == "manual"}

    def _normalize_prefs(self) -> None:
        sessions = self.prefs.get("candidature_sessions")
        if not isinstance(sessions, dict):
            self.prefs["candidature_sessions"] = {}
        else:
            self.prefs["candidature_sessions"] = {
                str(k): v for k, v in sessions.items()
                # Le chiavi legacy senza ':' vengono mantenute finché _migrate()
                # può reindicizzarle usando il guild_id presente nella sessione.
                if isinstance(v, dict)
            }
        active = self.prefs.get("candidature_active")
        if not isinstance(active, dict):
            self.prefs["candidature_active"] = {}
        migrations = self.prefs.get("migrations")
        if not isinstance(migrations, list):
            self.prefs["migrations"] = []

    def _migrate(self) -> None:
        """Porta i dati della vecchia versione single-server al formato multi-server.
        Idempotente: può girare a ogni avvio senza effetti collaterali."""
        done = self.prefs.setdefault("migrations", [])
        changed = False

        # 1) La vecchia lista di opt-out era globale e non riportava la guild di
        #    provenienza. Non è attribuibile in modo sicuro: la eliminiamo invece
        #    di copiarla in qualunque server. Anche se questo file viene ricreato,
        #    la migrazione non potrà mai propagare preferenze cross-guild.
        legacy_optout = self.prefs.pop("ticket_notify_optout", None)
        if legacy_optout is not None:
            changed = True

        # 2) Sessioni candidatura: la chiave era il solo user_id (una sola
        #    candidatura per utente in TUTTI i server). Ora è "guild_id:user_id".
        sessions = self.prefs.setdefault("candidature_sessions", {})
        for key in list(sessions):
            if ":" in str(key):
                continue
            session = sessions.pop(key)
            guild_id = session.get("guild_id")
            if guild_id:
                sessions[f"{guild_id}:{key}"] = session
            changed = True

        # owner_ids delle versioni precedenti non riportavano la provenienza:
        # potevano essere owner automatici oppure deleghe manuali. Per sicurezza
        # non li consideriamo deleghe esplicite. D'ora in poi solo owner_grants
        # può conservarle attraverso un trasferimento di proprietà.
        for gconf in self.data.values():
            grants = gconf.get("owner_grants") or {}
            owners = gconf.get("owner_ids") or []
            known = {int(uid) for uid in grants if str(uid).isdigit()}
            filtered = [uid for uid in owners if int(uid) in known]
            if filtered != owners:
                gconf["owner_ids"] = filtered
                changed = True

            # Migrazione one-shot delle impostazioni candidatura precedenti:
            # il vecchio tipo unico diventa il tipo per-guild "staff".
            types = gconf.setdefault("candidature_types", {})
            if not types and (
                gconf.get("staff_questions")
                or gconf.get("staff_accepted_role_ids")
                or gconf.get("nick_format")
            ):
                old_roles = gconf.get("staff_accepted_role_ids") or []
                types["staff"] = {
                    "name": "Staff",
                    "role_id": old_roles[0] if old_roles else None,
                    "mention_role_id": gconf.get("candidature_staff_role"),
                    "nickname_format": gconf.get("nick_format"),
                    "questions": list(gconf.get("staff_questions") or []),
                }
                changed = True
            for candidature_config in types.values():
                if isinstance(candidature_config, dict):
                    candidature_config.setdefault("mention_role_id", None)
            legacy_mention_role = gconf.get("candidature_staff_role")
            if legacy_mention_role and isinstance(types.get("staff"), dict):
                types["staff"]["mention_role_id"] = types["staff"].get("mention_role_id") or legacy_mention_role
            if "candidature_staff_role" in gconf:
                gconf.pop("candidature_staff_role", None)
                changed = True

        # 3) Owner, ruoli, branding e nickname globali della vecchia versione non
        #    avevano un guild_id. Non esiste un'attribuzione sicura, quindi non
        #    vengono mai migrati. Le configurazioni già salvate sotto una guild
        #    restano intatte grazie al merge per chiave eseguito in load().
        #    La perdita del marker non cambia questa proprietà di sicurezza.
        if MIGRATION_V2 not in done:
            done.append(MIGRATION_V2)
            changed = True
            log.info("Migrazione '%s' completata senza copiare dati globali legacy.", MIGRATION_V2)

        if changed:
            self.mark_dirty(*_ALL_FILES)

    # -- salvataggio --------------------------------------------------------

    def mark_dirty(self, *files: str) -> None:
        """Segna dei file come 'da riscrivere' e pianifica il flush debounced."""
        self._dirty.update(files or _ALL_FILES)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Nessun event loop (avvio, migrazione, shutdown): scrittura immediata.
            self.flush_sync()
            return
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = loop.create_task(self._flush_after_delay())

    def save(self) -> None:
        """Compatibile con il resto del codice: segna configurazione, ticket
        attivi e preferenze come da salvare. Non blocca e non scrive subito:
        il flush avviene accorpato entro SAVE_DEBOUNCE_SECONDS."""
        self.mark_dirty("config", "active", "prefs")

    async def _flush_after_delay(self) -> None:
        cancelled = False
        try:
            await asyncio.sleep(SAVE_DEBOUNCE_SECONDS)
            # Una mutazione può arrivare mentre il batch precedente è nel thread
            # di I/O. Continuiamo finché non restano file sporchi: nessun batch
            # può restare senza un flush pianificato.
            while self._dirty:
                await self.flush()
        except asyncio.CancelledError:
            # In chiusura il chiamante esegue flush(); qui non creiamo un nuovo
            # task mentre il loop sta sparendo.
            cancelled = True
            raise
        except Exception:
            log.exception("Flush della configurazione fallito.")
        finally:
            self._flush_task = None
            # In caso di errore di scrittura flush() ripristina _dirty; riproviamo
            # con il normale debounce anziché perdere la modifica.
            if self._dirty and not cancelled:
                self.mark_dirty()

    def _build_payloads(self) -> dict[Path, str]:
        """Serializza i file sporchi. Gira sull'event loop (quindi senza rischio di
        mutazioni concorrenti); la scrittura vera avviene poi in un thread."""
        if not self._dirty:
            return {}
        dirty, self._dirty = self._dirty, set()
        payloads: dict[Path, str] = {}

        if "config" in dirty:
            config_out = {
                gid: {k: v for k, v in gconf.items() if k != "tickets"}
                for gid, gconf in self.data.items()
            }
            payloads[CONFIG_FILE] = json.dumps(config_out, indent=2, ensure_ascii=False)
        if "active" in dirty:
            active_out = {
                gid: gconf.get("tickets", {})
                for gid, gconf in self.data.items()
                if gconf.get("tickets")
            }
            payloads[ACTIVE_FILE] = json.dumps(active_out, indent=2, ensure_ascii=False)
        if "history" in dirty:
            payloads[HISTORY_FILE] = json.dumps(self.history, indent=2, ensure_ascii=False)
        if "prefs" in dirty:
            payloads[PREFS_FILE] = json.dumps(self.prefs, indent=2, ensure_ascii=False)
        return payloads

    async def flush(self) -> None:
        """Scrive subito su disco tutto ciò che è in sospeso, senza bloccare l'event loop."""
        async with self._flush_lock:
            dirty_before = set(self._dirty)
            payloads = self._build_payloads()
            if not payloads:
                return
            try:
                await asyncio.to_thread(_write_payloads, payloads)
            except Exception:
                # _build_payloads ha già svuotato _dirty per poter accettare
                # mutazioni concorrenti. Rimettiamo nel set il batch fallito:
                # così né un errore I/O né uno shutdown successivo perdono dati.
                self._dirty.update(dirty_before)
                raise

    def flush_sync(self) -> None:
        """Variante sincrona, per l'avvio e lo spegnimento (nessun loop attivo)."""
        dirty_before = set(self._dirty)
        payloads = self._build_payloads()
        if payloads:
            try:
                _write_payloads(payloads)
            except Exception:
                self._dirty.update(dirty_before)
                raise

    # -- accesso ai dati per-guild -----------------------------------------

    def guild(self, guild_id) -> dict:
        """Configurazione + ticket attivi di un server, creandola se non esiste."""
        gid = str(guild_id)
        gconf = self.data.get(gid)
        if gconf is None:
            gconf = _clone(DEFAULT_GUILD)
            self.data[gid] = gconf
            self.mark_dirty("config")
        else:
            self._ensure_defaults(gconf)
        return gconf

    def peek(self, guild_id) -> dict | None:
        """Come guild(), ma NON crea nulla: per i percorsi caldi (on_message) dove
        non vogliamo che ogni messaggio di un server sconosciuto generi una scrittura."""
        return self.data.get(str(guild_id))

    def setting(self, guild_id, key):
        """Valore di un'impostazione del server, con fallback al default globale.

        Restituisce il valore memorizzato anche se è None: per alcune impostazioni
        (nick_format, branding) None ha un significato preciso ('non toccare il
        nickname', 'usa il nome del server') e non va sostituito col default."""
        gconf = self.peek(guild_id)
        if gconf is not None and key in gconf:
            return gconf[key]
        return DEFAULT_GUILD.get(key)

    def guild_history(self, guild_id) -> list:
        gid = str(guild_id)
        if gid not in self.history:
            self.history[gid] = []
            self.mark_dirty("history")
        return self.history[gid]

    def history_count(self, guild_id) -> int:
        """Quanti ticket chiusi ha un server. Sola lettura: a differenza di
        guild_history() non crea la lista e non provoca una scrittura su disco
        (serve nei comandi di sola consultazione, es. /config lista)."""
        return len(self.history.get(str(guild_id)) or ())

    def add_history_entry(self, guild_id, entry: dict) -> None:
        self.guild_history(guild_id).append(entry)
        self.mark_dirty("history")

    def global_conf(self) -> dict:
        """Dati non legati a un singolo server: solo l'indice delle candidature in
        corso, che per natura arriva dai DM e quindi non ha contesto guild."""
        return self.prefs

    # -- amministratori per-guild ------------------------------------------

    def admin_ids(self, guild_id) -> list[int]:
        gconf = self.peek(guild_id)
        return list(gconf.get("owner_ids") or []) if gconf else []

    def add_admin(self, guild_id, user_id: int) -> bool:
        gconf = self.guild(guild_id)
        owners = gconf.setdefault("owner_ids", [])
        if user_id in owners:
            return False
        owners.append(user_id)
        gconf.setdefault("owner_grants", {})[str(user_id)] = {"source": "manual"}
        self.mark_dirty("config")
        return True

    def remove_admin(self, guild_id, user_id: int) -> bool:
        gconf = self.guild(guild_id)
        owners = gconf.setdefault("owner_ids", [])
        if user_id not in owners:
            return False
        owners.remove(user_id)
        gconf.setdefault("owner_grants", {}).pop(str(user_id), None)
        self.mark_dirty("config")
        return True

    # -- opt-out notifiche, per-guild --------------------------------------

    def notify_optout(self, guild_id) -> list[int]:
        gconf = self.peek(guild_id)
        return list(gconf.get("ticket_notify_optout") or []) if gconf else []

    def set_notify_optout(self, guild_id, user_id: int, opted_out: bool) -> bool:
        """Ritorna True se lo stato è cambiato."""
        optout = self.guild(guild_id).setdefault("ticket_notify_optout", [])
        if opted_out and user_id not in optout:
            optout.append(user_id)
        elif not opted_out and user_id in optout:
            optout.remove(user_id)
        else:
            return False
        self.mark_dirty("config")
        return True

    # -- sessioni candidatura (chiave "guild_id:user_id") ------------------

    @staticmethod
    def session_key(guild_id, user_id) -> str:
        return f"{guild_id}:{user_id}"

    def _sessions(self) -> dict:
        return self.prefs.setdefault("candidature_sessions", {})

    def get_session(self, guild_id, user_id) -> dict | None:
        return self._sessions().get(self.session_key(guild_id, user_id))

    def set_session(self, guild_id, user_id, session: dict) -> None:
        self._sessions()[self.session_key(guild_id, user_id)] = session
        self.mark_dirty("prefs")

    def pop_session(self, guild_id, user_id) -> dict | None:
        session = self._sessions().pop(self.session_key(guild_id, user_id), None)
        active = self.prefs.setdefault("candidature_active", {})
        active_guild = self.active_session_guild(user_id)
        if active_guild == int(guild_id) or str(active.get(str(user_id))) == str(guild_id):
            active.pop(str(user_id), None)
        self.mark_dirty("prefs")
        return session

    def sessions_for_user(self, user_id) -> dict[int, dict]:
        """Tutte le candidature aperte da un utente, indicizzate per guild_id.
        Serve nei DM, dove non esiste contesto server."""
        found: dict[int, dict] = {}
        suffix = f":{user_id}"
        for key, session in self._sessions().items():
            if not str(key).endswith(suffix):
                continue
            raw_gid = str(key).rsplit(":", 1)[0]
            if raw_gid.isdigit():
                found[int(raw_gid)] = session
        return found

    @staticmethod
    def session_expired(session: dict | None) -> bool:
        """True se una candidatura è stata iniziata troppo tempo fa e va considerata
        abbandonata. Senza questo controllo un questionario mai completato
        bloccherebbe per sempre nuovi tentativi di quell'utente in quel server."""
        if not session:
            return False
        # Una candidatura completata resta in attesa dello staff e non è una
        # sessione di compilazione da far scadere.
        if session.get("status", "in_progress") != "in_progress":
            return False
        started = session.get("started_at")
        if not isinstance(started, int):
            return False
        cutoff = int(datetime.now(timezone.utc).timestamp()) - CANDIDATURE_SESSION_TTL_DAYS * 86400
        return started < cutoff

    def active_session_guild(self, user_id) -> int | None:
        raw = self.prefs.setdefault("candidature_active", {}).get(str(user_id))
        # I puntatori legacy erano semplici interi impostati automaticamente al
        # momento dell'ultima candidatura. Non sono una scelta dell'utente e non
        # possono instradare messaggi DM ambigui.
        if not isinstance(raw, dict) or raw.get("explicit") is not True:
            return None
        guild_id = raw.get("guild_id")
        return int(guild_id) if str(guild_id).isdigit() else None

    def set_active_session_guild(self, user_id, guild_id) -> None:
        active = self.prefs.setdefault("candidature_active", {})
        if guild_id is None:
            active.pop(str(user_id), None)
        else:
            active[str(user_id)] = {"guild_id": int(guild_id), "explicit": True}
        self.mark_dirty("prefs")

    def drop_guild_sessions(self, guild_id) -> int:
        """Elimina tutte le candidature in corso di un server (bot rimosso, reset...)."""
        prefix = f"{guild_id}:"
        sessions = self._sessions()
        removed = [key for key in sessions if str(key).startswith(prefix)]
        for key in removed:
            user_id = str(key).split(":", 1)[1]
            sessions.pop(key, None)
            active = self.prefs.setdefault("candidature_active", {})
            active_guild = self.active_session_guild(user_id)
            if active_guild == int(guild_id) or str(active.get(user_id)) == str(guild_id):
                active.pop(user_id, None)
        if removed:
            self.mark_dirty("prefs")
        return len(removed)

    # -- ciclo di vita del server ------------------------------------------

    def mark_guild_joined(self, guild_id, owner_id: int | None = None) -> dict:
        gconf = self.guild(guild_id)
        gconf["left_at"] = None
        self.reconcile_guild_owner(guild_id, owner_id)
        self.mark_dirty("config")
        return gconf

    def reconcile_guild_owner(self, guild_id, owner_id: int | None) -> bool:
        """Aggiorna il proprietario Discord osservato senza trasformarlo in admin
        persistente. Se una versione precedente aveva inserito il vecchio owner
        in owner_ids, lo rimuoviamo quando rileviamo il trasferimento: il nuovo
        owner è già autorizzato dinamicamente da is_admin_member()."""
        gconf = self.guild(guild_id)
        previous = gconf.get("observed_owner_id")
        changed = False
        if previous is not None and previous != owner_id:
            owners = gconf.setdefault("owner_ids", [])
            grants = gconf.setdefault("owner_grants", {})
            if previous in owners and str(previous) not in grants:
                owners.remove(previous)
                changed = True
        if gconf.get("observed_owner_id") != owner_id:
            gconf["observed_owner_id"] = owner_id
            changed = True
        if changed:
            self.mark_dirty("config")
        return changed

    def mark_guild_left(self, guild_id) -> None:
        """Il bot è stato rimosso: azzeriamo lo stato volatile e marchiamo la data.
        La configurazione resta per LEFT_GUILD_RETENTION_DAYS, così un re-invito
        (o una rimozione accidentale) non fa perdere il setup."""
        gconf = self.peek(guild_id)
        if gconf is None:
            return
        gconf["left_at"] = int(datetime.now(timezone.utc).timestamp())
        gconf["tickets"] = {}
        self.drop_guild_sessions(guild_id)
        self.mark_dirty("config", "active", "prefs")

    def forget_guild(self, guild_id, *, keep_history: bool = True) -> None:
        gid = str(guild_id)
        self.data.pop(gid, None)
        self.drop_guild_sessions(guild_id)
        if not keep_history:
            self.history.pop(gid, None)
        self.mark_dirty(*_ALL_FILES)

    def purge_stale_guilds(self) -> list[str]:
        """Elimina la configurazione dei server abbandonati da troppo tempo, così i
        file non crescono all'infinito (e ogni salvataggio non rallenta per sempre)."""
        cutoff = int((datetime.now(timezone.utc) - timedelta(days=LEFT_GUILD_RETENTION_DAYS)).timestamp())
        stale = [
            gid for gid, gconf in self.data.items()
            # 'is not None' e non un semplice test di verità: un left_at molto
            # vecchio (al limite 0) è falsy ma indica proprio il caso da ripulire.
            if gconf.get("left_at") is not None and gconf["left_at"] < cutoff
        ]
        for gid in stale:
            self.forget_guild(gid, keep_history=True)
        if stale:
            log.info("Rimossa la configurazione di %d server abbandonati da oltre %d giorni.",
                     len(stale), LEFT_GUILD_RETENTION_DAYS)
        return stale

    def purge_stale_sessions(self) -> list[tuple[int, int]]:
        """Abbandona le candidature iniziate in DM e mai completate da troppo tempo.

        Restituisce le coppie (guild_id, user_id) scartate. Senza scadenza una
        sessione lasciata a metà resterebbe per sempre nell'indice globale (che è
        condiviso da tutti i server) e continuerebbe a far rispondere
        `/candidatura invia` "ha già una candidatura in corso"."""
        now = int(datetime.now(timezone.utc).timestamp())
        cutoff = now - CANDIDATURE_SESSION_TTL_DAYS * 86400
        sessions = self._sessions()
        scadute: list[tuple[int, int]] = []
        toccato = False

        for key, session in list(sessions.items()):
            raw_gid, sep, raw_uid = str(key).partition(":")
            if not sep or not raw_gid.isdigit() or not raw_uid.isdigit() or not isinstance(session, dict):
                # Chiave malformata o dato corrotto (residuo di un formato
                # precedente): non è riconducibile a nessun server, quindi va via.
                sessions.pop(key, None)
                toccato = True
                continue
            started = session.get("started_at")
            if not isinstance(started, int):
                # Sessione senza timestamp (creata da una versione precedente): la
                # datiamo adesso invece di buttarla, così scadrà regolarmente più
                # avanti senza che una candidatura ancora viva vada persa.
                session["started_at"] = now
                toccato = True
                continue
            if session.get("status", "in_progress") == "in_progress" and started < cutoff:
                scadute.append((int(raw_gid), int(raw_uid)))

        if toccato:
            self.mark_dirty("prefs")
        for guild_id, user_id in scadute:
            self.pop_session(guild_id, user_id)
        if scadute:
            log.info("Scartate %d candidature mai completate da oltre %d giorni.",
                     len(scadute), CANDIDATURE_SESSION_TTL_DAYS)
        return scadute

    def purge_stale_summaries(self) -> int:
        """Dimentica i riferimenti ai riepiloghi di candidatura di cui nessuno ha mai
        deciso l'esito. /candidatura accettata|rifiutata li
        consumano, ma una candidatura semplicemente ignorata resterebbe indicizzata
        per sempre nella configurazione di quel server."""
        now = int(datetime.now(timezone.utc).timestamp())
        cutoff = now - SUMMARY_REF_TTL_DAYS * 86400
        rimossi = 0
        toccato = False

        for gconf in self.data.values():
            refs = gconf.get("candidature_summary_messages")
            if not isinstance(refs, dict):
                continue
            for user_id, ref in list(refs.items()):
                if not isinstance(ref, dict):
                    refs.pop(user_id, None)
                    rimossi += 1
                    continue
                created = ref.get("created_at")
                if not isinstance(created, int):
                    # Riferimento salvato prima che tenessimo la data: lo datiamo
                    # adesso, così scade più avanti invece di sparire subito.
                    ref["created_at"] = now
                    toccato = True
                    continue
                if created < cutoff:
                    refs.pop(user_id, None)
                    rimossi += 1

        if rimossi or toccato:
            self.mark_dirty("config")
        if rimossi:
            log.info("Rimossi %d riferimenti a riepiloghi di candidatura più vecchi di %d giorni.",
                     rimossi, SUMMARY_REF_TTL_DAYS)
        return rimossi

    def run_maintenance(self) -> dict[str, int]:
        """Pulizie periodiche di tutta l'istanza: server abbandonati, candidature mai
        completate, riferimenti a riepiloghi mai risolti.

        Va eseguita ogni tanto e non solo all'avvio: un bot che resta acceso per mesi
        accumulerebbe altrimenti dati di server e utenti che non esistono più, e
        poiché ogni salvataggio riscrive i file interi, il rallentamento ricadrebbe
        su tutti i server."""
        return {
            "guilds": len(self.purge_stale_guilds()),
            "sessions": len(self.purge_stale_sessions()),
            "summaries": self.purge_stale_summaries(),
        }


# Istanza condivisa (singleton) importata da tutti gli altri moduli
cfg = ConfigManager()


# ---------------------------------------------------------------------------
# Impostazioni per-server
# ---------------------------------------------------------------------------

def guild_setting(guild_id, key):
    """Valore di un'impostazione del server, con fallback al default globale."""
    return cfg.setting(guild_id, key)


def sla_seconds(guild_id) -> int:
    return int(guild_setting(guild_id, "sla_seconds") or DEFAULT_GUILD["sla_seconds"])


def claim_timeout_seconds(guild_id) -> int:
    return int(guild_setting(guild_id, "claim_timeout_seconds") or DEFAULT_GUILD["claim_timeout_seconds"])


def inactivity_hours(guild_id) -> int:
    return int(guild_setting(guild_id, "inactivity_hours") or DEFAULT_GUILD["inactivity_hours"])


def guild_teams(guild_id) -> list[str]:
    teams = guild_setting(guild_id, "teams")
    return list(teams) if teams else list(DEFAULT_TEAMS)


def branding_text(guild: discord.Guild | None) -> str:
    """Testo del footer di transcript/help. Se il server non ne ha configurato uno
    con /config branding, si usa il nome del server: nessun riferimento cablato."""
    if guild is None:
        return "Sistema Ticket"
    custom = guild_setting(guild.id, "branding")
    if custom:
        return str(custom)
    return f"{guild.name} · Sistema Ticket"


def format_minutes(seconds: int) -> str:
    minutes, sec = divmod(int(seconds), 60)
    if minutes and sec:
        return f"{minutes} min {sec} s"
    if minutes:
        return f"{minutes} minut{'o' if minutes == 1 else 'i'}"
    return f"{sec} secondi"


# ---------------------------------------------------------------------------
# Utility Discord
# ---------------------------------------------------------------------------

async def fetch_member(guild: discord.Guild | None, user_id: int | None) -> discord.Member | None:
    """Membro di un server, con fallback all'API se non è in cache.

    Serve perché il bot non fa più il chunking completo di tutti i server all'avvio
    (insostenibile in memoria con molti server): guild.get_member() può quindi
    restituire None per un membro che esiste davvero."""
    if not guild or not user_id:
        return None
    member = guild.get_member(int(user_id))
    if member is not None:
        return member
    try:
        return await guild.fetch_member(int(user_id))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


# ---------------------------------------------------------------------------
# Permessi
# ---------------------------------------------------------------------------

def get_bot_operator_ids() -> set[int]:
    """ID di chi hosta il bot (BOT_OWNER_IDS). Solo diagnostica: nessun potere
    sui ticket o sulla configurazione dei server."""
    return set(BOT_OPERATOR_IDS)


def is_bot_operator(user: discord.abc.User | int | None) -> bool:
    if user is None:
        return False
    user_id = user if isinstance(user, int) else user.id
    return user_id in BOT_OPERATOR_IDS


def is_admin_member(member: discord.Member, guild_id: int | None = None) -> bool:
    """Amministratore DI QUEL SERVER: proprietario Discord della guild, permesso
    Administrator, oppure aggiunto con /config owner add.

    Se il membro appartiene a un server diverso da quello richiesto la risposta è
    sempre False: essere proprietario o amministratore altrove non dà alcun
    privilegio qui."""
    if not isinstance(member, discord.Member):
        return False
    gid = int(guild_id) if guild_id is not None else member.guild.id
    if member.guild.id != gid:
        return False
    if member.guild.owner_id == member.id:
        return True
    if member.guild_permissions.administrator:
        return True
    return member.id in cfg.admin_ids(gid)


def is_guild_admin(interaction: discord.Interaction) -> bool:
    """Come is_admin_member, a partire da un'Interaction."""
    if interaction.guild is None:
        return False
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    return is_admin_member(member, interaction.guild.id)


def is_staff_member(member: discord.Member, guild_id: int) -> bool:
    """Verifica lo staff nel contesto della guild, senza condividere ruoli tra server.

    Gli amministratori e i ruoli di sezione restano staff per compatibilità con
    i comandi ticket; il ruolo globale viene letto dalla configurazione della
    singola guild e, se assente, non autorizza membri aggiuntivi.
    """
    if not isinstance(member, discord.Member):
        return False
    if member.guild.id != int(guild_id):
        return False
    if is_admin_member(member, guild_id):
        return True
    gconf = cfg.peek(guild_id)
    if not gconf:
        return False
    role_ids = {r.id for r in member.roles}
    staff_role_id = gconf.get("staff_role")
    if staff_role_id is not None and str(staff_role_id).isdigit() and int(staff_role_id) in role_ids:
        return True
    for section in (gconf.get("sections") or {}).values():
        if section.get("staff_role_id") in role_ids:
            return True
    return False


def is_staff(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    return is_staff_member(member, interaction.guild.id)


NO_GUILD_MSG = "⚠️ Questo comando funziona solo dentro un server."
NOT_ADMIN_MSG = (
    "🚫 Serve essere amministratore di **questo server** per usare il comando "
    "(proprietario del server, permesso `Amministratore`, o aggiunto con `/config owner add`)."
)
NOT_STAFF_MSG = "🚫 Non hai il permesso di usare questo comando."


async def _deny(interaction: discord.Interaction, message: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


async def require_guild_admin(interaction: discord.Interaction) -> bool:
    """True se chi ha invocato il comando amministra QUESTO server, altrimenti
    risponde con il motivo del rifiuto e ritorna False."""
    if interaction.guild is None:
        await _deny(interaction, NO_GUILD_MSG)
        return False
    if not is_guild_admin(interaction):
        await _deny(interaction, NOT_ADMIN_MSG)
        return False
    return True


async def require_staff(interaction: discord.Interaction, message: str = NOT_STAFF_MSG) -> bool:
    if interaction.guild is None:
        await _deny(interaction, NO_GUILD_MSG)
        return False
    if not is_staff(interaction):
        await _deny(interaction, message)
        return False
    return True


def guild_admin_only():
    """Decoratore per i comandi riservati agli amministratori del server corrente."""

    async def predicate(interaction: discord.Interaction) -> bool:
        return is_guild_admin(interaction)

    return app_commands.check(predicate)


def staff_only():
    """Decoratore per i comandi riservati allo staff del server corrente."""

    async def predicate(interaction: discord.Interaction) -> bool:
        return is_staff(interaction)

    return app_commands.check(predicate)


def bot_operator_only():
    """Decoratore per i comandi diagnostici di chi hosta il bot."""

    async def predicate(interaction: discord.Interaction) -> bool:
        return is_bot_operator(interaction.user)

    return app_commands.check(predicate)


def parse_color(value: str | None, default: int = 0x5865F2) -> discord.Color:
    if not value:
        return discord.Color(default)
    value = str(value).strip().lstrip("#")
    try:
        return discord.Color(int(value, 16))
    except ValueError:
        return discord.Color(default)
