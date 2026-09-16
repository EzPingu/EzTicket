"""
settings.py
Gruppi di comandi /config e /ezticket: espone a OGNI server le impostazioni che nella
versione single-server erano cablate nel codice (tempi SLA e anti-abbandono, ore
di inattività, team delle candidature, branding dei footer) più la lista degli amministratori del bot
per quel server.

Tutti i comandi agiscono SOLO sul server in cui vengono eseguiti e richiedono di
esserne amministratori (proprietario Discord della guild, permesso Administrator,
o aggiunto con /config owner add).

/config diagnostica è l'unico comando riservato a chi hosta il bot
(variabile d'ambiente BOT_OWNER_IDS) e mostra esclusivamente dati aggregati:
non permette di leggere o modificare la configurazione di altri server.
"""
from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone

import discord
from discord import app_commands

from config import (
    BASE_DIR,
    CANDIDATURE_SESSION_TTL_DAYS,
    DEFAULT_GUILD,
    DEFAULT_TEAMS,
    LEFT_GUILD_RETENTION_DAYS,
    MAINTENANCE_INTERVAL_HOURS,
    cfg,
    branding_text,
    claim_timeout_seconds,
    fetch_member,
    get_bot_operator_ids,
    guild_teams,
    inactivity_hours,
    is_bot_operator,
    require_guild_admin,
    require_staff,
    sla_seconds,
)
from tickets import active_timer_count

# Valori che, passati come testo, azzerano un'impostazione riportandola al
# comportamento predefinito.
RESET_WORDS = {"none", "nessuno", "no", "off", "reset", "default", "predefinito", "-", "disattiva"}

MAX_TEAMS = 25          # limite dell'autocomplete di Discord
MAX_GUILD_ADMINS = 20   # oltre non ha senso: sono sovrascritture su ogni canale ticket
MAX_ACCEPTED_ROLES = 5


def _is_reset(value: str | None) -> bool:
    return bool(value) and value.strip().lower() in RESET_WORDS


