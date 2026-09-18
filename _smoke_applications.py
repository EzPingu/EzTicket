from __future__ import annotations

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

os.environ["DISCORD_OAUTH_CLIENT_ID"] = "test_client_id_12345"
os.environ["DISCORD_OAUTH_CLIENT_SECRET"] = "test_client_secret_super_secret"
os.environ["SESSION_SECRET_KEY"] = "test_session_secret_key_67890"

from config import cfg
from manager_backend.app import app
from manager_backend.auth.session import session_store
from manager_backend.audit import audit_logger

client = TestClient(app)

def assert_eq(actual, expected, msg: str):
    if actual != expected:
        print(f"  FAIL: {msg} (atteso {expected}, ottenuto {actual})")
        sys.exit(1)
    print(f"  OK  {msg}")

def test_applications():
    print("--- Setup Mock Guild & Data ---")
    guild_id = "12345"
    user_id = "999"
    staff_id = 777
    
    cfg.data[guild_id] = {
        "candidature_summary_messages": {
            user_id: {
                "channel_id": "111",
                "message_id": "222",
                "created_at": 1000
            }
        },
        "staff_questions": ["Q1?"]
    }
    
    session = session_store.create_session(
        user_id=staff_id,
        username="staff_user",
        discord_guilds=[{"id": guild_id, "permissions": "0"}]
    )
    headers = {"Authorization": f"Bearer {session.session_token}", "X-Manager-Version": "1.2.8"}
    
    def mock_eval(user_id, guild_id, discord_guilds=None):
        from manager_backend.services.guild_service import GuildAccessInfo
        if str(guild_id) == "12345":
            return GuildAccessInfo(
                guild_id=str(guild_id),
                is_present=True,
                is_authorized=True,
                role="GUILD_STAFF",
                is_owner=False,
                is_admin=False,
                guild_config={}
            )
        return GuildAccessInfo(
            guild_id=str(guild_id),
            is_present=True,
            is_authorized=False,
            role="NONE",
            is_owner=False,
            is_admin=False,
            guild_config={}
        )

    with patch("manager_backend.services.guild_service.guild_service.evaluate_guild_access", side_effect=mock_eval):
        
        print("--- 1. List Applications ---")
        resp = client.get(f"/api/v1/guilds/{guild_id}/applications", headers=headers)
        assert_eq(resp.status_code, 200, "List returns 200")
        assert_eq(len(resp.json()), 1, "List returns 1 application")
        assert_eq(resp.json()[0]["user_id"], user_id, "User ID matches")

        print("--- 2. Get Application Detail ---")
        resp = client.get(f"/api/v1/guilds/{guild_id}/applications/{user_id}", headers=headers)
        assert_eq(resp.status_code, 200, "Detail returns 200")
        assert_eq(resp.json()["questions"], ["Q1?"], "Questions matches")
        
        print("--- 3. Accept Application (Bot Offline) ---")
        resp = client.post(f"/api/v1/guilds/{guild_id}/applications/{user_id}/accept", headers=headers, json={"full_onboard": True})
        assert_eq(resp.status_code, 503, "Accept fails when bot is offline")
        
        print("--- 4. IDOR Cross Guild ---")
        resp = client.get(f"/api/v1/guilds/99999/applications", headers=headers)
        assert_eq(resp.status_code, 403, "Access to other guild denied")

if __name__ == "__main__":
    test_applications()
    print("ALL TESTS PASSED")
