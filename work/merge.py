import sqlite3, shutil, os

from collections import Counter

# point this at the copied iOS container directory before running.
# never run against ~/Library directly; work on a copy.
IOS_CONTAINER = os.environ.get('WA_IOS_CONTAINER', '<IOS_CONTAINER>')

MAC = f'{IOS_CONTAINER}/ChatStorage.sqlite'
BASE = 'BASE.db'
OUT = 'MERGED.db'

SEAM_APPLE = 803891102          # 2026-06-23 13:05:02 Dhaka, last real msg in BASE
APPLE_EPOCH = 978307200

# ios type -> android type. ios/android swap audio & video codes.
# type 6 omitted: raw json reaction/poll payloads that would render as garbage.
# type 10 omitted: call events; android reserves type 10 for 1:1 only and BASE
# never uses it on a group message, so it is not worth the risk for 78 rows.
TYPE_MAP = {0: 0, 7: 0, 1: 1, 2: 3, 3: 2, 8: 9, 42: 0, 41: 0, 43: 0}

# media arrives without files, and message_media has 23 columns whatsapp never
# leaves null. rather than invent values for all of them we render media as a
# text line. the real files live in ~/Desktop/WA-ARCHIVE-2026-08-06/all_media/.
MEDIA_LABEL = {1: '\U0001F4F7 photo', 3: '\U0001F3A5 video',
               2: '\U0001F3A4 voice message', 9: '\U0001F4C4 document'}

def modal_template(db, table, where, skip):
    # most common non-null value per column, over rows whatsapp itself wrote.
    # used as an insert template so our rows are structurally indistinguishable.
    cols = [r[1] for r in db.execute(f'PRAGMA table_info({table})')]
    tpl = {}
    for c in cols:
        if c in skip or c == '_id':
            continue
        vals = Counter(r[0] for r in db.execute(
            f'SELECT "{c}" FROM {table} WHERE {where} LIMIT 4000') if r[0] is not None)
        if vals:
            tpl[c] = vals.most_common(1)[0][0]
    return tpl

def insert_from(db, table, tpl, overrides):
    row = dict(tpl)
    row.update(overrides)
    cols = ','.join(f'"{c}"' for c in row)
    qs = ','.join('?' * len(row))
    return db.execute(f'INSERT INTO {table}({cols}) VALUES({qs})', list(row.values()))

