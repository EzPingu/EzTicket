"""
manager_backend/auth/oauth.py
Integrazione con le API Discord OAuth2 (Code Grant + PKCE).
"""
from __future__ import annotations

import logging
from typing import Any, Optional
import httpx

from manager_backend.config import backend_cfg

log = logging.getLogger("ezticket.manager.oauth")

DISCORD_API_BASE = "https://discord.com/api/v10"
TOKEN_ENDPOINT = f"{DISCORD_API_BASE}/oauth2/token"
USER_ENDPOINT = f"{DISCORD_API_BASE}/users/@me"
USER_GUILDS_ENDPOINT = f"{DISCORD_API_BASE}/users/@me/guilds"


class DiscordOAuthError(Exception):
    def __init__(self, message: str, status_code: int = 400, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


class DiscordOAuthClient:
    """Client per la comunicazione con gli endpoint OAuth2 di Discord."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        self._http = http_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http
        return httpx.AsyncClient(timeout=10.0)

    async def exchange_code(
        self,
        code: str,
        code_verifier: str,
        redirect_uri: Optional[str] = None,
    ) -> dict[str, Any]:
        """Scambia l'Authorization Code e il PKCE code_verifier con il token Discord."""
        if not backend_cfg.is_oauth_configured:
            raise DiscordOAuthError("Credenziali Discord OAuth2 non configurate sul server", status_code=500)

        data = {
            "client_id": backend_cfg.discord_client_id,
            "client_secret": backend_cfg.discord_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri or backend_cfg.discord_redirect_uri,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        client = await self._get_client()
        should_close = self._http is None
        try:
            resp = await client.post(TOKEN_ENDPOINT, data=data, headers=headers)
            if resp.status_code != 200:
                log.warning("Errore scambio OAuth Discord (status %d): %s", resp.status_code, resp.text)
                raise DiscordOAuthError(
                    "Autenticazione Discord fallita: codice o verifier non valido",
                    status_code=400,
                    details=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
                )
            return resp.json()
        except httpx.RequestError as exc:
            log.error("Errore di rete verso Discord OAuth: %s", exc)
            raise DiscordOAuthError("Impossibile contattare i server di autenticazione Discord", status_code=502)
        finally:
            if should_close:
                await client.aclose()

    async def fetch_user_profile(self, access_token: str) -> dict[str, Any]:
        """Recupera l'identità dell'utente autenticato (@me)."""
        headers = {"Authorization": f"Bearer {access_token}"}
        client = await self._get_client()
        should_close = self._http is None
        try:
            resp = await client.get(USER_ENDPOINT, headers=headers)
            if resp.status_code != 200:
                raise DiscordOAuthError("Impossibile recuperare il profilo utente da Discord", status_code=401)
            return resp.json()
        except httpx.RequestError as exc:
            log.error("Errore recupero profilo utente Discord: %s", exc)
            raise DiscordOAuthError("Errore di connessione con Discord API", status_code=502)
        finally:
            if should_close:
                await client.aclose()

    async def fetch_user_guilds(self, access_token: str) -> list[dict[str, Any]]:
        """Recupera la lista delle guild a cui appartiene l'utente (opzionale per lookup veloce)."""
        headers = {"Authorization": f"Bearer {access_token}"}
        client = await self._get_client()
        should_close = self._http is None
        try:
            resp = await client.get(
                USER_GUILDS_ENDPOINT,
                headers=headers,
                params={"with_counts": "true"},
            )
            if resp.status_code != 200:
                return []
            return resp.json()
        except httpx.RequestError:
            return []
        finally:
            if should_close:
                await client.aclose()


oauth_client = DiscordOAuthClient()
