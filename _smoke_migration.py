"""Verifica della migrazione dai dati single-server a quelli multi-server."""
import json
import os
import shutil
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix='ezticket_mig_')
os.environ['DATA_DIR'] = tmp
os.environ.pop('BOT_OWNER_IDS', None)

GUILD_CONFIGURATO = '111111111111111111'
GUILD_DI_PASSAGGIO = '222222222222222222'

legacy_config = {
    GUILD_CONFIGURATO: {
        'transcript_channel': 900,
        'staff_role': 800,
        'sections': {'supporto': {'label': 'Supporto', 'category_id': 700}},
        'ticket_counter': 42,
        'blacklist': [555],
        'panel': {'channel_id': 600, 'message_id': 601},
        'owner_ids': [101],
        'owner_grants': {'101': {'source': 'manual'}},
        'staff_accepted_role_ids': [201],
        'branding': 'Brand A',
        'nick_format': 'A {nome}',
    },
    # guild finita nei dati solo perché è passato un messaggio: NON deve
    # ereditare i privilegi del vecchio owner globale
    GUILD_DI_PASSAGGIO: {
        'owner_ids': [102],
        'owner_grants': {'102': {'source': 'manual'}},
        'staff_accepted_role_ids': [202],
        'branding': 'Brand B',
        'nick_format': 'B {nome}',
    },
    '_global': {'qualcosa': 'da scartare'},
}
legacy_active = {
    GUILD_CONFIGURATO: {
        '1234': {'opener': 5, 'section': 'supporto', 'status': 'open', 'number': 42, 'opened_at': 1000}
    }
}
legacy_prefs = {
    'ticket_notify_optout': [777, 888],
    'candidature_sessions': {
        '999': {'guild_id': int(GUILD_CONFIGURATO), 'index': 1, 'answers': ['a'], 'ticket_channel_id': 1234}
    },
}

with open(os.path.join(tmp, 'config_ticket.json'), 'w', encoding='utf-8') as f:
    json.dump(legacy_config, f)
with open(os.path.join(tmp, 'tickets_active.json'), 'w', encoding='utf-8') as f:
    json.dump(legacy_active, f)
with open(os.path.join(tmp, 'staff_preferences.json'), 'w', encoding='utf-8') as f:
    json.dump(legacy_prefs, f)

import config
from config import cfg

ok = True


def check(label, condition, extra=''):
    global ok
    print(('  OK  ' if condition else ' FAIL ') + label + (f'  {extra}' if extra else ''))
    if not condition:
        ok = False


print('--- migrazione ---')
check('_global scartato', '_global' not in cfg.data, str(sorted(cfg.data)))

g1 = cfg.data[GUILD_CONFIGURATO]
g2 = cfg.data[GUILD_DI_PASSAGGIO]

check('dati preesistenti intatti', g1['transcript_channel'] == 900 and g1['staff_role'] == 800 and g1['ticket_counter'] == 42)
check('ticket attivi caricati', '1234' in g1['tickets'])
check('configurazioni esplicite restano nella propria guild',
      g1['owner_ids'] == [101] and g1['staff_accepted_role_ids'] == [201]
      and g1['branding'] == 'Brand A' and g1['nick_format'] == 'A {nome}'
      and g2['owner_ids'] == [102] and g2['staff_accepted_role_ids'] == [202]
      and g2['branding'] == 'Brand B' and g2['nick_format'] == 'B {nome}')
check('personalizzazione pannello preservata',
      g1['panel']['channel_id'] == 600 and g1['panel']['message_id'] == 601
      and g1['panel']['title'] is None,
      str(g1['panel']))
check('opt-out globale scartato senza propagazione',
      g1['ticket_notify_optout'] == [] and g2['ticket_notify_optout'] == []
      and 'ticket_notify_optout' not in cfg.prefs)
check('nuovi default presenti', g1['sla_seconds'] == 300 and g1['claim_timeout_seconds'] == 120 and g1['inactivity_hours'] == 24)
check('teams di default', g1['teams'] == ['Staff', 'Content Creator'])

print('--- sessioni candidatura ---')
check('sessione ri-chiavata guild:user', cfg.get_session(int(GUILD_CONFIGURATO), 999) is not None,
      str(list(cfg.prefs['candidature_sessions'])))
check('lookup da DM (per utente)', list(cfg.sessions_for_user(999)) == [int(GUILD_CONFIGURATO)])
check('nessuna collisione tra server', cfg.get_session(int(GUILD_DI_PASSAGGIO), 999) is None)

print('--- persistenza ---')
cfg.flush_sync()
with open(os.path.join(tmp, 'config_ticket.json'), encoding='utf-8') as f:
    riletto = json.load(f)
check('config riscritta senza i ticket attivi', 'tickets' not in riletto[GUILD_CONFIGURATO])
with open(os.path.join(tmp, 'tickets_active.json'), encoding='utf-8') as f:
    attivi = json.load(f)
check('ticket attivi su file separato', '1234' in attivi[GUILD_CONFIGURATO])

print('--- idempotenza (secondo avvio) ---')
cfg2 = config.ConfigManager()
h1 = cfg2.data[GUILD_CONFIGURATO]
check('configurazioni per-guild inalterate al secondo avvio',
      h1['owner_ids'] == [101] and cfg2.data[GUILD_DI_PASSAGGIO]['owner_ids'] == [102])
check('migrazione registrata', config.MIGRATION_V2 in cfg2.prefs['migrations'])
check('sessione ancora valida', cfg2.get_session(int(GUILD_CONFIGURATO), 999) is not None)

print('--- isolamento permessi ---')
check(
    'operator globale coerente con la policy BOT_OWNER_IDS',
    config.get_bot_operator_ids() == {config.BOT_OWNER_ID},
)
check('admin_ids per-guild', cfg.admin_ids(GUILD_CONFIGURATO) == [101] and cfg.admin_ids(GUILD_DI_PASSAGGIO) == [102])

print('--- preferenze/marcatore ricreati ---')
os.remove(os.path.join(tmp, 'staff_preferences.json'))
cfg3 = config.ConfigManager()
check('nessuna migrazione pericolosa dopo perdita preferenze',
      cfg3.data[GUILD_CONFIGURATO]['owner_ids'] == [101]
      and cfg3.data[GUILD_CONFIGURATO]['staff_accepted_role_ids'] == [201]
      and cfg3.data[GUILD_CONFIGURATO]['branding'] == 'Brand A'
      and cfg3.data[GUILD_DI_PASSAGGIO]['owner_ids'] == [102]
      and cfg3.data[GUILD_DI_PASSAGGIO]['staff_accepted_role_ids'] == [202]
      and cfg3.data[GUILD_DI_PASSAGGIO]['branding'] == 'Brand B')

shutil.rmtree(tmp, ignore_errors=True)
print()
print('RISULTATO:', 'TUTTO OK' if ok else 'CI SONO ERRORI')
sys.exit(0 if ok else 1)
