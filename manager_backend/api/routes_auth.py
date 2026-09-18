"""
manager_backend/api/routes_auth.py
Endpoint di autenticazione OAuth2 Discord e gestione della sessione.
"""
from __future__ import annotations

import logging
import time
import threading
import asyncio
from collections import defaultdict, deque
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from manager_backend.audit import audit_logger
from manager_backend.auth.oauth import DiscordOAuthError, oauth_client
from manager_backend.auth.session import SessionData, session_store
from manager_backend.config import backend_cfg
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
from manager_backend.services.version_service import get_policy, is_supported, is_update_available
from manager_backend.services.manager_security_service import claim_first_login, is_locked
from manager_backend.services.manager_notification_service import (
    send_login_dm,
    send_logout_dm,
    send_first_login_dm,
)

log = logging.getLogger("ezticket.manager.routes_auth")
router = APIRouter(prefix="/auth", tags=["Auth"])
_exchange_attempts: dict[str, deque[float]] = defaultdict(deque)
_exchange_attempts_lock = threading.Lock()
EXCHANGE_RATE_LIMIT = 10
EXCHANGE_RATE_WINDOW_SECONDS = 60


def _schedule_notification(coro, label: str) -> None:
    task = asyncio.create_task(coro)

    def _report(done: asyncio.Task) -> None:
        try:
            done.result()
        except Exception:
            log.exception("Notifica %s fallita in background.", label)

    task.add_done_callback(_report)


def _check_exchange_rate_limit(client_ip: str) -> int | None:
    now = time.monotonic()
    with _exchange_attempts_lock:
        attempts = _exchange_attempts[client_ip]
        while attempts and now - attempts[0] >= EXCHANGE_RATE_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= EXCHANGE_RATE_LIMIT:
            return max(1, int(EXCHANGE_RATE_WINDOW_SECONDS - (now - attempts[0]) + 0.999))
        attempts.append(now)
        if len(_exchange_attempts) > 2048:
            stale = [key for key, values in _exchange_attempts.items() if not values]
            for key in stale:
                _exchange_attempts.pop(key, None)
        return None


@router.post("/exchange", response_model=AuthSessionResponse)
async def exchange_oauth_code(
    payload: OAuthExchangeRequest,
    request: Request,
    x_manager_version: str | None = Header(None, alias="X-Manager-Version"),
) -> AuthSessionResponse:
    """Scambia l'Authorization Code (PKCE) restituito da Discord con una sessione sicura."""
    client_ip = get_client_ip(request)
    retry_after = _check_exchange_rate_limit(client_ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Troppi tentativi di autenticazione. Riprova più tardi.",
            headers={"Retry-After": str(retry_after)},
        )
    if payload.redirect_uri and payload.redirect_uri != backend_cfg.discord_redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Redirect URI OAuth non autorizzato.",
        )
    try:
        # 1. Scambio token con Discord OAuth2
        token_data = await oauth_client.exchange_code(
            code=payload.code,
            code_verifier=payload.code_verifier,
            redirect_uri=backend_cfg.discord_redirect_uri,
        )
        access_token = token_data.get("access_token", "")

        # 2. Recupero profilo utente (@me)
        user_profile = await oauth_client.fetch_user_profile(access_token)
        user_id = int(user_profile["id"])
        username = user_profile.get("username", "Unknown")
        global_name = user_profile.get("global_name")
        avatar = user_profile.get("avatar")

        if is_locked(user_id):
            audit_logger.record(
                "MANAGER_LOCKOUT_BLOCKED",
                user_id=user_id,
                client_ip=client_ip,
                success=False,
                details={"action": "LOGIN"},
            )
            raise HTTPException(status_code=423, detail="Accesso temporaneamente bloccato. Riprova tra pochi minuti.")

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

        first_login = claim_first_login(
            user_id,
            username=username,
            global_name=global_name,
            guild_id=(
                str(user_guilds[0].get("id"))
                if len(user_guilds) == 1 and isinstance(user_guilds[0], dict)
                else None
            ),
        )
        _schedule_notification(
            send_login_dm(
                user_id=user_id,
                username=username,
                global_name=global_name,
                login_at=session.created_at,
            ),
            "login DM",
        )
        if first_login:
            _schedule_notification(
                send_first_login_dm(
                    user_id=user_id, username=username, global_name=global_name
                ),
                "first-login DM",
            )

        policy = get_policy()
        installed_version = x_manager_version or "sconosciuta"
        version_is_unsupported = (
            not x_manager_version
            or not is_supported(x_manager_version)
        )
        update_available = (
            x_manager_version is not None
            and is_update_available(x_manager_version, policy.current_version)
        )
        if version_is_unsupported or update_available:
            notification_key = f"{installed_version}:{policy.current_version}:{policy.minimum_version}"
            if session_store.mark_update_notification(session.session_token, notification_key):
                _schedule_notification(
                    send_required_update_dm(
                        user_id=user_id,
                        installed_version=installed_version,
                        minimum_version=policy.minimum_version,
                        download_url=policy.download_url,
                    ),
                    "update DM",
                )

        audit_logger.record(
            "LOGIN",
            user_id=user_id,
            client_ip=client_ip,
            success=True,
            details={"username": username, "global_name": global_name},
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
    logout_at = int(time.time())

    _schedule_notification(
        send_logout_dm(
            user_id=session.user_id,
            username=session.global_name or session.username,
            login_at=session.created_at,
            logout_at=logout_at,
        ),
        "logout DM",
    )

    audit_logger.record(
        "LOGOUT",
        user_id=session.user_id,
        client_ip=client_ip,
        success=revoked,
        details={
            "username": session.username,
            "global_name": session.global_name,
            "login_at": session.created_at,
            "logout_at": logout_at,
        },
    )
    return LogoutResponse(
        success=revoked,
        message="Sessione terminata correttamente",
    )
