"""
manager_backend/security/dependencies.py
Dipendenze FastAPI riutilizzabili per Autenticazione, Risoluzione Utente e Autorizzazione Per-Guild.
"""
from __future__ import annotations

from typing import Optional
from fastapi import Depends, Header, HTTPException, Request, status

from config import is_bot_operator
from manager_backend.audit import audit_logger
from manager_backend.auth.session import SessionData, session_store
from manager_backend.models.schemas import UserProfileResponse
from manager_backend.services.guild_service import GuildAccessInfo, guild_service
from manager_backend.services.version_service import is_supported
from manager_backend.services.update_notification_service import send_required_update_dm
from manager_backend.services.version_service import get_policy


def get_client_ip(request: Request) -> str:
    """Estrae in modo sicuro l'indirizzo IP del client per il logging."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


async def get_current_session(
    request: Request,
    x_manager_version: Optional[str] = Header(None, alias="X-Manager-Version"),
    authorization: Optional[str] = Header(None, description="Bearer Session Token"),
) -> SessionData:
    """Valida il token di sessione opaco Bearer e restituisce la sessione utente attiva."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticazione richiesta. Fornire Bearer token valido.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ").strip()
    session = session_store.get_session(token)
    if not session:
        audit_logger.record(
            "AUTH_SESSION_INVALID",
            client_ip=get_client_ip(request),
            success=False,
            details={"path": request.url.path},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessione non valida o scaduta.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not x_manager_version or not is_supported(x_manager_version):
        policy = get_policy()
        installed_version = x_manager_version or "sconosciuta"
        notification_key = f"{installed_version}:{policy.minimum_version}"
        if session_store.mark_update_notification(session.session_token, notification_key):
            await send_required_update_dm(
                user_id=session.user_id,
                installed_version=installed_version,
                minimum_version=policy.minimum_version,
                download_url=policy.download_url,
            )
        audit_logger.record(
            "ACTION_FAILED",
            user_id=session.user_id,
            client_ip=get_client_ip(request),
            success=False,
            details={"action": "UNSUPPORTED_MANAGER_VERSION", "path": request.url.path},
        )
        raise HTTPException(
            status_code=status.HTTP_426_UPGRADE_REQUIRED,
            detail="Questa versione di EzTicket Manager non è più supportata.",
            headers={"X-Manager-Update-Required": "true"},
        )
    return session


async def get_current_user(
    session: SessionData = Depends(get_current_session),
) -> UserProfileResponse:
    """Restituisce il profilo pubblico dell'utente autenticato."""
    return UserProfileResponse(
        id=str(session.user_id),
        username=session.username,
        global_name=session.global_name,
        avatar=session.avatar,
        is_bot_operator=is_bot_operator(session.user_id),
    )


async def require_guild_access(
    guild_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> GuildAccessInfo:
    """Dependency fondamentale anti-IDOR/BOLA:

    Verifica che:
    1. Il guild_id sia formalmente valido (formato snowflake numerico);
    2. Il bot sia presente e attivo nella guild;
    3. L'utente autenticato abbia effettivi permessi di amministrazione o staff su QUESTA guild.
    """
    client_ip = get_client_ip(request)

    # 1. Validazione formato ID
    if not guild_id.isdigit():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Identificativo guild_id non valido.",
        )

    # 2. Valutazione server-side dei permessi
    access = guild_service.evaluate_guild_access(
        user_id=session.user_id,
        guild_id=guild_id,
        discord_guilds=session.discord_guilds,
    )

    # 3. Controllo presenza bot
    if not access.is_present:
        audit_logger.record(
            "GUILD_NOT_FOUND",
            user_id=session.user_id,
            guild_id=guild_id,
            client_ip=client_ip,
            success=False,
            details={"path": request.url.path},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Guild {guild_id} non trovata o bot non presente.",
        )

    # 4. Controllo autorizzazione (Anti-IDOR)
    if not access.is_authorized:
        audit_logger.record(
            "IDOR_ATTEMPT",
            user_id=session.user_id,
            guild_id=guild_id,
            client_ip=client_ip,
            success=False,
            details={"path": request.url.path, "action": "UNAUTHORIZED_GUILD_ACCESS"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Accesso negato: non sei autorizzato ad accedere alla guild {guild_id}.",
        )

    return access


async def require_guild_admin(
    access: GuildAccessInfo = Depends(require_guild_access),
) -> GuildAccessInfo:
    """Verifica che l'utente sia Amministratore o Proprietario della guild richiesta."""
    if not access.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operazione riservata esclusivamente agli amministratori del server.",
        )
    return access


async def require_guild_staff(
    access: GuildAccessInfo = Depends(require_guild_access),
) -> GuildAccessInfo:
    """Verifica che l'utente sia almeno Staff o Amministratore della guild richiesta."""
    if not access.is_authorized or access.role not in ("GUILD_ADMIN", "GUILD_STAFF"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operazione riservata ai membri dello staff del server.",
        )
    return access
