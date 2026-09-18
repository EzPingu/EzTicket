"""
candidature.py
Gruppo di comandi /candidatura con i sottocomandi aggiungi, settings, invia, attesa,
accettata e rifiutata, e
il sistema di candidatura staff a domande via DM (/domandestaff, /candidatura invia,
/candidaturestaffcanale, /sezionecandidature).

Tutto è per-server: ogni tipo candidatura contiene ruolo, nickname e domande propri.
Le sessioni di candidatura sono identificate
dalla coppia (server, utente), così lo stesso utente può candidarsi in più server
contemporaneamente senza conflitti.
"""
from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import re
import discord
from components_v2 import recolor_components_v2, render_components_v2
from discord import app_commands

from config import (
    cfg,
    branding_text,
    fetch_member,
    guild_teams,
    require_guild_admin,
    require_staff,
)


# ---------------------------------------------------------------------------
# Team disponibili: autocomplete per-server (una lista di scelte statica sarebbe
# identica in tutti i server, dato che i comandi sono sincronizzati globalmente)
# ---------------------------------------------------------------------------


_application_locks: dict[str, asyncio.Lock] = {}

def get_application_lock(guild_id: int | str, user_id: int | str) -> asyncio.Lock:
    key = f"{guild_id}:{user_id}"
    if key not in _application_locks:
        _application_locks[key] = asyncio.Lock()
    return _application_locks[key]

def candidature_type_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def candidature_types(guild: discord.Guild) -> dict[str, dict]:
    gconf = cfg.guild(guild.id)
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
    for item in types.values():
        if isinstance(item, dict):
            item.setdefault("mention_role_id", None)
    return types


async def candidature_type_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []
    query = (current or "").lower()
    return [
        app_commands.Choice(name=str(item.get("name", key))[:100], value=str(item.get("name", key))[:100])
        for key, item in candidature_types(interaction.guild).items()
        if query in str(item.get("name", key)).lower()
    ][:25]


def get_candidature_type(guild: discord.Guild, name: str) -> tuple[str, dict] | None:
    key = candidature_type_key(name)
    types = candidature_types(guild)
    if key in types:
        return key, types[key]
    for stored_key, item in types.items():
        if str(item.get("name", "")).casefold() == name.strip().casefold():
            return stored_key, item
    return None


def ensure_candidature_type(guild: discord.Guild, name: str | None = None) -> tuple[str, dict] | None:
    selected = get_candidature_type(guild, name) if name else None
    if selected:
        return selected
    types = candidature_types(guild)
    if name is None and len(types) == 1:
        key, value = next(iter(types.items()))
        return key, value
    return None


def resolve_nickname_for_type(guild: discord.Guild, utente: discord.Member, candidature_type: dict) -> str | None:
    fmt = candidature_type.get("nickname_format")
    if not fmt:
        return None
    values = {
        "username": utente.name,
        "display_name": utente.display_name,
        "id": str(utente.id),
        # Compatibilità con i placeholder precedenti.
        "nome": utente.name,
        "nick": utente.display_name,
        "server": guild.name,
    }
    try:
        return str(fmt).format(**values)[:32] or None
    except (KeyError, IndexError, ValueError):
        return str(fmt)[:32] or None


async def core_accept_application(
    guild: discord.Guild,
    utente: discord.Member,
    actor: discord.Member | discord.abc.User,
    full_onboard: bool = True,
    team: str | None = None,
    candidature_type: str | None = None,
) -> dict:
    gconf = cfg.guild(guild.id)
    refs = gconf.setdefault("candidature_summary_messages", {})
    session = cfg.get_session(guild.id, utente.id)
    ref = refs.get(str(utente.id))
    current_status = (session or ref or {}).get("status")
    if current_status in ("accepted", "rejected"):
        return {"success": False, "status": "already_processed", "message": "La candidatura è già stata elaborata."}
    if session is not None and current_status not in (None, "pending"):
        return {"success": False, "status": "not_completed", "message": "La candidatura non è ancora completata."}
    if session is None and ref is None:
        return {"success": False, "status": "not_found", "message": "Candidatura non trovata."}
    selected_name = candidature_type or (session or {}).get("candidature_type") or "Staff"
    selected = get_candidature_type(guild, selected_name)
    if selected is None:
        return {"success": False, "status": "type_not_found", "message": f"Tipo candidatura non trovato: {selected_name}."}
    selected_key, type_config = selected
    if session and session.get("candidature_type") and session["candidature_type"] != selected_key:
        return {"success": False, "status": "wrong_type", "message": "Il tipo indicato non corrisponde alla candidatura."}

    added_roles = []
    nick_status = None
    nickname_result = None
    role_results = []
    dm_sent = False
    if full_onboard:
        role_id = type_config.get("role_id")
        role = guild.get_role(int(role_id)) if str(role_id).isdigit() else None
        role_name = role.name if role is not None else f"ID {role_id}"
        if role is None and role_id:
            role_results.append({
                "name": role_name,
                "success": False,
                "error": "ruolo non trovato nel server",
            })
        elif role:
            try:
                await utente.add_roles(role, reason=f"Candidatura {type_config.get('name', selected_key)} accettata da {actor}")
                added_roles.append(role)
                role_results.append({"name": role.name, "success": True, "error": None})
            except discord.Forbidden:
                role_results.append({
                    "name": role.name,
                    "success": False,
                    "error": "permessi insufficienti",
                })
            except discord.HTTPException as exc:
                role_results.append({
                    "name": role.name,
                    "success": False,
                    "error": f"errore Discord ({exc})",
                })

        old_nick = utente.display_name
        new_nick = resolve_nickname_for_type(guild, utente, type_config)
        if new_nick is None:
            nick_status = "Nessun formato configurato"
            nickname_result = {"success": False, "new_nickname": None, "error": "nessun formato configurato"}
        else:
            try:
                await utente.edit(nick=new_nick, reason="Candidatura accettata")
                nick_status = f"`{old_nick}` → `{new_nick}`"
                nickname_result = {"success": True, "new_nickname": new_nick, "error": None}
            except discord.Forbidden:
                nick_status = "Errore: permessi insufficienti"
                nickname_result = {"success": False, "new_nickname": new_nick, "error": "permessi insufficienti"}
            except discord.HTTPException as exc:
                nick_status = f"Errore: errore Discord ({exc})"
                nickname_result = {"success": False, "new_nickname": new_nick, "error": f"errore Discord ({exc})"}
        dm_embed = build_staff_welcome_dm_embed(guild, utente, str(type_config.get("name", selected_key)))
        dm_sent = await _send_dm(utente, dm_embed)
    await _resolve_candidature_summary(guild, utente, accepted=True)
    if session is not None:
        session["status"] = "accepted"
        cfg.save()

    return {
        "success": True,
        "roles_added": [str(r.id) for r in added_roles],
        "nickname_changed": bool(nick_status and "→" in nick_status),
        "nickname_status": nick_status,
        "nickname_result": nickname_result,
        "role_results": role_results,
        "dm_sent": dm_sent,
        "summary_updated": True
    }

