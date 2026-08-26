"""
manager_backend/audit.py
Sistema di Audit Logging per eventi di sicurezza, autenticazione e accessi per-guild.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from manager_backend.security.crypto import hash_identifier

log = logging.getLogger("ezticket.manager.audit")


@dataclass
class AuditEvent:
    event_type: str
    timestamp: int = field(default_factory=lambda: int(time.time()))
    user_id: int | None = None
    guild_id: str | None = None
    ip_hash: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditLogger:
    """Gestore thread-safe dell'audit log in-memory e su logger."""

    def __init__(self, max_in_memory: int = 1000) -> None:
        self._max = max_in_memory
        self._events: list[AuditEvent] = []

    def record(
        self,
        event_type: str,
        *,
        user_id: int | str | None = None,
        guild_id: int | str | None = None,
        client_ip: str | None = None,
        details: dict[str, Any] | None = None,
        success: bool = True,
    ) -> AuditEvent:
        clean_user_id = int(user_id) if user_id is not None and str(user_id).isdigit() else None
        clean_guild_id = str(guild_id) if guild_id is not None else None
        ip_hash = hash_identifier(client_ip) if client_ip else None
        safe_details = {k: v for k, v in (details or {}).items() if not k.lower().endswith(("token", "secret", "password", "key"))}

        event = AuditEvent(
            event_type=event_type,
            user_id=clean_user_id,
            guild_id=clean_guild_id,
            ip_hash=ip_hash,
            details=safe_details,
            success=success,
        )

        self._events.append(event)
        if len(self._events) > self._max:
            self._events.pop(0)

        log_level = logging.INFO if success else logging.WARNING
        log.log(
            log_level,
            "AUDIT [%s] user=%s guild=%s success=%s ip_hash=%s details=%s",
            event.event_type,
            event.user_id,
            event.guild_id,
            event.success,
            event.ip_hash,
            json.dumps(event.details),
        )
        return event

    def get_events(
        self,
        guild_id: str | None = None,
        user_id: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        filtered = self._events
        if guild_id is not None:
            filtered = [e for e in filtered if e.guild_id == str(guild_id)]
        if user_id is not None:
            filtered = [e for e in filtered if e.user_id == user_id]
        return [e.to_dict() for e in reversed(filtered[-limit:])]


audit_logger = AuditLogger()
