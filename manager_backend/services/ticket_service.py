"""
manager_backend/services/ticket_service.py
Service layer per la gestione dei ticket attivi in EzTicket Manager.
Interagisce con EzTicket Core (ConfigManager, tickets.py) garantendo isolamento guild,
prevenzione IDOR e sincronizzazione unificata con i lock e le primitive del bot.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import discord
from fastapi import HTTPException, status

from config import cfg, fetch_member, is_staff_member
from manager_backend.models.schemas import (
    TicketClaimResponse,
    TicketCloseResponse,
    TicketDetailResponse,
    TicketMessageResponse,
    TicketSummaryResponse,
)
from tickets import (
    _get_guild,
    get_channel_messages,
    close_ticket_from_manager,
    get_ticket_lock,
    toggle_claim as core_toggle_claim,
    update_staff_panel_message,
)

log = logging.getLogger("ezticket.manager.ticket_service")


class TicketService:
    """Service adapter per operazioni sui ticket attivi."""

    @staticmethod
    def compute_sla_status(ticket: dict[str, Any]) -> str:
        """Calcola lo stato SLA dinamico: responded | breached | warning | pending."""
        if ticket.get("first_response_at"):
            return "responded"
        sla_deadline = ticket.get("sla_deadline")
        if not sla_deadline or not isinstance(sla_deadline, (int, float)):
            return "pending"
        now = int(time.time())
        if now > sla_deadline:
            return "breached"
        if (sla_deadline - now) <= 60:
            return "warning"
        return "pending"

    def get_active_tickets(self, guild_id: str | int) -> list[TicketSummaryResponse]:
        """Restituisce l'elenco dei ticket attivi per una specifica guild."""
        gid_str = str(guild_id)
        gconf = cfg.peek(gid_str)
        if gconf is None or gconf.get("left_at") is not None:
            return []

        raw_tickets = gconf.get("tickets") or {}
        sections = gconf.get("sections") or {}
        live_guild = _get_guild(int(gid_str)) if gid_str.isdigit() else None

        results: list[TicketSummaryResponse] = []
        for ch_id, ticket in raw_tickets.items():
            if not isinstance(ticket, dict):
                continue

            section_key = str(ticket.get("section") or "")
            section_meta = sections.get(section_key, {})

            opener_id = ticket.get("opener")
            opener_name: Optional[str] = None
            opener_avatar: Optional[str] = None
            if live_guild and opener_id:
                member = live_guild.get_member(int(opener_id))
                if member:
                    opener_name = member.display_name
                    opener_avatar = str(member.display_avatar.url)

            sla_status = self.compute_sla_status(ticket)

            results.append(
                TicketSummaryResponse(
                    guild_id=gid_str,
                    channel_id=str(ch_id),
                    number=int(ticket.get("number") or 0),
                    opener_id=str(opener_id) if opener_id else "0",
                    opener_name=opener_name,
                    opener_avatar=opener_avatar,
                    section=section_key,
                    section_label=section_meta.get("label"),
                    section_emoji=section_meta.get("emoji"),
                    claimed_by=str(ticket.get("claimed_by")) if ticket.get("claimed_by") else None,
                    status=str(ticket.get("status") or "open"),
                    created_at=int(ticket.get("opened_at") or 0),
                    motivo=ticket.get("motivo"),
                    sla_deadline=ticket.get("sla_deadline"),
                    sla_status=sla_status,
                    sla_notified=bool(ticket.get("sla_notified", False)),
                    first_response_at=ticket.get("first_response_at"),
                    claim_deadline=ticket.get("claim_deadline"),
                    claimer_responded=bool(ticket.get("claimer_responded", False)),
                )
            )

        # Ordina per numero ticket
        results.sort(key=lambda t: t.number)
        return results

    async def get_ticket_detail(self, guild_id: str | int, channel_id: str | int) -> Optional[TicketDetailResponse]:
        """Restituisce il dettaglio completo e sicuro di un ticket attivo."""
        gid_str = str(guild_id)
        cid_str = str(channel_id)

        gconf = cfg.peek(gid_str)
        if gconf is None or gconf.get("left_at") is not None:
            return None

        raw_tickets = gconf.get("tickets") or {}
        ticket = raw_tickets.get(cid_str)
        if not ticket or not isinstance(ticket, dict):
            return None

        sections = gconf.get("sections") or {}
        section_key = str(ticket.get("section") or "")
        section_meta = sections.get(section_key, {})

        live_guild = _get_guild(int(gid_str)) if gid_str.isdigit() else None
        opener_id = ticket.get("opener")
        opener_name: Optional[str] = None
        opener_avatar: Optional[str] = None
        if live_guild and opener_id:
            member = live_guild.get_member(int(opener_id))
            if member:
                opener_name = member.display_name
                opener_avatar = str(member.display_avatar.url)

        notes = [n for n in (ticket.get("notes") or []) if isinstance(n, dict)]
        added_members = [str(m) for m in (ticket.get("added_members") or [])]
        messages: list[TicketMessageResponse] = []
        try:
            channel_messages = await get_channel_messages(gid_str, cid_str)
        except (RuntimeError, ValueError):
            channel_messages = []
        messages = [TicketMessageResponse(**message) for message in channel_messages]

        return TicketDetailResponse(
            guild_id=gid_str,
            channel_id=cid_str,
            number=int(ticket.get("number") or 0),
            opener_id=str(opener_id) if opener_id else "0",
            opener_name=opener_name,
            opener_avatar=opener_avatar,
            section=section_key,
            section_label=section_meta.get("label"),
            section_emoji=section_meta.get("emoji"),
            claimed_by=str(ticket.get("claimed_by")) if ticket.get("claimed_by") else None,
            status=str(ticket.get("status") or "open"),
            created_at=int(ticket.get("opened_at") or 0),
            motivo=ticket.get("motivo"),
            sla_deadline=ticket.get("sla_deadline"),
            sla_status=self.compute_sla_status(ticket),
            sla_notified=bool(ticket.get("sla_notified", False)),
            first_response_at=ticket.get("first_response_at"),
            claim_deadline=ticket.get("claim_deadline"),
            claimer_responded=bool(ticket.get("claimer_responded", False)),
            added_members=added_members,
            notes_count=len(notes),
            notes=notes,
            messages=messages,
            transcript_url=ticket.get("transcript_url"),
        )

    async def claim_ticket(
        self,
        guild_id: str | int,
        channel_id: str | int,
        user_id: int,
    ) -> TicketClaimResponse:
        """Esegue il claim o rilascio di un ticket attraverso la funzione unificata core."""
        gid_str = str(guild_id)
        cid_str = str(channel_id)

        gconf = cfg.peek(gid_str)
        if gconf is None or gconf.get("left_at") is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Guild {gid_str} non trovata o bot non presente.",
            )

        raw_tickets = gconf.get("tickets") or {}
        ticket = raw_tickets.get(cid_str)
        if not ticket or not isinstance(ticket, dict):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Ticket {cid_str} non trovato in questa guild.",
            )

        if ticket.get("status") != "open":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossibile effettuare il claim: il ticket non è aperto.",
            )

        section_key = str(ticket.get("section") or "")
        section = (gconf.get("sections") or {}).get(section_key, {})

        # Interazione live con Discord se disponibile
        live_guild = _get_guild(int(gid_str)) if gid_str.isdigit() else None
        live_channel = (
            live_guild.get_channel(int(cid_str))
            if (live_guild and isinstance(live_guild.get_channel(int(cid_str)), discord.TextChannel))
            else None
        )
        live_member = (
            await fetch_member(live_guild, user_id)
            if live_guild
            else None
        )

        claimed, _ = await core_toggle_claim(
            live_guild,
            live_channel,
            gconf,
            ticket,
            section,
            live_member if live_member else user_id,
            guild_id=gid_str,
            channel_id=cid_str,
        )

        if live_guild and live_channel:
            await update_staff_panel_message(live_guild, live_channel, ticket)

        try:
            from manager_backend.services.stats_service import stats_service
            stats_service.invalidate_cache(gid_str)
        except Exception as _cache_err:
            log.warning("Cache invalidation dopo claim fallita (non bloccante): %s", _cache_err)

        message = (
            f"Ticket #{ticket.get('number', 0)} preso in carico con successo."
            if claimed
            else f"Presa in carico del ticket #{ticket.get('number', 0)} rilasciata."
        )

        return TicketClaimResponse(
            success=True,
            guild_id=gid_str,
            channel_id=cid_str,
            claimed_by=str(ticket.get("claimed_by")) if ticket.get("claimed_by") else None,
            claimed=claimed,
            message=message,
        )

    async def reply_to_ticket(
        self,
        guild_id: str | int,
        channel_id: str | int,
        user_id: int,
        message: str,
    ):
        from tickets import reply_to_ticket

        result = await reply_to_ticket(guild_id, channel_id, user_id, message)
        if not result.get("success"):
            code = result.get("status")
            status_code = status.HTTP_403_FORBIDDEN if code == "forbidden" else status.HTTP_400_BAD_REQUEST
            if code == "not_found":
                status_code = status.HTTP_404_NOT_FOUND
            raise HTTPException(status_code=status_code, detail=result.get("message", "Risposta non inviata."))
        from manager_backend.models.schemas import TicketReplyResponse
        return TicketReplyResponse(**result)

    async def close_ticket(
        self,
        guild_id: str | int,
        channel_id: str | int,
        user_id: int,
        reason: Optional[str] = None,
    ) -> TicketCloseResponse:
        """Chiude il ticket invocando la funzione unificata core_close_ticket sotto lock condiviso."""
        gid_str = str(guild_id)
        cid_str = str(channel_id)

        res = await close_ticket_from_manager(gid_str, cid_str, user_id, reason)

        if not res.get("success"):
            err_status = res.get("status")
            if err_status in ("guild_not_found", "not_found"):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=res.get("message", "Ticket non trovato o già chiuso."),
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=res.get("message", "Operazione non valida o ticket già in chiusura."),
                )

        try:
            from manager_backend.services.stats_service import stats_service
            stats_service.invalidate_cache(gid_str)
        except Exception as _cache_err:
            log.warning("Cache invalidation dopo close fallita (non bloccante): %s", _cache_err)

        return TicketCloseResponse(
            success=True,
            guild_id=str(res["guild_id"]),
            channel_id=str(res["channel_id"]),
            closed_by=str(res["closed_by"]),
            closed_at=int(res["closed_at"]),
            close_reason=res.get("close_reason"),
            transcript_url=res.get("transcript_url"),
            message=res.get("message", "Ticket chiuso con successo."),
        )


ticket_service = TicketService()
