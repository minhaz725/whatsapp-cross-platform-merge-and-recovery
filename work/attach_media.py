import sqlite3, shutil, os, hashlib, base64, struct, subprocess

from collections import Counter

# point this at the copied iOS container directory before running.
# never run against ~/Library directly; work on a copy.
IOS_CONTAINER = os.environ.get('WA_IOS_CONTAINER', '<IOS_CONTAINER>')

# attaches the real media files to the grafted messages. the merge carried media
# across as text lines because message_media has 23 columns whatsapp never leaves
# null; here we fill them from whatsapp's own rows plus values computed from the
# actual files, and copy the files into the phone's media tree.

C = IOS_CONTAINER
SRC, OUT = 'FIXED.db', 'MEDIA.db'
STAGE = 'media_out/com.whatsapp/WhatsApp/Media'
SEAM_APPLE = 803891102

# ios type -> (android type, folder, prefix, mime default)
KIND = {
    1: (1, 'WhatsApp Images',      'IMG', 'image/jpeg'),
    2: (3, 'WhatsApp Video',       'VID', 'video/mp4'),
    3: (2, 'WhatsApp Voice Notes', 'PTT', 'audio/ogg; codecs=opus'),
    8: (9, 'WhatsApp Documents',   'DOC', 'application/pdf'),
}
MIME_BY_EXT = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.webp': 'image/webp', '.heic': 'image/heic', '.mp4': 'video/mp4',
    '.opus': 'audio/ogg; codecs=opus', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4',
    '.pdf': 'application/pdf', '.zip': 'application/zip',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}

def jpeg_size(path):
    # walk the segment chain to the SOF marker; avoids a subprocess per file
    try:
        with open(path, 'rb') as f:
            if f.read(2) != b'\xff\xd8':
                return None
            while True:
                b = f.read(1)
                while b and b != b'\xff':
                    b = f.read(1)
                m = f.read(1)
                while m == b'\xff':
                    m = f.read(1)
                if not m:
                    return None
                if m[0] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    f.read(3)
                    h, w = struct.unpack('>HH', f.read(4))
                    return w, h
                ln = struct.unpack('>H', f.read(2))[0]
                f.seek(ln - 2, 1)
    except Exception:
        return None

def mdls_dims(path):
    # ffprobe reads the container directly. mdls was tried first but depends on
    # spotlight having indexed the freshly-copied file, which it usually has not.
    try:
        o = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries',
             'stream=width,height:format=duration', '-of', 'default=nw=1', path],
            capture_output=True, text=True, timeout=30).stdout
        w = h = 0
        dur = 0.0
        for line in o.splitlines():
            k, _, v = line.partition('=')
            if not v or v == 'N/A':
                continue
            if k == 'width' and not w:
                w = int(float(v))
            elif k == 'height' and not h:
                h = int(float(v))
            elif k == 'duration':
                dur = float(v)
        return w, h, int(round(dur))
    except Exception:
        return (0, 0, 0)

def b64sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()

def modal_template(db, table, where, skip):
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

