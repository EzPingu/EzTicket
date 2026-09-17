"""
manager_backend/config.py
Configurazione e variabili d'ambiente per il backend API di EzTicket Manager.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Carica il file .env presente in manager_backend/.env
load_dotenv(Path(__file__).parent / ".env")


@dataclass
class BackendConfig:
    # --- Server Settings ---
    host: str = os.getenv("BACKEND_HOST", "127.0.0.1")
    port: int = int(os.getenv("BACKEND_PORT", "8000"))
    cors_origins: list[str] = field(
        default_factory=lambda: [
            origin.strip()
            for origin in os.getenv(
                "BACKEND_CORS_ORIGINS",
                (
                    "http://localhost:3000,http://127.0.0.1:3000,"
                    "http://localhost:1420,http://127.0.0.1:1420,"
                    "http://localhost:5173,http://127.0.0.1:5173,"
                    "tauri://localhost,http://tauri.localhost,https://tauri.localhost"
                ),
            ).split(",")
            if origin.strip()
        ]
    )

    # --- Discord OAuth2 Credentials (Server-Side ONLY) ---
    discord_client_id: str = os.getenv("DISCORD_OAUTH_CLIENT_ID", "").strip()
    discord_client_secret: str = os.getenv("DISCORD_OAUTH_CLIENT_SECRET", "").strip()
    discord_redirect_uri: str = os.getenv(
        "DISCORD_OAUTH_REDIRECT_URI", "http://127.0.0.1:8765/callback"
    ).strip()
    discord_bot_token: str = os.getenv("DISCORD_BOT_TOKEN", os.getenv("DISCORD_TOKEN", "")).strip()

    # --- Session Settings ---
    session_secret_key: str = (
        os.getenv("SESSION_SECRET_KEY")
        or os.getenv("BACKEND_SECRET")
        or secrets.token_hex(32)
    )
    session_ttl_seconds: int = int(os.getenv("SESSION_TTL_SECONDS", "86400"))

    # --- Security & Audit ---
    audit_log_path: Path = Path(os.getenv("AUDIT_LOG_PATH", "manager_audit.log"))
    audit_webhook_url: str = os.getenv("AUDIT_WEBHOOK_URL", "").strip()

    # --- Manager update policy ---
    manager_current_version: str = os.getenv("MANAGER_CURRENT_VERSION", "0.1.0").strip()
    manager_minimum_version: str = os.getenv("MANAGER_MINIMUM_VERSION", "0.1.0").strip()
    manager_download_url: str = os.getenv("MANAGER_DOWNLOAD_URL", "").strip()

    @property
    def is_oauth_configured(self) -> bool:
        return bool(self.discord_client_id and self.discord_client_secret)


# Istanza singleton di configurazione backend
backend_cfg = BackendConfig()