async def core_reject_application(guild: discord.Guild, utente: discord.Member, actor: discord.Member | discord.abc.User, team: str | None = None, motivo: str | None = None) -> dict:
    gconf = cfg.guild(guild.id)
    refs = gconf.setdefault("candidature_summary_messages", {})
    session = cfg.get_session(guild.id, utente.id)
    ref = refs.get(str(utente.id))
    current_status = (session or ref or {}).get("status")
    if current_status in ("accepted", "rejected"):
        return {"success": False, "status": "already_processed", "message": "La candidatura è già stata elaborata."}
    if session is not None and current_status not in (None, "pending"):
        return {"success": False, "status": "not_completed", "message": "La candidatura non è ancora completata."}
    if session is None and ref is None:
        return {"success": False, "status": "not_found", "message": "Candidatura non trovata."}

    team_label = team or default_team_label(guild)
    description = (
        f"Ciao {utente.mention},\\\n\\\n"
        f"Ti informiamo che la tua candidatura per il ruolo di **{team_label}** "
        f"non è stata **accettata** in questa fase.\\\n\\\n"
        f"`[■■■■■■■■■■]` Stato: **Rifiutata**\\\n\\\n"
        f"Non scoraggiarti: potrai ricandidarti in futuro. Grazie per il tempo dedicato! 🙏"
    )
    if motivo:
        description += f"\\\n\\\n📝 **Motivo:** {motivo}"
        
    embed = discord.Embed(
        title="❌ Candidatura Rifiutata",
        description=description,
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=utente.display_avatar.url)
    embed.set_footer(text=f"Valutata da {actor.display_name}", icon_url=actor.display_avatar.url)
    
    dm_sent = await _send_dm(utente, embed)
    await _resolve_candidature_summary(guild, utente, accepted=False)
    if session is not None:
        session["status"] = "rejected"
        cfg.save()
    
    return {
        "success": True,
        "dm_sent": dm_sent,
        "summary_updated": True
    }
\
\
async def team_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    guild_id = interaction.guild.id if interaction.guild else 0
    teams = guild_teams(guild_id)
    current = (current or "").lower()
    return [
        app_commands.Choice(name=team[:100], value=team[:100])
        for team in teams
        if current in team.lower()
    ][:25]


def default_team_label(guild: discord.Guild | None) -> str:
    """Etichetta usata quando lo staffer non specifica il team."""
    teams = guild_teams(guild.id if guild else 0)
    return "/".join(teams[:3]) if teams else "Staff"


async def _send_dm(utente: discord.abc.User, embed: discord.Embed) -> bool:
    """Prova a inviare l'embed in DM all'utente. Ritorna True se riuscito."""
    try:
        await utente.send(view=render_components_v2(embed))
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


def build_attesa_embed(utente: discord.abc.User, team: str, footer_text: str, footer_icon: str | None = None) -> discord.Embed:
    """Embed 'candidatura in revisione', condiviso tra /candidatura attesa (manuale) e
    la messa in attesa automatica al termine del questionario /candidatura invia."""
    embed = discord.Embed(
        title="📥 Candidatura in Revisione",
        description=(
            f"Ciao {utente.mention}! 👋\n\n"
            f"La tua candidatura per entrare a far parte del nostro team come **{team}** "
            f"è stata ricevuta ed è attualmente **in fase di valutazione**.\n\n"
            f"`[■■■□□□□□□□]` Stato: **In Revisione**\n\n"
            f"📌 Ti invitiamo a restare attivo nel server: il nostro team ti contatterà "
            f"al più presto con un aggiornamento.\n\nGrazie per il tuo interesse! 🚀"
        ),
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=utente.display_avatar.url)
    embed.set_footer(text=footer_text, icon_url=footer_icon)
    return embed