def main():
    for p in (OUT,):
        if os.path.exists(p):
            os.remove(p)
    if os.path.exists('media_out'):
        shutil.rmtree('media_out')
    shutil.copy(SRC, OUT)

    db = sqlite3.connect(OUT)
    mac = sqlite3.connect(f'file:{C}/ChatStorage.sqlite?mode=ro', uri=True)

    tpl = modal_template(
        db, 'message_media', 'file_path IS NOT NULL',
        skip={'message_row_id', 'chat_row_id', 'file_path', 'file_size', 'mime_type',
              'width', 'height', 'file_hash', 'media_name', 'media_caption',
              'media_duration', 'enc_file_hash', 'media_key', 'direct_path',
              'file_length', 'page_count'})

    by_key = {r[0]: (r[1], r[2], r[3]) for r in db.execute(
        'SELECT key_id,_id,chat_row_id,message_type FROM message')}

    rows = mac.execute("""
        SELECT m.ZSTANZAID, m.ZMESSAGETYPE, m.ZISFROMME, m.ZMESSAGEDATE, m.ZTEXT,
               mi.ZMEDIALOCALPATH, mi.ZTITLE
        FROM ZWAMESSAGE m JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK
        WHERE m.ZMESSAGEDATE > ? AND mi.ZMEDIALOCALPATH IS NOT NULL
              AND m.ZSTANZAID IS NOT NULL
        ORDER BY m.ZMESSAGEDATE
    """, (SEAM_APPLE,)).fetchall()

    st = Counter()
    seq = Counter()
    for key_id, ztype, from_me, zdate, ztext, relpath, title in rows:
        if ztype not in KIND:
            st['unsupported_type'] += 1
            continue
        hit = by_key.get(key_id)
        if not hit:
            st['no_matching_message'] += 1
            continue
        mid, chat_row, _ = hit
        src = os.path.join(C, 'Message', relpath)
        if not os.path.exists(src):
            st['file_missing'] += 1
            continue

        atype, folder, prefix, default_mime = KIND[ztype]
        ext = os.path.splitext(relpath)[1].lower() or '.dat'
        mime = MIME_BY_EXT.get(ext, default_mime)
        day = __import__('datetime').datetime.utcfromtimestamp(
            zdate + 978307200 + 21600).strftime('%Y%m%d')
        seq[(prefix, day)] += 1
        name = f'{prefix}-{day}-WA{seq[(prefix, day)]:04d}{ext}'
        sub = f'{folder}/Sent' if from_me else folder
        rel = f'Media/{sub}/{name}'

        dst = os.path.join(STAGE, sub, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

        size = os.path.getsize(dst)
        w = h = dur = 0
        if mime.startswith('image/'):
            wh = jpeg_size(dst)
            if wh:
                w, h = wh
            else:
                w, h, _ = mdls_dims(dst)
        elif mime.startswith('video/') or mime.startswith('audio/'):
            w, h, dur = mdls_dims(dst)

        digest = b64sha(dst)
        # media_key / enc_file_hash / direct_path are server-side artefacts we cannot
        # reproduce. they are never null in BASE, so they are filled with values of
        # the right shape; with transferred=1 and the file present locally whatsapp
        # renders from disk rather than re-fetching.
        fake_key = base64.b64encode(hashlib.sha256(('k' + key_id).encode()).digest()).decode()
        enc_hash = base64.b64encode(hashlib.sha256(('e' + digest).encode()).digest()).decode()

        db.execute('DELETE FROM message_media WHERE message_row_id=?', (mid,))
        row = dict(tpl)
        row.update({
            'message_row_id': mid, 'chat_row_id': chat_row, 'file_path': rel,
            'file_size': size, 'file_length': size, 'mime_type': mime,
            'width': w, 'height': h, 'media_duration': dur, 'file_hash': digest,
            'enc_file_hash': enc_hash, 'media_key': fake_key,
            'direct_path': f'/v/t62.7118-24/{digest[:22]}',
            'media_name': title if atype == 9 else None,
            'media_caption': ztext or None, 'transferred': 1,
            'page_count': 0,
        })
        cols = ','.join(f'"{c}"' for c in row)
        db.execute(f'INSERT INTO message_media({cols}) VALUES({",".join("?"*len(row))})',
                   list(row.values()))

        # restore the real type and strip the placeholder label we injected earlier
        db.execute('UPDATE message SET message_type=?, text_data=? WHERE _id=?',
                   (atype, ztext or None, mid))
        st['attached'] += 1
        st[f'type_{atype}'] += 1

    db.commit()
    print('=== media attach ===')
    for k, v in sorted(st.items()):
        print(f'  {k:22} {v}')

    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk('media_out') for f in fs)
    n = sum(len(fs) for _, _, fs in os.walk('media_out'))
    print(f'  staged files           {n}  ({total/1e6:.0f} MB)')

    print('\n=== validation ===')
    print('  integrity_check  :', db.execute('PRAGMA integrity_check').fetchone()[0])
    print('  foreign_key_check:', len(db.execute('PRAGMA foreign_key_check').fetchall()))
    print('  media rows w/o message:', db.execute(
        'SELECT COUNT(*) FROM message_media mm LEFT JOIN message m ON mm.message_row_id=m._id '
        'WHERE m._id IS NULL').fetchone()[0])
    print('  messages         :', db.execute('SELECT COUNT(*) FROM message').fetchone()[0])
    db.close()

main()
