import sqlite3
from collections import Counter

# compares every column of the rows we created against the distribution whatsapp
# itself uses for comparable rows. catches both "always null where base never is"
# and "uses a value base has never used in this context".

base = sqlite3.connect('BASE.db')
mg = sqlite3.connect('MERGED.db')

MAXJID = base.execute('SELECT MAX(_id) FROM jid').fetchone()[0]
MAXCHAT = base.execute('SELECT MAX(_id) FROM chat').fetchone()[0]
MAXMSG = base.execute('SELECT MAX(_id) FROM message').fetchone()[0]

LOW_CARD = 40   # treat a column as categorical below this many distinct values
issues = []


def check(table, new_where, peer_where, label):
    cols = [r[1] for r in mg.execute(f'PRAGMA table_info({table})')]
    n_new = mg.execute(f'SELECT COUNT(*) FROM {table} WHERE {new_where}').fetchone()[0]
    n_peer = base.execute(f'SELECT COUNT(*) FROM {table} WHERE {peer_where}').fetchone()[0]
    if not n_new or not n_peer:
        print(f'  [{label}] skipped (new={n_new}, peer={n_peer})')
        return
    print(f'  [{label}] {n_new} new rows vs {n_peer} comparable rows in BASE')

    for c in cols:
        if c == '_id':
            continue
        pn = base.execute(f'SELECT COUNT(*) FROM {table} WHERE {peer_where} AND "{c}" IS NULL').fetchone()[0]
        nn = mg.execute(f'SELECT COUNT(*) FROM {table} WHERE {new_where} AND "{c}" IS NULL').fetchone()[0]

        # null where whatsapp is essentially never null
        if pn / n_peer < 0.02 and nn / n_new > 0.5:
            issues.append(f'{table}.{c} [{label}]: NULL in {nn}/{n_new} of mine, '
                          f'but only {pn}/{n_peer} of BASE')
            continue

        peer_vals = [r[0] for r in base.execute(
            f'SELECT DISTINCT "{c}" FROM {table} WHERE {peer_where} LIMIT {LOW_CARD + 1}')]
        if len(peer_vals) > LOW_CARD:
            continue  # free-form column, no value check possible
        peer_set = set(peer_vals)
        mine = Counter(r[0] for r in mg.execute(
            f'SELECT "{c}" FROM {table} WHERE {new_where}'))
        for v, cnt in mine.items():
            if v not in peer_set:
                issues.append(f'{table}.{c} [{label}]: value {v!r} used {cnt}x, '
                              f'never appears in BASE (BASE uses {sorted(peer_set, key=str)[:6]})')


print('=== conformance check: my rows vs whatsapp conventions ===\n')

check('jid', f'_id>{MAXJID} AND server="lid"', 'server="lid"', 'new lid jids')
check('jid', f'_id>{MAXJID} AND server<>"lid"', 'server<>"lid"', 'new non-lid jids')
check('chat', f'_id>{MAXCHAT}',
      f'_id<={MAXCHAT} AND jid_row_id IN (SELECT _id FROM jid WHERE server="lid") AND hidden=0',
      'new chats')
check('message', f'_id>{MAXMSG} AND sender_jid_row_id>0',
      f'_id<={MAXMSG} AND sender_jid_row_id>0', 'new group msgs')
check('message', f'_id>{MAXMSG} AND (sender_jid_row_id=0 OR sender_jid_row_id IS NULL)',
      f'_id<={MAXMSG} AND (sender_jid_row_id=0 OR sender_jid_row_id IS NULL)', 'new 1:1 msgs')
check('message_media', f'message_row_id>{MAXMSG}', f'message_row_id<={MAXMSG}', 'new media stubs')

print()
if issues:
    print(f'!! {len(issues)} POTENTIAL ISSUES\n')
    for i in issues:
        print('   -', i)
else:
    print('OK - no structural or value-level deviations found')

# referential sanity on everything we touched
print('\n=== referential checks ===')
for label, q in [
    ('messages with no chat', 'SELECT COUNT(*) FROM message m LEFT JOIN chat c ON m.chat_row_id=c._id WHERE c._id IS NULL'),
    ('chats with no jid', 'SELECT COUNT(*) FROM chat c LEFT JOIN jid j ON c.jid_row_id=j._id WHERE j._id IS NULL'),
    ('chats with no account jid', 'SELECT COUNT(*) FROM chat c LEFT JOIN jid j ON c.account_jid_row_id=j._id WHERE j._id IS NULL'),
    ('senders with no jid', 'SELECT COUNT(*) FROM message m LEFT JOIN jid j ON m.sender_jid_row_id=j._id WHERE m.sender_jid_row_id>0 AND j._id IS NULL'),
    ('media with no message', 'SELECT COUNT(*) FROM message_media mm LEFT JOIN message m ON mm.message_row_id=m._id WHERE m._id IS NULL'),
]:
    b = base.execute(q).fetchone()[0]
    m = mg.execute(q).fetchone()[0]
    flag = '  <-- INTRODUCED' if m > b else ''
    print(f'  {label:28} BASE={b:<4} MERGED={m:<4}{flag}')
