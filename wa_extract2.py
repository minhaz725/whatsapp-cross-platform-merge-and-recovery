#!/usr/bin/env python3
"""
wa_extract2.py — Extract chats and media from a macOS WhatsApp container.

Extended from wa_extract.py. The original assumed media is reachable from the
database via ZWAMEDIAITEM.ZMEDIALOCALPATH. On this container only 704 of 26,749
media rows carry that path, while 7,198 non-thumb media files sit on disk. The
remaining ~6,500 files are still attributable, because the folder they live in is
named after the chat JID. This version reconciles disk against the database and
files the unlinked remainder under the right conversation, clearly marked as
having approximate dates.

Requires only Python 3 (stdlib). No network, no third-party packages.

Usage:
  python3 wa_extract2.py \
    --container ~/Desktop/WA-coldcopy-2026-08-01-v2/group.net.whatsapp.WhatsApp.shared \
    --out ~/Desktop/WA-extracted

Point --container at a COLD COPY, never at the live ~/Library folder.
"""

import argparse
import csv
import hashlib
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

APPLE_EPOCH = 978307200  # 2001-01-01T00:00:00Z expressed in Unix seconds

# Approximate iOS/macOS WhatsApp message type codes. Codes drift between
# releases, so treat anything unmapped as "type:<n>" rather than trusting it.
TYPES = {
    0: "text", 1: "image", 2: "video", 3: "audio", 4: "contact",
    5: "location", 6: "call", 7: "link", 8: "document",
    11: "gif", 14: "deleted", 15: "sticker",
}

MEDIA_EXT = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif",
    ".mp4", ".mov", ".3gp", ".mkv", ".webm",
    ".opus", ".m4a", ".aac", ".mp3", ".ogg", ".wav",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".txt",
}

# derived artefacts, not real media
SKIP_EXT = {".thumb", ".mmsthumb", ".videothumb", ".favicon"}

# message types that should have carried a file
MEDIA_TYPES = {1, 2, 3, 8, 11, 15}

# a month needs at least this many messages to count as real coverage rather
# than a stray fragment resynced from the server
MONTH_FLOOR = 50

MAX_NAME = 80


def table_cols(con, table):
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def pick(available, *candidates):
    for c in candidates:
        if c in available:
            return c
    return None


def safe_name(name, fallback="Unknown"):
    name = (name or "").strip() or fallback
    name = re.sub(r"[/\\:\x00-\x1f]", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name or fallback)[:MAX_NAME]


# the zone every timestamp is rendered in; pinned explicitly so the archive
# does not shift when the machine travels
ARCHIVE_TZ = None


def to_dt(raw):
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    # some columns store Apple epoch seconds, a few store milliseconds
    if val > 1e11:
        val /= 1000.0
    try:
        return datetime.fromtimestamp(val + APPLE_EPOCH, tz=ARCHIVE_TZ)
    except (OverflowError, OSError, ValueError):
        return None


def mtime_dt(ts):
    return datetime.fromtimestamp(ts, tz=ARCHIVE_TZ)


def link_or_copy(src, dst):
    try:
        os.link(src, dst)
    except (OSError, NotImplementedError):
        shutil.copy2(src, dst)


def unique_path(directory, filename):
    dest = directory / filename
    if not dest.exists():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    n = 1
    while dest.exists():
        dest = directory / f"{stem}_{n}{suffix}"
        n += 1
    return dest


def prepare_db(src, workdir, label):
    """Copy the sqlite trio out so the WAL replays without touching the source."""
    src = Path(src)
    if not src.exists():
        return None
    workdir.mkdir(parents=True, exist_ok=True)
    dst = workdir / f"{label}.sqlite"
    for suffix in ("", "-wal", "-shm"):
        s = Path(str(src) + suffix)
        if s.exists():
            shutil.copy2(s, str(dst) + suffix)
    return dst


def open_db(path):
    con = sqlite3.connect(str(path))
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    return con


def jid_key(jid):
    """Normalise a JID for map lookups: strip the domain, keep the identifier."""
    if not jid:
        return None
    return str(jid).strip().lower()


