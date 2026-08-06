import sqlite3, shutil, os

# whatsapp assigns sort_id = _id for every message it creates -- verified against
# 14 messages it wrote itself after the restore, all equal, no exceptions. so a
# thread's display order is really its _id order, and any row whose _id is low but
# whose timestamp is late will sit in the wrong place forever.
#
# rows revived in place kept BASE's low _ids with late timestamps. renumbering
# sort_id alone papered over it: it fixed the existing thread but pushed 1011 rows
# above max(_id), so every NEW message landed underneath them.
#
# the real fix is to give the post-seam block _ids that increase with time, above
# everything else, and set sort_id = _id. that restores whatsapp's own invariant,
# so newly arriving messages sort last on their own.

import sys
SRC = sys.argv[1] if len(sys.argv)>1 else 'MEDIA.db'
OUT = sys.argv[2] if len(sys.argv)>2 else 'FINAL.db'
SEAM_MS = 1782198302000

if os.path.exists(OUT):
    os.remove(OUT)
shutil.copy(SRC, OUT)
db = sqlite3.connect(OUT)
db.execute('PRAGMA foreign_keys=OFF')

# --- discover every column that points at message._id ------------------------
refs = []
for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
    if t == 'message' or t.startswith('sqlite_'):
        continue
    try:
        cols = [r[1] for r in db.execute(f'PRAGMA table_info("{t}")')]
    except sqlite3.DatabaseError:
        continue
    for c in cols:
        if c == 'message_row_id' or c.endswith('_message_row_id'):
            refs.append((t, c))
print(f'reference columns discovered: {len(refs)}')

before_counts = {f'{t}.{c}': db.execute(
    f'SELECT COUNT(*) FROM "{t}" WHERE "{c}">0').fetchone()[0] for t, c in refs}
n_before = db.execute('SELECT COUNT(*) FROM message').fetchone()[0]

# --- build the remap ---------------------------------------------------------
max_id = db.execute('SELECT MAX(_id) FROM message').fetchone()[0]
rows = db.execute('SELECT _id FROM message WHERE timestamp>? ORDER BY timestamp, _id',
                  (SEAM_MS,)).fetchall()
db.execute('CREATE TEMP TABLE _idmap(old INTEGER PRIMARY KEY, new INTEGER)')
db.executemany('INSERT INTO _idmap VALUES(?,?)',
               [(old, max_id + 1 + i) for i, (old,) in enumerate(rows)])
print(f'remapping {len(rows)} post-seam rows to {max_id+1}..{max_id+len(rows)} '
      f'(all above the current max, so no id can collide)')

# --- apply: children first, then the message rows themselves -----------------
for t, c in refs:
    db.execute(f'UPDATE "{t}" SET "{c}" = (SELECT new FROM _idmap WHERE old="{t}"."{c}") '
               f'WHERE "{c}" IN (SELECT old FROM _idmap)')
db.execute('UPDATE message SET _id=(SELECT new FROM _idmap WHERE old=message._id) '
           'WHERE _id IN (SELECT old FROM _idmap)')

# whatsapp's invariant, restored for every row
db.execute('UPDATE message SET sort_id=_id')

# chat pointers still name the right rows (they were remapped), but their cached
# sort ids must follow
db.execute('UPDATE chat SET last_message_sort_id=(SELECT sort_id FROM message WHERE _id=chat.last_message_row_id) '
           'WHERE last_message_row_id IS NOT NULL')
db.execute('UPDATE chat SET display_message_sort_id=(SELECT sort_id FROM message WHERE _id=chat.display_message_row_id) '
           'WHERE display_message_row_id IS NOT NULL')
db.execute('UPDATE chat SET last_read_message_sort_id=(SELECT sort_id FROM message WHERE _id=chat.last_read_message_row_id) '
           'WHERE last_read_message_row_id IS NOT NULL')
db.execute('UPDATE chat SET last_read_receipt_sent_message_sort_id=(SELECT sort_id FROM message WHERE _id=chat.last_read_receipt_sent_message_row_id) '
           'WHERE last_read_receipt_sent_message_row_id IS NOT NULL')
db.commit()

# --- validation --------------------------------------------------------------
print('\n=== validation ===')
print('  integrity_check   :', db.execute('PRAGMA integrity_check').fetchone()[0])
print('  foreign_key_check :', len(db.execute('PRAGMA foreign_key_check').fetchall()))
n_after = db.execute('SELECT COUNT(*) FROM message').fetchone()[0]
print(f'  messages          : {n_before} -> {n_after}')

lost = [k for k, v in before_counts.items()
        if db.execute(f'SELECT COUNT(*) FROM "{k.split(".")[0]}" WHERE "{k.split(".")[1]}">0'
                      ).fetchone()[0] != v]
print(f'  reference columns with changed row counts: {len(lost)} {lost[:5]}')

orphans = 0
for t, c in refs:
    orphans += db.execute(
        f'SELECT COUNT(*) FROM "{t}" LEFT JOIN message m ON "{t}"."{c}"=m._id '
        f'WHERE "{t}"."{c}">0 AND m._id IS NULL').fetchone()[0]
print(f'  dangling references: {orphans}')

mx_id, mx_sort = db.execute('SELECT MAX(_id), MAX(sort_id) FROM message').fetchone()
print(f'  max _id / max sort_id: {mx_id} / {mx_sort}  (must be equal)')
print('  rows where sort_id<>_id:', db.execute(
    'SELECT COUNT(*) FROM message WHERE sort_id<>_id').fetchone()[0])

seq = db.execute('SELECT _id,timestamp FROM message WHERE timestamp>? ORDER BY _id',
                 (SEAM_MS,)).fetchall()
bad = sum(1 for i in range(1, len(seq)) if seq[i][1] < seq[i - 1][1])
print(f'  post-seam chronological violations by _id: {bad}')
print(f'  a new message would get _id {mx_id+1} -> lands after everything: '
      f'{mx_id+1 > mx_sort}')
db.close()
