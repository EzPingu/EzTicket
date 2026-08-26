"""Verifica dell'isolamento tra server: timer, opt-out notifiche, impostazioni."""
import asyncio
import os
import shutil
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix='ezticket_iso_')
os.environ['DATA_DIR'] = tmp
os.environ.pop('BOT_OWNER_IDS', None)

import config
import tickets
from config import cfg

A, B = 111, 222
ok = True


def check(label, condition, extra=''):
    global ok
    print(('  OK  ' if condition else ' FAIL ') + label + (f'  {extra}' if extra else ''))
    if not condition:
        ok = False


async def main():
    print('--- timer per-server ---')
    tickets.schedule_sla_check(A, 1001, 60)
    tickets.schedule_claim_check(A, 1001, 7, 60)
    tickets.schedule_sla_check(B, 2002, 60)
    await asyncio.sleep(0)
    check('chiavi separate per guild',
          sorted(tickets._scheduled_tasks) == ['claim:111:1001', 'sla:111:1001', 'sla:222:2002'],
          str(sorted(tickets._scheduled_tasks)))
    check('conteggio timer attivi', tickets.active_timer_count() == 3)

    rimossi = tickets.cancel_guild_timers(A)
    await asyncio.sleep(0)
    check('cancel_guild_timers tocca solo la sua guild',
          rimossi == 2 and sorted(tickets._scheduled_tasks) == ['sla:222:2002'],
          f'rimossi={rimossi} restano={sorted(tickets._scheduled_tasks)}')

    tickets.cancel_sla_check(B, 2002)
    await asyncio.sleep(0)
    check('cancel puntuale', tickets._scheduled_tasks == {})

    print('--- riprogrammazione senza duplicati ---')
    tickets.schedule_sla_check(A, 1001, 60)
    tickets.schedule_sla_check(A, 1001, 60)
    await asyncio.sleep(0)
    check('un solo task per ticket', len(tickets._scheduled_tasks) == 1)
    tickets.cancel_guild_timers(A)
    await asyncio.sleep(0)

    print('--- timer che scatta su una guild inesistente ---')
    tickets.bind_client(None)
    await tickets.run_sla_check(A, 1001)          # non deve sollevare eccezioni
    await tickets.run_claim_check(A, 1001, 7)
    check('nessun crash se il bot non e piu nel server', True)

    print('--- opt-out notifiche per-server ---')
    cfg.set_notify_optout(A, 555, True)
    check('opt-out registrato in A', cfg.notify_optout(A) == [555])
    check('opt-out NON propagato in B', cfg.notify_optout(B) == [], str(cfg.notify_optout(B)))
    cfg.set_notify_optout(B, 555, True)
    cfg.set_notify_optout(A, 555, False)
    check('opt-in in A non toglie quello di B',
          cfg.notify_optout(A) == [] and cfg.notify_optout(B) == [555])

    print('--- impostazioni per-server ---')
    cfg.guild(A)['sla_seconds'] = 900
    cfg.guild(A)['claim_timeout_seconds'] = 60
    check('SLA indipendenti',
          config.sla_seconds(A) == 900 and config.sla_seconds(B) == 300,
          f'A={config.sla_seconds(A)} B={config.sla_seconds(B)}')
    check('claim timeout indipendenti',
          config.claim_timeout_seconds(A) == 60 and config.claim_timeout_seconds(B) == 120)
    check('default per una guild mai vista', config.sla_seconds(999999) == 300)

    print('--- semantica di None (nickname / branding) ---')
    cfg.guild(A)['nick_format'] = None
    check('nick_format=None resta None', config.guild_setting(A, 'nick_format') is None)
    # Il default NON e piu il formato del server originale: un server nuovo non deve
    # rinominare i suoi membri con il titolo deciso da un'altra community.
    check('nick_format di default = None (nessun rename)', config.guild_setting(B, 'nick_format') is None,
          repr(config.guild_setting(B, 'nick_format')))
    cfg.guild(A)['branding'] = None
    check('branding=None non e sostituito dal default', cfg.guild(A)['branding'] is None)

    print('--- team per-server ---')
    cfg.guild(A)['teams'] = ['Moderatore']
    check('team indipendenti',
          config.guild_teams(A) == ['Moderatore'] and config.guild_teams(B) == ['Staff', 'Content Creator'])

    print('--- opzioni del pannello (limiti Discord) ---')
    cfg.guild(A)['sections'] = {
        'lunga': {'label': 'X' * 150, 'description': 'D' * 150, 'emoji': None},
        'corta': {'label': 'Supporto', 'description': None, 'emoji': '🎫'},
    }
    opts = tickets.panel_options(cfg.guild(A))
    check('etichetta tagliata a 100 caratteri', len(opts[0].label) == 100, str(len(opts[0].label)))
    check('descrizione tagliata a 100 caratteri', len(opts[0].description) == 100)
    check('descrizione assente resta None', opts[1].description is None)
    check('sezioni di un altro server non incluse', tickets.panel_options(cfg.guild(B)) is None)
    cfg.guild(A)['sections'] = {}

    print('--- sessioni candidatura: stesso utente in due server ---')
    cfg.set_session(A, 42, {'guild_id': A, 'index': 0, 'answers': []})
    cfg.set_session(B, 42, {'guild_id': B, 'index': 0, 'answers': []})
    check('due sessioni distinte', sorted(cfg.sessions_for_user(42)) == [A, B], str(sorted(cfg.sessions_for_user(42))))
    cfg.set_active_session_guild(42, B)
    check('puntatore sessione attiva', cfg.active_session_guild(42) == B)
    cfg.pop_session(B, 42)
    check('pop di una sessione non tocca l altra', sorted(cfg.sessions_for_user(42)) == [A])
    check('puntatore azzerato col pop', cfg.active_session_guild(42) is None)

    print('--- ciclo di vita del server ---')
    cfg.guild(B)['tickets'] = {'3003': {'status': 'open'}}
    cfg.mark_guild_left(B)
    check('left_at impostato e stato volatile azzerato',
          cfg.peek(B)['left_at'] is not None and cfg.peek(B)['tickets'] == {})
    check('config di A intatta', cfg.peek(A)['left_at'] is None and cfg.peek(A)['sla_seconds'] == 900)
    cfg.add_history_entry(B, {'number': 1})
    cfg.peek(B)['left_at'] = 0   # molto vecchio -> deve essere ripulito
    purgati = cfg.purge_stale_guilds()
    check('config del server abbandonato eliminata', str(B) in purgati and cfg.peek(B) is None, str(purgati))
    check('storico conservato', cfg.history.get(str(B)) == [{'number': 1}])

    print('--- salvataggio asincrono ---')
    cfg.save()
    await cfg.flush()
    check('file scritti', all(os.path.exists(os.path.join(tmp, n)) for n in
                             ('config_ticket.json', 'tickets_active.json', 'tickets_history.json', 'staff_preferences.json')))


asyncio.run(main())
shutil.rmtree(tmp, ignore_errors=True)
print()
print('RISULTATO:', 'TUTTO OK' if ok else 'CI SONO ERRORI')
sys.exit(0 if ok else 1)
