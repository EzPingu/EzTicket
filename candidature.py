"""
candidature.py
Gruppo di comandi /candidatura con i sottocomandi attesa, accettata, rifiutata,
il comando standalone /staffaccettato per l'onboarding completo nello staff, e
il sistema di candidatura staff a domande via DM (/domandestaff, /candidaturastaff,
/candidaturestaffcanale, /addettocandidature).

Tutto è per-server: i ruoli assegnati da /staffaccettato, il formato del nickname,
i team proposti e le domande del questionario appartengono a ciascuna guild
(configurabili con /config). Le sessioni di candidatura in corso sono identificate
dalla coppia (server, utente), così lo stesso utente può candidarsi in più server
contemporaneamente senza conflitti.
"""
from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands

from config import (
    cfg,
    branding_text,
    fetch_member,
    guild_setting,
    guild_teams,
    require_guild_admin,
    require_staff,
)


# ---------------------------------------------------------------------------
# Team disponibili: autocomplete per-server (una lista di scelte statica sarebbe
# identica in tutti i server, dato che i comandi sono sincronizzati globalmente)
# ---------------------------------------------------------------------------

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


def resolve_nickname(guild: discord.Guild, utente: discord.Member) -> str | None:
    """Nickname da assegnare secondo il formato configurato dal server
    (/config nickname). None = il server ha scelto di non toccare i nickname."""
    fmt = guild_setting(guild.id, "nick_format")
    if not fmt:
        return None
    try:
        nick = str(fmt).format(nome=utente.name, nick=utente.display_name, server=guild.name)
    except (KeyError, IndexError, ValueError):
        nick = str(fmt)
    return nick[:32] or None