def main():
    if os.path.exists(OUT):
        os.remove(OUT)
    shutil.copy(BASE, OUT)

    db = sqlite3.connect(OUT)
    mac = sqlite3.connect(f'file:{MAC}?mode=ro', uri=True)

    # templates derived from whatsapp's own rows
    chat_tpl = modal_template(
        db, 'chat',
        'jid_row_id IN (SELECT _id FROM jid WHERE server="lid") AND hidden=0',
        skip={'jid_row_id', 'subject', 'created_timestamp', 'sort_timestamp',
              'account_jid_row_id', 'display_message_row_id', 'last_message_row_id',
              'last_message_sort_id', 'display_message_sort_id'})
    msg_tpl_1v1 = modal_template(
        db, 'message', '(sender_jid_row_id=0 OR sender_jid_row_id IS NULL)',
        skip={'chat_row_id', 'key_id', 'timestamp', 'received_timestamp', 'text_data',
              'message_type', 'from_me', 'status', 'sort_id', 'sender_jid_row_id'})
    msg_tpl_grp = modal_template(
        db, 'message', 'sender_jid_row_id>0',
        skip={'chat_row_id', 'key_id', 'timestamp', 'received_timestamp', 'text_data',
              'message_type', 'from_me', 'status', 'sort_id', 'sender_jid_row_id'})

    jid_by_raw = {r[0]: r[1] for r in db.execute(
        'SELECT raw_string,_id FROM jid WHERE raw_string IS NOT NULL')}
    chat_by_jidrow = {r[0]: r[1] for r in db.execute(
        'SELECT jid_row_id,_id FROM chat WHERE jid_row_id IS NOT NULL')}
    existing = {r[0]: (r[1], r[2], r[3]) for r in db.execute(
        'SELECT key_id,_id,COALESCE(text_data,""),COALESCE(message_type,0) FROM message')}

    next_id = db.execute('SELECT COALESCE(MAX(_id),0)+1 FROM message').fetchone()[0]
    next_sort = max(next_id, db.execute(
        'SELECT COALESCE(MAX(sort_id),0)+1 FROM message').fetchone()[0])

    def get_jid_row(raw):
        if raw in jid_by_raw:
            return jid_by_raw[raw]
        user, _, server = raw.partition('@')
        # lid must be 18 -- type 0 means "phone number" and whatsapp will try to
        # parse the 15-digit lid as one.
        jtype = {'g.us': 1, 'broadcast': 5, 'lid': 18}.get(server, 0)
        cur = db.execute(
            'INSERT INTO jid(user,server,agent,type,raw_string,device) VALUES(?,?,0,?,?,0)',
            (user, server, jtype, raw))
        jid_by_raw[raw] = cur.lastrowid
        return cur.lastrowid

    def get_chat_row(raw, subject, created_ms):
        jrow = get_jid_row(raw)
        if jrow in chat_by_jidrow:
            return chat_by_jidrow[jrow]
        # account_jid_row_id is never null in a real chat; for group/broadcast/lid
        # chats it equals jid_row_id (2152/2152 in BASE).
        cur = insert_from(db, 'chat', chat_tpl, {
            'jid_row_id': jrow, 'subject': subject, 'created_timestamp': created_ms,
            'sort_timestamp': created_ms, 'account_jid_row_id': jrow, 'hidden': 0})
        chat_by_jidrow[jrow] = cur.lastrowid
        return cur.lastrowid

    rows = mac.execute("""
        SELECT m.ZSTANZAID, m.ZISFROMME, m.ZMESSAGEDATE, m.ZMESSAGETYPE, m.ZTEXT,
               m.ZGROUPEVENTTYPE, cs.ZCONTACTJID, cs.ZPARTNERNAME, gm.ZMEMBERJID, mi.ZTITLE
        FROM ZWAMESSAGE m
        JOIN ZWACHATSESSION cs ON m.ZCHATSESSION = cs.Z_PK
        LEFT JOIN ZWAGROUPMEMBER gm ON m.ZGROUPMEMBER = gm.Z_PK
        LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK
        WHERE m.ZMESSAGEDATE > ? AND m.ZSTANZAID IS NOT NULL
        ORDER BY m.ZMESSAGEDATE
    """, (SEAM_APPLE,)).fetchall()

    st = Counter()
    jid0, chat0 = len(jid_by_raw), len(chat_by_jidrow)
    touched = set()

    for (key_id, from_me, zdate, ztype, ztext, gevent,
         cjid, pname, memberjid, mtitle) in rows:

        ts = int((zdate + APPLE_EPOCH) * 1000)
        is_group = cjid.endswith('@g.us')
        chat_row = get_chat_row(cjid, pname if is_group else None, ts)
        touched.add(chat_row)

        # content type always wins. ZGROUPEVENTTYPE=2 sits on ordinary messages of
        # every kind, so it decides only the leftovers.
        if ztype in TYPE_MAP:
            atype = TYPE_MAP[ztype]
        elif gevent:
            atype = 7
        elif ztext:
            atype = 0
        else:
            st['skipped_unmappable'] += 1
            continue

        text = ztext or None
        if atype in MEDIA_LABEL:
            label = MEDIA_LABEL[atype]
            if atype == 9 and mtitle:
                label = f'\U0001F4C4 {mtitle}'
            text = f'{label}\n{text}' if text else label
            atype = 0                      # carried as text; no message_media row
            st['media_as_text'] += 1

        sender_row = get_jid_row(memberjid) if (is_group and not from_me and memberjid) else 0

        prev = existing.get(key_id)
        if prev:
            pid, ptext, ptype = prev
            husk = (not ptext) and (ptype == 11 or not ptype)
            if husk and text:
                db.execute('UPDATE message SET text_data=?, message_type=?, timestamp=? '
                           'WHERE _id=?', (text, atype, ts, pid))
                existing[key_id] = (pid, text, atype)
                st['revived'] += 1
            else:
                st['skipped_dup'] += 1
            continue

        tpl = msg_tpl_grp if sender_row else msg_tpl_1v1
        insert_from(db, 'message', tpl, {
            '_id': next_id, 'chat_row_id': chat_row, 'from_me': from_me,
            'key_id': key_id, 'sender_jid_row_id': sender_row,
            'status': 13 if from_me else 16, 'timestamp': ts,
            'received_timestamp': ts,
            'message_type': atype, 'text_data': text, 'sort_id': next_sort})

        existing[key_id] = (next_id, text or '', atype)
        next_id += 1
        next_sort += 1
        st['inserted'] += 1

    # whatsapp renders a thread ordered by sort_id, NOT by timestamp. revived rows
    # keep their old low sort_ids while fresh inserts get high ones, which pushed
    # 31 jul - 6 aug above the 23 jun - 30 jul block. renumber the whole post-seam
    # window chronologically. pre-seam rows are untouched: their max sort_id is
    # 67582, comfortably below where this block starts.
    seam_ms = (SEAM_APPLE + APPLE_EPOCH) * 1000
    start = db.execute('SELECT COALESCE(MAX(sort_id),0) FROM message WHERE timestamp<=?',
                       (seam_ms,)).fetchone()[0] + 1000
    ids = db.execute('SELECT _id FROM message WHERE timestamp>? ORDER BY timestamp, _id',
                     (seam_ms,)).fetchall()
    for n, (mid,) in enumerate(ids):
        db.execute('UPDATE message SET sort_id=? WHERE _id=?', (start + n, mid))
    st['resorted'] = len(ids)

    for c in touched:
        # a group the user left should not jump to the top of the list just because
        # a membership system event landed in it. only real content repositions a chat.
        if not db.execute('SELECT 1 FROM message WHERE chat_row_id=? AND timestamp>? '
                          'AND message_type<>7 LIMIT 1', (c, seam_ms)).fetchone():
            st['left_chat_not_bumped'] += 1
            continue
        r = db.execute('SELECT _id,sort_id,timestamp FROM message WHERE chat_row_id=? '
                       'ORDER BY timestamp DESC, _id DESC LIMIT 1', (c,)).fetchone()
        if r:
            db.execute('UPDATE chat SET last_message_row_id=?, display_message_row_id=?, '
                       'last_message_sort_id=?, display_message_sort_id=?, sort_timestamp=? '
                       'WHERE _id=?', (r[0], r[0], r[1], r[1], r[2], c))

    st['new_jids'] = len(jid_by_raw) - jid0
    st['new_chats'] = len(chat_by_jidrow) - chat0
    db.commit()

    print('=== merge stats ===')
    for k in ('inserted', 'revived', 'skipped_dup', 'skipped_unmappable',
              'media_as_text', 'new_jids', 'new_chats', 'resorted',
              'left_chat_not_bumped'):
        print(f'  {k:20} {st[k]}')
    print(f'  chats touched        {len(touched)}')

    print('\n=== validation ===')
    print('  integrity_check :', db.execute('PRAGMA integrity_check').fetchone()[0])
    print('  foreign_key_check:', len(db.execute('PRAGMA foreign_key_check').fetchall()), 'violations')
    print('  total messages  :', db.execute('SELECT COUNT(*) FROM message').fetchone()[0])
    db.close()

main()
