"""
main.py
Entry point del bot Discord "TicketBot" — sistema ticket completo + candidature.
Progettato per funzionare su un numero arbitrario di server contemporaneamente:
ogni guild ha configurazione, staff, ticket, automazioni e branding propri.

Avvio:
    1. pip install -r requirements.txt
    2. Crea un file .env accanto a main.py con:
           DISCORD_TOKEN=il-token-del-bot
           BOT_OWNER_IDS=123456789012345678      (opzionale, solo per /config diagnostica)
           DATA_DIR=/percorso/dati               (opzionale, dove salvare i JSON)
       In alternativa imposta le stesse variabili d'ambiente nel sistema/hosting.
    3. python main.py

Il token NON va mai scritto dentro un file di codice: chi lo legge controlla il bot
su tutti i server in cui è presente.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

# config.py carica automaticamente il file .env prima di leggere le variabili,
# quindi importarlo per primo rende disponibili DISCORD_TOKEN e BOT_OWNER_IDS.
from config import (
    cfg,
    branding_text,
    claim_timeout_seconds,
    get_bot_operator_ids,
    inactivity_hours,
    is_staff_member,
    sla_seconds,
    LEFT_GUILD_RETENTION_DAYS,
    MAINTENANCE_INTERVAL_HOURS,
)
from tickets import (
    ticket_group,
    TicketPanelView,
    TicketControlView,
    NotifyOptOutButton,
    NotifyOptInButton,
    bind_client,
    cancel_guild_timers,
    forget_deleted_ticket,
    handle_risponditicket,
    handle_setlogticketstaff,
    handle_slachannel,
    schedule_sla_check,
    schedule_claim_check,
    cancel_sla_check,
    cancel_claim_check,
    ticket_section_autocomplete,
)
from candidature import (
    candidatura_group,
    domandestaff_group,
    handle_candidaturestaffcanale,
    handle_sezionecandidature,
    handle_candidatura_dm_answer,
)
from settings import backup_group, config_group, ezticket_group

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
log = logging.getLogger("ticketbot")

# ---------------------------------------------------------------------------
# Token: letto da .env o dalle variabili d'ambiente, MAI dal codice.
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

# Numero di shard: normalmente lo decide Discord (None = automatico). Con molte
# migliaia di server può servire fissarlo a mano tramite SHARD_COUNT.
_raw_shards = os.getenv("SHARD_COUNT", "").strip()
SHARD_COUNT = int(_raw_shards) if _raw_shards.isdigit() and int(_raw_shards) > 0 else None

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

# Quanto attendere prima di aggiornare la presenza dopo un join/leave. Gli eventi
# arrivano a raffica e change_presence è soggetto a rate limit sul gateway: li
# accorpiamo in un solo aggiornamento (vedi schedule_presence_update).
PRESENCE_DEBOUNCE_SECONDS = 10.0
_presence_task: asyncio.Task | None = None

# Task di sfondo di cui teniamo un riferimento, così non vengono interrotti
# dal garbage collector a metà esecuzione.
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


class TicketBot(commands.AutoShardedBot):
    """AutoShardedBot: un solo processo, N shard gestite internamente. Necessario
    oltre i 2.500 server e comunque più robusto già da prima."""

    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            shard_count=SHARD_COUNT,
            # Con molti server non è sostenibile scaricare in memoria l'intera
            # lista membri di ognuno all'avvio: il chunking avviene su richiesta,
            # solo per la guild che serve (vedi tickets._collect_notify_recipients).
            chunk_guilds_at_startup=False,
            member_cache_flags=discord.MemberCacheFlags(joined=True, voice=False),
            # Nessuna cache dei messaggi: il bot non ne ha bisogno e su molti
            # server sarebbe il primo consumo di RAM.
            max_messages=None,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True, replied_user=False
            ),
        )
        self._startup_done = False

    async def setup_hook(self):
        # I timer devono poter risalire alla guild al momento dello scatto, non
        # tenersi in mano un oggetto Guild catturato in una closure.
        bind_client(self)

        # gruppi di comandi
        self.tree.add_command(ticket_group)
        self.tree.add_command(candidatura_group)
        self.tree.add_command(domandestaff_group)
        self.tree.add_command(config_group)
        self.tree.add_command(backup_group)
        self.tree.add_command(ezticket_group)

        # view persistenti: i bottoni/select continuano a funzionare dopo un riavvio
        self.add_view(TicketPanelView())
        self.add_view(TicketControlView())

        # I pulsanti delle notifiche vivono in DM, dove non esiste contesto server:
        # l'ID della guild è codificato nel loro custom_id e viene risolto da questi
        # DynamicItem, così l'opt-out vale solo per il server che lo ha generato.
        self.add_dynamic_items(NotifyOptOutButton, NotifyOptInButton)

        synced = await self.tree.sync()
        log.info("Sincronizzati %d comandi slash (globali).", len(synced))

    async def close(self):
        """Spegnimento pulito: l'ultimo salvataggio in sospeso va scritto su disco
        prima di chiudere il loop, altrimenti il debounce lo perderebbe."""
        maintenance_loop.cancel()
        if _presence_task is not None and not _presence_task.done():
            _presence_task.cancel()
        try:
            await cfg.flush()
        except Exception:
            log.exception("Salvataggio finale non riuscito; provo in modalità sincrona.")
            cfg.flush_sync()
        await super().close()


bot = TicketBot()


def start_manager_api():
    import uvicorn

    uvicorn.run(
        "manager_backend.app:app",
        host="0.0.0.0",
        port=10180,
        reload=False,
    )


threading.Thread(
    target=start_manager_api,
    daemon=True,
).start()


# ---------------------------------------------------------------------------
# Ciclo di vita
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    log.info(
        "Bot connesso come %s (ID: %s) su %d server, %d shard.",
        bot.user, bot.user.id, len(bot.guilds), bot.shard_count or 1,
    )
    # Debounce anche qui: on_ready si ripete a ogni riconnessione (e con più shard
    # arrivano in sequenza), quindi non chiamiamo change_presence ogni volta.
    schedule_presence_update()

    if bot._startup_done:
        # on_ready può ripetersi dopo una riconnessione: il lavoro di avvio no.
        return
    bot._startup_done = True

    if not get_bot_operator_ids():
        log.info("BOT_OWNER_IDS non impostata: /config diagnostica resterà disabilitato.")

    # PRIMA si allineano i dati ai server in cui il bot si trova davvero adesso,
    # POI si ripuliscono quelli abbandonati da troppo tempo. L'ordine conta: un
    # server da cui il bot era stato rimosso e in cui è stato re-invitato mentre il
    # processo era spento ha ancora 'left_at' impostato, e una purge eseguita prima
    # della riconciliazione ne cancellerebbe la configurazione pur essendoci dentro.
    presenti = {str(g.id) for g in bot.guilds}
    for guild in bot.guilds:
        if cfg.peek(guild.id) is not None:
            cfg.reconcile_guild_owner(guild.id, guild.owner_id)
    for gid, gconf in list(cfg.data.items()):
        if gid in presenti:
            if gconf.get("left_at") is not None:
                gconf["left_at"] = None
                cfg.mark_dirty("config")
        elif gconf.get("left_at") is None:
            cfg.mark_guild_left(gid)
            log.info(
                "Il bot non è più nel server %s: configurazione conservata per %d giorni.",
                gid, LEFT_GUILD_RETENTION_DAYS,
            )

    stale = cfg.purge_stale_guilds()
    if stale:
        log.info("Configurazione eliminata per %d server abbandonati.", len(stale))

    await reconcile_persisted_tickets()
    restore_ticket_timers()
    await cfg.flush()

    # Le pulizie non possono essere solo all'avvio: un bot in produzione resta
    # accesso per settimane, e nel frattempo i dati di TUTTI i server crescono.
    if not maintenance_loop.is_running():
        maintenance_loop.start()


async def update_presence():
    numero = len(bot.guilds)
    await bot.change_presence(
        status=discord.Status.dnd,
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"🎫 i ticket di {numero} server" if numero != 1 else "🎫 i ticket del server",
        )
    )


def schedule_presence_update(delay: float = PRESENCE_DEBOUNCE_SECONDS) -> None:
    """Aggiorna la presenza al massimo una volta per finestra di `delay` secondi.

    Gli eventi di join/leave arrivano a raffica (all'avvio, o quando una lista di
    server viene aggiunta in blocco): una change_presence per ognuno esaurirebbe
    il rate limit del gateway e rallenterebbe l'intero bot. Il conteggio viene
    letto quando l'aggiornamento parte, quindi l'ultimo evento è sempre incluso."""
    global _presence_task
    if _presence_task is not None and not _presence_task.done():
        return   # già in coda: quell'aggiornamento conterà anche questo evento

    async def _later():
        try:
            await asyncio.sleep(delay)
            await update_presence()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("Aggiornamento della presenza non riuscito.", exc_info=True)

    _presence_task = asyncio.create_task(_later())


@tasks.loop(hours=MAINTENANCE_INTERVAL_HOURS)
async def maintenance_loop():
    """Manutenzione periodica dell'intera istanza: configurazioni di server
    abbandonati, candidature mai completate, riferimenti a riepiloghi obsoleti.
    Non tocca lo storico dei ticket, che è dato utile per ogni community."""
    try:
        stats = cfg.run_maintenance()
    except Exception:
        log.exception("Giro di manutenzione non riuscito; riprovo al prossimo giro.")
        return

    if any(stats.values()):
        log.info(
            "Manutenzione: %d server abbandonati, %d candidature scadute, %d riepiloghi obsoleti.",
            stats["guilds"], stats["sessions"], stats["summaries"],
        )
        try:
            await cfg.flush()
        except Exception:
            log.exception("Salvataggio dopo la manutenzione non riuscito.")


@maintenance_loop.before_loop
async def _before_maintenance():
    await bot.wait_until_ready()


def restore_ticket_timers(only_guild: discord.Guild | None = None) -> int:
    """Ricostruisce i timer SLA e anti-abbandono per i ticket ancora aperti dopo un
    riavvio/crash del bot (o dopo il ritorno online di un singolo server), calcolando
    il tempo rimanente dai timestamp salvati su disco: nessun timer viene perso."""
    now = int(datetime.now(timezone.utc).timestamp())
    guilds = [only_guild] if only_guild is not None else list(bot.guilds)
    restored = 0

    for guild in guilds:
        gconf = cfg.peek(guild.id)
        if not gconf:
            continue
        for channel_id_str, ticket in list(gconf.get("tickets", {}).items()):
            if ticket.get("status") != "open":
                continue
            if not str(channel_id_str).isdigit():
                continue
            channel_id = int(channel_id_str)
            if guild.get_channel(channel_id) is None:
                continue  # il canale non esiste più (es. eliminato manualmente)

            if not ticket.get("first_response_at") and not ticket.get("sla_notified"):
                deadline = ticket.get("sla_deadline") or (ticket.get("opened_at", now) + sla_seconds(guild.id))
                schedule_sla_check(guild.id, channel_id, deadline - now)
                restored += 1

            claimed_by = ticket.get("claimed_by")
            claim_deadline = ticket.get("claim_deadline")
            if claimed_by and claim_deadline and not ticket.get("claimer_responded"):
                schedule_claim_check(guild.id, channel_id, claimed_by, claim_deadline - now)
                restored += 1

    if restored:
        log.info("Ripristinati %d timer (SLA/anti-abbandono).", restored)
    return restored


async def reconcile_persisted_tickets() -> int:
    """Verifica i ticket persistiti dopo il boot e archivia quelli eliminati."""
    invalidated = 0
    for guild in bot.guilds:
        gconf = cfg.peek(guild.id)
        if not gconf:
            continue
        for channel_id_str, ticket in list(gconf.get("tickets", {}).items()):
            if ticket.get("status") != "open" or not str(channel_id_str).isdigit():
                continue
            channel = guild.get_channel(int(channel_id_str))
            if channel is not None:
                continue
            try:
                await guild.fetch_channel(int(channel_id_str))
            except discord.NotFound:
                if forget_deleted_ticket(guild.id, int(channel_id_str)):
                    invalidated += 1
            except (discord.Forbidden, discord.HTTPException):
                # Un errore temporaneo o di permessi non dimostra che il canale
                # sia stato eliminato: il ticket resta persistito.
                continue
    if invalidated:
        await cfg.flush()
        log.info("Invalidati %d ticket persistiti con canale non più esistente.", invalidated)
    return invalidated


def build_setup_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="👋 Grazie per avermi invitato!",
        description=(
            f"Sono il sistema ticket di **{guild.name}**. Tutto ciò che configuri qui vale "
            f"**solo per questo server**: nessun'altra community vede i tuoi ticket o le tue impostazioni.\n\n"
            f"Ecco i 4 passi per partire 👇"
        ),
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="1️⃣ Ruolo dello staff",
        value="`/ticket staffmembers <ruolo>` — chi potrà gestire i ticket.",
        inline=False,
    )
    embed.add_field(
        name="2️⃣ Categorie dei ticket",
        value="`/ticket section add <nome> <etichetta> <categoria>` — una per tipo di richiesta.",
        inline=False,
    )
    embed.add_field(
        name="3️⃣ Canali di servizio",
        value=(
            "`/ticket transcriptchannel <canale>` — dove archiviare i transcript\n"
            "`/slachannel <canale>` — dove avvisare che un ticket è senza risposta\n"
            "`/setlogticketstaff <canale>` — log del sistema ticket"
        ),
        inline=False,
    )
    embed.add_field(
        name="4️⃣ Pubblica il pannello",
        value="`/ticket panel` — invia il menu con cui gli utenti aprono i ticket.",
        inline=False,
    )
    embed.add_field(
        name="⚙️ Personalizzazione",
        value=(
            "`/config lista` — vedi tutte le impostazioni di questo server\n"
            "`/config sla` · `/config claimtimeout` — tempi delle automazioni\n"
            "`/config owner add <utente>` — chi altro può configurare il bot qui\n"
            "`/help` — elenco completo dei comandi"
        ),
        inline=False,
    )
    embed.set_footer(text="Serve il permesso Amministratore (o /config owner add) per configurarmi.")
    return embed


async def send_setup_message(guild: discord.Guild) -> None:
    """Invia le istruzioni iniziali nel primo canale in cui il bot può scrivere,
    o in DM al proprietario del server se non ne trova nessuno."""
    embed = build_setup_embed(guild)

    candidati = []
    if guild.system_channel is not None:
        candidati.append(guild.system_channel)
    candidati.extend(c for c in guild.text_channels if c is not guild.system_channel)

    for channel in candidati:
        perms = channel.permissions_for(guild.me)
        if perms.send_messages and perms.embed_links:
            try:
                await channel.send(embed=embed)
                return
            except (discord.Forbidden, discord.HTTPException):
                continue

    owner = guild.owner
    if owner is not None:
        try:
            await owner.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass


@bot.event
async def on_guild_join(guild: discord.Guild):
    existing = cfg.peek(guild.id)
    era_configurato = bool(existing and (existing.get("sections") or existing.get("staff_role")))

    cfg.mark_guild_joined(guild.id, guild.owner_id)
    log.info("Aggiunto al server %s (%s) — %d membri.", guild.name, guild.id, guild.member_count or 0)
    schedule_presence_update()

    if era_configurato:
        # Re-invito (o riapparizione dopo un'interruzione): la configurazione era
        # stata conservata, quindi non ripetiamo il messaggio di benvenuto.
        restore_ticket_timers(guild)
        return

    await send_setup_message(guild)


@bot.event
async def on_guild_remove(guild: discord.Guild):
    annullati = cancel_guild_timers(guild.id)
    cfg.mark_guild_left(guild.id)
    log.info(
        "Rimosso dal server %s (%s): %d timer annullati, configurazione conservata per %d giorni.",
        guild.name, guild.id, annullati, LEFT_GUILD_RETENTION_DAYS,
    )
    schedule_presence_update()


@bot.event
async def on_guild_available(guild: discord.Guild):
    """Il server è tornato raggiungibile dopo un'interruzione: rimettiamo in piedi
    solo i suoi timer, senza toccare quelli degli altri server."""
    if not bot._startup_done:
        return
    gconf = cfg.peek(guild.id)
    if gconf:
        cfg.reconcile_guild_owner(guild.id, guild.owner_id)
    if gconf and gconf.get("left_at") is not None:
        gconf["left_at"] = None
        cfg.mark_dirty("config")
    restore_ticket_timers(guild)


@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild):
    """Un trasferimento di proprietà non deve lasciare l'ex owner tra gli
    amministratori manuali del bot. Il proprietario corrente resta autorizzato
    dinamicamente dalle verifiche di permesso, senza stato condiviso."""
    if before.owner_id != after.owner_id and cfg.peek(after.id) is not None:
        cfg.reconcile_guild_owner(after.id, after.owner_id)


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    """Un canale è stato eliminato a mano: se era un ticket aperto va archiviato e
    i suoi timer annullati, altrimenti resterebbe per sempre nei dati di quel
    server (contatori sbagliati, avvisi SLA su un canale che non esiste più).
    Tocca solo la guild del canale eliminato."""
    if forget_deleted_ticket(channel.guild.id, channel.id, channel.name):
        log.info("Canale ticket %s (%s) eliminato manualmente: ticket archiviato.", channel.name, channel.id)
        return

    # Se è stato eliminato il canale che ospitava il pannello, azzeriamo il
    # riferimento così /ticket panel non prova più a modificare un messaggio morto.
    gconf = cfg.peek(channel.guild.id)
    if gconf and (gconf.get("panel") or {}).get("channel_id") == channel.id:
        # Solo il riferimento al messaggio: titolo/descrizione/colore scelti da
        # questo server restano, così il prossimo /ticket panel li riusa.
        gconf["panel"]["channel_id"] = None
        gconf["panel"]["message_id"] = None
        cfg.mark_dirty("config")


@bot.event
async def on_message(message: discord.Message):
    """Gestisce due cose:
    1) nei DM: le risposte a un questionario di candidatura staff in corso
    2) nei canali ticket: tracking risposte per SLA/anti-abbandono."""
    if message.guild is None:
        if message.author.bot or message.webhook_id:
            return
        # Messaggio diretto (DM): può essere una risposta al questionario staff
        await handle_candidatura_dm_answer(bot, message)
        return

    # peek() e non guild(): un messaggio in un server che non ha mai configurato il
    # bot non deve creare una voce di configurazione (né una scrittura su disco).
    gconf = cfg.peek(message.guild.id)
    if not gconf:
        return
    ticket = gconf.get("tickets", {}).get(str(message.channel.id))
    if not ticket or ticket.get("status") != "open":
        return

    from tickets import serialize_ticket_message
    from manager_backend.services.ticket_realtime import ticket_realtime
    ticket_realtime.publish(message.guild.id, message.channel.id, serialize_ticket_message(message, message.guild.id))

    if message.author.bot or message.webhook_id:
        return

    is_staff_here = is_staff_member(message.author, message.guild.id)
    changed = False

    if not ticket.get("first_response_at") and is_staff_here:
        ticket["first_response_at"] = int(datetime.now(timezone.utc).timestamp())
        cancel_sla_check(message.guild.id, message.channel.id)
        changed = True

    if ticket.get("claimed_by") == message.author.id and not ticket.get("claimer_responded"):
        ticket["claimer_responded"] = True
        cancel_claim_check(message.guild.id, message.channel.id)
        changed = True

    if changed:
        cfg.save()

# ---------------------------------------------------------------------------
# Comandi standalone
# ---------------------------------------------------------------------------

@bot.tree.command(name="risponditicket", description="Invia un promemoria di attività dentro il ticket corrente")
@app_commands.describe(utente="Utente a cui inviare il promemoria")
@app_commands.guild_only()
async def risponditicket(interaction: discord.Interaction, utente: discord.Member):
    await handle_risponditicket(interaction, utente)


@bot.tree.command(name="setlogticketstaff", description="[Admin server] Imposta il canale di log del sistema ticket")
@app_commands.describe(canale="Canale in cui loggare gli eventi del sistema ticket")
@app_commands.guild_only()
async def setlogticketstaff(interaction: discord.Interaction, canale: discord.TextChannel):
    await handle_setlogticketstaff(interaction, canale)


@bot.tree.command(name="slachannel", description="[Admin server] Imposta il canale per gli avvisi SLA (ticket senza risposta)")
@app_commands.describe(canale="Canale in cui inviare gli avvisi SLA")
@app_commands.guild_only()
async def slachannel(interaction: discord.Interaction, canale: discord.TextChannel):
    await handle_slachannel(interaction, canale)


@bot.tree.command(name="candidaturestaffcanale", description="[Admin server] Canale dove arrivano le candidature staff completate")
@app_commands.describe(canale="Canale di destinazione delle candidature")
@app_commands.guild_only()
async def candidaturestaffcanale(interaction: discord.Interaction, canale: discord.TextChannel):
    await handle_candidaturestaffcanale(interaction, canale)


@bot.tree.command(name="sezionecandidature", description="[Admin server] Configura il ruolo menzionato per una candidatura")
@app_commands.describe(sezione="Sezione candidatura già esistente", ruolo="Ruolo da menzionare")
@app_commands.autocomplete(sezione=ticket_section_autocomplete)
@app_commands.guild_only()
async def sezionecandidature(interaction: discord.Interaction, sezione: str, ruolo: discord.Role):
    await handle_sezionecandidature(interaction, sezione, ruolo)


@bot.tree.command(name="help", description="Mostra tutti i comandi disponibili")
async def help_command(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "ℹ️ Usa `/help` dentro un server: la guida mostra le impostazioni di quel server.",
            ephemeral=True,
        )
        return

    sla_min = sla_seconds(guild.id) // 60
    claim_min = claim_timeout_seconds(guild.id) // 60
    ore = inactivity_hours(guild.id)

    embed = discord.Embed(
        title="🌌 TicketBot · Guida ai comandi",
        description=(
            f"Comandi disponibili in **{guild.name}**.\n"
            f"Ogni server ha configurazione e impostazioni indipendenti: quello che vedi qui "
            f"vale solo per questo server."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="⚙️ Configurazione (amministratori del server)",
        value=(
            "`/ticket panel` — pubblica il pannello ticket\n"
            "`/ticket section add|remove|list` — gestisci le categorie\n"
            "`/ticket transcriptchannel` — canale dei transcript\n"
            "`/ticket staffmembers` — ruolo staff globale del server\n"
            "`/ticket blacklist` — blocca/sblocca un utente\n"
            "`/slachannel` — canale degli avvisi SLA\n"
            "`/setlogticketstaff` — canale di log del sistema ticket"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎛️ Impostazioni del server (`/config`)",
        value=(
            "`/config lista` — riepilogo completo delle impostazioni\n"
            f"`/config sla <minuti>` — attesa prima dell'avviso SLA (ora: **{sla_min} min**)\n"
            f"`/config claimtimeout <minuti>` — anti-abbandono claim (ora: **{claim_min} min**)\n"
            f"`/config inattivita <ore>` — ore citate dal promemoria (ora: **{ore} h**)\n"
            "`/config branding` — testo del footer di transcript ed embed\n"
            "`/config team add|remove|lista` — team proposti dalle candidature\n"
            "`/config owner add|remove|lista` — chi può configurare il bot qui"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎫 Gestione ticket (staff)",
        value=(
            "`/ticket claim` — reclama/rilascia il ticket\n"
            "`/ticket close` — chiudi il ticket\n"
            "`/ticket add` · `/ticket remove` — aggiungi/rimuovi un utente\n"
            "`/ticket note` — aggiungi una nota interna\n"
            "`/ticket rename` · `/ticket move` — rinomina/sposta il ticket\n"
            "Lo staff può rispondere direttamente nel canale o dall'EzTicket Manager\n"
            "`/risponditicket <utente>` — invia un promemoria di attività al ticket\n"
            "Bottoni in ogni ticket: 🙋 Reclama · ➕ Aggiungi utente · 📄 Transcript · 🔒 Chiudi"
        ),
        inline=False,
    )
    embed.add_field(
        name="📋 Candidature (staff)",
        value=(
            "`/candidatura aggiungi` — crea un tipo di candidatura\n"
            "`/candidatura settings` — configura ruolo e nickname del tipo\n"
            "`/candidatura accettata|rifiutata` — gestisce l'esito della candidatura"
        ),
        inline=False,
    )
    embed.add_field(
        name="📝 Candidatura Staff a domande (via DM)",
        value=(
            "`/domandestaff modifica|rimuovi|lista` — domande del questionario\n"
            "`/candidaturestaffcanale <canale>` — canale dove arrivano le candidature\n"
            "`/sezionecandidature <sezione> <ruolo>` — ruolo menzionato per una sezione candidatura\n"
            "`/candidatura invia <utente> [ruolo]` — avvia il questionario in DM per l'utente"
        ),
        inline=False,
    )
    embed.add_field(
        name="⏰ Automazioni di questo server",
        value=(
            f"**SLA:** se un ticket resta senza risposta dallo staff per **{sla_min} minuti**, "
            f"viene inviato un avviso nel canale SLA taggando il ruolo staff.\n"
            f"**Anti-abbandono:** se chi ha claimato un ticket non risponde entro **{claim_min} minuti** "
            f"dal claim, il claim viene rimosso e il ticket torna disponibile.\n"
            f"*(entrambi modificabili con `/config`)*"
        ),
        inline=False,
    )
    embed.set_footer(text=branding_text(guild))
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.NoPrivateMessage):
        msg = "⚠️ Questo comando funziona solo dentro un server."
    elif isinstance(error, app_commands.MissingPermissions):
        msg = "🚫 Ti mancano i permessi necessari."
    elif isinstance(error, app_commands.CheckFailure):
        msg = "🚫 Non hai il permesso di usare questo comando."
    elif isinstance(error, app_commands.CommandOnCooldown):
        msg = f"⏳ Riprova tra {error.retry_after:.0f} secondi."
    else:
        log.exception("Errore comando slash: %s", error)
        msg = "⚠️ Si è verificato un errore durante l'esecuzione del comando."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


# ---------------------------------------------------------------------------
# Avvio
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit(
            "❌ Token mancante.\n"
            "   Crea un file .env accanto a main.py contenente:\n"
            "       DISCORD_TOKEN=il-token-del-bot\n"
            "   (oppure imposta la variabile d'ambiente DISCORD_TOKEN).\n"
            "   Il token si genera su https://discord.com/developers/applications "
            "→ la tua app → Bot → Reset Token."
        )
    try:
        bot.run(TOKEN)
    finally:
        # Se il loop si è chiuso prima del flush asincrono, salviamo comunque.
        cfg.flush_sync()
