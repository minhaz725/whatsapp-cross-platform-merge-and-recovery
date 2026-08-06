import sqlite3, shutil, os

# repairs the database already on the phone rather than replacing it. the import
# worked -- every message is present. two things are wrong with it:
#   1. whatsapp renders a thread by sort_id, not timestamp. rows revived in place
#      kept low sort_ids while fresh inserts got high ones, so 31 jul - 6 aug sits
#      above 23 jun - 30 jul instead of after it.
#   2. groups the user had left were bumped to the top of the chat list by
#      membership system events.

SRC = 'LIVE.db'
OUT = 'FIXED.db'
SEAM_MS = 1782198302000          # 2026-06-23 13:05:02 Dhaka
SYSTEM_TYPE = 7

if os.path.exists(OUT):
    os.remove(OUT)
shutil.copy(SRC, OUT)
db = sqlite3.connect(OUT)

before = db.execute('SELECT COUNT(*) FROM message').fetchone()[0]

# --- 1. renumber the post-seam block chronologically -------------------------
pre_max = db.execute('SELECT COALESCE(MAX(sort_id),0) FROM message WHERE timestamp<=?',
                     (SEAM_MS,)).fetchone()[0]
start = pre_max + 1000
ids = db.execute('SELECT _id FROM message WHERE timestamp>? ORDER BY timestamp, _id',
                 (SEAM_MS,)).fetchall()
for n, (mid,) in enumerate(ids):
    db.execute('UPDATE message SET sort_id=? WHERE _id=?', (start + n, mid))
print(f'renumbered {len(ids)} post-seam rows, starting at {start} (pre-seam max {pre_max})')

# --- 2. repoint each affected chat at its newest *real* message --------------
# every visible chat, not just the ones the merge touched -- restore-time sync
# events landed in groups we never grafted into.
chats = [r[0] for r in db.execute(
    'SELECT _id FROM chat WHERE COALESCE(hidden,0)=0')]
repointed = unbumped = 0
sunk = 0
for c in chats:
    # a chat's list position must follow its newest message with real content.
    # restore-time group sync writes empty type-7 events into every group at the
    # same second, which otherwise pins them all to the top of the list.
    row = db.execute(
        'SELECT _id,sort_id,timestamp FROM message WHERE chat_row_id=? AND message_type<>? '
        'AND text_data IS NOT NULL AND text_data<>"" '
        'ORDER BY timestamp DESC,_id DESC LIMIT 1', (c, SYSTEM_TYPE)).fetchone()
    if not row:
        # nothing but system noise ever landed here -- sink it to its creation date
        created = db.execute('SELECT created_timestamp FROM chat WHERE _id=?', (c,)).fetchone()[0]
        if created:
            db.execute('UPDATE chat SET sort_timestamp=? WHERE _id=?', (created, c))
            sunk += 1
        continue
    newest_any = db.execute(
        'SELECT timestamp FROM message WHERE chat_row_id=? ORDER BY timestamp DESC LIMIT 1',
        (c,)).fetchone()[0]
    if newest_any > row[2]:
        unbumped += 1
    db.execute('UPDATE chat SET last_message_row_id=?, display_message_row_id=?, '
               'last_message_sort_id=?, display_message_sort_id=?, sort_timestamp=? '
               'WHERE _id=?', (row[0], row[0], row[1], row[1], row[2], c))
    repointed += 1
print(f'repointed {repointed} chats; {unbumped} not surfaced by a system event; '
      f'{sunk} content-free chats sunk to their creation date')

db.commit()

print('\n=== validation ===')
print('  integrity_check  :', db.execute('PRAGMA integrity_check').fetchone()[0])
print('  foreign_key_check:', len(db.execute('PRAGMA foreign_key_check').fetchall()), 'violations')
after = db.execute('SELECT COUNT(*) FROM message').fetchone()[0]
print(f'  messages         : {before} -> {after} (must be unchanged)')

rows = db.execute('SELECT sort_id,timestamp FROM message WHERE timestamp>? ORDER BY sort_id',
                  (SEAM_MS,)).fetchall()
bad = sum(1 for i in range(1, len(rows)) if rows[i][1] < rows[i - 1][1])
print(f'  ordering         : {len(rows)} post-seam rows, {bad} violations')

dupe = db.execute('SELECT COUNT(*) FROM (SELECT sort_id FROM message WHERE timestamp>? '
                  'GROUP BY sort_id HAVING COUNT(*)>1)', (SEAM_MS,)).fetchone()[0]
print(f'  duplicate sort_id: {dupe}')
db.close()