async def _resolve_candidature_summary(guild: discord.Guild, utente: discord.abc.User, accepted: bool) -> None:
    """Se esistono gli embed generati al termine del questionario /candidatura invia per questo
    utente, li aggiorna entrambi: il riepilogo 'Nuova Candidatura Staff' cambia solo colore
    (verde/rosso), mentre l'embed sotto (che diceva 'messa in Attesa') cambia colore e mostra
    l'ESITO in grassetto al posto del testo di attesa. Chiamata dai comandi candidatura,
    /candidatura accettata e /candidatura rifiutata."""
    gconf = cfg.guild(guild.id)
    refs = gconf.setdefault("candidature_summary_messages", {})
    ref = refs.get(str(utente.id))
    if not ref:
        return

    channel = guild.get_channel(ref.get("channel_id"))
    if not isinstance(channel, discord.TextChannel):
        ref["status"] = "accepted" if accepted else "rejected"
        cfg.save()
        return
    color = discord.Color.green() if accepted else discord.Color.red()

    # embed in alto: "📋 Nuova Candidatura Staff" -> cambia solo colore, nessun testo aggiunto
    try:
        summary_msg = await channel.fetch_message(ref["message_id"])
        if summary_msg.embeds:
            summary_embed = summary_msg.embeds[0]
            summary_embed.color = color
            await summary_msg.edit(view=render_components_v2(summary_embed))
        else:
            summary_view = discord.ui.LayoutView.from_message(summary_msg)
            if isinstance(summary_view, discord.ui.LayoutView):
                await summary_msg.edit(view=recolor_components_v2(summary_view, color))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, KeyError):
        pass

    # embed in basso: "⏳ Candidatura messa in Attesa" -> cambia colore e mostra l'esito
    notice_message_id = ref.get("notice_message_id")
    if notice_message_id:
        try:
            notice_msg = await channel.fetch_message(notice_message_id)
            if notice_msg.embeds:
                notice_embed = notice_msg.embeds[0]
                notice_embed.color = color
                notice_embed.title = "✅ Esito Candidatura" if accepted else "❌ Esito Candidatura"
                notice_embed.description = "**ESITO: ACCETTATO**" if accepted else "**ESITO: RIFIUTATO**"
                await notice_msg.edit(view=render_components_v2(notice_embed))
            else:
                notice_view = discord.ui.LayoutView.from_message(notice_msg)
                if isinstance(notice_view, discord.ui.LayoutView):
                    await notice_msg.edit(view=recolor_components_v2(notice_view, color))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    ref["status"] = "accepted" if accepted else "rejected"
    cfg.save()


