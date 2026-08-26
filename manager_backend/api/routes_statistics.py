"""
manager_backend/api/routes_statistics.py
Endpoint per statistiche dettagliate per-guild: timeline temporali, SLA, sezioni, staff e metriche di risoluzione.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from manager_backend.audit import audit_logger
from manager_backend.auth.session import SessionData
from manager_backend.models.schemas import GuildStatisticsResponse
from manager_backend.security.dependencies import (
    get_client_ip,
    get_current_session,
    require_guild_staff,
)
from manager_backend.services.guild_service import GuildAccessInfo
from manager_backend.services.stats_service import stats_service

router = APIRouter(prefix="/guilds/{guild_id}/statistics", tags=["Statistics"])


@router.get("", response_model=GuildStatisticsResponse)
async def get_guild_statistics(
    guild_id: str,
    request: Request,
    days: int = Query(30, ge=1, le=365, description="Intervallo di analisi in giorni (1-365)"),
    force_refresh: bool = Query(False, description="Forza il ricalcolo immediato ignorando la cache in-memory"),
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> GuildStatisticsResponse:
    """Restituisce statistiche aggregate e serie temporali per la guild specificata.

    Include ripartizioni per giorno, settimana, mese, sezione, staff e metriche SLA.
    I dati non disponibili nel modello di storage attuale sono contrassegnati esplicitamente come None.
    """
    stats = stats_service.get_guild_statistics(guild_id, days=days, force_refresh=force_refresh)

    audit_logger.record(
        "STATISTICS_VIEW",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=get_client_ip(request),
        details={
            "days": days,
            "sample_size": stats.resolution_time.sample_size,
            "total_sections": len(stats.tickets_by_section),
            "total_staff": len(stats.tickets_by_staff),
        },
        success=True,
    )
    return stats