def build_jid_map(con, contacts_con):
    """Map every JID we can name to a human-readable label.

    Media folders are named after the chat JID, so this is what lets unlinked
    files on disk be filed under the right conversation.
    """
    jmap = {}

    def put(jid, name):
        k = jid_key(jid)
        if not k or not name:
            return
        name = str(name).strip()
        if name and k not in jmap:
            jmap[k] = name

    ses = table_cols(con, "ZWACHATSESSION")
    if ses and "ZCONTACTJID" in ses:
        namecol = pick(ses, "ZPARTNERNAME")
        sel = f"ZCONTACTJID, {namecol}" if namecol else "ZCONTACTJID, NULL"
        for jid, name in con.execute(f"SELECT {sel} FROM ZWACHATSESSION"):
            put(jid, name)

    gmb = table_cols(con, "ZWAGROUPMEMBER")
    if gmb and "ZMEMBERJID" in gmb:
        namecol = pick(gmb, "ZCONTACTNAME", "ZFIRSTNAME")
        sel = f"ZMEMBERJID, {namecol}" if namecol else "ZMEMBERJID, NULL"
        for jid, name in con.execute(f"SELECT {sel} FROM ZWAGROUPMEMBER"):
            put(jid, name)

    push = table_cols(con, "ZWAPROFILEPUSHNAME")
    if push and {"ZJID", "ZPUSHNAME"} <= push:
        for jid, name in con.execute(
                "SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME"):
            put(jid, name)

    # address book carries ZLID, which resolves bare @lid media folders
    if contacts_con is not None:
        cols = table_cols(contacts_con, "ZWAADDRESSBOOKCONTACT")
        if cols:
            namecol = pick(cols, "ZFULLNAME", "ZHIGHLIGHTEDNAME", "ZGIVENNAME",
                           "ZBUSINESSNAME")
            for idcol in ("ZLID", "ZWHATSAPPID", "ZPHONENUMBER", "ZINTEROPJID"):
                if idcol in cols and namecol:
                    for jid, name in contacts_con.execute(
                            f"SELECT {idcol}, {namecol} FROM ZWAADDRESSBOOKCONTACT "
                            f"WHERE {idcol} IS NOT NULL"):
                        put(jid, name)
    return jmap


def resolve_folder(folder, jmap):
    """Folder names under Message/Media are chat JIDs. Return (label, matched)."""
    k = jid_key(folder)
    if k in jmap:
        return safe_name(jmap[k]), True
    # try the bare identifier against every known domain variant
    bare = k.split("@")[0] if k else ""
    for suffix in ("@s.whatsapp.net", "@lid", "@g.us", "@c.us", ""):
        cand = bare + suffix
        if cand in jmap:
            return safe_name(jmap[cand]), True
    return None, False


def build_query(con):
    msg = table_cols(con, "ZWAMESSAGE")
    if not msg:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        sys.exit("No ZWAMESSAGE table. Tables present:\n  " + "\n  ".join(tables))

    ses = table_cols(con, "ZWACHATSESSION")
    gmb = table_cols(con, "ZWAGROUPMEMBER")
    med = table_cols(con, "ZWAMEDIAITEM")

    date_col = pick(msg, "ZMESSAGEDATE", "ZSENTDATE") or "Z_PK"
    text_col = pick(msg, "ZTEXT") or "NULL"
    type_col = pick(msg, "ZMESSAGETYPE") or "NULL"
    fromme_col = pick(msg, "ZISFROMME") or "NULL"
    gm_fk = pick(msg, "ZGROUPMEMBER")
    mi_fk = pick(msg, "ZMEDIAITEM")
    fromjid_col = f"m.{pick(msg, 'ZFROMJID')}" if pick(msg, "ZFROMJID") else "NULL"
    pushname_col = f"m.{pick(msg, 'ZPUSHNAME')}" if pick(msg, "ZPUSHNAME") else "NULL"

    partner = f"s.{pick(ses, 'ZPARTNERNAME')}" if pick(ses, "ZPARTNERNAME") else "NULL"
    chat_jid = f"s.{pick(ses, 'ZCONTACTJID')}" if pick(ses, "ZCONTACTJID") else "NULL"
    gm_name = f"g.{pick(gmb, 'ZCONTACTNAME')}" if pick(gmb, "ZCONTACTNAME") else "NULL"
    gm_jid = f"g.{pick(gmb, 'ZMEMBERJID')}" if pick(gmb, "ZMEMBERJID") else "NULL"

    path_col = pick(med, "ZMEDIALOCALPATH", "ZLOCALPATH", "ZXMPPTHUMBPATH")
    media_path = f"m2.{path_col}" if path_col else "NULL"
    media_title = f"m2.{pick(med, 'ZTITLE')}" if pick(med, "ZTITLE") else "NULL"

    sql = f"""
        SELECT m.Z_PK,
               m.ZCHATSESSION,
               m.{fromme_col},
               m.{date_col},
               m.{text_col},
               m.{type_col},
               {partner},
               {chat_jid},
               {gm_name},
               {gm_jid},
               {media_path},
               {media_title},
               {fromjid_col},
               {pushname_col}
        FROM ZWAMESSAGE m
        LEFT JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
    """
    if gmb and gm_fk:
        sql += f"        LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.{gm_fk}\n"
    if med and mi_fk:
        sql += f"        LEFT JOIN ZWAMEDIAITEM m2 ON m2.Z_PK = m.{mi_fk}\n"
    elif med and "ZMESSAGE" in med:
        sql += "        LEFT JOIN ZWAMEDIAITEM m2 ON m2.ZMESSAGE = m.Z_PK\n"
    sql += f"        ORDER BY m.ZCHATSESSION, m.{date_col}"
    return sql


