"""
tickets.py
Sistema ticket completo: pannello con select menu, sezioni configurabili,
bottoni (claim / chiudi / aggiungi utente / transcript), e transcript
riprodotto tramite webhook nel canale configurato.

Tutto è per-server: ogni guild ha le proprie sezioni, i propri ruoli staff, i
propri tempi SLA/anti-abbandono, la propria lista di opt-out notifiche e il
proprio branding. Nessun dato e nessun permesso attraversa i confini di un server.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime, timezone

import discord
from discord import app_commands

from config import (
    cfg,
    branding_text,
    claim_timeout_seconds,
    fetch_member,
    format_minutes,
    inactivity_hours,
    is_guild_admin,
    is_staff,
    is_staff_member,
    parse_color,
    require_guild_admin,
    require_staff,
    sla_seconds,
)

log = logging.getLogger("ticketbot.tickets")

PANEL_SELECT_ID = "ticket_panel_select"
BTN_CLAIM_ID = "ticket_claim"
BTN_CLOSE_ID = "ticket_close"
BTN_ADDUSER_ID = "ticket_adduser"
BTN_TRANSCRIPT_ID = "ticket_transcript"

DEFAULT_SECTION_EMOJI = "🎫"

# Testi di default del pannello. Ogni server può sovrascriverli con /ticket panel e
# la scelta viene salvata nella sua config: serve per poter ricostruire il pannello
# identico a com'era quando cambiano le sezioni, senza chiedere di ripubblicarlo.
DEFAULT_PANEL_TITLE = "Centro Assistenza"
DEFAULT_PANEL_DESCRIPTION = "Seleziona una categoria dal menu per aprire un ticket con il nostro staff."

# Numero massimo di amministratori del server aggiunti esplicitamente ai permessi
# di un canale ticket (Discord limita le sovrascritture per canale).
MAX_ADMIN_OVERWRITES = 20
# Quanti DM di notifica inviare in parallelo. Il limite è GLOBALE (un solo
# semaforo per tutta l'istanza, non uno per ticket): il rate limit di Discord è
# per bot, non per server, quindi 30 ticket aperti insieme in 30 community
# diverse non devono poter saturarlo a danno di tutti gli altri.
DM_CONCURRENCY = 5
# Massimo di sezioni per server: le opzioni di un select menu Discord sono 25.
MAX_SECTIONS = 25
# Attesa prima di ritentare un avviso SLA non consegnato per un errore transitorio.
SLA_RETRY_SECONDS = 60

# ---------------------------------------------------------------------------
# Client condiviso. I timer memorizzano il guild_id (non l'oggetto Guild), così
# non tengono in vita oggetti obsoleti e continuano a funzionare correttamente
# dopo una riconnessione o su un altro shard.
# ---------------------------------------------------------------------------
_client: discord.Client | None = None


def bind_client(client: discord.Client) -> None:
    global _client
    _client = client


def _get_guild(guild_id: int) -> discord.Guild | None:
    return _client.get_guild(int(guild_id)) if _client else None


# Task asyncio in corso per i timer SLA/anti-abbandono. La chiave include il
# guild_id, così è possibile annullare in blocco i timer di un solo server
# (es. quando il bot ne viene rimosso) senza toccare quelli degli altri.
_scheduled_tasks: dict[str, asyncio.Task] = {}

# Task di sfondo (notifiche DM, avvisi) di cui vogliamo mantenere un riferimento
# per evitare che il garbage collector li interrompa a metà.
_background_tasks: set[asyncio.Task] = set()

# Budget condiviso da TUTTI i server per l'invio dei DM di notifica: il rate limit
# di Discord è per bot, quindi il parallelismo va limitato una volta sola e non
# per singola apertura di ticket. Creato qui e non dentro le funzioni, altrimenti
# ogni ticket avrebbe il proprio budget e il limite non varrebbe più.
_dm_semaphore = asyncio.Semaphore(DM_CONCURRENCY)


def _spawn(coro) -> None:
    """Esegue una coroutine in sottofondo senza bloccare il chiamante."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _schedule(key: str, delay: float, coro_factory) -> None:
    # Un timer può riprogrammare sé stesso (retry SLA). In quel caso non
    # cancelliamo il task corrente: il suo finally non rimuoverà il nuovo task.
    existing = _scheduled_tasks.get(key)
    if existing is not asyncio.current_task():
        _cancel(key)

    async def runner():
        try:
            await asyncio.sleep(max(delay, 0))
            await coro_factory()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Timer '%s' terminato con errore.", key)
        finally:
            if _scheduled_tasks.get(key) is asyncio.current_task():
                _scheduled_tasks.pop(key, None)

    _scheduled_tasks[key] = asyncio.create_task(runner())


def _cancel(key: str) -> None:
    task = _scheduled_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()


def cancel_guild_timers(guild_id: int) -> int:
    """Annulla tutti i timer di un singolo server (bot rimosso dalla guild)."""
    prefix_ids = (f"sla:{guild_id}:", f"claim:{guild_id}:")
    keys = [k for k in list(_scheduled_tasks) if k.startswith(prefix_ids)]
    for key in keys:
        _cancel(key)
    return len(keys)


def active_timer_count() -> int:
    """Timer SLA/anti-abbandono attualmente in attesa, su tutti i server (diagnostica)."""
    return sum(1 for t in _scheduled_tasks.values() if not t.done())


def format_duration(seconds: int | None) -> str:
    if not seconds or seconds < 0:
        return "—"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}g")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{sec}s")
    return " ".join(parts)


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "ticket"


# ---------------------------------------------------------------------------
# Embed helpers
# ---------------------------------------------------------------------------

