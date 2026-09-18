"""Discord DMs used by the Manager authentication flow."""
from __future__ import annotations

from manager_backend.services.update_notification_service import send_discord_dm


def _time_field(timestamp: int) -> dict:
    return {"name": "Data e ora", "value": f"<t:{timestamp}:F>", "inline": False}


async def send_login_dm(*, user_id: int, username: str, global_name: str | None, login_at: int) -> bool:
    display_name = global_name or username
    payload = {
        "content": f"Ciao <@{user_id}>!",
        "embeds": [{"title": "🔐 Login", "description": "Accesso effettuato con successo ✅",
                    "color": 0x5865F2,
                    "fields": [{"name": "Nome Discord", "value": display_name, "inline": True},
                               {"name": "Discord ID", "value": str(user_id), "inline": True},
                               _time_field(login_at)],
                    "footer": {"text": "EzTicket Manager • Sicurezza"}}],
        "components": [{"type": 1, "components": [{"type": 2, "style": 4,
                        "label": "Non sono stato io", "custom_id": f"manager_login_not_me:{user_id}"}]}],
    }
    return await send_discord_dm(user_id=user_id, payload=payload)


async def send_first_login_dm(
    *, user_id: int, username: str, global_name: str | None = None
) -> bool:
    display_name = global_name or username
    payload = {"embeds": [{"title": "👋 Benvenuto in EzTicket Manager",
                           "description": (
                               f"Benvenuto {display_name}!\n\n"
                               "Questa è la tua prima connessione a EzTicket Manager. "
                               "Ecco una guida rapida alle funzioni principali."
                           ),
                           "color": 0x57D39B,
                           "fields": [
                               {"name": "🎫 Gestione ticket", "value": "Visualizza e gestisci i ticket dei tuoi server.", "inline": False},
                               {"name": "💬 Risposte", "value": "Rispondi rapidamente alle richieste direttamente dal Manager.", "inline": False},
                               {"name": "🔒 Chiusura ticket", "value": "Chiudi i ticket risolti e conserva lo storico in sicurezza.", "inline": False},
                               {"name": "📋 Candidature", "value": "Consulta e gestisci le candidature ricevute.", "inline": False},
                               {"name": "✅ Accettazione/rifiuto candidature", "value": "Valuta ogni candidatura e comunica l'esito in modo semplice.", "inline": False},
                               {"name": "📊 Statistiche", "value": "Controlla andamento, attività e tempi di gestione.", "inline": False},
                               {"name": "🏠 Informazioni del server", "value": "Consulta configurazione, staff e informazioni dei server autorizzati.", "inline": False},
                               {"name": "Suggerimento di sicurezza", "value": "Se non riconosci un accesso, usa il pulsante rosso nel DM Login.", "inline": False},
                           ],
                           "footer": {"text": "EzTicket Manager • Tutorial"}}]}
    return await send_discord_dm(user_id=user_id, payload=payload)


async def send_logout_dm(*, user_id: int, username: str, login_at: int, logout_at: int) -> bool:
    payload = {"embeds": [{"title": "🟢 LOGOUT eseguito con successo",
                           "description": (
                               "L'app ha rilevato che sei uscito dall'app, i tuoi dati sono stati "
                               "salvati e protetti con il logout."
                           ),
                           "color": 0xED6A5A,
                           "fields": [{"name": "Nome Discord", "value": username, "inline": True},
                                      {"name": "Discord ID", "value": str(user_id), "inline": True},
                                      {"name": "Login", "value": f"<t:{login_at}:F>", "inline": False},
                                      {"name": "Logout", "value": f"<t:{logout_at}:F>", "inline": False}],
                           "footer": {"text": "EzTicket Manager • Sicurezza"}}]}
    return await send_discord_dm(user_id=user_id, payload=payload)


async def send_lockout_expired_dm(*, user_id: int) -> bool:
    payload = {
        "flags": 32768,
        "components": [{
            "type": 17,
            "accent_color": 0x57D39B,
            "components": [{
                "type": 10,
                "content": (
                    "🟢 **Blocco di sicurezza disattivato**\n\n"
                    "Il blocco è stato disattivato automaticamente poiché sono trascorsi "
                    "i 5 minuti previsti.\n\n"
                    "🔓 I nuovi accessi a EzTicket Manager sono nuovamente consentiti.\n\n"
                    "🛡️ La procedura di sicurezza è stata completata."
                ),
            }],
        }],
    }
    return await send_discord_dm(user_id=user_id, payload=payload)
