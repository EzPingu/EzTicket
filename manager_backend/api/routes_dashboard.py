"""
manager_backend/api/routes_dashboard.py
Endpoint per il sommario della Dashboard di EzTicket Manager.
Restituisce metriche per-guild in tempo reale su ticket attivi, chiusi, candidature, SLA e tempi medi.
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, Query, Request

from manager_backend.audit import audit_logger
from manager_backend.auth.session import SessionData
from manager_backend.models.schemas import DashboardSummaryResponse
from manager_backend.security.dependencies import (
    get_client_ip,
    get_current_session,
    require_guild_staff,
)
from manager_backend.services.guild_service import GuildAccessInfo
from manager_backend.services.stats_service import stats_service

router = APIRouter(prefix="/guilds/{guild_id}/dashboard", tags=["Dashboard"])


@router.get("", response_model=DashboardSummaryResponse)
async def get_guild_dashboard(
    guild_id: str,
    request: Request,
    force_refresh: bool = Query(False, description="Forza il ricalcolo immediato ignorando la cache in-memory"),
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> DashboardSummaryResponse:
    """Restituisce il riepilogo metriche della Dashboard per la guild richiesta.

    Accesso consentito esclusivamente ai membri dello staff e agli amministratori autorizzati.
    Filtro rigorosamente per-guild e protezione IDOR.
    """
    summary = stats_service.get_dashboard_summary(guild_id, force_refresh=force_refresh)

    audit_logger.record(
        "DASHBOARD_VIEW",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=get_client_ip(request),
        details={
            "active_tickets": summary.active_tickets,
            "closed_tickets": summary.closed_tickets_total,
            "pending_applications": summary.pending_applications,
        },
        success=True,
    )
    return summary
