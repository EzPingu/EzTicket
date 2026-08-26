"""Verifica persistenza, lifecycle owner, JSON corrotti e retry SLA."""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading

tmp = tempfile.mkdtemp(prefix="ezticket_resilience_")
os.environ["DATA_DIR"] = tmp
os.environ.pop("BOT_OWNER_IDS", None)

# JSON valido ma con root errata: il bootstrap deve ripartire dai default.
for filename in ("config_ticket.json", "tickets_active.json", "tickets_history.json", "staff_preferences.json"):
    with open(os.path.join(tmp, filename), "w", encoding="utf-8") as f:
        json.dump([], f)

import config
import tickets

ok = True


def check(label, condition, extra=""):
    global ok
    print(("  OK  " if condition else " FAIL ") + label + (f"  {extra}" if extra else ""))
    if not condition:
        ok = False


check("JSON root invalido non blocca il bootstrap",
      isinstance(config.cfg.data, dict) and isinstance(config.cfg.history, dict) and isinstance(config.cfg.prefs, dict))

with open(os.path.join(tmp, "config_ticket.json"), "w", encoding="utf-8") as f:
    json.dump({"111": {"sections": [], "owner_ids": ["bad"], "tickets": {"x": "bad"}}, "222": "bad"}, f)
with open(os.path.join(tmp, "tickets_active.json"), "w", encoding="utf-8") as f:
    json.dump({"111": []}, f)
with open(os.path.join(tmp, "tickets_history.json"), "w", encoding="utf-8") as f:
    json.dump({"111": "bad", "222": []}, f)
with open(os.path.join(tmp, "staff_preferences.json"), "w", encoding="utf-8") as f:
    json.dump({"candidature_sessions": {"111:2": "bad"}, "candidature_active": [], "migrations": "bad"}, f)
malformed = config.ConfigManager()
check("strutture JSON annidate corrotte vengono neutralizzate",
      malformed.data["111"]["sections"] == {}
      and malformed.data["111"]["tickets"] == {}
      and malformed.data["111"]["owner_ids"] == []
      and "111" not in malformed.history and malformed.history.get("222") == []
      and malformed.prefs["candidature_sessions"] == {}
      and malformed.prefs["candidature_active"] == {})


async def verify_flush_race():
    manager = config.ConfigManager()
    original_write = config._write_payloads
    original_delay = config.SAVE_DEBOUNCE_SECONDS
    started = threading.Event()
    release = threading.Event()

    def blocked_write(payloads):
        started.set()
        release.wait(timeout=2)
        original_write(payloads)

    config.SAVE_DEBOUNCE_SECONDS = 0.01
    config._write_payloads = blocked_write
    try:
        manager.guild(111)["branding"] = "prima"
        manager.mark_dirty("config")
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
        manager.guild(111)["branding"] = "dopo"
        manager.mark_dirty("config")
        release.set()
        await asyncio.sleep(0.1)
        await manager.flush()
        with open(os.path.join(tmp, "config_ticket.json"), encoding="utf-8") as f:
            persisted = json.load(f)
        check("modifica durante flush viene salvata", persisted["111"]["branding"] == "dopo")
    finally:
        release.set()
        config._write_payloads = original_write
        config.SAVE_DEBOUNCE_SECONDS = original_delay


async def verify_sla_retry():
    guild_id, channel_id = 222, 333

    class TemporaryDiscordError(Exception):
        pass

    class FakeTextChannel:
        mention = "#ticket"
        jump_url = "https://example.invalid/ticket"

        async def send(self, **kwargs):
            raise TemporaryDiscordError("temporaneo")

    class FakeGuild:
        def __init__(self, channel):
            self.channel = channel

        def get_channel(self, requested):
            return self.channel if requested == channel_id else None

    class FakeClient:
        def __init__(self, guild):
            self.guild = guild

        def get_guild(self, requested):
            return self.guild if requested == guild_id else None

    original_http = tickets.discord.HTTPException
    original_text = tickets.discord.TextChannel
    tickets.discord.HTTPException = TemporaryDiscordError
    tickets.discord.TextChannel = FakeTextChannel
    tickets.bind_client(FakeClient(FakeGuild(FakeTextChannel())))
    try:
        gconf = config.cfg.guild(guild_id)
        gconf["tickets"] = {str(channel_id): {"status": "open", "opener": 9, "section": "x", "sla_notified": False}}
        gconf["sections"] = {"x": {"label": "Supporto"}}
        await tickets.run_sla_check(guild_id, channel_id)
        key = f"sla:{guild_id}:{channel_id}"
        check("errore SLA non segna il ticket notificato", not gconf["tickets"][str(channel_id)]["sla_notified"])
        check("errore SLA pianifica retry isolato", key in tickets._scheduled_tasks)
        tickets.cancel_sla_check(guild_id, channel_id)
    finally:
        tickets.discord.HTTPException = original_http
        tickets.discord.TextChannel = original_text
        tickets.bind_client(None)


async def main():
    print("--- race flush ---")
    await verify_flush_race()

    print("--- owner dinamico ---")
    manager = config.ConfigManager()
    manager.mark_guild_joined(444, 10)
    check("owner corrente non viene persistito come admin", manager.admin_ids(444) == [])
    manager.peek(444)["owner_ids"] = [10]  # residuo auto-owner della versione precedente
    manager.reconcile_guild_owner(444, 20)
    check("ex-owner perde il privilegio manuale dopo trasferimento", manager.admin_ids(444) == [])
    check("nuovo owner resta solo dinamico", manager.peek(444)["observed_owner_id"] == 20)
    manager.add_admin(444, 10)
    manager.reconcile_guild_owner(444, 30)
    check("delega esplicita sopravvive al trasferimento", manager.admin_ids(444) == [10])

    print("--- retry SLA ---")
    await verify_sla_retry()


asyncio.run(main())
shutil.rmtree(tmp, ignore_errors=True)
print()
print("RISULTATO:", "TUTTO OK" if ok else "CI SONO ERRORI")
sys.exit(0 if ok else 1)
