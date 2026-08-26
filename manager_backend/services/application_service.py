from __future__ import annotations

import logging
from typing import Optional
import discord

from config import cfg, fetch_member
from tickets import _get_guild

from manager_backend.models.schemas import (
    ApplicationSummaryResponse,
    ApplicationDetailResponse,
    ApplicationAcceptResponse,
    ApplicationRejectResponse,
)

from candidature import core_accept_application, core_reject_application, get_application_lock

log = logging.getLogger("ezticket.manager.application_service")


class ApplicationService:
    def list_applications(self, guild_id: str) -> list[ApplicationSummaryResponse]:
        gconf = cfg.peek(guild_id)
        if not gconf:
            return []
            
        refs = gconf.get("candidature_summary_messages") or {}
        
        results = []
        for uid, ref in refs.items():
            results.append(
                ApplicationSummaryResponse(
                    guild_id=guild_id,
                    user_id=str(uid),
                    channel_id=str(ref.get("channel_id", "")),
                    message_id=str(ref.get("message_id", "")),
                    notice_message_id=str(ref.get("notice_message_id")) if ref.get("notice_message_id") else None,
                    created_at=int(ref.get("created_at", 0)),
                    status="pending_review"
                )
            )
        
        return sorted(results, key=lambda x: x.created_at)

    async def get_application_detail(self, guild_id: str, user_id: str) -> Optional[ApplicationDetailResponse]:
        gconf = cfg.peek(guild_id)
        if not gconf:
            return None
            
        refs = gconf.get("candidature_summary_messages") or {}
        ref = refs.get(str(user_id))
        
        if not ref:
            return None
            
        summary = ApplicationSummaryResponse(
            guild_id=guild_id,
            user_id=str(user_id),
            channel_id=str(ref.get("channel_id", "")),
            message_id=str(ref.get("message_id", "")),
            notice_message_id=str(ref.get("notice_message_id")) if ref.get("notice_message_id") else None,
            created_at=int(ref.get("created_at", 0)),
            status="pending_review"
        )
        
        detail = ApplicationDetailResponse(**summary.model_dump())
        detail.questions = gconf.get("staff_questions") or []
        
        # Try to fetch Q&A from Discord
        live_guild = _get_guild(int(guild_id)) if guild_id.isdigit() else None
        if live_guild and ref.get("channel_id") and ref.get("message_id"):
            channel = live_guild.get_channel(ref["channel_id"])
            if channel and isinstance(channel, discord.TextChannel):
                try:
                    msg = await channel.fetch_message(ref["message_id"])
                    if msg.embeds:
                        embed = msg.embeds[0]
                        for field in embed.fields:
                            detail.answers.append(field.value)
                        detail.qa_available = True
                except Exception as e:
                    log.warning(f"Could not fetch application Q&A for user {user_id} in guild {guild_id}: {e}")
                    
        return detail

    async def accept_application(self, guild_id: str, user_id: str, actor_id: int, full_onboard: bool = True) -> ApplicationAcceptResponse:
        live_guild = _get_guild(int(guild_id)) if guild_id.isdigit() else None
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

            result = ApplicationAcceptResponse(
                success=True,
                guild_id=guild_id,
                user_id=user_id,
                roles_added=res.get("roles_added", []),
                nickname_changed=res.get("nickname_changed", False),
                dm_sent=res.get("dm_sent", False),
                summary_updated=res.get("summary_updated", False)
            )

        try:
            from manager_backend.services.stats_service import stats_service
            stats_service.invalidate_cache(guild_id)
        except Exception as _cache_err:
            log.warning("Cache invalidation dopo accept fallita (non bloccante): %s", _cache_err)

        return result

    async def reject_application(self, guild_id: str, user_id: str, actor_id: int, reason: Optional[str] = None) -> ApplicationRejectResponse:
        live_guild = _get_guild(int(guild_id)) if guild_id.isdigit() else None
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

            result = ApplicationRejectResponse(
                success=True,
                guild_id=guild_id,
                user_id=user_id,
                reason=reason,
                dm_sent=res.get("dm_sent", False),
                summary_updated=res.get("summary_updated", False)
            )

        try:
            from manager_backend.services.stats_service import stats_service
            stats_service.invalidate_cache(guild_id)
        except Exception as _cache_err:
            log.warning("Cache invalidation dopo reject fallita (non bloccante): %s", _cache_err)

        return result

application_service = ApplicationService()
