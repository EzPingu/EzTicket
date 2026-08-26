"""Verifica C3: le candidature DM concorrenti non vengono mai auto-instradate."""
import asyncio
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

tmp = tempfile.mkdtemp(prefix="ezticket_dm_")
os.environ["DATA_DIR"] = tmp
os.environ.pop("BOT_OWNER_IDS", None)

import candidature
from config import cfg

A, B, USER = 111, 222, 42
ok = True


def check(label, condition, extra=""):
    global ok
    print(("  OK  " if condition else " FAIL ") + label + (f"  {extra}" if extra else ""))
    if not condition:
        ok = False


class FakeGuild:
    def __init__(self, guild_id, name):
        self.id = guild_id
        self.name = name
        self.icon = None


class FakeBot:
    def __init__(self):
        self.guilds = {A: FakeGuild(A, "Guild A"), B: FakeGuild(B, "Guild B")}

    def get_guild(self, guild_id):
        return self.guilds.get(guild_id)


class FakeUser:
    id = USER

    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


class FakeMessage:
    def __init__(self, user, content):
        self.author = user
        self.content = content


async def main():
    bot = FakeBot()
    user = FakeUser()
    now = int(datetime.now(timezone.utc).timestamp())
    cfg.guild(A)["staff_questions"] = ["Domanda A", "Seconda A"]
    cfg.guild(B)["staff_questions"] = ["Domanda B"]
    cfg.set_session(A, USER, {"guild_id": A, "index": 0, "answers": [], "started_at": now})
    cfg.set_session(B, USER, {"guild_id": B, "index": 0, "answers": [], "started_at": now})

    # Simula un puntatore automatico lasciato dalla versione precedente: non è
    # una scelta utente e deve mostrare il menu senza consumare il messaggio.
    cfg.prefs["candidature_active"][str(USER)] = B
    asked = []
    original_ask = candidature._ask_which_guild

    async def record_ask(_, __, guild_ids):
        asked.append(guild_ids)

    candidature._ask_which_guild = record_ask
    try:
        consumed = await candidature.handle_candidatura_dm_answer(bot, FakeMessage(user, "risposta ambigua"))
    finally:
        candidature._ask_which_guild = original_ask

    check("puntatore legacy non instrada automaticamente", consumed and asked == [[A, B]], str(asked))
    check("messaggio ambiguo non finisce in nessuna guild",
          cfg.get_session(A, USER)["answers"] == [] and cfg.get_session(B, USER)["answers"] == [])

    # Una scelta esplicita abilita l'instradamento soltanto verso la guild scelta.
    cfg.set_active_session_guild(USER, A)
    consumed = await candidature.handle_candidatura_dm_answer(bot, FakeMessage(user, "risposta per A"))
    check("scelta esplicita instrada la risposta", consumed and cfg.get_session(A, USER)["answers"] == ["risposta per A"])
    check("guild non scelta resta isolata", cfg.get_session(B, USER)["answers"] == [])
    check("puntatore esplicito è strutturato", cfg.prefs["candidature_active"][str(USER)] == {"guild_id": A, "explicit": True})

    # L'avvio di un altro questionario annulla una scelta precedente: il
    # messaggio seguente deve tornare alla disambiguazione esplicita.
    cfg.set_active_session_guild(USER, None)
    asked = []
    candidature._ask_which_guild = record_ask
    try:
        consumed = await candidature.handle_candidatura_dm_answer(bot, FakeMessage(user, "ancora ambigua"))
    finally:
        candidature._ask_which_guild = original_ask
    check("nessuna scelta precedente è riusata implicitamente", consumed and asked == [[A, B]])
    check("nessuna risposta successiva contamina B", cfg.get_session(B, USER)["answers"] == [])


asyncio.run(main())
shutil.rmtree(tmp, ignore_errors=True)
print()
print("RISULTATO:", "TUTTO OK" if ok else "CI SONO ERRORI")
sys.exit(0 if ok else 1)