async def _send_dm(utente: discord.abc.User, embed: discord.Embed) -> bool:
    """Prova a inviare l'embed in DM all'utente. Ritorna True se riuscito."""
    try:
        await utente.send(embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


def build_attesa_embed(utente: discord.abc.User, team: str, footer_text: str, footer_icon: str | None = None) -> discord.Embed:
    """Embed 'candidatura in revisione', condiviso tra /candidatura attesa (manuale) e
    la messa in attesa automatica al termine del questionario /candidaturastaff."""
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
    """Se esistono gli embed generati al termine del questionario /candidaturastaff per questo
    utente, li aggiorna entrambi: il riepilogo 'Nuova Candidatura Staff' cambia solo colore
    (verde/rosso), mentre l'embed sotto (che diceva 'messa in Attesa') cambia colore e mostra
    l'ESITO in grassetto al posto del testo di attesa. Chiamata da /staffaccettato,
    /candidatura accettata e /candidatura rifiutata."""
    gconf = cfg.guild(guild.id)
    refs = gconf.setdefault("candidature_summary_messages", {})
    ref = refs.pop(str(utente.id), None)
    if not ref:
        return
    cfg.save()

    channel = guild.get_channel(ref.get("channel_id"))
    if not isinstance(channel, discord.TextChannel):
        return
    color = discord.Color.green() if accepted else discord.Color.red()

    # embed in alto: "📋 Nuova Candidatura Staff" -> cambia solo colore, nessun testo aggiunto
    try:
        summary_msg = await channel.fetch_message(ref["message_id"])
        if summary_msg.embeds:
            summary_embed = summary_msg.embeds[0]
            summary_embed.color = color
            await summary_msg.edit(embed=summary_embed)
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
                await notice_msg.edit(embed=notice_embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


class CandidaturaGroup(app_commands.Group):
    def __init__(self):
        super().__init__(
            name="candidatura",
            description="Gestione delle candidature al team",
            guild_only=True,
        )

    @app_commands.command(name="attesa", description="Segnala che una candidatura è in fase di valutazione")
    @app_commands.describe(utente="Utente che ha inviato la candidatura", ruolo="Team per cui si è candidato")
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
        await interaction.response.send_message(content=f"{utente.mention} • {interaction.user.mention}", embed=embed)
        await _send_dm(utente, embed)

    @app_commands.command(name="accettata", description="Segnala che una candidatura è stata accettata")
    @app_commands.describe(utente="Utente che ha inviato la candidatura", ruolo="Team per cui si è candidato")
    @app_commands.autocomplete(ruolo=team_autocomplete)
    async def accettata(self, interaction: discord.Interaction, utente: discord.Member, ruolo: str | None = None):
        if not await require_staff(interaction):
            return
        team = ruolo or default_team_label(interaction.guild)
        embed = discord.Embed(
            title="✅ Candidatura Accettata!",
            description=(
                f"Congratulazioni {utente.mention}! 🎉\n\n"
                f"La tua candidatura per il ruolo di **{team}** è stata **accettata**.\n\n"
                f"`[■■■■■■■■■■]` Stato: **Accettata**\n\n"
                f"Il nostro team ti contatterà a breve per illustrarti i prossimi passi. "
                f"Benvenuto/a a bordo! 🚀✨"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=utente.display_avatar.url)
        embed.set_footer(text=f"Valutata da {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(content=f"{utente.mention} • {interaction.user.mention}", embed=embed)
        await _send_dm(utente, embed)
        await _resolve_candidature_summary(interaction.guild, utente, accepted=True)

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
        team = ruolo or default_team_label(interaction.guild)
        description = (
            f"Ciao {utente.mention},\n\n"
            f"Ti informiamo che la tua candidatura per il ruolo di **{team}** "
            f"non è stata **accettata** in questa fase.\n\n"
            f"`[■■■■■■■■■■]` Stato: **Rifiutata**\n\n"
            f"Non scoraggiarti: potrai ricandidarti in futuro. Grazie per il tempo dedicato! 🙏"
        )
        if motivo:
            description += f"\n\n📝 **Motivo:** {motivo}"
        embed = discord.Embed(
            title="❌ Candidatura Rifiutata",
            description=description,
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=utente.display_avatar.url)
        embed.set_footer(text=f"Valutata da {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(content=f"{utente.mention} • {interaction.user.mention}", embed=embed)
        await _send_dm(utente, embed)
        await _resolve_candidature_summary(interaction.guild, utente, accepted=False)


candidatura_group = CandidaturaGroup()


# ---------------------------------------------------------------------------
# /staffaccettato — onboarding completo: ruoli + nickname + DM di benvenuto
# ---------------------------------------------------------------------------

def build_staff_welcome_dm_embed(guild: discord.Guild, utente: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="🎉 Candidatura Accettata!",
        description=(
            f"Ciao, la tua candidatura per entrare nello staff di **{guild.name}** "
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


async def handle_staffaccettato(interaction: discord.Interaction, utente: discord.Member):
    """Gestisce /staffaccettato: assegna i ruoli configurati da QUESTO server
    (/config ruoliaccettato), aggiorna il nickname secondo il formato del server
    (/config nickname) e invia il DM di benvenuto nello staff."""
    if not await require_staff(interaction):
        return

    await interaction.response.defer(thinking=True)

    guild = interaction.guild
    gconf = cfg.guild(guild.id)

    added_roles = []
    for role_id in list(gconf.get("staff_accepted_role_ids") or []):
        role = guild.get_role(role_id)
        if role:
            try:
                await utente.add_roles(role, reason=f"Candidatura staff accettata da {interaction.user}")
                added_roles.append(role)
            except discord.Forbidden:
                pass

    old_nick = utente.display_name
    new_nick = resolve_nickname(guild, utente)
    if new_nick is None:
        nick_status = "➖ Nessun formato configurato (`/config nickname`): nickname lasciato invariato."
    else:
        try:
            await utente.edit(nick=new_nick, reason="Candidatura staff accettata")
            nick_status = f"`{old_nick}` → `{new_nick}`"
        except discord.Forbidden:
            nick_status = (
                "⚠️ Non modificato: il ruolo del bot deve stare **più in alto** del ruolo dell'utente "
                "(Impostazioni Server > Ruoli), e non deve trattarsi del proprietario del server."
            )

    dm_embed = build_staff_welcome_dm_embed(guild, utente)
    dm_sent = await _send_dm(utente, dm_embed)

    result_embed = discord.Embed(
        title="✅ Candidatura Staff Accettata",
        description=f"{utente.mention} è stato accettato nello staff! 🎉",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    result_embed.set_thumbnail(url=utente.display_avatar.url)
    result_embed.add_field(
        name="🎭 Ruoli assegnati",
        value=(
            ", ".join(r.mention for r in added_roles)
            if added_roles
            else "⚠️ Nessuno: configura i ruoli di questo server con `/config ruoliaccettato`."
        ),
        inline=False,
    )
    result_embed.add_field(name="🏷️ Nickname", value=nick_status, inline=False)
    result_embed.add_field(
        name="📩 DM di benvenuto",
        value="✅ Inviato" if dm_sent else "⚠️ Non inviato (DM chiusi dall'utente)",
        inline=False,
    )
    result_embed.set_footer(text=f"Eseguito da {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
    await interaction.followup.send(content=utente.mention, embed=result_embed)
    await _resolve_candidature_summary(guild, utente, accepted=True)


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
    )
    async def modifica(self, interaction: discord.Interaction, numero: app_commands.Range[int, 1, 50], testo: str):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        questions = gconf.setdefault("staff_questions", [])
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
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="rimuovi", description="[Admin server] Rimuovi una domanda della candidatura staff")
    @app_commands.describe(numero="Numero della domanda da rimuovere")
    async def rimuovi(self, interaction: discord.Interaction, numero: app_commands.Range[int, 1, 50]):
        if not await require_guild_admin(interaction):
            return
        gconf = cfg.guild(interaction.guild.id)
        questions = gconf.setdefault("staff_questions", [])
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
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="lista", description="Elenca le domande configurate in questo server")
    async def lista(self, interaction: discord.Interaction):
        gconf = cfg.guild(interaction.guild.id)
        questions = gconf.get("staff_questions", [])
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
        await interaction.response.send_message(embed=embed, ephemeral=True)


domandestaff_group = DomandeStaffGroup()


# ---------------------------------------------------------------------------
# /candidaturestaffcanale e /addettocandidature — configurazione destinazione
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


async def handle_addettocandidature(interaction: discord.Interaction, ruolo: discord.Role):
    if not await require_guild_admin(interaction):
        return
    gconf = cfg.guild(interaction.guild.id)
    gconf["candidature_staff_role"] = ruolo.id
    cfg.save()
    await interaction.response.send_message(
        f"✅ Il ruolo {ruolo.mention} verrà taggato per ogni nuova candidatura staff di questo server.",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# /candidaturastaff — avvia il questionario via DM, e gestione delle risposte
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


async def handle_candidaturastaff(interaction: discord.Interaction, utente: discord.Member):
    """Gestisce /candidaturastaff: avvia in DM il questionario configurato con /domandestaff."""
    if not await require_staff(interaction):
        return

    if utente.bot:
        await interaction.response.send_message("⚠️ Non puoi avviare una candidatura per un bot.", ephemeral=True)
        return

    guild = interaction.guild
    gconf = cfg.guild(guild.id)
    if str(interaction.channel.id) not in gconf["tickets"]:
        await interaction.response.send_message(
            "⚠️ `/candidaturastaff` va usato dentro un canale ticket.", ephemeral=True
        )
        return
    questions = [q for q in gconf.get("staff_questions", []) if q]
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
    if cfg.session_expired(esistente):
        # Questionario mai completato e troppo vecchio: lo scartiamo invece di
        # bloccare per sempre nuovi tentativi in questo server.
        cfg.pop_session(guild.id, utente.id)
        esistente = None
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
        await utente.send(embed=intro_embed)
        await utente.send(embed=build_question_embed(guild, 0, len(questions), questions[0]))
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
    })
    cfg.set_active_session_guild(utente.id, None)
    # Non impostiamo una guild attiva automaticamente: con candidature in più
    # server, solo la scelta esplicita dell'utente nel DM può instradare risposte.

    embed = discord.Embed(
        description=f"✅ Domande della candidatura staff inviate in DM a {utente.mention}.",
        color=discord.Color.green(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
                content="⚠️ Quella candidatura non è più in corso.", embed=None, view=None
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
                embed=None, view=None,
            )
            return

        cfg.set_active_session_guild(interaction.user.id, guild_id)
        questions = [q for q in gconf.get("staff_questions", []) if q]
        index = min(session.get("index", 0), max(len(questions) - 1, 0))

        await interaction.response.edit_message(
            content=f"✅ Perfetto: le tue prossime risposte valgono per **{guild.name}**.",
            embed=None,
            view=None,
        )
        if questions:
            try:
                await interaction.user.send(embed=build_question_embed(guild, index, len(questions), questions[index]))
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
        await message.channel.send(embed=embed, view=GuildSessionView(bot, options))
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
    sessions = cfg.sessions_for_user(message.author.id)
    if not sessions:
        return False

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
    questions = [q for q in gconf.get("staff_questions", []) if q]
    if not questions:
        cfg.pop_session(guild_id, message.author.id)
        return False

    session["answers"].append(message.content or "*(nessun testo, es. solo un allegato)*")
    session["index"] += 1
    cfg.mark_dirty("prefs")

    if session["index"] < len(questions):
        try:
            await message.author.send(
                embed=build_question_embed(guild, session["index"], len(questions), questions[session["index"]])
            )
        except (discord.Forbidden, discord.HTTPException):
            pass
        return True

    # Tutte le domande hanno una risposta: chiudiamo la sessione e mandiamo il riepilogo
    cfg.pop_session(guild_id, message.author.id)

    channel_id = gconf.get("candidature_staff_channel")
    role_id = gconf.get("candidature_staff_role")
    channel = guild.get_channel(channel_id) if channel_id else None
    now_ts = int(datetime.now(timezone.utc).timestamp())

    ticket_channel_id = session.get("ticket_channel_id")
    ticket_channel = guild.get_channel(ticket_channel_id) if ticket_channel_id else None

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
    embed.set_footer(text="Candidatura ricevuta tramite il questionario /candidaturastaff")

    sent_msg = None
    if isinstance(channel, discord.TextChannel):
        content = f"<@&{role_id}>" if role_id else None
        try:
            sent_msg = await channel.send(
                content=content, embed=embed,
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
                await starter.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

    if sent_msg is not None:
        # salviamo il riferimento al messaggio riepilogativo, così /staffaccettato e
        # /candidatura accettata|rifiutata potranno aggiornarne colore ed esito in seguito
        summary_ref = {
            "channel_id": channel.id,
            "message_id": sent_msg.id,
            # Data di creazione: serve alla manutenzione periodica per scartare i
            # riferimenti a candidature di cui nessuno ha mai deciso l'esito,
            # altrimenti la config di ogni server crescerebbe senza limite.
            "created_at": now_ts,
        }
        gconf.setdefault("candidature_summary_messages", {})[str(message.author.id)] = summary_ref
        cfg.save()

        notice_embed = discord.Embed(
            title="⏳ Candidatura messa in Attesa",
            description=(
                "Questa candidatura è stata messa automaticamente in **Attesa**.\n\n"
                "Puoi decidere l'esito andando nel ticket e usando:\n"
                "• `/staffaccettato <utente>` per **accettarla**\n"
                "• `/candidatura rifiutata <utente> [motivo]` per **rifiutarla**"
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
            sent_notice = await channel.send(embed=notice_embed, view=notice_view)
            summary_ref["notice_message_id"] = sent_notice.id
            cfg.save()
        except discord.HTTPException:
            pass

    # Messa in attesa "vera": stesso embed che manderebbe uno staffer con /candidatura attesa,
    # inviato sia nel ticket da cui è partita la candidatura sia in DM all'utente.
    auto_attesa_embed = build_attesa_embed(
        message.author, "Staff",
        footer_text=f"{guild.name} · Messa automaticamente in Attesa · Questionario /candidaturastaff",
    )
    if isinstance(ticket_channel, discord.TextChannel):
        try:
            await ticket_channel.send(content=message.author.mention, embed=auto_attesa_embed)
        except discord.HTTPException:
            pass
    await _send_dm(message.author, auto_attesa_embed)

    return True
