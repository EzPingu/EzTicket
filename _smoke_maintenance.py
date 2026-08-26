"""Verifica della manutenzione periodica: candidature abbandonate, riferimenti ai
riepiloghi obsoleti, server lasciati da troppo tempo. Senza queste pulizie i file
crescono senza limite e ogni salvataggio rallenta per TUTTI i server."""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

tmp = tempfile.mkdtemp(prefix='ezticket_maint_')
os.environ['DATA_DIR'] = tmp
os.environ.pop('BOT_OWNER_IDS', None)

import config
from config import cfg

A, B = 111, 222
ok = True
NOW = int(datetime.now(timezone.utc).timestamp())
GIORNO = 86400


def check(label, condition, extra=''):
    global ok
    print(('  OK  ' if condition else ' FAIL ') + label + (f'  {extra}' if extra else ''))
    if not condition:
        ok = False


print('--- scadenza di una singola sessione ---')
check('sessione recente non scaduta',
      cfg.session_expired({'started_at': NOW - GIORNO}) is False)
check('sessione oltre il TTL scaduta',
      cfg.session_expired({'started_at': NOW - (config.CANDIDATURE_SESSION_TTL_DAYS + 1) * GIORNO}) is True)
check('sessione senza timestamp non scaduta (legacy)', cfg.session_expired({'index': 0}) is False)
check('nessuna sessione non scaduta', cfg.session_expired(None) is False)

print('--- purge delle candidature mai completate ---')
vecchia = NOW - (config.CANDIDATURE_SESSION_TTL_DAYS + 5) * GIORNO
cfg.set_session(A, 10, {'guild_id': A, 'index': 0, 'answers': [], 'started_at': vecchia})
cfg.set_session(A, 11, {'guild_id': A, 'index': 0, 'answers': [], 'started_at': NOW})
cfg.set_session(B, 10, {'guild_id': B, 'index': 0, 'answers': [], 'started_at': NOW})
cfg.set_active_session_guild(10, A)
# sessione legacy senza started_at + chiave malformata
cfg._sessions()['333:12'] = {'guild_id': 333, 'index': 0, 'answers': []}
cfg._sessions()['chiave-rotta'] = {'index': 0}

scadute = cfg.purge_stale_sessions()
check('solo la sessione vecchia scartata', scadute == [(A, 10)], str(scadute))
check('sessione recente dello stesso utente in un altro server intatta',
      cfg.get_session(B, 10) is not None)
check('sessione recente di un altro utente intatta', cfg.get_session(A, 11) is not None)
check('puntatore della sessione attiva azzerato', cfg.active_session_guild(10) is None)
check('chiave malformata rimossa', 'chiave-rotta' not in cfg._sessions())
check('sessione legacy datata invece di essere buttata',
      cfg.get_session(333, 12) is not None
      and cfg.get_session(333, 12).get('started_at') is not None)
check('purge ripetuta non ha effetti', cfg.purge_stale_sessions() == [])

print('--- purge dei riferimenti ai riepiloghi ---')
refs_a = cfg.guild(A).setdefault('candidature_summary_messages', {})
refs_a['10'] = {'channel_id': 1, 'message_id': 2, 'created_at': NOW - (config.SUMMARY_REF_TTL_DAYS + 1) * GIORNO}
refs_a['11'] = {'channel_id': 1, 'message_id': 3, 'created_at': NOW}
refs_a['12'] = {'channel_id': 1, 'message_id': 4}          # legacy, senza data
refs_a['13'] = 'dato corrotto'
refs_b = cfg.guild(B).setdefault('candidature_summary_messages', {})
refs_b['10'] = {'channel_id': 9, 'message_id': 9, 'created_at': NOW}

rimossi = cfg.purge_stale_summaries()
check('rimossi solo il vecchio e quello corrotto', rimossi == 2, str(rimossi))
check('riferimento recente conservato', '11' in refs_a)
check('riferimento legacy datato adesso',
      '12' in refs_a and refs_a['12'].get('created_at') is not None)
check('altro server non toccato', refs_b == {'10': {'channel_id': 9, 'message_id': 9, 'created_at': NOW}})
check('purge ripetuta non ha effetti', cfg.purge_stale_summaries() == 0)

print('--- giro completo di manutenzione ---')
cfg.mark_guild_left(B)
cfg.peek(B)['left_at'] = 0                                  # abbandonato da sempre
cfg.set_session(A, 20, {'guild_id': A, 'index': 0, 'answers': [], 'started_at': vecchia})
refs_a['20'] = {'channel_id': 1, 'message_id': 5, 'created_at': NOW - (config.SUMMARY_REF_TTL_DAYS + 2) * GIORNO}

stats = cfg.run_maintenance()
check('conteggi del giro di manutenzione',
      stats == {'guilds': 1, 'sessions': 1, 'summaries': 1}, str(stats))
check('server abbandonato eliminato', cfg.peek(B) is None)
check('server attivo intatto', cfg.peek(A) is not None and '11' in refs_a)
check('manutenzione a vuoto = tutto zero',
      cfg.run_maintenance() == {'guilds': 0, 'sessions': 0, 'summaries': 0})

print('--- persistenza dopo la manutenzione ---')
cfg.flush_sync()
check('file scritti', all(os.path.exists(os.path.join(tmp, n)) for n in
                         ('config_ticket.json', 'staff_preferences.json')))

shutil.rmtree(tmp, ignore_errors=True)
print()
print('RISULTATO:', 'TUTTO OK' if ok else 'CI SONO ERRORI')
sys.exit(0 if ok else 1)
