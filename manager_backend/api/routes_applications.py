from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from manager_backend.audit import audit_logger
from manager_backend.auth.session import SessionData
from manager_backend.models.schemas import (
    ApplicationSummaryResponse,
    ApplicationDetailResponse,
    ApplicationAcceptRequest,
    ApplicationAcceptResponse,
    ApplicationRejectRequest,
    ApplicationRejectResponse,
    ApplicationDmRequest,
    ApplicationDmResponse,
)
from manager_backend.security.dependencies import (
    get_client_ip,
    get_current_session,
    require_guild_staff,
)
from manager_backend.services.guild_service import GuildAccessInfo
from manager_backend.services.application_service import application_service

router = APIRouter(prefix="/guilds/{guild_id}/applications", tags=["Applications"])


@router.get("", response_model=list[ApplicationSummaryResponse])
async def list_applications(
    guild_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> list[ApplicationSummaryResponse]:
    apps = await application_service.list_applications(guild_id)
    audit_logger.record(
        "APPLICATION_VIEW",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=get_client_ip(request),
        details={"action": "LIST_APPLICATIONS", "count": len(apps)},
        success=True,
    )
    return apps


@router.get("/{user_id}", response_model=ApplicationDetailResponse)
async def get_application_detail(
    guild_id: str,
    user_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> ApplicationDetailResponse:
    client_ip = get_client_ip(request)

    detail = await application_service.get_application_detail(guild_id, user_id)
    if detail is None:
        audit_logger.record(
            "ACTION_FAILED",
            user_id=session.user_id,
            guild_id=guild_id,
            client_ip=client_ip,
            details={"action": "VIEW_APPLICATION", "target_user_id": user_id, "reason": "NOT_FOUND"},
            success=False,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidatura non trovata",
        )

    audit_logger.record(
        "APPLICATION_VIEW",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=client_ip,
        details={"action": "VIEW_APPLICATION_DETAIL", "target_user_id": user_id},
        success=True,
    )
    return detail


@router.post("/{user_id}/accept", response_model=ApplicationAcceptResponse)
async def accept_application(
    guild_id: str,
    user_id: str,
    request: Request,
    body: Optional[ApplicationAcceptRequest] = Body(default_factory=ApplicationAcceptRequest),
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> ApplicationAcceptResponse:
    client_ip = get_client_ip(request)
    
    try:
        res = await application_service.accept_application(guild_id, user_id, session.user_id, body.full_onboard if body else True)
    except ValueError as e:
        reason = str(e)
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE if reason == "bot_offline" else (status.HTTP_422_UNPROCESSABLE_ENTITY if reason in ("member_not_found", "actor_not_found") else status.HTTP_404_NOT_FOUND)
        audit_logger.record(
            "ACTION_FAILED",
            user_id=session.user_id,
            guild_id=guild_id,
            client_ip=client_ip,
            details={"action": "ACCEPT_APPLICATION", "target_user_id": user_id, "reason": reason.upper()},
            success=False,
        )
        raise HTTPException(status_code=status_code, detail=f"Errore: {reason}")
        
    audit_logger.record(
        "APPLICATION_ACCEPT",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=client_ip,
        details={
            "target_user_id": user_id,
            "full_onboard": body.full_onboard if body else True,
            "roles_added": res.roles_added,
            "nickname_changed": res.nickname_changed,
            "dm_sent": res.dm_sent,
            "summary_updated": res.summary_updated
        },
        success=True,
    )
    return res


@router.post("/{user_id}/dm", response_model=ApplicationDmResponse)
async def send_application_dm(
    guild_id: str,
    user_id: str,
    body: ApplicationDmRequest,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> ApplicationDmResponse:
    try:
        result = await application_service.send_dm(guild_id, user_id, session.user_id, body.message)
    except ValueError as exc:
        reason = str(exc)
        code = (
            status.HTTP_503_SERVICE_UNAVAILABLE if reason == "bot_offline"
            else status.HTTP_404_NOT_FOUND if reason == "application_not_found"
            else status.HTTP_409_CONFLICT if reason == "application_already_processed"
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        audit_logger.record(
            "ACTION_FAILED",
            user_id=session.user_id,
            guild_id=guild_id,
            client_ip=get_client_ip(request),
            details={"action": "APPLICATION_DM", "target_user_id": user_id, "reason": reason},
            success=False,
        )
        raise HTTPException(status_code=code, detail=f"Errore: {reason}")
    audit_logger.record(
        "APPLICATION_DM",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=get_client_ip(request),
        details={"target_user_id": user_id},
        success=result.dm_sent,
    )
    return result


@router.post("/{user_id}/reject", response_model=ApplicationRejectResponse)
async def reject_application(
    guild_id: str,
    user_id: str,
    request: Request,
    body: Optional[ApplicationRejectRequest] = Body(default_factory=ApplicationRejectRequest),
    session: SessionData = Depends(get_current_session),
    access: GuildAccessInfo = Depends(require_guild_staff),
) -> ApplicationRejectResponse:
    client_ip = get_client_ip(request)
    
    try:
        res = await application_service.reject_application(guild_id, user_id, session.user_id, body.reason if body else None)
    except ValueError as e:
        reason = str(e)
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE if reason == "bot_offline" else (status.HTTP_422_UNPROCESSABLE_ENTITY if reason in ("member_not_found", "actor_not_found") else status.HTTP_404_NOT_FOUND)
        audit_logger.record(
            "ACTION_FAILED",
            user_id=session.user_id,
            guild_id=guild_id,
            client_ip=client_ip,
            details={"action": "REJECT_APPLICATION", "target_user_id": user_id, "reason": reason.upper()},
            success=False,
        )
        raise HTTPException(status_code=status_code, detail=f"Errore: {reason}")

    audit_logger.record(
        "APPLICATION_REJECT",
        user_id=session.user_id,
        guild_id=guild_id,
        client_ip=client_ip,
        details={
            "target_user_id": user_id,
            "reason_provided": bool(body and body.reason),
            "dm_sent": res.dm_sent,
            "summary_updated": res.summary_updated
        },
        success=True,
    )
    return res