async def team_name_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete sui team già configurati in QUESTO server (per /config team remove)."""
    guild_id = interaction.guild.id if interaction.guild else 0
    current = (current or "").lower()
    return [
        app_commands.Choice(name=team[:100], value=team[:100])
        for team in guild_teams(guild_id)
        if current in team.lower()
    ][:25]


class ConfigGroup(app_commands.Group):
    def __init__(self):
        super().__init__(
            name="config",
            description="Impostazioni del bot per questo server",
            guild_only=True,
        )

    # ------------------------------------------------------------------ lista
    @app_commands.command(name="lista", description="Mostra tutte le impostazioni di questo server")
    async def lista(self, interaction: discord.Interaction):
        if not await require_staff(interaction, "🚫 Solo lo staff di questo server può vedere la configurazione."):
            return

        guild = interaction.guild
        gconf = cfg.guild(guild.id)

        def ch(key: str) -> str:
            channel = guild.get_channel(gconf.get(key)) if gconf.get(key) else None
            return channel.mention if channel else "— non impostato"

        def role(role_id) -> str:
            r = guild.get_role(role_id) if role_id else None
            return r.mention if r else "— non impostato"

        embed = discord.Embed(
            title="⚙️ Configurazione di questo server",
            description=f"Impostazioni valide **solo per {guild.name}**. Ogni server ha le sue.",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="📢 Canali",
            value=(
                f"Transcript: {ch('transcript_channel')}\n"
                f"Avvisi SLA: {ch('sla_channel')}\n"
                f"Log sistema ticket: {ch('log_ticket_staff_channel')}\n"
                f"Candidature staff: {ch('candidature_staff_channel')}"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎭 Ruoli",
            value=(
                f"Staff globale: {role(gconf.get('staff_role'))}\n"
                f"Tipi candidatura: {len(gconf.get('candidature_types') or {})}"
            ),
            inline=False,
        )
        embed.add_field(
            name="⏱️ Automazioni",
            value=(
                f"Avviso SLA dopo: **{sla_seconds(guild.id) // 60} min** (`/config sla`)\n"
                f"Anti-abbandono claim: **{claim_timeout_seconds(guild.id) // 60} min** (`/config claimtimeout`)\n"
                f"Inattività citata nel promemoria: **{inactivity_hours(guild.id)} ore** (`/config inattivita`)"
            ),
            inline=False,
        )

        embed.add_field(
            name="🏷️ Nickname / Branding / Team",
            value=(
                f"Branding footer: `{branding_text(guild)}` (`/config branding`)\n"
                f"Team candidature: {', '.join(guild_teams(guild.id)) or '—'} (`/config team`)"
            ),
            inline=False,
        )

        owner_ids = gconf.get("owner_ids") or []
        embed.add_field(
            name="👑 Amministratori del bot in questo server",
            value=(
                f"Proprietario del server: <@{guild.owner_id}>\n"
                f"Chiunque abbia il permesso `Amministratore`\n"
                f"Aggiunti con `/config owner add`: "
                + (", ".join(f"<@{uid}>" for uid in owner_ids) or "— nessuno")
            ),
            inline=False,
        )
        embed.add_field(
            name="📊 Stato",
            value=(
                f"Sezioni configurate: **{len(gconf.get('sections') or {})}**\n"
                f"Ticket aperti: **{len(gconf.get('tickets') or {})}** · "
                f"chiusi (storico): **{cfg.history_count(guild.id)}**\n"
                f"Ticket totali creati: **{gconf.get('ticket_counter', 0)}**\n"
                f"Utenti in blacklist: **{len(gconf.get('blacklist') or [])}** · "
                f"notifiche disattivate: **{len(gconf.get('ticket_notify_optout') or [])}**"
            ),
            inline=False,
        )
        embed.set_footer(text=branding_text(guild))
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------------------------- sla
    @app_commands.command(name="sla", description="[Admin server] Dopo quanti minuti avvisare che un ticket è senza risposta")
    @app_commands.describe(minuti="Minuti di attesa prima dell'avviso SLA (default: 5)")
    async def sla(self, interaction: discord.Interaction, minuti: app_commands.Range[int, 1, 1440]):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        gconf["sla_seconds"] = int(minuti) * 60
        cfg.save()
        await interaction.response.send_message(
            f"✅ Avviso SLA impostato a **{minuti} minut{'o' if minuti == 1 else 'i'}**.\n"
            f"ℹ️ Vale per i ticket aperti da adesso: quelli già aperti mantengono il timer originale.",
            ephemeral=True,
        )

    # ----------------------------------------------------------- claimtimeout
    @app_commands.command(name="claimtimeout", description="[Admin server] Entro quanti minuti il claimer deve rispondere")
    @app_commands.describe(minuti="Minuti concessi a chi prende in carico un ticket (default: 2)")
    async def claimtimeout(self, interaction: discord.Interaction, minuti: app_commands.Range[int, 1, 1440]):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        gconf["claim_timeout_seconds"] = int(minuti) * 60
        cfg.save()
        await interaction.response.send_message(
            f"✅ Anti-abbandono impostato a **{minuti} minut{'o' if minuti == 1 else 'i'}**: se chi prende in "
            f"carico un ticket non risponde entro questo tempo, il claim viene rimosso automaticamente.",
            ephemeral=True,
        )

    # ------------------------------------------------------------- inattivita
    @app_commands.command(name="inattivita", description="[Admin server] Ore di inattività citate da /risponditicket")
    @app_commands.describe(ore="Ore indicate nel promemoria prima della chiusura (default: 24)")
    async def inattivita(self, interaction: discord.Interaction, ore: app_commands.Range[int, 1, 720]):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        gconf["inactivity_hours"] = int(ore)
        cfg.save()
        await interaction.response.send_message(
            f"✅ Il promemoria `/risponditicket` citerà **{ore} ore** di attesa prima della chiusura.",
            ephemeral=True,
        )

    # --------------------------------------------------------------- branding
    @app_commands.command(name="branding", description="[Admin server] Testo del footer di transcript ed embed")
    @app_commands.describe(testo="Testo del footer. Scrivi 'reset' per tornare al nome del server.")
    async def branding(self, interaction: discord.Interaction, testo: app_commands.Range[str, 1, 100]):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        if _is_reset(testo):
            gconf["branding"] = None
            cfg.save()
            await interaction.response.send_message(
                f"✅ Branding riportato al valore predefinito: `{branding_text(interaction.guild)}`.", ephemeral=True
            )
            return
        gconf["branding"] = testo
        cfg.save()
        await interaction.response.send_message(f"✅ Branding impostato su `{testo}`.", ephemeral=True)

    # ------------------------------------------------------------------- team
    team = app_commands.Group(name="team", description="Team proposti dai comandi /candidatura")

    @team.command(name="add", description="[Admin server] Aggiungi un team alle candidature")
    @app_commands.describe(nome="Nome del team, es: Moderatore")
    async def team_add(self, interaction: discord.Interaction, nome: app_commands.Range[str, 1, 100]):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        teams = gconf.setdefault("teams", list(DEFAULT_TEAMS))
        nome = nome.strip()
        if any(t.lower() == nome.lower() for t in teams):
            await interaction.response.send_message(f"⚠️ Il team **{nome}** esiste già.", ephemeral=True)
            return
        if len(teams) >= MAX_TEAMS:
            await interaction.response.send_message(
                f"⚠️ Hai raggiunto il massimo di {MAX_TEAMS} team (limite del menu di Discord).", ephemeral=True
            )
            return
        teams.append(nome)
        cfg.save()
        await interaction.response.send_message(
            f"✅ Team **{nome}** aggiunto. Team attuali: {', '.join(teams)}", ephemeral=True
        )

    @team.command(name="remove", description="[Admin server] Rimuovi un team dalle candidature")
    @app_commands.describe(nome="Team da rimuovere")
    @app_commands.autocomplete(nome=team_name_autocomplete)
    async def team_remove(self, interaction: discord.Interaction, nome: str):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        teams = gconf.setdefault("teams", list(DEFAULT_TEAMS))
        match = next((t for t in teams if t.lower() == nome.strip().lower()), None)
        if match is None:
            await interaction.response.send_message(f"⚠️ Nessun team chiamato **{nome}** in questo server.", ephemeral=True)
            return
        teams.remove(match)
        cfg.save()
        await interaction.response.send_message(
            f"🗑️ Team **{match}** rimosso. Team rimasti: {', '.join(teams) or '— nessuno'}", ephemeral=True
        )

    @team.command(name="lista", description="Elenca i team configurati in questo server")
    async def team_lista(self, interaction: discord.Interaction):
        teams = guild_teams(interaction.guild.id)
        embed = discord.Embed(
            title="👥 Team delle candidature",
            description="\n".join(f"• {t}" for t in teams) or "— nessun team configurato",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=branding_text(interaction.guild))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ owner
    owner = app_commands.Group(name="owner", description="Amministratori del bot in questo server")

    @owner.command(name="add", description="[Admin server] Dai a un utente il controllo del bot in questo server")
    @app_commands.describe(utente="Utente che potrà configurare il bot in questo server")
    async def owner_add(self, interaction: discord.Interaction, utente: discord.Member):
        if not await require_guild_admin(interaction):
            return
        if utente.bot:
            await interaction.response.send_message("⚠️ Non puoi nominare un bot amministratore.", ephemeral=True)
            return
        gconf = cfg.guild(interaction.guild.id)
        if len(gconf.get("owner_ids") or []) >= MAX_GUILD_ADMINS:
            await interaction.response.send_message(
                f"⚠️ Massimo {MAX_GUILD_ADMINS} amministratori aggiunti manualmente. "
                f"Per gli altri usa il permesso `Amministratore` di Discord.",
                ephemeral=True,
            )
            return
        if not cfg.add_admin(interaction.guild.id, utente.id):
            await interaction.response.send_message(
                f"⚠️ {utente.mention} è già amministratore del bot in questo server.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"✅ {utente.mention} può ora configurare il bot in **{interaction.guild.name}** "
            f"e ha accesso a tutti i ticket di questo server.\n"
            f"ℹ️ Questo non gli dà alcun potere negli altri server.",
            ephemeral=True,
        )

    @owner.command(name="remove", description="[Admin server] Revoca il controllo del bot a un utente")
    @app_commands.describe(utente="Utente da rimuovere dagli amministratori del bot")
    async def owner_remove(self, interaction: discord.Interaction, utente: discord.User):
        if not await require_guild_admin(interaction):
            return
        if not cfg.remove_admin(interaction.guild.id, utente.id):
            await interaction.response.send_message(
                f"⚠️ {utente.mention} non è tra gli amministratori aggiunti con `/config owner add`.\n"
                f"ℹ️ Il proprietario del server e chi ha il permesso `Amministratore` lo sono automaticamente "
                f"e non possono essere rimossi da qui.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"🗑️ {utente.mention} non è più amministratore del bot in questo server.", ephemeral=True
        )

    @owner.command(name="lista", description="Elenca chi può configurare il bot in questo server")
    async def owner_lista(self, interaction: discord.Interaction):
        if not await require_staff(interaction, "🚫 Solo lo staff di questo server può vedere questa lista."):
            return
        guild = interaction.guild
        owner_ids = cfg.admin_ids(guild.id)
        righe = []
        for uid in owner_ids:
            member = await fetch_member(guild, uid)
            righe.append(f"• <@{uid}>" + ("" if member else " *(non più nel server)*"))
        embed = discord.Embed(
            title="👑 Chi può configurare il bot qui",
            description=(
                f"**Sempre:**\n"
                f"• <@{guild.owner_id}> *(proprietario del server)*\n"
                f"• chiunque abbia il permesso `Amministratore`\n\n"
                f"**Aggiunti con `/config owner add`:**\n" + ("\n".join(righe) or "• — nessuno")
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=branding_text(guild))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ----------------------------------------------------------- diagnostica
    @app_commands.command(name="diagnostica", description="[Host del bot] Stato tecnico aggregato dell'istanza")
    async def diagnostica(self, interaction: discord.Interaction):
        if not is_bot_operator(interaction.user):
            await interaction.response.send_message(
                "🚫 Comando riservato a chi hosta il bot (variabile d'ambiente `BOT_OWNER_IDS`).\n"
                "ℹ️ Per configurare il bot in questo server usa `/config lista`.",
                ephemeral=True,
            )
            return

        client = interaction.client
        shard_count = getattr(client, "shard_count", None) or 1
        latenze = getattr(client, "latencies", None)
        ticket_aperti = sum(len(g.get("tickets") or {}) for g in cfg.data.values())
        storico = sum(len(v) for v in cfg.history.values())
        abbandonati = sum(1 for g in cfg.data.values() if g.get("left_at") is not None)

        embed = discord.Embed(
            title="🩺 Diagnostica istanza",
            description="Solo dati aggregati: nessuna configurazione di singoli server è esposta qui.",
            color=discord.Color.teal(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="🌐 Connessione",
            value=(
                f"Server connessi: **{len(client.guilds)}**\n"
                f"Shard: **{shard_count}**\n"
                f"Latenza media: **{client.latency * 1000:.0f} ms**"
                + (f"\nLatenza per shard: " + ", ".join(f"#{s}: {l * 1000:.0f}ms" for s, l in latenze) if latenze and len(latenze) > 1 else "")
            ),
            inline=False,
        )
        embed.add_field(
            name="💾 Dati",
            value=(
                f"Server in configurazione: **{len(cfg.data)}** (di cui abbandonati: **{abbandonati}**)\n"
                f"Ticket aperti (totale): **{ticket_aperti}**\n"
                f"Ticket nello storico: **{storico}**\n"
                f"Candidature in corso: **{len(cfg.global_conf().get('candidature_sessions') or {})}**\n"
                f"Cartella dati: `{BASE_DIR}`\n"
                f"Ritenzione server abbandonati: **{LEFT_GUILD_RETENTION_DAYS} giorni**\n"
                f"Pulizie automatiche: **ogni {MAINTENANCE_INTERVAL_HOURS}h** "
                f"(candidature mai completate: {CANDIDATURE_SESSION_TTL_DAYS}g)"
            ),
            inline=False,
        )
        embed.add_field(
            name="⏱️ Timer attivi",
            value=f"SLA/anti-abbandono in attesa: **{active_timer_count()}**",
            inline=False,
        )
        embed.add_field(
            name="🐍 Runtime",
            value=(
                f"Python **{platform.python_version()}** · discord.py **{discord.__version__}**\n"
                f"Piattaforma: `{sys.platform}`\n"
                f"Host del bot configurati: **{len(get_bot_operator_ids())}**"
            ),
            inline=False,
        )
        embed.set_footer(text="Default: SLA "
                              f"{DEFAULT_GUILD['sla_seconds'] // 60} min · claim "
                              f"{DEFAULT_GUILD['claim_timeout_seconds'] // 60} min")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class BackupGroup(app_commands.Group):
    """Comandi relativi allo stato dei dati persistiti e ripristinati."""

    def __init__(self):
        super().__init__(
            name="backup",
            description="Stato dei dati salvati del bot",
            guild_only=True,
        )

    @app_commands.command(name="status", description="Mostra lo stato del ripristino dopo un riavvio")
    async def status(self, interaction: discord.Interaction):
        if not await require_staff(interaction):
            return

        embed = discord.Embed(
            title="🔄 Riavvio rilevato",
            description=(
                "Ho rilevato un riavvio o un crash del bot.\n"
                "Tutti i dati e le impostazioni salvate in precedenza sono stati ripristinati correttamente."
            ),
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(embed=embed)


config_group = ConfigGroup()
backup_group = BackupGroup()


class ManagerGroup(app_commands.Group):
    """Comandi di configurazione usati dal Manager per questa guild."""

    def __init__(self):
        super().__init__(
            name="manager",
            description="Impostazioni del Manager per questo server",
            guild_only=True,
        )

    @app_commands.command(name="staffrole", description="[Admin server] Imposta il ruolo che identifica lo staff")
    @app_commands.describe(ruolo="Ruolo Discord assegnato agli staffer di questo server")
    async def staffrole(self, interaction: discord.Interaction, ruolo: discord.Role):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        gconf["staff_role"] = ruolo.id
        cfg.save()
        await interaction.response.send_message(
            f"✅ Ruolo staff impostato: {ruolo.mention}",
            ephemeral=True,
        )


class EzTicketGroup(app_commands.Group):
    """Namespace dei comandi specifici di EzTicket Manager."""

    def __init__(self):
        super().__init__(
            name="ezticket",
            description="Comandi EzTicket",
            guild_only=True,
        )
        self.add_command(ManagerGroup())


ezticket_group = EzTicketGroup()