class CandidaturaGroup(app_commands.Group):
    def __init__(self):
        super().__init__(
            name="candidatura",
            description="Gestione delle candidature al team",
            guild_only=True,
        )

    @app_commands.command(name="aggiungi", description="[Admin server] Crea un tipo di candidatura")
    @app_commands.describe(ruolo="Nome del tipo, ad esempio Staff o Content Creator")
    async def aggiungi(self, interaction: discord.Interaction, ruolo: app_commands.Range[str, 1, 50]):
        if not await require_guild_admin(interaction):
            return
        name = ruolo.strip()
        key = candidature_type_key(name)
        if not key:
            await interaction.response.send_message("⚠️ Nome candidatura non valido.", ephemeral=True)
            return
        types = candidature_types(interaction.guild)
        if key in types:
            await interaction.response.send_message(f"⚠️ Il tipo **{name}** esiste già.", ephemeral=True)
            return
        types[key] = {
            "name": name,
            "role_id": None,
            "mention_role_id": None,
            "nickname_format": None,
            "questions": [],
        }
        cfg.save()
        await interaction.response.send_message(f"✅ Tipo candidatura **{name}** creato. Configuralo con `/candidatura settings`.", ephemeral=True)

    @app_commands.command(name="settings", description="[Admin server] Configura un tipo di candidatura")
    @app_commands.describe(
        tipo="Tipo di candidatura da configurare",
        ruolo_discord="Ruolo assegnato quando la candidatura viene accettata",
        nickname="Formato nickname: {username}, {display_name}, {id}",
    )
    @app_commands.autocomplete(tipo=candidature_type_autocomplete)
    async def settings(
        self,
        interaction: discord.Interaction,
        tipo: str,
        ruolo_discord: discord.Role | None = None,
        nickname: app_commands.Range[str, 1, 64] | None = None,
    ):
        if not await require_guild_admin(interaction):
            return
        selected = get_candidature_type(interaction.guild, tipo)
        if selected is None:
            await interaction.response.send_message(f"⚠️ Tipo candidatura non trovato: **{tipo}**.", ephemeral=True)
            return
        _, type_config = selected
        if ruolo_discord is not None:
            type_config["role_id"] = ruolo_discord.id
        if nickname is not None:
            type_config["nickname_format"] = None if nickname.strip().lower() in {"none", "off", "reset"} else nickname
        cfg.save()
        configured_role = interaction.guild.get_role(type_config["role_id"]) if type_config.get("role_id") else None
        await interaction.response.send_message(
            f"✅ Configurazione **{type_config.get('name', tipo)}** aggiornata.\n"
            f"Ruolo: {configured_role.mention if configured_role else '— non configurato'}\n"
            f"Nickname: `{type_config.get('nickname_format') or '— disattivato'}`",
            ephemeral=True,
        )

    @app_commands.command(name="invia", description="Avvia in DM una candidatura per un utente")
    @app_commands.describe(utente="Utente a cui inviare il questionario", ruolo="Tipo di candidatura")
    @app_commands.autocomplete(ruolo=candidature_type_autocomplete)
    async def invia(self, interaction: discord.Interaction, utente: discord.Member, ruolo: str | None = None):
        await handle_candidatura_invia(interaction, utente, ruolo)

    @app_commands.command(name="attesa", description="Segnala che una candidatura è in fase di valutazione")
    @app_commands.describe(utente="Utente che ha inviato la candidatura", ruolo="Tipo di candidatura")
    @app_commands.autocomplete(ruolo=team_autocomplete)
    async def attesa(self, interaction: discord.Interaction, utente: discord.Member, ruolo: str | None = None):
        if not await require_staff(interaction):
            return
        team = ruolo or default_team_label(interaction.guild)
        embed = build_attesa_embed(
            utente, team,
            footer_text=f"Valutata da {interaction.user.display_name}",
            footer_icon=interaction.user.display_avatar.url,
        )
        await interaction.response.send_message(content=f"{utente.mention} • {interaction.user.mention}", view=render_components_v2(embed))
        await _send_dm(utente, embed)

    @app_commands.command(name="accettata", description="Segnala che una candidatura è stata accettata")
    @app_commands.describe(utente="Utente che ha inviato la candidatura", ruolo="Tipo di candidatura")
    @app_commands.autocomplete(ruolo=candidature_type_autocomplete)
    async def accettata(self, interaction: discord.Interaction, utente: discord.Member, ruolo: str):
        if not await require_staff(interaction):
            return
            
        async with get_application_lock(interaction.guild.id, utente.id):
            res = await core_accept_application(interaction.guild, utente, interaction.user, candidature_type=ruolo)
            if not res.get("success"):
                await interaction.response.send_message(f"⚠️ {res['message']}", ephemeral=True)
                return
                
            team = ruolo
            result_lines = []
            nickname_result = res.get("nickname_result")
            if nickname_result and nickname_result.get("success"):
                new_nickname = nickname_result.get("new_nickname")
                result_lines.append(
                    f"Nickname di {utente.mention} cambiato a `{new_nickname}` correttamente"
                )
            elif nickname_result and nickname_result.get("error") != "nessun formato configurato":
                result_lines.append(
                    f"Nickname di {utente.mention} — Errore: {nickname_result.get('error', 'errore sconosciuto')}"
                )

            for role_result in res.get("role_results", []):
                if role_result.get("success"):
                    result_lines.append(
                        f"Ruolo `{role_result['name']}` aggiunto a {utente.mention} correttamente"
                    )
                else:
                    result_lines.append(
                        f"Ruolo `{role_result['name']}` — Errore: {role_result.get('error', 'errore sconosciuto')}"
                    )

            result_lines.append(f"Modificato da: {interaction.user.mention}")
            embed = discord.Embed(
                title="✅ Candidatura Accettata!",
                description=(
                    f"Congratulazioni {utente.mention}! 🎉\
\
"
                    f"La tua candidatura per il ruolo di **{team}** è stata **accettata**.\
\
"
                    f"`[■■■■■■■■■■]` Stato: **Accettata**\
\
"
                    f"Il nostro team ti contatterà a breve per illustrarti i prossimi passi. "
                    f"Benvenuto/a a bordo! 🚀✨\n\n"
                    + "\n".join(result_lines)
                ),
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_thumbnail(url=utente.display_avatar.url)
            embed.set_footer(text=f"Valutata da {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
            await interaction.response.send_message(content=f"{utente.mention} • {interaction.user.mention}", view=render_components_v2(embed))

    @app_commands.command(name="rifiutata", description="Segnala che una candidatura è stata rifiutata")
    @app_commands.describe(
        utente="Utente che ha inviato la candidatura",
        ruolo="Team per cui si è candidato",
        motivo="Motivo del rifiuto (opzionale)",
    )
    @app_commands.autocomplete(ruolo=team_autocomplete)
    async def rifiutata(
        self,
        interaction: discord.Interaction,
        utente: discord.Member,
        ruolo: str | None = None,
        motivo: str | None = None,
    ):
        if not await require_staff(interaction):
            return
            
        async with get_application_lock(interaction.guild.id, utente.id):
            res = await core_reject_application(interaction.guild, utente, interaction.user, team=ruolo, motivo=motivo)
            if not res.get("success"):
                await interaction.response.send_message(f"⚠️ {res['message']}", ephemeral=True)
                return
                
            team = ruolo or default_team_label(interaction.guild)
            description = (
                f"Ciao {utente.mention},\
\
"
                f"Ti informiamo che la tua candidatura per il ruolo di **{team}** "
                f"non è stata **accettata** in questa fase.\
\
"
                f"`[■■■■■■■■■■]` Stato: **Rifiutata**\
\
"
                f"Non scoraggiarti: potrai ricandidarti in futuro. Grazie per il tempo dedicato! 🙏"
            )
            if motivo:
                description += f"\
\
📝 **Motivo:** {motivo}"
            embed = discord.Embed(
                title="❌ Candidatura Rifiutata",
                description=description,
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_thumbnail(url=utente.display_avatar.url)
            embed.set_footer(text=f"Valutata da {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
            await interaction.response.send_message(content=f"{utente.mention} • {interaction.user.mention}", view=render_components_v2(embed))


candidatura_group = CandidaturaGroup()


# ---------------------------------------------------------------------------
# Onboarding completo: ruoli + nickname + DM di benvenuto
# ---------------------------------------------------------------------------

def build_staff_welcome_dm_embed(guild: discord.Guild, utente: discord.Member, candidature_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="🎉 Candidatura Accettata!",
        description=(
            f"Ciao, la tua candidatura **{candidature_name}** per entrare nello staff di **{guild.name}** "
            f"è stata **accettata**! 🎉\n\n"
            f"Da questo momento avrai accesso ai nuovi canali dello staff. Ti consigliamo di "
            f"leggere attentamente il regolamento e tutti gli altri canali importanti dello staff.\n\n"
            f"Quando trovi un messaggio con la reazione ✅ ricordati di reagire per confermare "
            f"la lettura.\n\n"
            f"Benvenuto nel team e buona fortuna! 💙"
        ),
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=f"{guild.name} · Staff Team")
    return embed


# ---------------------------------------------------------------------------
# /domandestaff — gestione delle domande per la candidatura staff (per-server)
# ---------------------------------------------------------------------------

class DomandeStaffGroup(app_commands.Group):
    def __init__(self):
        super().__init__(
            name="domandestaff",
            description="Gestisci le domande della candidatura staff",
            guild_only=True,
        )

    @app_commands.command(name="modifica", description="[Admin server] Aggiungi o modifica una domanda della candidatura staff")
    @app_commands.describe(
        numero="Numero della domanda (1, 2, 3...). Per aggiungerne una nuova usa il numero successivo all'ultima.",
        testo="Testo della domanda",
        tipo="Tipo di candidatura",
    )
    @app_commands.autocomplete(tipo=candidature_type_autocomplete)
    async def modifica(self, interaction: discord.Interaction, numero: app_commands.Range[int, 1, 50], testo: str, tipo: str | None = None):
        if not await require_guild_admin(interaction):
            return
        selected = ensure_candidature_type(interaction.guild, tipo)
        if selected is None:
            await interaction.response.send_message("⚠️ Specifica un tipo candidatura esistente.", ephemeral=True)
            return
        _, candidature_config = selected
        questions = candidature_config.setdefault("questions", [])
        idx = numero - 1
        if idx > len(questions):
            await interaction.response.send_message(
                f"⚠️ Puoi aggiungere al massimo la domanda **#{len(questions) + 1}** "
                f"(le domande vanno aggiunte in ordine, una alla volta).",
                ephemeral=True,
            )
            return
        is_new = idx == len(questions)
        if is_new:
            questions.append(testo)
        else:
            questions[idx] = testo
        cfg.save()
        embed = discord.Embed(
            title="✅ Domanda aggiunta" if is_new else "✅ Domanda modificata",
            description=f"**#{numero}:** {testo}",
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Domande configurate in questo server: {len(questions)}")
        await interaction.response.send_message(view=render_components_v2(embed), ephemeral=True)

    @app_commands.command(name="rimuovi", description="[Admin server] Rimuovi una domanda della candidatura staff")
    @app_commands.describe(numero="Numero della domanda da rimuovere", tipo="Tipo di candidatura")
    @app_commands.autocomplete(tipo=candidature_type_autocomplete)
    async def rimuovi(self, interaction: discord.Interaction, numero: app_commands.Range[int, 1, 50], tipo: str | None = None):
        if not await require_guild_admin(interaction):
            return
        selected = ensure_candidature_type(interaction.guild, tipo)
        if selected is None:
            await interaction.response.send_message("⚠️ Specifica un tipo candidatura esistente.", ephemeral=True)
            return
        _, candidature_config = selected
        questions = candidature_config.setdefault("questions", [])
        idx = numero - 1
        if idx < 0 or idx >= len(questions):
            await interaction.response.send_message("⚠️ Numero domanda non valido.", ephemeral=True)
            return
        removed = questions.pop(idx)
        cfg.save()
        embed = discord.Embed(
            title="🗑️ Domanda rimossa",
            description=f"**Era #{numero}:** {removed}",
            color=discord.Color.orange(),
        )
        embed.set_footer(text=f"Domande rimaste: {len(questions)}")
        await interaction.response.send_message(view=render_components_v2(embed), ephemeral=True)

    @app_commands.command(name="lista", description="Elenca le domande configurate in questo server")
    @app_commands.describe(tipo="Tipo di candidatura")
    @app_commands.autocomplete(tipo=candidature_type_autocomplete)
    async def lista(self, interaction: discord.Interaction, tipo: str | None = None):
        if not await require_staff(interaction):
            return
        selected = ensure_candidature_type(interaction.guild, tipo)
        if selected is None:
            await interaction.response.send_message("⚠️ Specifica un tipo candidatura esistente.", ephemeral=True)
            return
        _, candidature_config = selected
        questions = candidature_config.get("questions", [])
        if not questions:
            await interaction.response.send_message(
                "⚠️ Nessuna domanda configurata in questo server. Usa `/domandestaff modifica <numero> <testo>`.",
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="📋 Domande Candidatura Staff",
            description="\n".join(f"**{i + 1}.** {q}" for i, q in enumerate(questions)),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{len(questions)} domande configurate · {branding_text(interaction.guild)}")
        await interaction.response.send_message(view=render_components_v2(embed), ephemeral=True)


domandestaff_group = DomandeStaffGroup()


# ---------------------------------------------------------------------------
# /candidaturestaffcanale e /sezionecandidature — configurazione destinazione
# ---------------------------------------------------------------------------

async def handle_candidaturestaffcanale(interaction: discord.Interaction, canale: discord.TextChannel):
    if not await require_guild_admin(interaction):
        return
    gconf = cfg.guild(interaction.guild.id)
    gconf["candidature_staff_channel"] = canale.id
    cfg.save()
    await interaction.response.send_message(
        f"✅ Le candidature staff completate di questo server verranno inviate in {canale.mention}.",
        ephemeral=True,
    )


async def handle_sezionecandidature(
    interaction: discord.Interaction,
    sezione: str,
    ruolo: discord.Role,
):
    if not await require_guild_admin(interaction):
        return
    sections = cfg.guild(interaction.guild.id).get("sections", {})
    if not sections:
        await interaction.response.send_message(
            "❌ Non ci sono sezioni candidatura configurate in questo server.",
            ephemeral=True,
        )
        return
    section = sections.get(sezione)
    if section is None:
        await interaction.response.send_message(
            "❌ Questa sezione candidatura non esiste in questo server.",
            ephemeral=True,
        )
        return

    section["candidature_mention_role_id"] = ruolo.id
    cfg.save()
    await interaction.response.send_message(
        "✅ Sezione candidatura configurata.\n\n"
        f"Sezione:\n{section.get('label', sezione)}\n\n"
        f"Ruolo menzionato:\n{ruolo.mention}",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# /candidatura invia — avvia il questionario via DM
# ---------------------------------------------------------------------------

def build_question_embed(guild: discord.Guild | None, index: int, total: int, question: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"📝 Candidatura Staff · Domanda {index + 1}/{total}",
        description=question,
        color=discord.Color.blurple(),
    )
    if guild is not None:
        embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed.set_footer(text="Rispondi direttamente qui in DM con un messaggio per continuare.")
    return embed


async def handle_candidatura_invia(interaction: discord.Interaction, utente: discord.Member, tipo: str | None = None):
    """Gestisce /candidatura invia con il questionario configurato."""
    if not await require_staff(interaction):
        return

    if utente.bot:
        await interaction.response.send_message("⚠️ Non puoi avviare una candidatura per un bot.", ephemeral=True)
        return

    guild = interaction.guild
    gconf = cfg.guild(guild.id)
    if str(interaction.channel.id) not in gconf["tickets"]:
        await interaction.response.send_message(
            "⚠️ `/candidatura invia` va usato dentro un canale ticket.", ephemeral=True
        )
        return
    selected = ensure_candidature_type(guild, tipo)
    if selected is None:
        await interaction.response.send_message(
            "⚠️ Tipo candidatura non trovato o non configurato. Usa `/candidatura aggiungi`.",
            ephemeral=True,
        )
        return
    candidature_key, candidature_config = selected
    candidature_name = str(candidature_config.get("name", candidature_key))
    questions = [q for q in candidature_config.get("questions", []) if q]
    if not questions:
        await interaction.response.send_message(
            "⚠️ Nessuna domanda configurata in questo server. Usa `/domandestaff modifica <numero> <testo>` "
            "prima di avviare una candidatura.",
            ephemeral=True,
        )
        return

    # Blocca solo se c'è già una candidatura in corso IN QUESTO SERVER: lo stesso
    # utente può averne una aperta altrove senza che le due si disturbino.
    esistente = cfg.get_session(guild.id, utente.id)
    if esistente and esistente.get("status") in ("accepted", "rejected"):
        cfg.pop_session(guild.id, utente.id)
        esistente = None
    elif cfg.session_expired(esistente):
        # Questionario mai completato e troppo vecchio: lo scartiamo invece di
        # bloccare per sempre nuovi tentativi in questo server.
        cfg.pop_session(guild.id, utente.id)
        esistente = None
    if esistente is None:
        ref = (gconf.get("candidature_summary_messages") or {}).get(str(utente.id))
        if isinstance(ref, dict) and ref.get("status", "pending") in ("pending", "in_progress"):
            esistente = ref
    if esistente is not None:
        await interaction.response.send_message(
            f"⚠️ {utente.mention} ha già una candidatura staff in corso in questo server.", ephemeral=True
        )
        return

    intro_embed = discord.Embed(
        title="📝 Candidatura Staff",
        description=(
            f"Ciao {utente.display_name}! 👋\n\n"
            f"Ti facciamo alcune domande per la tua candidatura nello staff di **{guild.name}**. "
            f"Rispondi qui in DM, una domanda alla volta: appena rispondi arriva la successiva.\n\n"
            f"**{len(questions)} domande in totale.**"
        ),
        color=discord.Color.blurple(),
    )
    if guild.icon:
        intro_embed.set_thumbnail(url=guild.icon.url)
    try:
        await utente.send(view=render_components_v2(intro_embed))
        await utente.send(view=render_components_v2(build_question_embed(guild, 0, len(questions), questions[0])))
    except (discord.Forbidden, discord.HTTPException):
        await interaction.response.send_message(
            f"❌ Non riesco a scrivere in DM a {utente.mention} (ha i messaggi diretti chiusi).", ephemeral=True
        )
        return

    cfg.set_session(guild.id, utente.id, {
        "guild_id": guild.id,
        "index": 0,
        "answers": [],
        "started_by": interaction.user.id,
        "started_at": int(datetime.now(timezone.utc).timestamp()),
        "ticket_channel_id": interaction.channel.id,
        "candidature_type": candidature_key,
        "candidature_type_name": candidature_name,
    })
    cfg.set_active_session_guild(utente.id, None)
    # Non impostiamo una guild attiva automaticamente: con candidature in più
    # server, solo la scelta esplicita dell'utente nel DM può instradare risposte.

    embed = discord.Embed(
        description=f"✅ Domande della candidatura staff inviate in DM a {utente.mention}.",
        color=discord.Color.green(),
    )
    await interaction.response.send_message(view=render_components_v2(embed), ephemeral=True)


# ---------------------------------------------------------------------------
# Disambiguazione nei DM: se l'utente ha candidature aperte in più server,
# gli chiediamo a quale si riferiscono le sue risposte.
# ---------------------------------------------------------------------------

class GuildSessionSelect(discord.ui.Select):
    def __init__(self, bot: discord.Client, options: list[discord.SelectOption]):
        super().__init__(placeholder="🏠 Seleziona il server a cui stai rispondendo...", min_values=1, max_values=1, options=options)
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        guild_id = int(self.values[0])
        session = cfg.get_session(guild_id, interaction.user.id)
        if session is None or cfg.session_expired(session):
            if session is not None:
                cfg.pop_session(guild_id, interaction.user.id)
            await interaction.response.edit_message(
                content="⚠️ Quella candidatura non è più in corso.",
                view=None,
            )
            return

        # Il menu è stato mostrato qualche minuto fa: nel frattempo il bot potrebbe
        # essere stato rimosso da quel server. Se non c'è più, non ricreiamo la sua
        # configurazione (peek, non guild) e chiudiamo la candidatura.
        guild = self.bot.get_guild(guild_id)
        gconf = cfg.peek(guild_id)
        if guild is None or gconf is None:
            cfg.pop_session(guild_id, interaction.user.id)
            await interaction.response.edit_message(
                content="⚠️ Non faccio più parte di quel server: la candidatura è stata annullata.",
                view=None,
            )
            return

        cfg.set_active_session_guild(interaction.user.id, guild_id)
        candidature_key = session.get("candidature_type")
        candidature_config = candidature_types(guild).get(candidature_key, {})
        questions = [q for q in candidature_config.get("questions", []) if q]
        index = min(session.get("index", 0), max(len(questions) - 1, 0))

        await interaction.response.edit_message(
            content=f"✅ Perfetto: le tue prossime risposte valgono per **{guild.name}**.",
            view=None,
        )
        if questions:
            try:
                await interaction.user.send(view=render_components_v2(build_question_embed(guild, index, len(questions), questions[index])))
            except (discord.Forbidden, discord.HTTPException):
                pass


class GuildSessionView(discord.ui.View):
    def __init__(self, bot: discord.Client, options: list[discord.SelectOption]):
        super().__init__(timeout=300)
        self.add_item(GuildSessionSelect(bot, options))


async def _ask_which_guild(bot: discord.Client, message: discord.Message, guild_ids: list[int]) -> None:
    options = []
    for guild_id in guild_ids[:25]:
        guild = bot.get_guild(guild_id)
        options.append(discord.SelectOption(
            label=(guild.name if guild else f"Server {guild_id}")[:100],
            value=str(guild_id),
        ))
    embed = discord.Embed(
        title="🤔 A quale candidatura stai rispondendo?",
        description=(
            "Hai una candidatura staff in corso in **più server** contemporaneamente: "
            "scegli qui sotto a quale server si riferiscono le tue risposte.\n\n"
            "⚠️ Il messaggio che hai appena inviato non è stato registrato: dopo la scelta "
            "ti rimando la domanda a cui rispondere."
        ),
        color=discord.Color.orange(),
    )
    try:
        await message.channel.send(view=render_components_v2(embed, GuildSessionView(bot, options)))
    except (discord.Forbidden, discord.HTTPException):
        pass


async def handle_candidatura_dm_answer(bot: discord.Client, message: discord.Message) -> bool:
    """Chiamata dall'on_message per i messaggi diretti: se l'autore ha una
    candidatura staff in corso, registra la risposta e invia la domanda
    successiva (o il riepilogo finale). Ritorna True se il messaggio è stato
    consumato come risposta a una candidatura.

    Nei DM non esiste contesto server, quindi la sessione va risolta a partire
    dall'utente: se ne ha più di una aperta, si usa esclusivamente una guild
    scelta esplicitamente dall'utente; altrimenti gli viene mostrato il menu."""
    sessions = {
        guild_id: session
        for guild_id, session in cfg.sessions_for_user(message.author.id).items()
        if session.get("status", "in_progress") == "in_progress"
    }
    if not sessions:
        threads = cfg.prefs.get("candidature_dm_threads") or {}
        candidates = [
            (key, thread)
            for key, thread in threads.items()
            if key.rsplit(":", 1)[-1] == str(message.author.id)
            and isinstance(thread, dict)
            and (cfg.get_session(key.rsplit(":", 1)[0], message.author.id) or {}).get("status") == "pending"
        ]
        active = cfg.active_session_guild(message.author.id)
        if active:
            candidates = [item for item in candidates if item[0].startswith(f"{active}:")]
        if len(candidates) != 1:
            return False
        key, thread = candidates[0]
        thread.setdefault("messages", []).append({
            "direction": "user",
            "author_id": message.author.id,
            "author_name": message.author.display_name,
            "author_avatar": message.author.display_avatar.url,
            "content": message.content or "*(nessun testo, es. solo un allegato)*",
            "created_at": int(datetime.now(timezone.utc).timestamp()),
        })
        cfg.save()
        try:
            await message.author.send("La tua risposta è stata inoltrata allo staff.")
        except (discord.Forbidden, discord.HTTPException):
            pass
        return True

    # Prima di scegliere una sessione scartiamo quelle che non hanno più una
    # destinazione valida (bot rimosso dal server, oppure questionario iniziato
    # troppo tempo fa). Altrimenti una candidatura morta potrebbe intercettare
    # un DM destinato a un'altra ancora viva, o comparire nel menu di scelta.
    for guild_id in list(sessions):
        if bot.get_guild(guild_id) is None or cfg.session_expired(sessions[guild_id]):
            cfg.pop_session(guild_id, message.author.id)
            sessions.pop(guild_id, None)
    if not sessions:
        return False

    if len(sessions) == 1:
        guild_id, session = next(iter(sessions.items()))
    else:
        active = cfg.active_session_guild(message.author.id)
        if active in sessions:
            guild_id, session = active, sessions[active]
        else:
            # Non registriamo il messaggio finché l'utente non ha scelto: anche
            # vecchi puntatori automatici non possono contaminare un'altra guild.
            await _ask_which_guild(bot, message, sorted(sessions))
            return True

    guild = bot.get_guild(guild_id)
    if guild is None:
        # Il bot non è più in quel server: la candidatura non ha più destinazione.
        cfg.pop_session(guild_id, message.author.id)
        return False

    gconf = cfg.guild(guild.id)
    candidature_key = session.get("candidature_type")
    available_types = candidature_types(guild)
    if not candidature_key and len(available_types) == 1:
        candidature_key = next(iter(available_types))
        session["candidature_type"] = candidature_key
    candidature_config = available_types.get(candidature_key, {})
    questions = [q for q in candidature_config.get("questions", []) if q]
    if not questions:
        cfg.pop_session(guild_id, message.author.id)
        return False

    session["answers"].append(message.content or "*(nessun testo, es. solo un allegato)*")
    session["index"] += 1
    cfg.mark_dirty("prefs")

    if session["index"] < len(questions):
        try:
            await message.author.send(
                view=render_components_v2(build_question_embed(guild, session["index"], len(questions), questions[session["index"]]))
            )
        except (discord.Forbidden, discord.HTTPException):
            pass
        return True

    # Tutte le domande hanno una risposta: la sessione diventa una candidatura
    # pending e resta disponibile allo staff fino all'accettazione/rifiuto.
    session["status"] = "pending"
    session["completed_at"] = int(datetime.now(timezone.utc).timestamp())
    session["guild_id"] = guild.id
    session["user_id"] = message.author.id
    cfg.save()

    completion_embed = discord.Embed(
        title="✅ Candidatura completata!",
        description=(
            "La tua candidatura è stata inviata correttamente.\n\n"
            "Ora attendi che il nostro staff la valuti.\n\n"
            "Riceverai una comunicazione quando verrà presa una decisione."
        ),
        color=discord.Color.green(),
    )
    await _send_dm(message.author, completion_embed)

    channel_id = gconf.get("candidature_staff_channel")
    channel = guild.get_channel(channel_id) if channel_id else None
    now_ts = int(datetime.now(timezone.utc).timestamp())

    ticket_channel_id = session.get("ticket_channel_id")
    ticket_channel = guild.get_channel(ticket_channel_id) if ticket_channel_id else None
    section_key = next(
        (
            ticket.get("section")
            for ticket in (gconf.get("tickets") or {}).values()
            if str(ticket.get("channel_id")) == str(ticket_channel_id)
        ),
        None,
    )
    ticket_section = (gconf.get("sections") or {}).get(section_key or "", {})
    mention_role_id = ticket_section.get("candidature_mention_role_id")

    embed = discord.Embed(
        title="📋 Nuova Candidatura Staff",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=message.author.display_avatar.url)
    embed.add_field(name="👤 Candidato", value=f"{message.author.mention} ({message.author})", inline=False)
    embed.add_field(name="🕐 Data e ora", value=f"<t:{now_ts}:F>", inline=False)
    for i, (question, answer) in enumerate(zip(questions, session["answers"])):
        embed.add_field(name=f"❔ {i + 1}. {question}"[:256], value=(answer or "—")[:1024], inline=False)
    embed.set_footer(text="Candidatura ricevuta tramite il questionario /candidatura invia")

    sent_msg = None
    if isinstance(channel, discord.TextChannel):
        content = f"<@&{mention_role_id}>" if mention_role_id else None
        try:
            sent_msg = await channel.send(
                content=content, view=render_components_v2(embed),
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except discord.HTTPException:
            pass
    else:
        # nessun canale configurato: avvisiamo comunque chi ha avviato la candidatura
        starter = await fetch_member(guild, session.get("started_by"))
        if starter:
            try:
                await starter.send(
                    f"⚠️ In **{guild.name}** non è configurato nessun canale con "
                    f"`/candidaturestaffcanale`: ecco la candidatura completata:",
                )
                await starter.send(view=render_components_v2(embed))
            except (discord.Forbidden, discord.HTTPException):
                pass

    if sent_msg is not None:
        # Salviamo il riferimento per aggiornare colore ed esito dopo la valutazione.
        summary_ref = {
            "channel_id": channel.id,
            "message_id": sent_msg.id,
            # Data di creazione: serve alla manutenzione periodica per scartare i
            # riferimenti a candidature di cui nessuno ha mai deciso l'esito,
            # altrimenti la config di ogni server crescerebbe senza limite.
            "created_at": now_ts,
            "status": "pending",
        }
        gconf.setdefault("candidature_summary_messages", {})[str(message.author.id)] = summary_ref
        cfg.save()

        notice_embed = discord.Embed(
            title="⏳ Candidatura messa in Attesa",
            description=(
                "Questa candidatura è stata messa automaticamente in **Attesa**.\n\n"
                "Puoi decidere l'esito andando nel ticket e usando:\n"
                f"• `/candidatura accettata utente:{message.author.display_name} ruolo:{candidature_config.get('name', candidature_key)}` per **accettarla**\n"
                f"• `/candidatura rifiutata utente:{message.author.display_name} ruolo:{candidature_config.get('name', candidature_key)}` per **rifiutarla**"
            ),
            color=discord.Color.orange(),
        )
        notice_view = None
        if isinstance(ticket_channel, discord.TextChannel):
            notice_view = discord.ui.View()
            notice_view.add_item(discord.ui.Button(
                label="Vai al ticket", emoji="🎫", url=ticket_channel.jump_url, style=discord.ButtonStyle.link,
            ))
        try:
            sent_notice = await channel.send(view=render_components_v2(notice_embed, notice_view))
            summary_ref["notice_message_id"] = sent_notice.id
            cfg.save()
        except discord.HTTPException:
            pass

    # Messa in attesa "vera": stesso embed che manderebbe uno staffer con /candidatura attesa,
    # inviato nel ticket da cui è partita la candidatura.
    auto_attesa_embed = build_attesa_embed(
        message.author, candidature_config.get("name", candidature_key),
        footer_text=f"{guild.name} · Messa automaticamente in Attesa · Questionario /candidatura invia",
    )
    if isinstance(ticket_channel, discord.TextChannel):
        try:
            await ticket_channel.send(content=message.author.mention, view=render_components_v2(auto_attesa_embed))
        except discord.HTTPException:
            pass
    return True
