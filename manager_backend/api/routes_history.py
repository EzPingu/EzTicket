"""
manager_backend/api/routes_history.py
Endpoint per la consultazione dello storico ticket chiusi: paginazione, filtri avanzati e dettaglio.
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from manager_backend.audit import audit_logger
from manager_backend.auth.session import SessionData
from manager_backend.models.schemas import (
    PaginatedTicketHistoryResponse,
    TicketHistoryDetailResponse,
)
from manager_backend.security.dependencies import (
    get_client_ip,
    get_current_session,
    require_guild_staff,
)
from manager_backend.services.guild_service import GuildAccessInfo
from manager_backend.services.history_service import history_service

router = APIRouter(prefix="/guilds/{guild_id}/tickets/history", tags=["Ticket History"])


@router.get("", response_model=PaginatedTicketHistoryResponse)
async def list_ticket_history(
    guild_id: str,
    request: Request,
    page: int = Query(1, ge=1, description="Numero di pagina (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Elementi per pagina (1-100)"),
    start_date: Optional[int] = Query(None, description="Timestamp unix epoch iniziale per filtro data"),
    end_date: Optional[int] = Query(None, description="Timestamp unix epoch finale per filtro data"),
    opener_id: Optional[str] = Query(None, description="Filtro ID utente che ha aperto il ticket"),
    staff_id: Optional[str] = Query(None, description="Filtro ID staff che ha preso in carico o chiuso il ticket"),
    section: Optional[str] = Query(None, description="Filtro chiave sezione ticket"),
    search: Optional[str] = Query(None, max_length=100, description="Ricerca testuale su numero, canale, motivo o note"),
    sort_by: str = Query("closed_at", regex="^(closed_at|opened_at|number|duration_seconds)$", description="Campo di ordinamento"),
    order: str = Query("desc", regex="^(asc|desc)$", description="Direzione ordinamento"),
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> PaginatedTicketHistoryResponse:
    """Restituisce lo storico paginato dei ticket chiusi per la specifica guild.

    Supporta filtri per data, opener, staff, sezione, ricerca libera e ordinamento.
    Protegge da memory exhaustion evitando il dump massivo senza limiti.
    """
    history_page = history_service.get_history_paginated(
        guild_id=guild_id,
        page=page,
        page_size=page_size,
        start_date=start_date,
        end_date=end_date,
        opener_id=opener_id,
        staff_id=staff_id,
        section=section,
        search=search,
        sort_by=sort_by,
        order=order,
    )

    audit_logger.record(
        "TICKET_HISTORY_VIEW",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=get_client_ip(request),
        details={
            "page": page,
            "page_size": page_size,
            "total_items": history_page.total,
            "filters": {
                "start_date": start_date,
                "end_date": end_date,
                "opener_id": opener_id,
                "staff_id": staff_id,
                "section": section,
                "search": search,
            },
        },
        success=True,
    )
    return history_page


@router.get("/{ticket_id_or_number}", response_model=TicketHistoryDetailResponse)
async def get_ticket_history_detail(
    guild_id: str,
    ticket_id_or_number: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> TicketHistoryDetailResponse:
    """Restituisce il dettaglio completo di un ticket archiviato nello storico della guild.

    Permette la ricerca per numero sequenziale (#123) o per identificativo canale.
    """
    client_ip = get_client_ip(request)
    detail = history_service.get_history_detail(guild_id, ticket_id_or_number)

    if detail is None:
        audit_logger.record(
            "ACCESS_DENIED",
            user_id=session.user_id,
            guild_id=guild_id,
            client_ip=client_ip,
            details={
                "action": "VIEW_HISTORY_DETAIL",
                "target": ticket_id_or_number,
                "reason": "TICKET_NOT_FOUND_IN_HISTORY",
            },
            success=False,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {ticket_id_or_number} non trovato nello storico della guild {guild_id}.",
        )

    audit_logger.record(
        "TICKET_HISTORY_DETAIL_VIEW",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=client_ip,
        details={
            "ticket_number": detail.number,
            "channel_name": detail.channel_name,
        },
        success=True,
    )
    return detail
