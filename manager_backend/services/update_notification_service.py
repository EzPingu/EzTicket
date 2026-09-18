"""Server-side Discord DM notifications for mandatory Manager updates."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from manager_backend.config import backend_cfg

log = logging.getLogger("ezticket.manager.update_notification")
DISCORD_API_BASE = "https://discord.com/api/v10"

async def send_discord_dm(*, user_id: int, payload: dict) -> bool:
    """Send a bot DM through the shared Discord REST notification path."""
    if not backend_cfg.discord_bot_token:
        log.warning("Discord bot token non configurato: DM non inviato")
        return False
    headers = {"Authorization": f"Bot {backend_cfg.discord_bot_token}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            channel_response = await client.post(
                f"{DISCORD_API_BASE}/users/@me/channels",
                headers=headers,
                json={"recipient_id": str(user_id)},
            )
            if channel_response.status_code not in (200, 201):
                return False
            try:
                channel_id = channel_response.json().get("id")
            except (TypeError, ValueError, AttributeError):
                log.warning("Risposta non valida durante la creazione del DM a user %s", user_id)
                return False
            if not channel_id:
                return False
            message_response = await client.post(
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                headers=headers,
                json=payload,
            )
            return message_response.status_code in (200, 201)
    except (httpx.HTTPError, ValueError, TypeError):
        log.exception("Errore di rete durante l'invio del DM a user %s", user_id)
        return False


async def send_required_update_dm(
    *,
    user_id: int,
    installed_version: str,
    minimum_version: str,
    download_url: str,
) -> bool:
    current_timestamp = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "flags": 1 << 15,
        "components": [
            {
                "type": 17,
                "accent_color": 15158332,
                "components": [
                    {
                        "type": 10,
                        "content": (
                            f"Ciao <@{user_id}>! 👋\n\n"
                            "# ⚠️ Aggiornamento necessario\n\n"
                            "Per continuare ad utilizzare EzTicket Manager devi aggiornare "
                            "alla nuova versione.\n\n"
                            f"**Versione installata:** {installed_version}\n"
                            f"**Versione minima:** {minimum_version}\n"
                            f"**ID:** {user_id}\n"
                            f"**Data:** <t:{current_timestamp}:F>\n\n"
                            "By EzPingu"
                        ),
                    },
                    {
                        "type": 14,
                        "divider": True,
                        "spacing": 1,
                    },
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
                    },
                ],
            }
        ],
    }
    return await send_discord_dm(user_id=user_id, payload=payload)
