"""
manager_backend/auth/session.py
Gestione delle sessioni utente server-side con token opachi ad alta entropia.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from manager_backend.config import backend_cfg
from manager_backend.security.crypto import generate_session_token


@dataclass
class SessionData:
    session_token: str
    user_id: int
    username: str
    global_name: Optional[str] = None
    avatar: Optional[str] = None
    discord_access_token: Optional[str] = None
    discord_guilds: Optional[list[dict]] = None
    created_at: int = 0
    expires_at: int = 0
    update_notification_key: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


class SessionStore:
    """Archivio in-memoria thread-safe delle sessioni attive."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionData] = {}

    def create_session(
        self,
        *,
        user_id: int,
        username: str,
        global_name: Optional[str] = None,
        avatar: Optional[str] = None,
        discord_access_token: Optional[str] = None,
        discord_guilds: Optional[list[dict]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> SessionData:
        now = int(time.time())
        ttl = ttl_seconds or backend_cfg.session_ttl_seconds
        token = generate_session_token()

        session = SessionData(
            session_token=token,
            user_id=int(user_id),
            username=username,
            global_name=global_name,
            avatar=avatar,
            discord_access_token=discord_access_token,
            discord_guilds=discord_guilds or [],
            created_at=now,
            expires_at=now + ttl,
        )

        with self._lock:
            self._sessions[token] = session
        return session

    def get_session(self, token: str) -> Optional[SessionData]:
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return None
            if session.is_expired:
                self._sessions.pop(token, None)
                return None
            return session

    def revoke_session(self, token: str) -> bool:
        with self._lock:
            return self._sessions.pop(token, None) is not None

    def revoke_user_sessions(self, user_id: int) -> int:
        """Revoke every active session belonging to one Discord user."""
        with self._lock:
            tokens = [token for token, session in self._sessions.items()
                      if session.user_id == int(user_id)]
            for token in tokens:
                self._sessions.pop(token, None)
            return len(tokens)

    def purge_expired(self) -> int:
        now = time.time()
        with self._lock:
            expired_keys = [k for k, v in self._sessions.items() if v.expires_at <= now]
            for k in expired_keys:
                self._sessions.pop(k, None)
            return len(expired_keys)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def mark_update_notification(self, session_token: str, key: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_token)
            if not session or session.is_expired or session.update_notification_key == key:
                return False
            session.update_notification_key = key
            return True


session_store = SessionStore()
