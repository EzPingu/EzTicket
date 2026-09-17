"""
manager_backend/api/routes_auth.py
Endpoint di autenticazione OAuth2 Discord e gestione della sessione.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from manager_backend.audit import audit_logger
from manager_backend.auth.oauth import DiscordOAuthError, oauth_client
from manager_backend.auth.session import SessionData, session_store
from manager_backend.models.schemas import (
    AuthSessionResponse,
    LogoutResponse,
    OAuthExchangeRequest,
    UserProfileResponse,
)
from manager_backend.security.dependencies import (
    get_client_ip,
    get_current_session,
    get_current_user,
)
from manager_backend.services.update_notification_service import send_required_update_dm
from manager_backend.services.version_service import get_policy, is_supported

log = logging.getLogger("ezticket.manager.routes_auth")
router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/exchange", response_model=AuthSessionResponse)
async def exchange_oauth_code(
    payload: OAuthExchangeRequest,
    request: Request,
    x_manager_version: str | None = Header(None, alias="X-Manager-Version"),
) -> AuthSessionResponse:
    """Scambia l'Authorization Code (PKCE) restituito da Discord con una sessione sicura."""
    client_ip = get_client_ip(request)
    try:
        # 1. Scambio token con Discord OAuth2
        token_data = await oauth_client.exchange_code(
            code=payload.code,
            code_verifier=payload.code_verifier,
            redirect_uri=payload.redirect_uri,
        )
        access_token = token_data.get("access_token", "")

        # 2. Recupero profilo utente (@me)
        user_profile = await oauth_client.fetch_user_profile(access_token)
        user_id = int(user_profile["id"])
        username = user_profile.get("username", "Unknown")
        global_name = user_profile.get("global_name")
        avatar = user_profile.get("avatar")

        # 3. Recupero lista guild dell'utente per autorizzazione veloce
        user_guilds = await oauth_client.fetch_user_guilds(access_token)

        # 4. Creazione sessione server-side protetta
        session = session_store.create_session(
            user_id=user_id,
            username=username,
            global_name=global_name,
            avatar=avatar,
            discord_access_token=access_token,
            discord_guilds=user_guilds,
        )

        if not x_manager_version or not is_supported(x_manager_version):
            policy = get_policy()
            notification_key = f"{x_manager_version or 'sconosciuta'}:{policy.minimum_version}"
            if session_store.mark_update_notification(session.session_token, notification_key):
                await send_required_update_dm(
                    user_id=user_id,
                    installed_version=x_manager_version or "sconosciuta",
                    minimum_version=policy.minimum_version,
                    download_url=policy.download_url,
                )

        audit_logger.record(
            "LOGIN",
            user_id=user_id,
            client_ip=client_ip,
            success=True,
            details={"username": username},
        )

        return AuthSessionResponse(
            session_token=session.session_token,
            expires_at=session.expires_at,
            user=UserProfileResponse(
                id=str(user_id),
                username=username,
                global_name=global_name,
                avatar=avatar,
            ),
        )

    except DiscordOAuthError as exc:
        audit_logger.record(
            "ACTION_FAILED",
            client_ip=client_ip,
            success=False,
            details={"reason": exc.message, "status": exc.status_code},
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.message,
        )


@router.get("/me", response_model=UserProfileResponse)
async def get_me(
    current_user: UserProfileResponse = Depends(get_current_user),
) -> UserProfileResponse:
    """Restituisce le informazioni dell'utente attualmente autenticato."""
    return current_user


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> LogoutResponse:
    """Invalida immediatamente la sessione attiva."""
    client_ip = get_client_ip(request)
    revoked = session_store.revoke_session(session.session_token)

    audit_logger.record(
        "LOGOUT",
        user_id=session.user_id,
        client_ip=client_ip,
        success=revoked,
    )
    return LogoutResponse(
        success=revoked,
        message="Sessione terminata correttamente",
    )
