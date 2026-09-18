"""
manager_backend/audit.py
Sistema di Audit Logging per eventi di sicurezza, autenticazione e accessi per-guild.
"""
from __future__ import annotations

import json
import logging
import time
import asyncio
import threading
from dataclasses import asdict, dataclass, field
from typing import Any
import httpx

from manager_backend.config import backend_cfg
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
        self._file_lock = threading.Lock()
        self._events_lock = threading.RLock()

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
        safe_details = self._sanitize(details or {})

        event = AuditEvent(
            event_type=event_type,
            user_id=clean_user_id,
            guild_id=clean_guild_id,
            ip_hash=ip_hash,
            details=safe_details,
            success=success,
        )

        with self._events_lock:
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
        self._write_durable(event)
        self._dispatch_webhook(event)
        return event

    def _write_durable(self, event: AuditEvent) -> None:
        path = backend_cfg.audit_log_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._file_lock:
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event.to_dict(), ensure_ascii=True) + "\n")
        except OSError as exc:
            log.warning("Scrittura audit persistente fallita: %s", exc)

    @classmethod
    def _sanitize(cls, value: Any, key: str = "") -> Any:
        blocked = ("token", "secret", "password", "cookie", "webhook", "authorization")
        if any(part in key.lower() for part in blocked):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(k): cls._sanitize(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._sanitize(item, key) for item in value]
        return value

    @staticmethod
    async def _post_webhook(event: AuditEvent) -> None:
        if not backend_cfg.audit_webhook_url:
            return
        color = 0x57D39B if event.success else 0xED6A5A
        title = "✅ " + event.event_type if event.success else "❌ Azione fallita"
        display_name = event.details.get("global_name") or event.details.get("username")
        user_label = f"{display_name} (`{event.user_id}`)" if display_name and event.user_id else f"`{event.user_id or 'n/d'}`"
        fields = [
            {"name": "Azione", "value": f"`{event.event_type}`", "inline": True},
            {"name": "Utente", "value": user_label[:1024], "inline": True},
            {"name": "Guild", "value": f"`{event.guild_id or 'n/d'}`", "inline": True},
            {"name": "Ora", "value": f"<t:{event.timestamp}:F>", "inline": False},
            {"name": "Risultato", "value": "✅ Operazione riuscita" if event.success else "❌ Operazione negata", "inline": True},
        ]
        if display_name:
            fields.insert(1, {"name": "Nome Discord", "value": str(display_name)[:256], "inline": True})
        for key in ("ticket_id", "channel_id", "ticket_number", "application_id", "target_user_id"):
            if key in event.details:
                fields.append({"name": key.replace("_", " ").title(), "value": f"`{event.details[key]}`", "inline": True})
        if event.details:
            details = json.dumps(event.details, ensure_ascii=True)
            fields.append({"name": "Dettagli", "value": f"```json\n{details[:900]}\n```", "inline": False})
        payload = {
            "embeds": [{
                "title": title[:256],
                "color": color,
                "fields": fields,
                "footer": {"text": "EzTicket Manager Audit"},
            }]
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.post(backend_cfg.audit_webhook_url, json=payload)
                if response.status_code == 429:
                    log.warning("Audit webhook rate limited: status=429")
                    return
                response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Invio audit webhook fallito: %s", exc)

    def _dispatch_webhook(self, event: AuditEvent) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._post_webhook(event))

    def get_events(
        self,
        guild_id: str | None = None,
        user_id: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._events_lock:
            filtered = list(self._events)
        if guild_id is not None:
            filtered = [e for e in filtered if e.guild_id == str(guild_id)]
        if user_id is not None:
            filtered = [e for e in filtered if e.user_id == user_id]
        return [e.to_dict() for e in reversed(filtered[-limit:])]


audit_logger = AuditLogger()
