"""
manager_backend/config.py
Configurazione e variabili d'ambiente per il backend API di EzTicket Manager.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BackendConfig:
    # --- Server Settings ---
    host: str = os.getenv("BACKEND_HOST", "127.0.0.1")
    port: int = int(os.getenv("BACKEND_PORT", "8000"))
    cors_origins: list[str] = field(
        default_factory=lambda: [
            origin.strip()
            for origin in os.getenv("BACKEND_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,tauri://localhost").split(",")
            if origin.strip()
        ]
    )

    # --- Discord OAuth2 Credentials (Server-Side ONLY) ---
    discord_client_id: str = os.getenv("DISCORD_OAUTH_CLIENT_ID", "").strip()
    discord_client_secret: str = os.getenv("DISCORD_OAUTH_CLIENT_SECRET", "").strip()
    discord_redirect_uri: str = os.getenv(
        "DISCORD_OAUTH_REDIRECT_URI", "http://127.0.0.1:8765/callback"
    ).strip()

    # --- Session Settings ---
    session_secret_key: str = (
        os.getenv("SESSION_SECRET_KEY")
        or os.getenv("BACKEND_SECRET")
        or secrets.token_hex(32)
    )
    session_ttl_seconds: int = int(os.getenv("SESSION_TTL_SECONDS", "86400"))  # 24 ore

    # --- Security & Audit ---
    audit_log_path: Path = Path(os.getenv("AUDIT_LOG_PATH", "manager_audit.log"))

    @property
    def is_oauth_configured(self) -> bool:
        return bool(self.discord_client_id and self.discord_client_secret)


# Istanza singleton di configurazione backend
backend_cfg = BackendConfig()
