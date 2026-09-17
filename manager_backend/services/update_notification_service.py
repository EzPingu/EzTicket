"""Server-side Discord DM notifications for mandatory Manager updates."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from manager_backend.config import backend_cfg

log = logging.getLogger("ezticket.manager.update_notification")
DISCORD_API_BASE = "https://discord.com/api/v10"


async def send_required_update_dm(
    *,
    user_id: int,
    installed_version: str,
    minimum_version: str,
    download_url: str,
) -> bool:
    if not backend_cfg.discord_bot_token:
        log.error("Discord bot token non configurato: impossibile inviare il DM di aggiornamento")
        return False

    current_timestamp = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "content": f"Ciao <@{user_id}>! 👋",
        "embeds": [
            {
                "title": "⚠️ Aggiornamento necessario",
                "description": (
                    "Per continuare ad utilizzare EzTicket Manager devi aggiornare "
                    "alla nuova versione."
                ),
                "fields": [
                    {"name": "Versione installata", "value": installed_version, "inline": True},
                    {"name": "Versione minima", "value": minimum_version, "inline": True},
                    {"name": "ID", "value": str(user_id), "inline": True},
                    {
                        "name": "Data",
                        "value": f"<t:{current_timestamp}:F>",
                        "inline": False,
                    },
                ],
                "footer": {"text": "By EzPingu"},
            }
        ],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "Scarica nuova versione",
                        "emoji": {"name": "🟢"},
                        "url": download_url,
                    }
                ],
            }
        ],
    }
    headers = {"Authorization": f"Bot {backend_cfg.discord_bot_token}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            channel_response = await client.post(
                f"{DISCORD_API_BASE}/users/@me/channels",
                headers=headers,
                json={"recipient_id": str(user_id)},
            )
            if channel_response.status_code not in (200, 201):
                log.warning("Creazione DM Discord fallita per user %s: HTTP %s", user_id, channel_response.status_code)
                return False
            channel_id = channel_response.json().get("id")
            if not channel_id:
                log.warning("Discord non ha restituito il canale DM per user %s", user_id)
                return False
            message_response = await client.post(
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                headers=headers,
                json=payload,
            )
            if message_response.status_code not in (200, 201):
                log.warning("Invio DM Discord fallito per user %s: HTTP %s", user_id, message_response.status_code)
                return False
            return True
    except httpx.HTTPError:
        log.exception("Errore di rete durante l'invio del DM di aggiornamento a user %s", user_id)
        return False