def build_panel_embed(guild: discord.Guild, gconf: dict, title: str, description: str, color: discord.Color) -> discord.Embed:
    embed = discord.Embed(
        title=f"✨ {title}",
        description=(
            f"{description}\n\n"
            f"🔹 Scegli la categoria più adatta alla tua richiesta dal **menu qui sotto**\n"
            f"🔹 Un membro dello staff ti risponderà il prima possibile\n"
            f"🔹 Spiega il problema con più dettagli possibili per velocizzare i tempi"
        ),
        color=color,
    )

    if gconf["sections"]:
        listing = "\n\n".join(
            f"{s.get('emoji', DEFAULT_SECTION_EMOJI)}  **{s.get('label')}**\n"
            f"╰ {s.get('description') or '*Nessuna descrizione disponibile*'}"
            for s in gconf["sections"].values()
        )
        embed.add_field(name="📂  C A T E G O R I E   D I S P O N I B I L I", value=listing, inline=False)
    else:
        embed.add_field(name="📂  Categorie disponibili", value="⚠️ Nessuna categoria è ancora stata configurata.", inline=False)

    embed.add_field(name="​", value="━━━━━━━━━━━━━━━━━━━━━━━━━━━", inline=False)
    embed.add_field(name="💙  Serve aiuto?", value="Il nostro team è pronto ad assisterti: apri un ticket quando vuoi, siamo qui per te.", inline=False)

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.set_footer(
        text="Seleziona un'opzione dal menu qui sotto per aprire un ticket ⤵️",
        icon_url=guild.icon.url if guild.icon else None,
    )
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_member_welcome_embed(section: dict, opener: discord.Member, number: int, motivo: str | None) -> discord.Embed:
    """Embed di benvenuto mostrato a chi ha aperto il ticket: nessuna info tecnica/staff."""
    color = parse_color(section.get("color"), 0x5865F2)
    embed = discord.Embed(
        title=f"{section.get('emoji', DEFAULT_SECTION_EMOJI)}  Ticket #{number:04d} · {section.get('label')}",
        description=(
            f"### 👋 Ciao {opener.mention}, benvenuto nel tuo ticket!\n\n"
            f"Grazie per averci contattato per **{section.get('label')}**. "
            f"Il nostro staff è stato avvisato e ti risponderà il prima possibile.\n\n"
            f"**📌 Un consiglio:** spiega il tuo problema con più dettagli possibili "
            f"(cosa succede, da quando, cosa hai già provato) così il team potrà aiutarti più in fretta.\n\n"
            f"Un membro del team ti risponderà qui a breve 💙"
        ),
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    if motivo:
        embed.add_field(name="📝 La tua richiesta", value=motivo[:1024], inline=False)
    if section.get("description"):
        embed.add_field(name="ℹ️ Informazioni categoria", value=section["description"], inline=False)
    return embed


def build_staff_panel_embed(guild: discord.Guild, section: dict, ticket: dict) -> discord.Embed:
    """Embed riservato allo staff: stato essenziale del ticket, minimal e compatto."""
    color = parse_color(section.get("color"), 0xED4245)
    opener_mention = f"<@{ticket.get('opener')}>"
    claimed_by = ticket.get("claimed_by")
    claimed_text = f"🙋 <@{claimed_by}>" if claimed_by else "🟢 Libero"
    opened_at = ticket.get("opened_at")
    opened_text = f"<t:{opened_at}:R>" if opened_at else "—"

    embed = discord.Embed(
        title=f"🛠️ Pannello Staff — #{ticket.get('number', 0):04d}",
        color=color,
    )
    embed.add_field(name="👤 Aperto da", value=opener_mention, inline=True)
    embed.add_field(name="🙋 Preso in carico", value=claimed_text, inline=True)
    embed.add_field(name="🕐 Apertura", value=opened_text, inline=True)
    if ticket.get("motivo"):
        embed.add_field(name="📝 Motivo", value=ticket["motivo"][:512], inline=False)
    if claimed_by:
        embed.add_field(
            name="🔒 Canale bloccato",
            value="Solo chi ha preso in carico e gli amministratori del server possono scrivere qui.",
            inline=False,
        )
    embed.set_footer(text="Usa i pulsanti qui sotto · /rispondi per scrivere")
    return embed


# ---------------------------------------------------------------------------
# Aggiungi utente al ticket: select nativa Discord (più affidabile di un Modal
# con parsing manuale dell'ID)
# ---------------------------------------------------------------------------

class AddUserSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=90)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="👤 Seleziona l'utente da aggiungere...", min_values=1, max_values=1)
    async def select_user(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        target = select.values[0]
        member = await fetch_member(interaction.guild, target.id)
        if member is None:
            await interaction.response.send_message("❌ Utente non trovato in questo server.", ephemeral=True)
            return
        try:
            await interaction.channel.set_permissions(
                member, view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Non ho i permessi per modificare questo canale (`Gestisci Permessi`).", ephemeral=True
            )
            return

        gconf = cfg.guild(interaction.guild.id)
        ticket = gconf["tickets"].get(str(interaction.channel.id))
        if ticket is not None and member.id not in ticket.setdefault("added_members", []):
            ticket["added_members"].append(member.id)
            cfg.save()

        embed = discord.Embed(
            description=f"✅ {member.mention} è stato aggiunto al ticket da {interaction.user.mention}.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)


# ---------------------------------------------------------------------------
# Transcript via webhook
# ---------------------------------------------------------------------------

async def get_or_create_webhook(channel: discord.TextChannel, name: str = "Ticket Transcript") -> discord.Webhook | None:
    try:
        webhooks = await channel.webhooks()
    except discord.Forbidden:
        return None
    for wh in webhooks:
        if wh.name == name:
            return wh
    try:
        return await channel.create_webhook(name=name)
    except discord.Forbidden:
        return None


async def handle_rispondi(interaction: discord.Interaction, messaggio: str):
    """Gestisce /rispondi: lo staff scrive nel ticket tramite webhook con il
    proprio nome e la propria immagine profilo al posto del messaggio normale."""
    if interaction.guild is None:
        await interaction.response.send_message("⚠️ Questo comando funziona solo dentro un server.", ephemeral=True)
        return
    gconf = cfg.guild(interaction.guild.id)
    ticket = gconf["tickets"].get(str(interaction.channel.id))
    if not ticket:
        await interaction.response.send_message("⚠️ Questo comando va usato dentro un canale ticket.", ephemeral=True)
        return
    if not await require_staff(interaction, "🚫 Solo lo staff può rispondere tramite questo comando."):
        return
    claimed_by = ticket.get("claimed_by")
    if claimed_by and claimed_by != interaction.user.id and not is_guild_admin(interaction):
        await interaction.response.send_message(
            f"🔒 Questo ticket è stato preso in carico da <@{claimed_by}>: solo lui/lei e gli "
            f"amministratori del server possono rispondere.",
            ephemeral=True,
        )
        return

    webhook = await get_or_create_webhook(interaction.channel, "Ticket Reply")
    if webhook is None:
        await interaction.response.send_message(
            "❌ Non riesco a creare/usare un webhook in questo canale (permessi mancanti: `Gestisci Webhook`).",
            ephemeral=True,
        )
        return

    try:
        await webhook.send(
            content=messaggio,
            username=interaction.user.display_name[:80] or "Staff",
            avatar_url=interaction.user.display_avatar.url,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except discord.HTTPException:
        await interaction.response.send_message("❌ Invio del messaggio fallito.", ephemeral=True)
        return

    # Lo staff può scrivere nel ticket SOLO con /rispondi, quindi è qui che va
    # registrata la prima risposta: altrimenti i timer SLA/anti-abbandono non
    # verrebbero mai fermati e scatterebbero avvisi falsi.
    changed = False
    if not ticket.get("first_response_at"):
        ticket["first_response_at"] = int(datetime.now(timezone.utc).timestamp())
        cancel_sla_check(interaction.guild.id, interaction.channel.id)
        changed = True
    if ticket.get("claimed_by") == interaction.user.id and not ticket.get("claimer_responded"):
        ticket["claimer_responded"] = True
        cancel_claim_check(interaction.guild.id, interaction.channel.id)
        changed = True
    if changed:
        cfg.save()

    await interaction.response.send_message("✅ Messaggio inviato nel ticket.", ephemeral=True)


async def handle_risponditicket(interaction: discord.Interaction, utente: discord.Member):
    """Gestisce /risponditicket: invia un promemoria di attività dentro il ticket corrente."""
    if interaction.guild is None:
        await interaction.response.send_message("⚠️ Questo comando funziona solo dentro un server.", ephemeral=True)
        return
    gconf = cfg.guild(interaction.guild.id)
    if str(interaction.channel.id) not in gconf["tickets"]:
        await interaction.response.send_message("⚠️ Questo comando va usato dentro un canale ticket.", ephemeral=True)
        return
    if not await require_staff(interaction, "🚫 Solo lo staff può inviare un promemoria."):
        return

    ore = inactivity_hours(interaction.guild.id)
    embed = discord.Embed(
        title="📨 Sei ancora lì?",
        description=(
            f"Ciao {utente.mention}, il tuo ticket è ancora aperto ma è da un po' che non riceviamo tue notizie.\n\n"
            f"Se non risponderai entro **{ore} ore** da questo messaggio, il ticket verrà chiuso. 🕒"
        ),
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="✉️ Inviato da", value=interaction.user.mention, inline=True)
    embed.add_field(name="📍 Ticket", value=interaction.channel.mention, inline=True)
    await interaction.response.send_message(content=utente.mention, embed=embed)

    try:
        await utente.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


async def handle_setlogticketstaff(interaction: discord.Interaction, canale: discord.TextChannel):
    """Gestisce /setlogticketstaff: imposta il canale di log del sistema ticket
    (opt-out/opt-in notifiche, avvisi SLA, anti-abbandono)."""
    if not await require_guild_admin(interaction):
        return
    gconf = cfg.guild(interaction.guild.id)
    gconf["log_ticket_staff_channel"] = canale.id
    cfg.save()
    await interaction.response.send_message(
        f"✅ Canale di log del sistema ticket impostato su {canale.mention}.", ephemeral=True
    )


async def handle_slachannel(interaction: discord.Interaction, canale: discord.TextChannel):
    """Gestisce /slachannel: imposta il canale dove inviare gli avvisi SLA (ticket senza risposta)."""
    if not await require_guild_admin(interaction):
        return
    gconf = cfg.guild(interaction.guild.id)
    gconf["sla_channel"] = canale.id
    cfg.save()
    await interaction.response.send_message(
        f"✅ Canale SLA impostato su {canale.mention}. Verrà usato per gli avvisi sui ticket senza risposta.",
        ephemeral=True,
    )


async def send_transcript(guild: discord.Guild, channel: discord.TextChannel, ticket: dict, closer: discord.Member) -> bool:
    """Invia un embed riassuntivo (apertura, aperto da, claimato da, chiuso da/quando) più un
    file .txt con il log completo dei messaggi (firmato in fondo) nel canale transcript
    configurato, e in DM a chi ha aperto il ticket e a chi lo ha claimato."""
    gconf = cfg.guild(guild.id)
    transcript_channel_id = gconf.get("transcript_channel")
    firma = branding_text(guild)

    opener_id = ticket.get("opener")
    claimed_by = ticket.get("claimed_by")
    opened_at = ticket.get("opened_at")
    closed_at = int(datetime.now(timezone.utc).timestamp())

    embed = discord.Embed(
        title=f"📁 Transcript · Ticket #{ticket.get('number', 0):04d} · #{channel.name}",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🕐 Ora apertura ticket e giorno",
        value=f"<t:{opened_at}:F>" if opened_at else "—",
        inline=False,
    )
    embed.add_field(
        name="👤 Ticket Aperto da",
        value=f"<@{opener_id}>" if opener_id else "—",
        inline=False,
    )
    embed.add_field(
        name="🙋 Claimato da staffer",
        value=f"<@{claimed_by}>" if claimed_by else "Nessuno",
        inline=False,
    )
    embed.add_field(
        name="🔒 Chiuso ore e giorno",
        value=f"<t:{closed_at}:F>",
        inline=False,
    )
    embed.add_field(
        name="🔒 Chiuso da",
        value=closer.mention,
        inline=False,
    )
    embed.add_field(
        name="⏱️ Durata ticket",
        value=format_duration((closed_at - opened_at) if opened_at else None),
        inline=False,
    )
    embed.set_footer(text=firma)

    try:
        messages = [m async for m in channel.history(limit=None, oldest_first=True)]
    except discord.Forbidden:
        messages = []

    text_lines = [
        f"[{m.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {m.author} ({m.author.id}): {m.content}"
        for m in messages
        if m.content or m.embeds or m.attachments
    ]
    if not text_lines:
        text_lines.append("Nessun messaggio registrato.")
    text_lines.append("")
    text_lines.append(firma)
    txt_bytes = "\n".join(text_lines).encode("utf-8")
    txt_filename = f"transcript-{channel.name}.txt"

    async def _deliver(target) -> None:
        try:
            await target.send(embed=embed)
            await target.send(
                content="📄 Trascrizione completa del ticket:",
                file=discord.File(fp=io.BytesIO(txt_bytes), filename=txt_filename),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    sent_to_channel = False
    if transcript_channel_id:
        transcript_channel = guild.get_channel(transcript_channel_id)
        if isinstance(transcript_channel, discord.TextChannel):
            await _deliver(transcript_channel)
            sent_to_channel = True

    # DM a chi ha aperto il ticket e a chi lo ha claimato (se ancora nel server)
    notified_ids = set()
    opener_member = await fetch_member(guild, opener_id)
    if opener_member:
        await _deliver(opener_member)
        notified_ids.add(opener_member.id)
    if claimed_by and claimed_by not in notified_ids:
        claimer_member = await fetch_member(guild, claimed_by)
        if claimer_member:
            await _deliver(claimer_member)

    return sent_to_channel


# ---------------------------------------------------------------------------
# Blocco chat al claim: solo il claimer e gli amministratori del server
# ---------------------------------------------------------------------------

async def apply_claim_lock(guild: discord.Guild, channel: discord.TextChannel, gconf: dict, section: dict, claimer: discord.Member):
    staff_role_id = section.get("staff_role_id") or gconf.get("staff_role")
    role = guild.get_role(staff_role_id) if staff_role_id else None
    if role:
        try:
            await channel.set_permissions(role, view_channel=True, send_messages=False, read_message_history=True)
        except discord.Forbidden:
            pass
    try:
        await channel.set_permissions(
            claimer, view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True,
        )
    except discord.Forbidden:
        pass


async def revert_claim_lock(guild: discord.Guild, channel: discord.TextChannel, gconf: dict, section: dict, previous_claimer_id: int | None):
    staff_role_id = section.get("staff_role_id") or gconf.get("staff_role")
    role = guild.get_role(staff_role_id) if staff_role_id else None
    if role:
        try:
            await channel.set_permissions(
                role, view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
            )
        except discord.Forbidden:
            pass
    if previous_claimer_id:
        member = await fetch_member(guild, previous_claimer_id)
        if member:
            try:
                await channel.set_permissions(member, overwrite=None)
            except discord.Forbidden:
                pass


async def toggle_claim(guild: discord.Guild, channel: discord.TextChannel, gconf: dict, ticket: dict, section: dict, staffer: discord.Member):
    """Reclama/rilascia il ticket, applicando o rimuovendo il blocco chat. Ritorna (claimato: bool, precedente_claimer_id)."""
    if ticket.get("claimed_by") == staffer.id:
        ticket["claimed_by"] = None
        ticket["claim_deadline"] = None
        ticket["claimer_responded"] = False
        cfg.save()
        await revert_claim_lock(guild, channel, gconf, section, staffer.id)
        cancel_claim_check(guild.id, channel.id)
        return False, staffer.id

    previous = ticket.get("claimed_by")
    if previous:
        prev_member = await fetch_member(guild, previous)
        if prev_member:
            try:
                await channel.set_permissions(prev_member, overwrite=None)
            except discord.Forbidden:
                pass

    timeout = claim_timeout_seconds(guild.id)
    ticket["claimed_by"] = staffer.id
    ticket["claim_deadline"] = int(datetime.now(timezone.utc).timestamp()) + timeout
    ticket["claimer_responded"] = False
    cfg.save()
    await apply_claim_lock(guild, channel, gconf, section, staffer)
    schedule_claim_check(guild.id, channel.id, staffer.id, timeout)
    return True, previous


async def update_staff_panel_message(guild: discord.Guild, channel: discord.TextChannel, ticket: dict):
    """Rigenera l'embed del pannello staff con lo stato aggiornato (claim, ecc.)."""
    gconf = cfg.guild(guild.id)
    section = gconf["sections"].get(ticket.get("section"), {})
    msg_id = ticket.get("staff_message_id")
    if not msg_id:
        return
    try:
        msg = await channel.fetch_message(msg_id)
        await msg.edit(embed=build_staff_panel_embed(guild, section, ticket))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


# ---------------------------------------------------------------------------
# Log del sistema ticket, sempre nel canale configurato DA QUEL SERVER
# ---------------------------------------------------------------------------

async def log_to_ticket_channel(guild: discord.Guild, embed: discord.Embed) -> None:
    gconf = cfg.peek(guild.id)
    if not gconf:
        return
    channel_id = gconf.get("log_ticket_staff_channel")
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass


# ---------------------------------------------------------------------------
# Timer SLA: avvisa lo staff se un ticket resta senza risposta troppo a lungo
# ---------------------------------------------------------------------------

def schedule_sla_check(guild_id: int, channel_id: int, delay: float) -> None:
    _schedule(f"sla:{guild_id}:{channel_id}", delay, lambda: run_sla_check(guild_id, channel_id))


def cancel_sla_check(guild_id: int, channel_id: int) -> None:
    _cancel(f"sla:{guild_id}:{channel_id}")


async def run_sla_check(guild_id: int, channel_id: int) -> None:
    guild = _get_guild(guild_id)
    if guild is None:
        return  # il bot non è più in questo server
    gconf = cfg.peek(guild_id)
    if not gconf:
        return
    ticket = (gconf.get("tickets") or {}).get(str(channel_id))
    if not ticket or ticket.get("status") != "open":
        return
    if ticket.get("first_response_at") or ticket.get("sla_notified"):
        return
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    section = (gconf.get("sections") or {}).get(ticket.get("section"), {})
    role_id = section.get("staff_role_id") or gconf.get("staff_role")
    attesa = format_minutes(sla_seconds(guild_id))

    embed = discord.Embed(
        title=f"⏰ Ticket in attesa da {attesa}",
        description="Un utente sta aspettando una risposta: dagli un'occhiata appena puoi 💙",
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="👤 Utente", value=f"<@{ticket.get('opener')}>", inline=True)
    embed.add_field(name="📂 Categoria", value=section.get("label", "N/D"), inline=True)
    embed.add_field(name="📝 Motivo", value=(ticket.get("motivo") or "Non specificato")[:512], inline=False)

    link_view = discord.ui.View()
    link_view.add_item(discord.ui.Button(label="Apri Ticket", emoji="🔗", url=channel.jump_url, style=discord.ButtonStyle.link))

    # Canale degli avvisi: quello configurato con /slachannel; se questo server non
    # ne ha impostato nessuno l'avviso finisce nel ticket stesso, così l'automazione
    # funziona anche in un server appena configurato invece di restare muta.
    sla_channel_id = gconf.get("sla_channel")
    sla_channel = guild.get_channel(sla_channel_id) if sla_channel_id else None
    fallback = not isinstance(sla_channel, discord.TextChannel)
    destinazione = channel if fallback else sla_channel

    role_mention = f"<@&{role_id}>" if role_id else None
    try:
        await destinazione.send(
            content=role_mention, embed=embed, view=None if fallback else link_view,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
    except discord.HTTPException:
        # Non segnare il ticket come notificato se Discord non ha ricevuto
        # l'avviso. Riprogrammiamo lo stesso timer, sempre nella sua guild.
        schedule_sla_check(guild_id, channel_id, SLA_RETRY_SECONDS)
        return

    log_embed = discord.Embed(title="⚠️ Avviso Ticket Inviato", color=discord.Color.orange())
    log_embed.add_field(name="🎫 Ticket", value=channel.mention, inline=False)
    log_embed.add_field(name="👤 Utente", value=f"<@{ticket.get('opener')}>", inline=False)
    log_embed.add_field(name="⏰ Motivo", value=f"Nessuna risposta entro {attesa}.", inline=False)
    log_embed.add_field(name="📢 Ruolo notificato", value=(f"<@&{role_id}>" if role_id else "Nessuno"), inline=False)
    if fallback:
        log_embed.add_field(
            name="ℹ️ Nota",
            value="Nessun canale SLA configurato (`/slachannel`): l'avviso è stato inviato nel ticket stesso.",
            inline=False,
        )
    await log_to_ticket_channel(guild, log_embed)

    ticket["sla_notified"] = True
    cfg.save()


# ---------------------------------------------------------------------------
# Timer anti-abbandono: rimuove il claim se lo staffer non risponde in tempo
# ---------------------------------------------------------------------------

def schedule_claim_check(guild_id: int, channel_id: int, staffer_id: int, delay: float) -> None:
    _schedule(f"claim:{guild_id}:{channel_id}", delay, lambda: run_claim_check(guild_id, channel_id, staffer_id))


def cancel_claim_check(guild_id: int, channel_id: int) -> None:
    _cancel(f"claim:{guild_id}:{channel_id}")


async def run_claim_check(guild_id: int, channel_id: int, staffer_id: int) -> None:
    guild = _get_guild(guild_id)
    if guild is None:
        return
    gconf = cfg.peek(guild_id)
    if not gconf:
        return
    ticket = (gconf.get("tickets") or {}).get(str(channel_id))
    if not ticket or ticket.get("status") != "open":
        return
    if ticket.get("claimed_by") != staffer_id or ticket.get("claimer_responded"):
        return
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    attesa = format_minutes(claim_timeout_seconds(guild_id))
    section = (gconf.get("sections") or {}).get(ticket.get("section"), {})
    await revert_claim_lock(guild, channel, gconf, section, staffer_id)
    ticket["claimed_by"] = None
    ticket["claim_deadline"] = None
    ticket["claimer_responded"] = False
    cfg.save()
    await update_staff_panel_message(guild, channel, ticket)

    member = await fetch_member(guild, staffer_id)
    display = member.mention if member else f"<@{staffer_id}>"

    embed = discord.Embed(
        title="🚫 Ticket tornato disponibile",
        description=(
            f"{display} non ha risposto entro {attesa} dalla presa in carico, "
            f"quindi il ticket è stato rimesso a disposizione di tutto lo staff."
        ),
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass

    log_embed = discord.Embed(title="🚫 Anti-Abbandono Attivato", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
    log_embed.add_field(name="👤 Staff", value=display, inline=False)
    log_embed.add_field(name="🎫 Ticket", value=channel.mention, inline=False)
    log_embed.add_field(name="⏰ Motivo", value=f"Nessuna risposta dopo il claim per {attesa}.", inline=False)
    await log_to_ticket_channel(guild, log_embed)


# ---------------------------------------------------------------------------
# Notifiche DM allo staff quando viene aperto un nuovo ticket (opt-out PER SERVER)
#
# I pulsanti vivono in DM, dove non esiste contesto guild: l'ID del server viene
# quindi codificato nel custom_id del bottone (discord.ui.DynamicItem), così
# l'opt-out vale solo per il server da cui è arrivata la notifica.
# ---------------------------------------------------------------------------

async def log_notify_change(guild: discord.Guild | None, user: discord.abc.User, opted_out: bool) -> None:
    """Logga il cambio di preferenza SOLO nel server a cui si riferisce."""
    if guild is None:
        return
    if opted_out:
        embed = discord.Embed(
            title="🔕 Notifiche disattivate",
            description=f"{user.mention} non riceverà più i DM per i nuovi ticket aperti in questo server.",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
    else:
        embed = discord.Embed(
            title="🔔 Notifiche riattivate",
            description=f"{user.mention} tornerà a ricevere i DM per i nuovi ticket aperti in questo server.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
    await log_to_ticket_channel(guild, embed)


def _dm_button_guild_alive(guild_id: int) -> bool:
    """True se ha ancora senso applicare la scelta di un vecchio pulsante DM.

    I messaggi diretti restano cliccabili per sempre, anche anni dopo. Senza
    questo controllo un click su un avviso vecchio ricreerebbe da zero (via
    ``cfg.guild()``) la configurazione di un server da cui il bot è già stato
    rimosso, riempiendo i dati di tutti gli altri server di voci fantasma."""
    gconf = cfg.peek(guild_id)
    return gconf is not None and gconf.get("left_at") is None


async def _reject_dead_dm_button(interaction: discord.Interaction, azione: str) -> None:
    """Risposta ai pulsanti DM di un server che il bot ha lasciato: togliamo la
    view (così non è più cliccabile) e spieghiamo perché non serve più."""
    try:
        await interaction.response.edit_message(view=None)
    except discord.HTTPException:
        return
    try:
        await interaction.followup.send(
            embed=discord.Embed(
                title="ℹ️ Server non più disponibile",
                description=(
                    f"Non posso {azione} per quel server: non ne faccio più parte, "
                    f"quindi da lì non riceverai comunque altri avvisi.\n"
                    f"Le tue preferenze negli altri server non sono state toccate."
                ),
                color=discord.Color.greyple(),
            )
        )
    except discord.HTTPException:
        pass


class NotifyOptOutButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tnotify:out:(?P<gid>\d+)"):
    """Pulsante 'disattiva avvisi' inviato in DM. Il guild_id è nel custom_id."""

    def __init__(self, guild_id: int, *, done: bool = False):
        self.guild_id = int(guild_id)
        super().__init__(
            discord.ui.Button(
                label="🔕 Avvisi disattivati" if done else "Non vuoi più ricevere questi messaggi?",
                emoji=None if done else "🔕",
                style=discord.ButtonStyle.danger,
                custom_id=f"tnotify:out:{self.guild_id}",
                disabled=done,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
        return cls(int(match["gid"]))

    async def callback(self, interaction: discord.Interaction):
        if not _dm_button_guild_alive(self.guild_id):
            await _reject_dead_dm_button(interaction, "disattivare gli avvisi")
            return

        guild = interaction.client.get_guild(self.guild_id)
        nome_server = guild.name if guild else "questo server"

        cfg.set_notify_optout(self.guild_id, interaction.user.id, True)

        await interaction.response.edit_message(view=TicketNotifyOptOutView(self.guild_id, done=True))
        await interaction.followup.send(
            embed=discord.Embed(
                title="👋 Fatto, avvisi disattivati",
                description=(
                    f"Non riceverai più i DM per i nuovi ticket di **{nome_server}**.\n"
                    f"Gli avvisi degli altri server restano attivi. "
                    f"Se cambi idea, usa il pulsante qui sotto."
                ),
                color=discord.Color.orange(),
            ),
            view=TicketNotifyOptInView(self.guild_id),
        )
        await log_notify_change(guild, interaction.user, opted_out=True)


class NotifyOptInButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tnotify:in:(?P<gid>\d+)"):
    """Pulsante 'riattiva avvisi' per un singolo server."""

    def __init__(self, guild_id: int, *, done: bool = False):
        self.guild_id = int(guild_id)
        super().__init__(
            discord.ui.Button(
                label="🟢 Notifiche riattivate" if done else "Riattiva notifiche",
                emoji=None if done else "🟢",
                style=discord.ButtonStyle.success,
                custom_id=f"tnotify:in:{self.guild_id}",
                disabled=done,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
        return cls(int(match["gid"]))

    async def callback(self, interaction: discord.Interaction):
        if not _dm_button_guild_alive(self.guild_id):
            await _reject_dead_dm_button(interaction, "riattivare le notifiche")
            return

        guild = interaction.client.get_guild(self.guild_id)
        nome_server = guild.name if guild else "questo server"

        cfg.set_notify_optout(self.guild_id, interaction.user.id, False)

        await interaction.response.edit_message(view=TicketNotifyOptInView(self.guild_id, done=True))
        await interaction.followup.send(
            embed=discord.Embed(
                title="🔔 Bentornato tra gli avvisi!",
                description=f"Riceverai di nuovo un DM per ogni nuovo ticket di **{nome_server}**. ✅",
                color=discord.Color.green(),
            )
        )
        await log_notify_change(guild, interaction.user, opted_out=False)


class TicketNotifyOptOutView(discord.ui.View):
    """View allegata al DM di 'nuovo ticket aperto' di un server specifico."""

    def __init__(self, guild_id: int, *, done: bool = False):
        super().__init__(timeout=None)
        self.add_item(NotifyOptOutButton(guild_id, done=done))


class TicketNotifyOptInView(discord.ui.View):
    """View inviata dopo l'opt-out: permette di riattivare gli avvisi di quel server."""

    def __init__(self, guild_id: int, *, done: bool = False):
        super().__init__(timeout=None)
        self.add_item(NotifyOptInButton(guild_id, done=done))


async def _collect_notify_recipients(guild: discord.Guild, gconf: dict, section: dict, opener_id: int) -> dict[int, discord.Member]:
    role_ids = {rid for rid in (section.get("staff_role_id"), gconf.get("staff_role")) if rid}
    if not role_ids:
        return {}

    # role.members legge la cache membri. Il bot non fa più il chunking di tutti i
    # server all'avvio (insostenibile con molti server), quindi lo facciamo qui la
    # prima volta che serve per questa guild.
    if not guild.chunked:
        try:
            await guild.chunk(cache=True)
        except (discord.ClientException, discord.HTTPException, asyncio.TimeoutError):
            log.warning("Chunking di %s (%s) non riuscito: alcune notifiche potrebbero non partire.", guild.name, guild.id)

    optout = set(gconf.get("ticket_notify_optout") or [])
    recipients: dict[int, discord.Member] = {}
    for role_id in role_ids:
        role = guild.get_role(role_id)
        if not role:
            continue
        for member in role.members:
            if member.bot or member.id == opener_id or member.id in optout:
                continue
            recipients[member.id] = member
    return recipients


async def notify_staff_new_ticket(guild: discord.Guild, opener: discord.Member, section: dict, channel: discord.TextChannel, motivo: str | None) -> None:
    """Invia un DM allo staff pertinente DI QUESTO SERVER (ruolo globale della guild +
    ruolo di sezione), escludendo chi ha disattivato gli avvisi per questo server."""
    gconf = cfg.peek(guild.id)
    if not gconf:
        return

    recipients = await _collect_notify_recipients(guild, gconf, section, opener.id)
    if not recipients:
        return

    embed = discord.Embed(
        title="🎫 Nuovo ticket aperto",
        description="Un utente ha bisogno di assistenza: passa a dargli una mano quando puoi 💙",
        color=parse_color(section.get("color"), 0x5865F2),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="🏠 Server", value=guild.name, inline=True)
    embed.add_field(name="👤 Utente", value=opener.mention, inline=True)
    embed.add_field(name="📂 Categoria", value=section.get("label", "N/D"), inline=True)
    embed.add_field(name="📍 Canale", value=channel.mention, inline=True)
    embed.add_field(name="📌 Motivo", value=(motivo or "Non specificato")[:512], inline=False)
    embed.set_thumbnail(url=opener.display_avatar.url)
    embed.set_footer(text=f"{branding_text(guild)} · puoi disattivare questi avvisi col pulsante qui sotto")

    view = TicketNotifyOptOutView(guild.id)

    async def _dm(member: discord.Member) -> None:
        async with _dm_semaphore:
            try:
                await member.send(embed=embed, view=view)
            except (discord.Forbidden, discord.HTTPException):
                pass

    await asyncio.gather(*(_dm(m) for m in recipients.values()))


# ---------------------------------------------------------------------------
# Views persistenti
# ---------------------------------------------------------------------------

class TicketSectionSelect(discord.ui.Select):
    def __init__(self, options: list[discord.SelectOption] | None = None):
        opts = options or [discord.SelectOption(label="Nessuna sezione configurata", value="_none")]
        super().__init__(
            placeholder="📩 Seleziona una categoria per aprire un ticket...",
            min_values=1,
            max_values=1,
            options=opts,
            custom_id=PANEL_SELECT_ID,
        )

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value == "_none":
            await interaction.response.send_message("⚠️ Nessuna sezione è ancora stata configurata.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("⚠️ Il pannello funziona solo dentro un server.", ephemeral=True)
            return

        gconf = cfg.guild(interaction.guild.id)
        section = gconf["sections"].get(value)
        if not section:
            await interaction.response.send_message("⚠️ Questa sezione non esiste più.", ephemeral=True)
            return

        if interaction.user.id in gconf.get("blacklist", []):
            await interaction.response.send_message("🚫 Non puoi aprire ticket in questo server.", ephemeral=True)
            return

        # controlla se ha già un ticket aperto in questa sezione (di questo server)
        for ch_id, t in gconf["tickets"].items():
            if t["opener"] == interaction.user.id and t["section"] == value and t["status"] == "open":
                await interaction.response.send_message(
                    f"⚠️ Hai già un ticket aperto in questa categoria: <#{ch_id}>", ephemeral=True
                )
                return

        await interaction.response.send_modal(TicketReasonModal(value, section))


class TicketReasonModal(discord.ui.Modal, title="🎫 Apri un ticket"):
    motivo = discord.ui.TextInput(
        label="Descrivi brevemente il tuo problema",
        style=discord.TextStyle.paragraph,
        placeholder="Es: Non riesco ad accedere al mio account...",
        required=True,
        max_length=500,
    )

    def __init__(self, section_key: str, section: dict):
        super().__init__()
        self.section_key = section_key
        self.section = section

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = await create_ticket_channel(
            interaction.guild, interaction.user, self.section_key, self.section, motivo=str(self.motivo)
        )
        if channel is None:
            await interaction.followup.send("❌ Non sono riuscito a creare il ticket (permessi mancanti).", ephemeral=True)
            return
        await interaction.followup.send(f"✅ Ticket creato: {channel.mention}", ephemeral=True)


class TicketPanelView(discord.ui.View):
    """View persistente del pannello (select menu con le sezioni)."""

    def __init__(self, options: list[discord.SelectOption] | None = None):
        super().__init__(timeout=None)
        self.add_item(TicketSectionSelect(options))


class TicketControlView(discord.ui.View):
    """View persistente dei controlli dentro ogni ticket."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Reclama", emoji="🙋", style=discord.ButtonStyle.primary, custom_id=BTN_CLAIM_ID)
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        gconf = cfg.guild(interaction.guild.id)
        ticket = gconf["tickets"].get(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message("⚠️ Questo canale non è un ticket valido.", ephemeral=True)
            return
        if not await require_staff(interaction, "🚫 Solo lo staff può reclamare il ticket."):
            return

        section = gconf["sections"].get(ticket.get("section"), {})
        claimed, _ = await toggle_claim(interaction.guild, interaction.channel, gconf, ticket, section, interaction.user)
        embed = build_staff_panel_embed(interaction.guild, section, ticket)
        await interaction.response.edit_message(embed=embed)

        if claimed:
            await interaction.followup.send(
                f"🙋 Ticket preso in carico da {interaction.user.mention}. "
                f"D'ora in poi solo lui/lei e gli amministratori del server possono scrivere qui."
            )
        else:
            await interaction.followup.send(
                f"↩️ {interaction.user.mention} ha rilasciato il ticket. Tutto lo staff può tornare a scrivere."
            )

    @discord.ui.button(label="Aggiungi utente", emoji="➕", style=discord.ButtonStyle.secondary, custom_id=BTN_ADDUSER_ID)
    async def add_user_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_staff(interaction, "🚫 Solo lo staff può aggiungere utenti."):
            return
        await interaction.response.send_message(
            "👤 Seleziona l'utente da aggiungere al ticket:", view=AddUserSelectView(), ephemeral=True
        )

    @discord.ui.button(label="Transcript", emoji="📄", style=discord.ButtonStyle.secondary, custom_id=BTN_TRANSCRIPT_ID)
    async def transcript_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_staff(interaction, "🚫 Solo lo staff può generare il transcript."):
            return
        gconf = cfg.guild(interaction.guild.id)
        ticket = gconf["tickets"].get(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message("⚠️ Questo canale non è un ticket valido.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await send_transcript(interaction.guild, interaction.channel, ticket, interaction.user)
        if ok:
            await interaction.followup.send("✅ Transcript inviato nel canale configurato.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Nessun canale transcript configurato (`/ticket transcriptchannel`).", ephemeral=True)

    @discord.ui.button(label="Chiudi Ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id=BTN_CLOSE_ID)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        gconf = cfg.guild(interaction.guild.id)
        ticket = gconf["tickets"].get(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message("⚠️ Questo canale non è un ticket valido.", ephemeral=True)
            return
        if not (is_staff(interaction) or interaction.user.id == ticket["opener"]):
            await interaction.response.send_message("🚫 Non puoi chiudere questo ticket.", ephemeral=True)
            return
        await interaction.response.send_message(
            "⚠️ Confermi di voler chiudere questo ticket? Verrà generato il transcript e il canale eliminato tra 5 secondi.",
            view=ConfirmCloseView(),
            ephemeral=True,
        )


class ConfirmCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Conferma chiusura", emoji="✅", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="🔒 Chiusura in corso, generazione transcript...", view=None)
        await close_ticket(interaction.guild, interaction.channel, interaction.user)

    @discord.ui.button(label="Annulla", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Chiusura annullata.", view=None)


# ---------------------------------------------------------------------------
# Funzioni di supporto ticket
# ---------------------------------------------------------------------------

async def create_ticket_channel(
    guild: discord.Guild, opener: discord.Member, section_key: str, section: dict, motivo: str | None = None
) -> discord.TextChannel | None:
    gconf = cfg.guild(guild.id)
    category = guild.get_channel(section.get("category_id")) if section.get("category_id") else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        category = None

    gconf["ticket_counter"] += 1
    number = gconf["ticket_counter"]

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        opener: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True,
            manage_permissions=True, read_message_history=True, attach_files=True, embed_links=True,
        ),
    }

    staff_role_id = section.get("staff_role_id") or gconf.get("staff_role")
    role = guild.get_role(staff_role_id) if staff_role_id else None
    if role:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
        )

    # Gli amministratori DI QUESTO SERVER (elenco /config owner) hanno sempre
    # accesso completo, a prescindere dal claim. Nessun ID globale: un owner di
    # un altro server non ottiene alcun permesso qui.
    for admin_id in cfg.admin_ids(guild.id)[:MAX_ADMIN_OVERWRITES]:
        admin_member = await fetch_member(guild, admin_id)
        if admin_member and admin_member not in overwrites:
            overwrites[admin_member] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True, manage_messages=True,
            )

    channel_name = f"{slugify(section_key)}-{number:04d}"
    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"Ticket aperto da {opener} ({opener.id})",
        )
    except discord.Forbidden:
        # Il canale non è stato creato: restituiamo il numero al contatore di QUESTO
        # server, altrimenti ogni tentativo fallito lascerebbe un buco nella
        # numerazione dei suoi ticket. Solo se nel frattempo nessun altro ha già
        # preso il numero successivo, per non generare due ticket con lo stesso.
        if gconf.get("ticket_counter") == number:
            gconf["ticket_counter"] = number - 1
        cfg.save()
        return None

    opened_at = int(datetime.now(timezone.utc).timestamp())
    sla = sla_seconds(guild.id)
    ticket = {
        "opener": opener.id,
        "section": section_key,
        "motivo": motivo,
        "claimed_by": None,
        "status": "open",
        "number": number,
        "opened_at": opened_at,
        "staff_message_id": None,
        # timer SLA (avviso se nessuno staff risponde entro sla_seconds di questa guild)
        "sla_deadline": opened_at + sla,
        "sla_notified": False,
        "first_response_at": None,
        # timer anti-abbandono (rimozione claim se il claimer non risponde)
        "claim_deadline": None,
        "claimer_responded": False,
        # extra per lo storico/gestione
        "added_members": [],
        "notes": [],
    }
    gconf["tickets"][str(channel.id)] = ticket
    cfg.save()

    # messaggio 1: benvenuto per chi ha aperto il ticket (nessuna info staff)
    member_embed = build_member_welcome_embed(section, opener, number, motivo)
    await channel.send(
        content=opener.mention,
        embed=member_embed,
        allowed_mentions=discord.AllowedMentions(users=True),
    )

    # messaggio 2: pannello riservato allo staff, con i pulsanti di gestione
    staff_embed = build_staff_panel_embed(guild, section, ticket)
    staff_msg = await channel.send(
        content=role.mention if role else None,
        embed=staff_embed,
        view=TicketControlView(),
        allowed_mentions=discord.AllowedMentions(roles=True),
    )
    ticket["staff_message_id"] = staff_msg.id
    cfg.save()

    # Le notifiche DM partono in sottofondo: con molti staffer (e molti server)
    # non devono far attendere chi ha appena aperto il ticket.
    _spawn(notify_staff_new_ticket(guild, opener, section, channel, motivo))
    schedule_sla_check(guild.id, channel.id, sla)

    return channel


async def close_ticket(guild: discord.Guild, channel: discord.TextChannel, closer: discord.Member):
    gconf = cfg.guild(guild.id)
    ticket = gconf["tickets"].get(str(channel.id))

    cancel_sla_check(guild.id, channel.id)
    cancel_claim_check(guild.id, channel.id)

    if ticket:
        transcript_sent = await send_transcript(guild, channel, ticket, closer)
        closed_at = int(datetime.now(timezone.utc).timestamp())
        opened_at = ticket.get("opened_at")
        duration = (closed_at - opened_at) if opened_at else None

        cfg.add_history_entry(guild.id, {
            "number": ticket.get("number"),
            "channel_name": channel.name,
            "section": ticket.get("section"),
            "motivo": ticket.get("motivo"),
            "opener": ticket.get("opener"),
            "claimed_by": ticket.get("claimed_by"),
            "added_members": ticket.get("added_members", []),
            "notes": ticket.get("notes", []),
            "closed_by": closer.id,
            "opened_at": opened_at,
            "closed_at": closed_at,
            "duration_seconds": duration,
            "transcript_sent": transcript_sent,
            "rating": None,  # riservato per una futura funzione di valutazione
        })

        gconf["tickets"].pop(str(channel.id), None)
        cfg.save()

    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket chiuso da {closer}")
    except (discord.Forbidden, discord.NotFound):
        pass


def forget_deleted_ticket(guild_id: int, channel_id: int, channel_name: str = "") -> bool:
    """Un canale ticket è stato eliminato a mano (senza passare da /ticket close):
    annulla i suoi timer, archivia il ticket nello storico di QUEL server e lo
    rimuove dai ticket aperti. Ritorna True se il canale era davvero un ticket.

    Senza questo, il ticket resterebbe per sempre tra gli aperti di quella guild:
    conteggi sbagliati in /config lista, impossibilità di riaprire un ticket nella
    stessa sezione e timer che continuano a girare su un canale inesistente."""
    gconf = cfg.peek(guild_id)
    if not gconf:
        return False
    ticket = (gconf.get("tickets") or {}).pop(str(channel_id), None)
    if ticket is None:
        return False

    cancel_sla_check(guild_id, channel_id)
    cancel_claim_check(guild_id, channel_id)

    closed_at = int(datetime.now(timezone.utc).timestamp())
    opened_at = ticket.get("opened_at")
    cfg.add_history_entry(guild_id, {
        "number": ticket.get("number"),
        "channel_name": channel_name or str(channel_id),
        "section": ticket.get("section"),
        "motivo": ticket.get("motivo"),
        "opener": ticket.get("opener"),
        "claimed_by": ticket.get("claimed_by"),
        "added_members": ticket.get("added_members", []),
        "notes": ticket.get("notes", []),
        "closed_by": None,          # nessuno: il canale è stato eliminato a mano
        "opened_at": opened_at,
        "closed_at": closed_at,
        "duration_seconds": (closed_at - opened_at) if opened_at else None,
        "transcript_sent": False,   # il canale non c'è più: niente transcript
        "channel_deleted": True,
        "rating": None,
    })
    cfg.save()
    return True


# ---------------------------------------------------------------------------
# Pannello ticket: pubblicazione e aggiornamento
# ---------------------------------------------------------------------------

def panel_options(gconf: dict) -> list[discord.SelectOption] | None:
    """Opzioni del menu a tendina del pannello per UN server.

    Le etichette sono tagliate a 100 caratteri: è il limite di Discord e senza il
    taglio una sezione con un nome lungo farebbe fallire l'invio del pannello
    (l'admin di quel server si ritroverebbe con un pannello che non si pubblica)."""
    options = [
        discord.SelectOption(
            label=(s.get("label") or key)[:100],
            value=key,
            description=(s.get("description") or "")[:100] or None,
            emoji=s.get("emoji") or None,
        )
        for key, s in gconf["sections"].items()
    ]
    return options or None


def _panel_payload(guild: discord.Guild, gconf: dict) -> tuple[discord.Embed, "TicketPanelView"]:
    """Embed + view del pannello, ricostruiti dalla personalizzazione salvata da
    questo server. Usato sia per pubblicarlo che per riallinearlo."""
    panel = gconf.get("panel") or {}
    embed = build_panel_embed(
        guild,
        gconf,
        panel.get("title") or DEFAULT_PANEL_TITLE,
        panel.get("description") or DEFAULT_PANEL_DESCRIPTION,
        parse_color(panel.get("color")),
    )
    return embed, TicketPanelView(panel_options(gconf))


async def publish_panel(
    guild: discord.Guild,
    gconf: dict,
    target: discord.TextChannel,
    *,
    title: str | None = None,
    description: str | None = None,
    color: str | None = None,
) -> tuple[discord.Message, bool]:
    """Pubblica il pannello di un server e memorizza la sua personalizzazione.

    Se il pannello esiste già **nello stesso canale** lo modifichiamo invece di
    inviarne un altro: rieseguire /ticket panel per cambiare un titolo non deve
    lasciare una scia di pannelli morti nel canale. Ritorna (messaggio, aggiornato).
    """
    panel = gconf.setdefault("panel", {})
    if title is not None:
        panel["title"] = title
    if description is not None:
        panel["description"] = description
    if color is not None:
        panel["color"] = color

    embed, view = _panel_payload(guild, gconf)

    old_channel_id = panel.get("channel_id")
    old_message_id = panel.get("message_id")
    if old_channel_id == target.id and old_message_id:
        try:
            message = await target.fetch_message(old_message_id)
            await message.edit(embed=embed, view=view)
            cfg.mark_dirty("config")
            return message, True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass   # cancellato a mano o non più raggiungibile: ne inviamo uno nuovo

    message = await target.send(embed=embed, view=view)
    panel["channel_id"] = target.id
    panel["message_id"] = message.id
    cfg.mark_dirty("config")
    return message, False


async def refresh_panel(guild: discord.Guild, gconf: dict) -> bool:
    """Riallinea il pannello già pubblicato di un server dopo una modifica alle
    sezioni. Non ne pubblica mai uno nuovo: se non c'è, o non è più raggiungibile,
    ritorna False e il chiamante avvisa l'admin di usare /ticket panel."""
    panel = gconf.get("panel") or {}
    channel_id, message_id = panel.get("channel_id"), panel.get("message_id")
    if not channel_id or not message_id:
        return False

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return False

    embed, view = _panel_payload(guild, gconf)
    try:
        message = await channel.fetch_message(message_id)
        await message.edit(embed=embed, view=view)
        return True
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        # Il messaggio non esiste più: dimentichiamo il riferimento (ma teniamo la
        # personalizzazione, così il prossimo /ticket panel la riusa).
        panel["channel_id"] = None
        panel["message_id"] = None
        cfg.mark_dirty("config")
        return False


# ---------------------------------------------------------------------------
# Comandi slash: gruppo /ticket
# ---------------------------------------------------------------------------

class TicketGroup(app_commands.Group):
    def __init__(self):
        super().__init__(
            name="ticket",
            description="Gestione del sistema ticket",
            guild_only=True,
        )

    # ---- /ticket panel ----
    @app_commands.command(name="panel", description="[Admin server] Invia o aggiorna il pannello ticket")
    @app_commands.describe(
        canale="Canale in cui inviare il pannello (default: canale attuale)",
        titolo="Titolo del pannello",
        descrizione="Descrizione del pannello",
        colore="Colore hex del pannello, es: #5865F2",
    )
    async def panel(
        self,
        interaction: discord.Interaction,
        canale: discord.TextChannel | None = None,
        titolo: str | None = None,
        descrizione: str | None = None,
        colore: str | None = None,
    ):
        if not await require_guild_admin(interaction):
            return

        # Inviare/modificare un messaggio e leggere lo storico del canale può
        # superare i 3 secondi di risposta di Discord: prendiamo tempo subito.
        await interaction.response.defer(ephemeral=True, thinking=True)

        gconf = cfg.guild(interaction.guild.id)
        target = canale or interaction.channel

        try:
            _, aggiornato = await publish_panel(
                interaction.guild, gconf, target,
                title=titolo, description=descrizione, color=colore,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Non ho i permessi per scrivere in {target.mention}.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Invio del pannello non riuscito: {exc}", ephemeral=True)
            return

        await interaction.followup.send(
            f"{'♻️ Pannello aggiornato' if aggiornato else '✅ Pannello pubblicato'} in {target.mention}.",
            ephemeral=True,
        )

    # ---- /ticket section ... (subgroup) ----
    section = app_commands.Group(name="section", description="Gestisci le sezioni/categorie del pannello ticket")

    @section.command(name="add", description="[Admin server] Aggiungi una sezione al pannello ticket")
    @app_commands.describe(
        nome="Nome identificativo (es: supporto)",
        etichetta="Testo mostrato nel menu (es: Supporto Tecnico)",
        emoji="Emoji della sezione",
        categoria="Categoria Discord in cui creare i ticket di questa sezione",
        ruolo_staff="Ruolo staff dedicato a questa sezione (opzionale)",
        descrizione="Breve descrizione della sezione",
        colore="Colore hex dell'embed del ticket, es: #57F287",
    )
    async def section_add(
        self,
        interaction: discord.Interaction,
        nome: str,
        etichetta: str,
        categoria: discord.CategoryChannel,
        emoji: str | None = None,
        ruolo_staff: discord.Role | None = None,
        descrizione: str | None = None,
        colore: str | None = None,
    ):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        key = slugify(nome)
        if key not in gconf["sections"] and len(gconf["sections"]) >= MAX_SECTIONS:
            await interaction.response.send_message(
                f"⚠️ Questo server ha già {MAX_SECTIONS} sezioni: è il massimo consentito da un menu "
                f"a tendina di Discord. Rimuovine una con `/ticket section remove` prima di aggiungerne un'altra.",
                ephemeral=True,
            )
            return

        # Il refresh del pannello richiede una fetch + edit su Discord: rispondiamo
        # dopo, per non rischiare il timeout dei 3 secondi.
        await interaction.response.defer(ephemeral=True, thinking=True)

        gconf["sections"][key] = {
            "label": etichetta,
            "emoji": emoji or DEFAULT_SECTION_EMOJI,
            "description": descrizione,
            "category_id": categoria.id,
            "staff_role_id": ruolo_staff.id if ruolo_staff else None,
            "color": colore,
        }
        cfg.save()

        aggiornato = await refresh_panel(interaction.guild, gconf)
        await interaction.followup.send(
            f"✅ Sezione **{etichetta}** (`{key}`) creata → categoria {categoria.mention}."
            + ("\n♻️ Il pannello è stato aggiornato automaticamente."
               if aggiornato else
               "\n⚠️ Nessun pannello da aggiornare: usa `/ticket panel` per pubblicarlo."),
            ephemeral=True,
        )

    @section.command(name="remove", description="[Admin server] Rimuovi una sezione dal pannello ticket")
    @app_commands.describe(nome="Nome della sezione da rimuovere")
    async def section_remove(self, interaction: discord.Interaction, nome: str):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        key = slugify(nome)
        if key not in gconf["sections"]:
            await interaction.response.send_message("⚠️ Sezione non trovata.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        del gconf["sections"][key]
        cfg.save()

        aggiornato = await refresh_panel(interaction.guild, gconf)
        await interaction.followup.send(
            f"🗑️ Sezione `{key}` rimossa."
            + ("\n♻️ Il pannello è stato aggiornato automaticamente."
               if aggiornato else
               "\n⚠️ Nessun pannello da aggiornare: usa `/ticket panel` per pubblicarlo."),
            ephemeral=True,
        )

    @section.command(name="list", description="Elenca le sezioni configurate in questo server")
    async def section_list(self, interaction: discord.Interaction):
        gconf = cfg.guild(interaction.guild.id)
        if not gconf["sections"]:
            await interaction.response.send_message("⚠️ Nessuna sezione configurata in questo server.", ephemeral=True)
            return
        embed = discord.Embed(title="📂 Sezioni ticket configurate", color=discord.Color.blurple())
        for key, s in gconf["sections"].items():
            cat = interaction.guild.get_channel(s.get("category_id"))
            role = interaction.guild.get_role(s.get("staff_role_id")) if s.get("staff_role_id") else None
            embed.add_field(
                name=f"{s.get('emoji', DEFAULT_SECTION_EMOJI)} {s.get('label')} (`{key}`)",
                value=(
                    f"Categoria: {cat.mention if cat else '—'}\n"
                    f"Staff dedicato: {role.mention if role else 'ruolo globale del server'}\n"
                    f"Descrizione: {s.get('description') or '—'}"
                ),
                inline=False,
            )
        embed.set_footer(text=branding_text(interaction.guild))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---- /ticket transcriptchannel ----
    @app_commands.command(name="transcriptchannel", description="[Admin server] Imposta il canale dove inviare i transcript")
    @app_commands.describe(canale="Canale di destinazione dei transcript")
    async def transcriptchannel(self, interaction: discord.Interaction, canale: discord.TextChannel):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        gconf["transcript_channel"] = canale.id
        cfg.save()
        await interaction.response.send_message(f"✅ Canale transcript impostato su {canale.mention}.", ephemeral=True)

    # ---- /ticket staffmembers ----
    @app_commands.command(name="staffmembers", description="[Admin server] Imposta il ruolo staff globale per i ticket")
    @app_commands.describe(ruolo="Ruolo i cui membri potranno gestire tutti i ticket di questo server")
    async def staffmembers(self, interaction: discord.Interaction, ruolo: discord.Role):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        gconf["staff_role"] = ruolo.id
        cfg.save()
        await interaction.response.send_message(f"✅ Ruolo staff di questo server impostato su {ruolo.mention}.", ephemeral=True)

    # ---- /ticket blacklist ----
    @app_commands.command(name="blacklist", description="[Admin server] Blocca/sblocca un utente dall'apertura di ticket")
    @app_commands.describe(utente="Utente da bloccare o sbloccare")
    async def blacklist(self, interaction: discord.Interaction, utente: discord.Member):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        if utente.id in gconf["blacklist"]:
            gconf["blacklist"].remove(utente.id)
            cfg.save()
            await interaction.response.send_message(f"✅ {utente.mention} rimosso dalla blacklist di questo server.", ephemeral=True)
        else:
            gconf["blacklist"].append(utente.id)
            cfg.save()
            await interaction.response.send_message(f"🚫 {utente.mention} aggiunto alla blacklist di questo server.", ephemeral=True)

    # ---- /ticket add ----
    @app_commands.command(name="add", description="Aggiungi un utente al ticket corrente")
    @app_commands.describe(utente="Utente da aggiungere")
    async def add(self, interaction: discord.Interaction, utente: discord.Member):
        gconf = cfg.guild(interaction.guild.id)
        ticket = gconf["tickets"].get(str(interaction.channel.id))
        if ticket is None:
            await interaction.response.send_message("⚠️ Questo comando va usato dentro un canale ticket.", ephemeral=True)
            return
        if not await require_staff(interaction, "🚫 Solo lo staff può aggiungere utenti."):
            return
        await interaction.channel.set_permissions(utente, view_channel=True, send_messages=True, read_message_history=True)
        if utente.id not in ticket.setdefault("added_members", []):
            ticket["added_members"].append(utente.id)
            cfg.save()
        await interaction.response.send_message(f"✅ {utente.mention} aggiunto al ticket.")

    # ---- /ticket note ----
    @app_commands.command(name="note", description="Aggiungi una nota interna (visibile solo nello storico) al ticket")
    @app_commands.describe(testo="Contenuto della nota interna")
    async def note(self, interaction: discord.Interaction, testo: str):
        gconf = cfg.guild(interaction.guild.id)
        ticket = gconf["tickets"].get(str(interaction.channel.id))
        if ticket is None:
            await interaction.response.send_message("⚠️ Questo comando va usato dentro un canale ticket.", ephemeral=True)
            return
        if not await require_staff(interaction, "🚫 Solo lo staff può aggiungere note interne."):
            return
        ticket.setdefault("notes", []).append({
            "author": interaction.user.id,
            "text": testo,
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
        })
        cfg.save()
        embed = discord.Embed(
            title="🗒️ Nota interna aggiunta",
            description=testo,
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Aggiunta da {interaction.user.display_name} · visibile solo allo staff/storico")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---- /ticket remove ----
    @app_commands.command(name="remove", description="Rimuovi un utente dal ticket corrente")
    @app_commands.describe(utente="Utente da rimuovere")
    async def remove(self, interaction: discord.Interaction, utente: discord.Member):
        gconf = cfg.guild(interaction.guild.id)
        if str(interaction.channel.id) not in gconf["tickets"]:
            await interaction.response.send_message("⚠️ Questo comando va usato dentro un canale ticket.", ephemeral=True)
            return
        if not await require_staff(interaction, "🚫 Solo lo staff può rimuovere utenti."):
            return
        await interaction.channel.set_permissions(utente, overwrite=None)
        await interaction.response.send_message(f"✅ {utente.mention} rimosso dal ticket.")

    # ---- /ticket rename ----
    @app_commands.command(name="rename", description="Rinomina il ticket corrente")
    @app_commands.describe(nome="Nuovo nome del canale")
    async def rename(self, interaction: discord.Interaction, nome: str):
        gconf = cfg.guild(interaction.guild.id)
        if str(interaction.channel.id) not in gconf["tickets"]:
            await interaction.response.send_message("⚠️ Questo comando va usato dentro un canale ticket.", ephemeral=True)
            return
        if not await require_staff(interaction, "🚫 Solo lo staff può rinominare il ticket."):
            return
        await interaction.channel.edit(name=slugify(nome))
        await interaction.response.send_message(f"✅ Ticket rinominato in `{slugify(nome)}`.")

    # ---- /ticket claim ----
    @app_commands.command(name="claim", description="Reclama/rilascia il ticket corrente")
    async def claim(self, interaction: discord.Interaction):
        gconf = cfg.guild(interaction.guild.id)
        ticket = gconf["tickets"].get(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message("⚠️ Questo comando va usato dentro un canale ticket.", ephemeral=True)
            return
        if not await require_staff(interaction, "🚫 Solo lo staff può reclamare il ticket."):
            return

        section = gconf["sections"].get(ticket.get("section"), {})
        claimed, _ = await toggle_claim(interaction.guild, interaction.channel, gconf, ticket, section, interaction.user)
        await update_staff_panel_message(interaction.guild, interaction.channel, ticket)

        if claimed:
            await interaction.response.send_message(
                f"🙋 Ticket preso in carico da {interaction.user.mention}. "
                f"D'ora in poi solo lui/lei e gli amministratori del server possono scrivere qui."
            )
        else:
            await interaction.response.send_message(
                f"↩️ {interaction.user.mention} ha rilasciato il ticket. Tutto lo staff può tornare a scrivere."
            )

    # ---- /ticket move ----
    @app_commands.command(name="move", description="Sposta il ticket corrente in un'altra categoria")
    @app_commands.describe(categoria="Categoria di destinazione")
    async def move(self, interaction: discord.Interaction, categoria: discord.CategoryChannel):
        gconf = cfg.guild(interaction.guild.id)
        if str(interaction.channel.id) not in gconf["tickets"]:
            await interaction.response.send_message("⚠️ Questo comando va usato dentro un canale ticket.", ephemeral=True)
            return
        if not await require_staff(interaction, "🚫 Solo lo staff può spostare il ticket."):
            return
        try:
            await interaction.channel.edit(category=categoria, reason=f"Ticket spostato da {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Non ho i permessi per spostare questo canale.", ephemeral=True)
            return
        await interaction.response.send_message(f"📁 Ticket spostato nella categoria **{categoria.name}**.")

    # ---- /ticket close ----
    @app_commands.command(name="close", description="Chiudi il ticket corrente")
    @app_commands.describe(motivo="Motivo della chiusura (opzionale)")
    async def close(self, interaction: discord.Interaction, motivo: str | None = None):
        gconf = cfg.guild(interaction.guild.id)
        ticket = gconf["tickets"].get(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message("⚠️ Questo comando va usato dentro un canale ticket.", ephemeral=True)
            return
        if not (is_staff(interaction) or interaction.user.id == ticket["opener"]):
            await interaction.response.send_message("🚫 Non puoi chiudere questo ticket.", ephemeral=True)
            return
        text = "🔒 Chiusura in corso, generazione transcript..."
        if motivo:
            text += f"\n📝 Motivo: {motivo}"
        await interaction.response.send_message(text)
        await close_ticket(interaction.guild, interaction.channel, interaction.user)


ticket_group = TicketGroup()
