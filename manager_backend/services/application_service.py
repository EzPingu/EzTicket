from __future__ import annotations

import logging
import time
from typing import Optional
import discord
from components_v2 import render_components_v2

from config import cfg, fetch_member
from discord_bridge import is_available, run_on_discord_loop
from tickets import _get_guild

from manager_backend.models.schemas import (
    ApplicationSummaryResponse,
    ApplicationDetailResponse,
    ApplicationAcceptResponse,
    ApplicationRejectResponse,
    ApplicationDmResponse,
)

from candidature import core_accept_application, core_reject_application, get_application_lock

log = logging.getLogger("ezticket.manager.application_service")


class ApplicationService:
    async def _profile(self, guild_id: int, user_id: int | str) -> tuple[str | None, str | None]:
        async def lookup() -> tuple[str | None, str | None]:
            guild = _get_guild(guild_id)
            if guild is None:
                return None, None
            member = guild.get_member(int(user_id))
            if member is None:
                member = await fetch_member(guild, int(user_id))
            if member is None:
                return None, None
            return member.display_name, str(member.display_avatar.url)
        if not is_available():
            return None, None
        return await run_on_discord_loop(lookup())

    async def list_applications(self, guild_id: str) -> list[ApplicationSummaryResponse]:
        gconf = cfg.peek(guild_id)
        if not gconf:
            return []

        refs = gconf.get("candidature_summary_messages") or {}
        sessions = cfg.prefs.get("candidature_sessions") or {}
        user_ids = {str(uid) for uid in refs}
        user_ids.update(
            key.rsplit(":", 1)[1]
            for key, session in sessions.items()
            if key.startswith(f"{guild_id}:") and isinstance(session, dict)
            and session.get("status") in (None, "pending", "accepted", "rejected")
        )

        results = []
        for uid in user_ids:
            ref = refs.get(uid) or {}
            session = cfg.get_session(guild_id, uid) or {}
            candidate_name, candidate_avatar = await self._profile(int(guild_id), uid)
            staffer_name, staffer_avatar = await self._profile(int(guild_id), session.get("started_by")) if session.get("started_by") else (None, None)
            type_name = session.get("candidature_type_name") or session.get("candidature_type")
            results.append(
                ApplicationSummaryResponse(
                    guild_id=guild_id,
                    user_id=str(uid),
                    channel_id=str(ref.get("channel_id", "")),
                    message_id=str(ref.get("message_id", "")),
                    notice_message_id=str(ref.get("notice_message_id")) if ref.get("notice_message_id") else None,
                    created_at=int(session.get("completed_at") or ref.get("created_at", 0)),
                    status="pending_review" if session.get("status", ref.get("status", "pending")) == "pending" else str(session.get("status", ref.get("status", "pending"))),
                    candidate_name=candidate_name,
                    candidate_avatar=candidate_avatar,
                    candidature_type=type_name,
                    staffer_name=staffer_name,
                    staffer_avatar=staffer_avatar,
                )
            )
        return sorted(results, key=lambda x: x.created_at)

    async def get_application_detail(self, guild_id: str, user_id: str) -> Optional[ApplicationDetailResponse]:
        gconf = cfg.peek(guild_id)
        if not gconf:
            return None
            
        refs = gconf.get("candidature_summary_messages") or {}
        ref = refs.get(str(user_id))
        session = cfg.get_session(guild_id, user_id)
        if not ref and not session:
            return None

        ref = ref or {}
        candidate_name, candidate_avatar = await self._profile(int(guild_id), user_id)
        staffer_name, staffer_avatar = await self._profile(int(guild_id), session.get("started_by")) if session and session.get("started_by") else (None, None)
        candidature_type = (session or {}).get("candidature_type")
        candidature_types = gconf.get("candidature_types") or {}
        type_config = candidature_types.get(candidature_type, {}) if candidature_type else {}
        status = str((session or ref).get("status", "pending"))
            
        summary = ApplicationSummaryResponse(
            guild_id=guild_id,
            user_id=str(user_id),
            channel_id=str(ref.get("channel_id", "")),
            message_id=str(ref.get("message_id", "")),
            notice_message_id=str(ref.get("notice_message_id")) if ref.get("notice_message_id") else None,
            created_at=int(ref.get("created_at", 0)),
            status="pending_review" if status == "pending" else status
        )
        
        detail = ApplicationDetailResponse(**summary.model_dump())
        detail.candidate_name = candidate_name
        detail.candidate_avatar = candidate_avatar
        detail.candidature_type = (session or {}).get("candidature_type_name") or candidature_type
        detail.staffer_name = staffer_name
        detail.staffer_avatar = staffer_avatar
        detail.questions = type_config.get("questions") or gconf.get("staff_questions") or []
        detail.answers = list((session or {}).get("answers") or [])
        detail.dm_messages = list((cfg.prefs.get("candidature_dm_threads") or {}).get(f"{guild_id}:{user_id}", {}).get("messages") or [])
        
        # Try to fetch Q&A from Discord
        if ref.get("channel_id") and ref.get("message_id"):
            async def fetch_summary() -> list[str]:
                guild = _get_guild(int(guild_id))
                channel = guild.get_channel(int(ref["channel_id"])) if guild else None
                if not isinstance(channel, discord.TextChannel):
                    return []
                msg = await channel.fetch_message(int(ref["message_id"]))
                return [field.value for field in msg.embeds[0].fields] if msg.embeds else []
            try:
                if not is_available():
                    return detail
                fetched_answers = await run_on_discord_loop(fetch_summary())
                if not detail.answers:
                    detail.answers = fetched_answers
                detail.qa_available = bool(detail.answers)
            except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
                log.warning("Could not fetch application Q&A for user %s in guild %s: %s", user_id, guild_id, exc)
                    
        return detail

    async def send_dm(self, guild_id: str, user_id: str, actor_id: int, message: str) -> ApplicationDmResponse:
        gconf = cfg.peek(guild_id)
        session = cfg.get_session(int(guild_id), int(user_id))
        summary = (gconf or {}).get("candidature_summary_messages", {}).get(str(user_id))
        if not session and not summary:
            raise ValueError("application_not_found")
        if (
            session and session.get("status") in ("accepted", "rejected")
        ) or (
            summary and summary.get("status") in ("accepted", "rejected")
        ):
            raise ValueError("application_already_processed")

        async def send() -> tuple[bool, int, str, str]:
            guild = _get_guild(int(guild_id))
            if guild is None:
                raise ValueError("bot_offline")
            member = await fetch_member(guild, int(user_id))
            actor = await fetch_member(guild, actor_id)
            if member is None:
                raise ValueError("member_not_found")
            if actor is None:
                raise ValueError("actor_not_found")
            embed = discord.Embed(
                title="💬 Messaggio dello staff",
                description=(
                    "Uno staffer ti ha scritto riguardo alla tua candidatura.\n\n"
                    f"**Messaggio:**\n{message}\n\n"
                    "Rispondi a questo messaggio per contattare lo staff."
                ),
                color=discord.Color.blurple(),
            )
            embed.set_author(name=actor.display_name, icon_url=actor.display_avatar.url)
            try:
                await member.send(view=render_components_v2(embed))
            except (discord.Forbidden, discord.HTTPException):
                return False, actor.id, actor.display_name, str(actor.display_avatar.url)
            return True, actor.id, actor.display_name, str(actor.display_avatar.url)

        dm_sent, actor_discord_id, actor_name, actor_avatar = await run_on_discord_loop(send())
        if not dm_sent:
            return ApplicationDmResponse(success=False, guild_id=guild_id, user_id=user_id, dm_sent=False)

        key = f"{guild_id}:{user_id}"
        threads = cfg.prefs.setdefault("candidature_dm_threads", {})
        thread = threads.setdefault(key, {"guild_id": int(guild_id), "user_id": int(user_id), "messages": []})
        thread.setdefault("messages", []).append({
            "direction": "staff",
            "author_id": actor_discord_id,
            "author_name": actor_name,
            "author_avatar": actor_avatar,
            "content": message,
            "created_at": int(time.time()),
        })
        cfg.set_active_session_guild(user_id, int(guild_id))
        cfg.save()
        return ApplicationDmResponse(success=True, guild_id=guild_id, user_id=user_id, dm_sent=True)

    async def accept_application(self, guild_id: str, user_id: str, actor_id: int, full_onboard: bool = True) -> ApplicationAcceptResponse:
        async def accept() -> ApplicationAcceptResponse:
            live_guild = _get_guild(int(guild_id))
            if not live_guild:
                raise ValueError("bot_offline")
            member = await fetch_member(live_guild, int(user_id))
            if not member:
                raise ValueError("member_not_found")
            actor_member = await fetch_member(live_guild, actor_id)
            actor = actor_member or live_guild.get_member(actor_id)
            if not actor:
                raise ValueError("actor_not_found")
            async with get_application_lock(guild_id, user_id):
                res = await core_accept_application(live_guild, member, actor, full_onboard=full_onboard)
                if not res.get("success"):
                    raise ValueError("already_processed")
            return ApplicationAcceptResponse(
                success=True,
                guild_id=guild_id,
                user_id=user_id,
                roles_added=res.get("roles_added", []),
                nickname_changed=res.get("nickname_changed", False),
                dm_sent=res.get("dm_sent", False),
                summary_updated=res.get("summary_updated", False)
            )
        result = await run_on_discord_loop(accept())

        try:
            from manager_backend.services.stats_service import stats_service
            stats_service.invalidate_cache(guild_id)
        except Exception as _cache_err:
            log.warning("Cache invalidation dopo accept fallita (non bloccante): %s", _cache_err)

        return result

    async def reject_application(self, guild_id: str, user_id: str, actor_id: int, reason: Optional[str] = None) -> ApplicationRejectResponse:
        async def reject() -> ApplicationRejectResponse:
            live_guild = _get_guild(int(guild_id))
            if not live_guild:
                raise ValueError("bot_offline")
            member = await fetch_member(live_guild, int(user_id))
            if not member:
                raise ValueError("member_not_found")
            actor_member = await fetch_member(live_guild, actor_id)
            actor = actor_member or live_guild.get_member(actor_id)
            if not actor:
                raise ValueError("actor_not_found")
            async with get_application_lock(guild_id, user_id):
                res = await core_reject_application(live_guild, member, actor, motivo=reason)
                if not res.get("success"):
                    raise ValueError("already_processed")
            return ApplicationRejectResponse(
                success=True,
                guild_id=guild_id,
                user_id=user_id,
                reason=reason,
                dm_sent=res.get("dm_sent", False),
                summary_updated=res.get("summary_updated", False)
            )
        result = await run_on_discord_loop(reject())

        try:
            from manager_backend.services.stats_service import stats_service
            stats_service.invalidate_cache(guild_id)
        except Exception as _cache_err:
            log.warning("Cache invalidation dopo reject fallita (non bloccante): %s", _cache_err)

        return result

application_service = ApplicationService()