def chat_names_by_session(con, jmap):
    """Stable, collision-free display name per chat session."""
    names, used = {}, {}
    ses = table_cols(con, "ZWACHATSESSION")
    if not ses:
        return names
    namecol = pick(ses, "ZPARTNERNAME")
    jidcol = pick(ses, "ZCONTACTJID")
    sel = f"Z_PK, {namecol or 'NULL'}, {jidcol or 'NULL'}"
    for pk, partner, jid in con.execute(f"SELECT {sel} FROM ZWACHATSESSION"):
        base = safe_name(partner or jid, f"Chat_{pk}")
        if base in used:
            used[base] += 1
            base = f"{base} ({used[base]})"
        else:
            used[base] = 0
        names[pk] = base
    return names


def extract_calls(container, workdir, out):
    db = prepare_db(container / "CallHistory.sqlite", workdir, "CallHistory")
    if not db:
        return 0
    con = open_db(db)
    ev = table_cols(con, "ZWACDCALLEVENT")
    if not ev:
        con.close()
        return 0
    date_col = pick(ev, "ZDATE")
    dur_col = pick(ev, "ZDURATION")
    out_col = pick(ev, "ZOUTCOME")
    grp_col = pick(ev, "ZGROUPJIDSTRING")
    cid_col = pick(ev, "ZCALLIDSTRING")
    part = table_cols(con, "ZWACDCALLEVENTPARTICIPANT")
    pfk = pick(part, "Z1PARTICIPANTS", "ZCALLEVENT")

    participants = {}
    if part and pfk and "ZJIDSTRING" in part:
        for ev_pk, jid in con.execute(
                f"SELECT {pfk}, ZJIDSTRING FROM ZWACDCALLEVENTPARTICIPANT"):
            participants.setdefault(ev_pk, []).append(jid or "")

    sel = ", ".join([
        "Z_PK",
        date_col or "NULL", dur_col or "NULL", out_col or "NULL",
        grp_col or "NULL", cid_col or "NULL",
    ])
    rows = con.execute(f"SELECT {sel} FROM ZWACDCALLEVENT").fetchall()
    with (out / "calls.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "duration_seconds", "outcome", "group_jid",
                    "participants", "call_id"])
        for pk, raw_date, dur, outcome, gjid, cid in rows:
            dt = to_dt(raw_date)
            w.writerow([
                dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "",
                dur if dur is not None else "",
                outcome if outcome is not None else "",
                gjid or "",
                ";".join(participants.get(pk, [])),
                cid or "",
            ])
    con.close()
    return len(rows)


