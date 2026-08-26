"""
manager_backend/api/routes_tickets.py
Endpoint per la gestione dei ticket attivi: lista, dettaglio, claim e chiusura.
Tutti gli endpoint applicano controlli server-side anti-IDOR/BOLA e audit logging.
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from manager_backend.audit import audit_logger
from manager_backend.auth.session import SessionData
from manager_backend.models.schemas import (
    TicketClaimResponse,
    TicketCloseRequest,
    TicketCloseResponse,
    TicketDetailResponse,
    TicketSummaryResponse,
)
from manager_backend.security.dependencies import (
    get_client_ip,
    get_current_session,
    require_guild_staff,
)
from manager_backend.services.guild_service import GuildAccessInfo
from manager_backend.services.ticket_service import ticket_service

router = APIRouter(prefix="/guilds/{guild_id}/tickets/active", tags=["Active Tickets"])


@router.get("", response_model=list[TicketSummaryResponse])
async def list_active_tickets(
    guild_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> list[TicketSummaryResponse]:
    """Restituisce SOLO i ticket attivi della guild richiesta.

    Accesso consentito esclusivamente allo staff e agli amministratori autorizzati.
    """
    tickets = ticket_service.get_active_tickets(guild_id)

    audit_logger.record(
        "TICKET_VIEW",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=get_client_ip(request),
        details={"action": "LIST_ACTIVE_TICKETS", "count": len(tickets)},
        success=True,
    )
    return tickets


@router.get("/{channel_id}", response_model=TicketDetailResponse)
async def get_active_ticket_detail(
    guild_id: str,
    channel_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> TicketDetailResponse:
    """Restituisce il dettaglio del ticket richiesto solo se appartiene alla guild specificata.

    Protegge contro IDOR/BOLA impedendo l'accesso cross-guild a canali di altri server.
    """
    client_ip = get_client_ip(request)

    if not channel_id.isdigit():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Identificativo channel_id non valido.",
        )

    ticket = ticket_service.get_ticket_detail(guild_id, channel_id)
    if ticket is None:
        audit_logger.record(
            "ACCESS_DENIED",
            user_id=session.user_id,
            guild_id=guild_id,
            client_ip=client_ip,
            details={"action": "VIEW_TICKET_DETAIL", "channel_id": channel_id, "reason": "TICKET_NOT_FOUND_IN_GUILD"},
            success=False,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {channel_id} non trovato nella guild {guild_id}.",
        )

    audit_logger.record(
        "TICKET_VIEW",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=client_ip,
        details={"action": "VIEW_TICKET_DETAIL", "channel_id": channel_id, "ticket_number": ticket.number},
        success=True,
    )
    return ticket


@router.post("/{channel_id}/claim", response_model=TicketClaimResponse)
async def claim_ticket(
    guild_id: str,
    channel_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> TicketClaimResponse:
    """Esegue il claim o rilascio del ticket impiegando la logica e i lock del sistema EzTicket."""
    client_ip = get_client_ip(request)

    if not channel_id.isdigit():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Identificativo channel_id non valido.",
        )

    try:
        res = await ticket_service.claim_ticket(guild_id, channel_id, session.user_id)
    except HTTPException as exc:
        audit_logger.record(
            "ACCESS_DENIED",
            user_id=session.user_id,
            guild_id=guild_id,
            client_ip=client_ip,
            details={"action": "CLAIM_TICKET", "channel_id": channel_id, "error": exc.detail},
            success=False,
        )
        raise

    audit_logger.record(
        "TICKET_CLAIM",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=client_ip,
        details={"action": "CLAIM_TICKET", "channel_id": channel_id, "claimed": res.claimed},
        success=True,
    )
    return res


@router.post("/{channel_id}/close", response_model=TicketCloseResponse)
async def close_ticket(
    guild_id: str,
    channel_id: str,
    request: Request,
    body: Optional[TicketCloseRequest] = Body(default_factory=TicketCloseRequest),
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> TicketCloseResponse:
    """Chiude il ticket e archivia i dati nello storico della guild.

    Garantisce determinismo sotto richieste concorrenti evitando doppi transcript o doppi archivi.
    """
    client_ip = get_client_ip(request)

    if not channel_id.isdigit():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Identificativo channel_id non valido.",
        )

    reason = body.reason if body else None

    try:
        res = await ticket_service.close_ticket(guild_id, channel_id, session.user_id, reason=reason)
    except HTTPException as exc:
        audit_logger.record(
            "ACCESS_DENIED",
            user_id=session.user_id,
            guild_id=guild_id,
            client_ip=client_ip,
            details={"action": "CLOSE_TICKET", "channel_id": channel_id, "error": exc.detail},
            success=False,
        )
        raise

    audit_logger.record(
        "TICKET_CLOSE",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=client_ip,
        details={"action": "CLOSE_TICKET", "channel_id": channel_id, "reason": reason},
        success=True,
    )
    return res
