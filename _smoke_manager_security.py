"""Focused smoke checks for persistent Manager security controls."""
from __future__ import annotations

from manager_backend.auth.session import session_store
from manager_backend.services.manager_security_service import (
    apply_lockout,
    end_lockout_early,
    is_locked,
)
import asyncio
import manager_backend.services.manager_notification_service as notifications
import re
import main as bot_main


def main() -> None:
    user_id = 987654321012345
    session_store.clear()
    session = session_store.create_session(user_id=user_id, username="security-smoke")
    assert apply_lockout(user_id) > 0
    assert is_locked(user_id)
    assert session_store.get_session(session.session_token) is None
    assert end_lockout_early(user_id)
    assert not is_locked(user_id)
    sent: list[dict] = []

    async def capture(*, user_id: int, payload: dict) -> bool:
        sent.append(payload)
        return True

    notifications.send_discord_dm = capture
    asyncio.run(notifications.send_login_dm(
        user_id=user_id, username="user", global_name="Discord Name", login_at=1
    ))
    login = sent.pop()
    assert login["embeds"][0]["description"] == "Accesso effettuato con successo ✅"
    assert login["components"][0]["components"][0]["custom_id"].endswith(str(user_id))
    asyncio.run(notifications.send_first_login_dm(
        user_id=user_id, username="user", global_name="Discord Name"
    ))
    tutorial = sent.pop()["embeds"][0]
    tutorial_text = " ".join([tutorial["description"]] + [f["name"] for f in tutorial["fields"]])
    for section in (
        "🎫 Gestione ticket", "💬 Risposte", "🔒 Chiusura ticket",
        "📋 Candidature", "✅ Accettazione/rifiuto candidature",
        "📊 Statistiche", "🏠 Informazioni del server",
    ):
        assert section in tutorial_text
    asyncio.run(notifications.send_logout_dm(
        user_id=user_id, username="Discord Name", login_at=1, logout_at=2
    ))
    assert sent.pop()["embeds"][0]["title"] == "🟢 LOGOUT eseguito con successo"
    button = bot_main.ManagerNotMeButton(123456789)
    match = re.fullmatch(r"manager_login_not_me:(?P<user_id>[0-9]+)", button.item.custom_id)
    assert match is not None
    reconstructed = asyncio.run(bot_main.ManagerNotMeButton.from_custom_id(None, button.item, match))
    assert reconstructed.user_id == 123456789
    assert reconstructed.item.custom_id == "manager_login_not_me:123456789"
    print("Manager security smoke: OK")


if __name__ == "__main__":
    main()