def extract_contacts(contacts_con, out):
    if contacts_con is None:
        return 0
    cols = table_cols(contacts_con, "ZWAADDRESSBOOKCONTACT")
    if not cols:
        return 0
    wanted = [
        ("full_name", pick(cols, "ZFULLNAME")),
        ("given_name", pick(cols, "ZGIVENNAME")),
        ("last_name", pick(cols, "ZLASTNAME")),
        ("business_name", pick(cols, "ZBUSINESSNAME")),
        ("phone", pick(cols, "ZPHONENUMBER")),
        ("whatsapp_id", pick(cols, "ZWHATSAPPID")),
        ("lid", pick(cols, "ZLID")),
        ("push_name", pick(cols, "ZABOUTTEXT")),
        ("username", pick(cols, "ZUSERNAME")),
    ]
    sel = ", ".join(c if c else "NULL" for _, c in wanted)
    rows = contacts_con.execute(
        f"SELECT {sel} FROM ZWAADDRESSBOOKCONTACT").fetchall()
    with (out / "contacts.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([label for label, _ in wanted])
        for r in rows:
            w.writerow(["" if v is None else v for v in r])
    return len(rows)


def write_manifest(out, stats):
    """Inventory with SHA-256. all_media/ is hardlinked, so it is listed but
    not re-hashed — the bytes are the same inodes as media/."""
    lines = []
    total_bytes = 0
    for root, dirs, files in os.walk(out):
        dirs.sort()
        rel_root = Path(root).relative_to(out)
        if rel_root.parts and rel_root.parts[0] in ("all_media", "_dbwork"):
            continue
        for fn in sorted(files):
            p = Path(root) / fn
            if fn == "MANIFEST.txt":
                continue
            try:
                size = p.stat().st_size
                h = hashlib.sha256()
                with p.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        h.update(chunk)
                lines.append(f"{h.hexdigest()}  {size:>12}  {p.relative_to(out)}")
                total_bytes += size
            except OSError as e:
                lines.append(f"{'ERROR':<64}  {'':>12}  {p.relative_to(out)}  ({e})")

    flat = out / "all_media"
    flat_n = len(list(flat.iterdir())) if flat.is_dir() else 0

    with (out / "MANIFEST.txt").open("w", encoding="utf-8") as fh:
        fh.write("WhatsApp archive manifest\n")
        fh.write(f"generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        fh.write("EXTRACTION SUMMARY\n")
        for k, v in stats.items():
            fh.write(f"  {k:<44}{v}\n")
        fh.write(f"\n  {'hashed files':<44}{len(lines)}\n")
        fh.write(f"  {'hashed bytes':<44}{total_bytes:,}\n")
        fh.write(f"  {'all_media entries (hardlinks, not re-hashed)':<44}{flat_n}\n")
        fh.write("\n\nSHA-256                                                           "
                 "        SIZE  PATH\n")
        fh.write("\n".join(lines))
        fh.write("\n")
    return len(lines), total_bytes


README = """WhatsApp archive — what is in here and what is not
===================================================

Created from the WhatsApp macOS app's local data on {date}.

ALL TIMES IN THIS ARCHIVE ARE {tz_label} ({tz_offset}).
Every timestamp in the transcripts, the CSVs and the media filenames is written
in that one timezone, no matter where the computer was when the archive was made
or where you are when you read it. If you compare this archive against WhatsApp
on a phone set to a different timezone, the clock times will differ by the
offset between the two zones. The underlying moments are the same.

WHAT EACH FOLDER HOLDS
----------------------
chats/
    One plain-text transcript per conversation, in time order.
    Format:  [YYYY-MM-DD HH:MM:SS] Sender: message
    Attachments appear inline as <image: filename>.

messages.csv
    Every message as one row: timestamp, chat, sender, direction (in/out),
    type, text, media_file. Open in Excel or Numbers to search and filter.

chats_summary.csv
    One row per conversation: message count, media count, first and last
    message date. Sorted by message count, busiest first.

media/<Chat Name>/
    Attachments that the database linked to a specific message.
    Filename starts with the real date the message was sent, and the file's
    modification date is set to match. These dates are EXACT.

media/<Chat Name>/_unlinked/
    Attachments found on disk in that conversation's folder, but with no
    surviving database record of which message they belong to.
    Their filenames carry "_approx" and the date shown is the date the Mac
    downloaded the file, NOT the date it was originally sent.
    These are real photos and videos from that conversation. Only the
    timestamp is uncertain.

media/_unmatched/
    Media files that could not be tied to any conversation at all.
    Nothing was thrown away; anything unattributable landed here.

all_media/
    Every attachment in one flat folder, named so it sorts chronologically.
    These are hardlinks, so this folder costs no extra disk space.

media_unlinked.csv
    Index of every file in the _unlinked folders, with its conversation.

calls.csv       Call history: time, duration, outcome, participants.
contacts.csv    Address book: names, phone numbers, WhatsApp IDs.
MANIFEST.txt    Every file with its size and SHA-256 checksum, so you can
                verify years from now that nothing has rotted or changed.

IMPORTANT LIMITATIONS — PLEASE READ
-----------------------------------
1. THIS IS NOT YOUR COMPLETE WHATSAPP HISTORY.
   WhatsApp caps a linked computer at ONE YEAR of chat history. The app's own
   Settings > Chat history screen states this. Nothing older than that window
   ever reached this Mac, and no amount of scrolling would have retrieved it.
   Continuous coverage here begins {history_start}. Before that there are only
   {pre_history} scattered messages spread over 2018-2025 - fragments, not a record.
   A phone backup (e.g. Google Drive) is NOT capped this way: it holds the
   phone's full local history. Keep any such backup as a separate,
   complementary archive covering the years this one cannot. Do not delete it.

2. THESE TRANSCRIPTS CANNOT BE PUT BACK INTO WHATSAPP.
   There is no way to import them as live chats. This is a readable and
   searchable archive for humans, nothing more.

3. THE TEXT GOES BACK FURTHER THAN THE PICTURES.
   Every media file on this Mac was downloaded after {media_start}.
   Nothing older exists here - not even a thumbnail. Older conversations
   still have their full text, but their photos, videos and voice notes
   appear in the transcripts as <image> / <video> placeholders with no file.
   {missing_media} media messages are placeholders of this kind.

4. MANY ATTACHMENT DATES ARE APPROXIMATE.
   {approx_count} media files sit in _unlinked folders. Their dates are the
   time this Mac downloaded them, not the time they were sent. Files in the
   main media/<Chat Name>/ folders have exact dates taken from the database.

5. DELETED MESSAGES ARE NOT RECOVERED.
   Only what still existed in the app's database was extracted.

VERIFYING THIS ARCHIVE LATER
----------------------------
    cd <this folder>
    shasum -a 256 -c <(awk 'NF==3 && length($1)==64 {{print $1"  "$3}}' MANIFEST.txt)
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", required=True,
                    help="path to group.net.whatsapp.WhatsApp.shared (use a cold copy)")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--no-media", action="store_true",
                    help="transcripts and CSVs only, skip copying media")
    ap.add_argument("--no-manifest", action="store_true",
                    help="skip the SHA-256 manifest pass")
    ap.add_argument("--timezone", default=None,
                    help="IANA zone to render every timestamp in, e.g. "
                         "Pacific/Auckland. Pin this so the archive does not "
                         "shift when the machine changes timezone. "
                         "Defaults to the machine's current zone.")
    args = ap.parse_args()

    global ARCHIVE_TZ
    if args.timezone:
        try:
            ARCHIVE_TZ = ZoneInfo(args.timezone)
        except Exception:
            sys.exit(f"Unknown timezone: {args.timezone}")
        tz_label = args.timezone
    else:
        ARCHIVE_TZ = datetime.now(timezone.utc).astimezone().tzinfo
        tz_label = str(Path("/etc/localtime").resolve()).split("zoneinfo/")[-1]

    container = Path(os.path.expanduser(args.container)).resolve()
    out = Path(os.path.expanduser(args.out)).resolve()
    if not container.is_dir():
        sys.exit(f"Not a directory: {container}")
    if "/Library/Group Containers" in str(container):
        sys.exit("Refusing to read the live container. Point --container at a cold copy.")

    out.mkdir(parents=True, exist_ok=True)
    (out / "chats").mkdir(exist_ok=True)
    media_root = out / "media"
    flat_root = out / "all_media"
    if not args.no_media:
        media_root.mkdir(exist_ok=True)
        flat_root.mkdir(exist_ok=True)

    workdir = out / "_dbwork"
    db = prepare_db(container / "ChatStorage.sqlite", workdir, "ChatStorage")
    if not db:
        sys.exit(f"ChatStorage.sqlite not found in {container}")
    con = open_db(db)

    contacts_db = prepare_db(container / "ContactsV2.sqlite", workdir, "ContactsV2")
    contacts_con = open_db(contacts_db) if contacts_db else None

    jmap = build_jid_map(con, contacts_con)
    session_names = chat_names_by_session(con, jmap)
    print(f"jid map entries    : {len(jmap)}")

    rows = con.execute(build_query(con)).fetchall()
    print(f"message rows       : {len(rows)}")

    chats = {}
    copied_paths = set()
    flat_names = set()
    media_copied = media_missing = 0
    month_counts = Counter()
    media_mtimes = []
    missing_media_msgs = 0

    def add_flat(dest):
        name = dest.name
        if name in flat_names:
            name = f"{dest.parent.name}_{name}"
        flat = unique_path(flat_root, name)
        link_or_copy(dest, flat)
        flat_names.add(flat.name)

    with (out / "messages.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "chat", "sender", "direction",
                         "type", "text", "media_file"])

        for (pk, sess, from_me, raw_date, text, mtype, partner, chat_jid,
             gm_name, gm_jid, mpath, mtitle, from_jid, push_name) in rows:

            dt = to_dt(raw_date)
            stamp = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""
            chat_name = session_names.get(sess) or safe_name(
                partner or chat_jid, f"Chat_{sess}")

            if from_me:
                sender = "Me"
            elif gm_name:
                sender = safe_name(gm_name, "Group member")
            elif gm_jid:
                looked_up = jmap.get(jid_key(gm_jid))
                sender = safe_name(looked_up or push_name or gm_jid, "Group member")
            elif from_jid:
                looked_up = jmap.get(jid_key(from_jid))
                sender = safe_name(looked_up or push_name or from_jid, chat_name)
            else:
                sender = chat_name

            kind = TYPES.get(mtype, f"type:{mtype}" if mtype is not None else "")
            body = (text or "").replace("\r\n", "\n").strip()

            entry = chats.setdefault(sess, {
                "name": chat_name, "lines": [], "count": 0,
                "first": stamp, "last": stamp, "media": 0, "approx": 0,
            })
            entry["count"] += 1
            if stamp:
                entry["first"] = min(entry["first"] or stamp, stamp)
                entry["last"] = max(entry["last"] or stamp, stamp)
                month_counts[stamp[:7]] += 1

            dest_rel = ""
            if mpath and not args.no_media:
                src = container / mpath
                if not src.exists():
                    src = container / "Message" / mpath
                if src.exists() and src.is_file():
                    chat_dir = media_root / chat_name
                    chat_dir.mkdir(parents=True, exist_ok=True)
                    prefix = dt.strftime("%Y-%m-%d_%H%M%S") if dt else f"nodate_{pk}"
                    base = safe_name(mtitle) if mtitle else src.name
                    if not Path(base).suffix:
                        base += src.suffix
                    dest = unique_path(chat_dir, f"{prefix}_{base}")
                    shutil.copy2(src, dest)
                    if dt:
                        ts = dt.timestamp()
                        os.utime(dest, (ts, ts))
                    add_flat(dest)
                    copied_paths.add(str(src.resolve()))
                    dest_rel = str(dest.relative_to(out))
                    entry["media"] += 1
                    media_copied += 1
                    if dt:
                        media_mtimes.append(dt.strftime("%Y-%m-%d"))
                else:
                    media_missing += 1

            # a media-type message whose file never reached this Mac shows up
            # in the transcript as a bare placeholder
            if mtype in MEDIA_TYPES and not dest_rel:
                missing_media_msgs += 1

            display = body if body else (f"<{kind}>" if kind else "")
            if dest_rel and body:
                display = f"{body} <{kind}: {Path(dest_rel).name}>"
            elif dest_rel:
                display = f"<{kind}: {Path(dest_rel).name}>"
            entry["lines"].append(f"[{stamp}] {sender}: {display}")

            writer.writerow([stamp, chat_name, sender,
                             "out" if from_me else "in", kind, body, dest_rel])

    # reconcile disk against the database: anything unreferenced is filed by
    # its parent folder, which is named after the chat JID
    orphans_attributed = orphans_unmatched = 0
    unresolved_folders = Counter()
    if not args.no_media:
        unlinked_rows = []
        media_dir = container / "Message" / "Media"
        for root, dirs, files in os.walk(media_dir):
            dirs.sort()
            rel = Path(root).relative_to(media_dir)
            folder = rel.parts[0] if rel.parts else ""
            for fn in sorted(files):
                src = Path(root) / fn
                suffix = src.suffix.lower()
                if suffix in SKIP_EXT or suffix not in MEDIA_EXT:
                    continue
                if str(src.resolve()) in copied_paths:
                    continue
                try:
                    mtime = src.stat().st_mtime
                except OSError:
                    continue
                stamp = mtime_dt(mtime).strftime("%Y-%m-%d_%H%M%S")
                label, matched = resolve_folder(folder, jmap)
                if matched:
                    chat_dir = media_root / label / "_unlinked"
                    orphans_attributed += 1
                else:
                    label = folder or "unknown"
                    chat_dir = media_root / "_unmatched" / safe_name(label)
                    unresolved_folders[folder] += 1
                    orphans_unmatched += 1
                chat_dir.mkdir(parents=True, exist_ok=True)
                dest = unique_path(chat_dir, f"{stamp}_approx_{fn}")
                shutil.copy2(src, dest)
                add_flat(dest)
                media_mtimes.append(stamp[:10])
                unlinked_rows.append([
                    mtime_dt(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    label, "yes" if matched else "no",
                    str(dest.relative_to(out)), src.stat().st_size,
                ])
                for e in chats.values():
                    if e["name"] == label:
                        e["approx"] += 1
                        break

        with (out / "media_unlinked.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["approx_timestamp", "chat", "chat_attributed",
                        "file", "size_bytes"])
            w.writerows(sorted(unlinked_rows))

    for entry in chats.values():
        path = unique_path(out / "chats", f"{entry['name']}.txt")
        path.write_text("\n".join(entry["lines"]), encoding="utf-8")

    with (out / "chats_summary.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["chat", "messages", "media_files_exact_date",
                    "media_files_approx_date", "first_message", "last_message"])
        for e in sorted(chats.values(), key=lambda x: -x["count"]):
            w.writerow([e["name"], e["count"], e["media"], e["approx"],
                        e["first"], e["last"]])

    n_calls = extract_calls(container, workdir, out)
    n_contacts = extract_contacts(contacts_con, out)

    dated = [e["first"] for e in chats.values() if e["first"]]
    earliest = min(dated) if dated else "unknown"
    latest = max((e["last"] for e in chats.values() if e["last"]), default="unknown")

    con.close()
    if contacts_con:
        contacts_con.close()
    shutil.rmtree(workdir, ignore_errors=True)

    # where does continuous coverage actually begin? the first month holding a
    # meaningful volume, not the oldest stray message
    busy = sorted(m for m, n in month_counts.items() if n >= MONTH_FLOOR)
    history_start = f"in {busy[0]}" if busy else "at an unclear point"
    pre_history = sum(n for m, n in month_counts.items() if busy and m < busy[0])

    media_dates = sorted(media_mtimes)
    media_start = media_dates[0] if media_dates else "an unknown date"

    stats = {
        "messages extracted": len(rows),
        "chats written": len(chats),
        "media copied with exact date": media_copied,
        "media copied with approximate date": orphans_attributed,
        "media unattributable": orphans_unmatched,
        "media referenced in DB but missing on disk": media_missing,
        "media messages with no file on this Mac": missing_media_msgs,
        "call records": n_calls,
        "contacts": n_contacts,
        "oldest message (single stray)": earliest,
        "continuous coverage begins": busy[0] if busy else "unclear",
        "messages before that point": pre_history,
        "oldest media file of any kind": media_start,
        "latest message": latest,
    }

    now = datetime.now(tz=ARCHIVE_TZ)
    stats["timestamps rendered in"] = f"{tz_label} ({now.strftime('%z')})"

    (out / "README.txt").write_text(README.format(
        date=now.strftime("%Y-%m-%d"),
        tz_label=tz_label,
        tz_offset=f"UTC{now.strftime('%z')[:3]}:{now.strftime('%z')[3:]}",
        history_start=history_start,
        pre_history=f"{pre_history:,}",
        media_start=media_start,
        missing_media=f"{missing_media_msgs:,}",
        approx_count=f"{orphans_attributed + orphans_unmatched:,}",
    ), encoding="utf-8")

    if not args.no_manifest:
        n_hashed, total_bytes = write_manifest(out, stats)
        stats["manifest files"] = n_hashed

    print()
    for k, v in stats.items():
        print(f"{k:<44}{v}")
    if unresolved_folders:
        print("\nfolders with no chat match:")
        for f, n in unresolved_folders.most_common():
            print(f"  {f:<40}{n} files")
    print(f"\noutput: {out}")


if __name__ == "__main__":
    main()
