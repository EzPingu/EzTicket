"""
manager_backend/api/routes_guilds.py
Endpoint per la consultazione e il contesto delle guild autorizzate.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from manager_backend.auth.session import SessionData
from manager_backend.models.schemas import GuildDetailResponse, GuildSummaryResponse
from manager_backend.security.dependencies import (
    get_current_session,
    require_guild_access,
)
from manager_backend.services.guild_service import GuildAccessInfo, guild_service

router = APIRouter(prefix="/guilds", tags=["Guilds"])


@router.get("", response_model=list[GuildSummaryResponse])
async def list_authorized_guilds(
    session: SessionData = Depends(get_current_session),
) -> list[GuildSummaryResponse]:
    """Restituisce SOLO le guild in cui il bot è presente E l'utente ha permessi di amministrazione o staff."""
    return guild_service.get_accessible_guilds(
        user_id=session.user_id,
        discord_guilds=session.discord_guilds,
    )


@router.get("/{guild_id}", response_model=GuildDetailResponse)
async def get_guild_detail(
    guild_id: str,
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_access),
) -> GuildDetailResponse:
    """Restituisce i dettagli autorizzati di una specifica guild.

    Protetto rigorosamente contro IDOR/BOLA: se l'utente non è autorizzato per questa guild,
    la dependency `require_guild_access` blocca la richiesta con 403 Forbidden.
    """
    detail = guild_service.get_guild_detail(
        user_id=session.user_id,
        guild_id=guild_id,
        discord_guilds=session.discord_guilds,
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dettagli per la guild {guild_id} non disponibili.",
        )
    return detail
