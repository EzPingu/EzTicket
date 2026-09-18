"""Persistent Manager first-login and Discord-user lockout state."""
from __future__ import annotations

import time
import threading
from typing import Any

from config import cfg
from manager_backend.audit import audit_logger
from manager_backend.auth.session import session_store

FIRST_LOGIN_KEY = "manager_first_login_users"
LOCKOUT_KEY = "manager_lockouts"
LOCKOUT_SECONDS = 300
_first_login_lock = threading.Lock()


def _state() -> tuple[dict[str, Any], dict[str, Any]]:
    first = cfg.prefs.setdefault(FIRST_LOGIN_KEY, {})
    lockouts = cfg.prefs.setdefault(LOCKOUT_KEY, {})
    if not isinstance(first, dict):
        first = {}
        cfg.prefs[FIRST_LOGIN_KEY] = first
    if not isinstance(lockouts, dict):
        lockouts = {}
        cfg.prefs[LOCKOUT_KEY] = lockouts
    return first, lockouts


def is_locked(user_id: int) -> bool:
    _, lockouts = _state()
    until = float(lockouts.get(str(user_id), 0) or 0)
    if until <= time.time():
        if str(user_id) in lockouts:
            lockouts.pop(str(user_id), None)
            cfg.mark_dirty("prefs")
        return False
    return True


def lockout_until(user_id: int) -> int | None:
    _, lockouts = _state()
    until = int(float(lockouts.get(str(user_id), 0) or 0))
    return until if until > int(time.time()) else None


def get_active_lockouts() -> dict[int, int]:
    _, lockouts = _state()
    now = int(time.time())
    return {
        int(user_id): int(until)
        for user_id, until in lockouts.items()
        if int(float(until or 0)) > now
    }


def expire_lockout(user_id: int, until: int) -> bool:
    _, lockouts = _state()
    current = int(float(lockouts.get(str(user_id), 0) or 0))
    if current != int(until) or current > int(time.time()):
        return False
    lockouts.pop(str(user_id), None)
    cfg.mark_dirty("prefs")
    audit_logger.record(
        "MANAGER_LOCKOUT_EXPIRED",
        user_id=user_id,
        details={"duration_seconds": LOCKOUT_SECONDS},
    )
    return True


def is_first_login(user_id: int) -> bool:
    first, _ = _state()
    return not bool(first.get(str(user_id)))


def mark_first_login(
    user_id: int,
    *,
    username: str | None = None,
    global_name: str | None = None,
    guild_id: str | None = None,
) -> bool:
    first, _ = _state()
    if first.get(str(user_id)):
        return False
    first[str(user_id)] = int(time.time())
    cfg.mark_dirty("prefs")
    details: dict[str, Any] = {"persistent": True}
    if username:
        details["username"] = username
    if global_name:
        details["global_name"] = global_name
    audit_logger.record("FIRST_TIME_LOGIN", user_id=user_id, guild_id=guild_id, details=details)
    return True


def claim_first_login(
    user_id: int,
    *,
    username: str | None = None,
    global_name: str | None = None,
    guild_id: str | None = None,
) -> bool:
    """Atomically claim the one-time first-login marker."""
    with _first_login_lock:
        if not is_first_login(user_id):
            return False
        return mark_first_login(
            user_id,
            username=username,
            global_name=global_name,
            guild_id=guild_id,
        )


def apply_lockout(user_id: int, *, client_ip: str | None = None) -> int:
    until = int(time.time()) + LOCKOUT_SECONDS
    _, lockouts = _state()
    lockouts[str(user_id)] = until
    revoked = session_store.revoke_user_sessions(user_id)
    cfg.mark_dirty("prefs")
    audit_logger.record(
        "MANAGER_LOCKOUT_APPLIED",
        user_id=user_id,
        client_ip=client_ip,
        details={"duration_seconds": LOCKOUT_SECONDS, "expires_at": until, "sessions_revoked": revoked},
    )
    return until


def cancel_lockout(user_id: int, *, client_ip: str | None = None) -> bool:
    _, lockouts = _state()
    existed = str(user_id) in lockouts
    lockouts.pop(str(user_id), None)
    if existed:
        cfg.mark_dirty("prefs")
    audit_logger.record(
        "MANAGER_LOCKOUT_CANCELLED",
        user_id=user_id,
        client_ip=client_ip,
        details={"was_active": existed},
    )
    return existed


def end_lockout_early(user_id: int) -> bool:
    """Internal/backend hook for trusted maintenance or support tooling."""
    return cancel_lockout(user_id)
