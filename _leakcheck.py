import json, os, tempfile
tmp = tempfile.mkdtemp(prefix="ez_leak_")
os.environ["DATA_DIR"] = tmp
json.dump(
    {"111111111111111111": {"staff_role": 800, "ticket_counter": 5}, "222222222222222222": {}},
    open(os.path.join(tmp, "config_ticket.json"), "w"),
)
import config

g2 = config.cfg.data["222222222222222222"]
print("guild di solo passaggio -> branding:", repr(g2["branding"]))
print("guild di solo passaggio -> staff_accepted_role_ids:", g2["staff_accepted_role_ids"])
print("guild di solo passaggio -> owner_ids:", g2["owner_ids"])
