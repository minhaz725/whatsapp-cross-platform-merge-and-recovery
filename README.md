# whatsapp-cross-platform-merge-and-recovery

Reconstructing a complete WhatsApp history by merging an **iOS-schema companion
database** into an **Android backup**, when neither source was complete on its own.

**Result:** 63,801 messages across 3,044 chats, 1 Nov 2018 → 6 Aug 2026, restored to an
Android phone with media attached. Recovered 4,116 messages and 840 media files that
existed in no backup anywhere.

- [The problem](#the-problem)
- [Why this needed building](#why-this-needed-building)
- [The prerequisite most guides skip](#the-prerequisite-most-guides-skip)
- [Approach](#approach)
- [Mistakes](#mistakes)
- [Result](#result)
- [What I would do differently](#what-i-would-do-differently)
- [Scripts](#scripts)
- [Tooling notes](#tooling-notes)
- [Warnings](#warnings)

---

## The problem

A phone was bricked and flashed on 31 July 2026, destroying all local WhatsApp data.
Two partial sources survived:

| Source | Format | Coverage | Catch |
|---|---|---|---|
| Google Drive backup | Android `msgstore.db.crypt15` | Nov 2018 → 23 Jun 2026 | Stops six weeks early |
| MacBook, linked device | iOS `ChatStorage.sqlite` | ~Aug 2025 → present | Dies the instant any phone registers |

The gap between them, **23 June to 6 August 2026**, existed in exactly one place: a
laptop, in Apple's schema, on a device that would be destroyed by the very act of
restoring the phone.

Two limits shaped everything:

- **A linked device carries only about a year of history.** Older chats appear in the
  sidebar but hold nothing. Measured directly: only 83 messages predated one year back,
  and every one was a chat-list preview stub rather than conversation.
- **Linked devices log out 14 days after the primary phone goes offline.** The phone
  died 31 July, so the companion had until roughly 14 August before signing itself out
  and taking those six weeks with it.

Other constraints:

- **One-way door.** Registering any phone kills the companion instantly and permanently.
  Everything on the Mac had to be captured first.
- **Expensive iterations.** Each attempt costs a full uninstall, reinstall and SMS
  re-registration. Rapid retries risk anti-abuse flags.

## Why this needed building

`natario1/whatsapp-database-merger` merges Android to Android. `whatsapp-chat-exporter`
reads iOS but exports to HTML, not to a database. **Nothing converts an iOS
`ChatStorage.sqlite` into an Android `msgstore.db`.**

## The prerequisite most guides skip

Local Android backups are encrypted with a key held in
`/data/data/com.whatsapp/files/`, which needs root. The way around it:

> **Turn on end-to-end encrypted backup and choose the 64-digit key, not a password.**

That is the only supported way to obtain a key you actually hold. Without it, no
decrypt / modify / re-encrypt cycle is possible at all.

## Approach

```
Drive crypt15 ──wadecrypt──► BASE.db (Android)
                                 │
iOS ChatStorage.sqlite ──────────┤  graft only what postdates the seam
                                 ▼
                             MERGED.db ──► attach media ──► remap ids
                                 │
                                 └──waencrypt──► msgstore.db.crypt15
```

Four ideas did most of the work.

**1. Find the seam empirically.** The last message in the backup carrying real text was
23 Jun 2026 13:05:02. The Mac held the *same message* at the same second, so the two
sources abut exactly. Both dates intuition offered were wrong: 10:59 was the last
message sent from the old phone, 13:18 was when the backup finished uploading. Neither
is the seam. Query it; never infer it from a displayed backup date.

**2. Graft only what postdates the seam.** 5,124 messages across 97 chats, not ten
months of overlap. This single decision made the problem tractable: the ambiguous window
shrank to 472 messages, small enough to verify by eye.

**3. Build rows from templates, not by hand.** For every column, take the modal value
WhatsApp itself uses across comparable rows, then override only the fields that carry
meaning. New rows become structurally indistinguishable from WhatsApp's own.

**4. Verify by conformance, not just validity.** `conform.py` compares every column of
every generated row against WhatsApp's distributions for comparable rows, flagging both
nulls where WhatsApp is never null and values WhatsApp never uses in that context. It
caught 53 defects that `PRAGMA integrity_check` reported as perfectly fine.

## Mistakes

Every one of these shipped to a phone and had to be diagnosed from the failure.

### Structural validity is not acceptability

The first merge passed `integrity_check`, `foreign_key_check` and a message-count
reconciliation. **The import crashed at 70%, and the app then crashed on launch.**

Valid SQLite is not the same thing as a database WhatsApp will accept. Three defects:

- **LID addresses typed as phone numbers.** 13 new contacts written with `jid.type = 0`,
  meaning "phone number". WhatsApp uses **18** for `@lid`. It was trying to parse a
  15-digit internal identifier as a phone number.
- **`chat.account_jid_row_id` left NULL** on all 15 new chats. WhatsApp populates it in
  every one of its own 2,419 chats, zero exceptions. For group, broadcast and `@lid`
  chats it equals `jid_row_id` (2,152/2,152).
- **48 more NULL columns** WhatsApp never leaves NULL: 25 in `chat`, 23 in
  `message_media`.

The fix was structural rather than a patch: template-derived rows plus the conformance
checker.

### iOS and Android swap audio and video type codes

iOS `2 = video`, `3 = audio`. Android `3 = video`, `2 = audio`. A direct copy silently
converts every video into an audio message. Nothing errors. You find out by looking.

### `ZGROUPEVENTTYPE` is not a system-message flag

Value `2` appears on **ordinary messages of every kind**, including 2,820 plain text
messages. Checking it before the type map relabelled 704 media messages and thousands of
texts as group system notices. Content type must win; the flag decides only leftovers.

### Reviving a placeholder in place does not work

WhatsApp had already written ~900 empty "Waiting for this message" rows for 31 Jul to
6 Aug. Filling them via `UPDATE` seemed elegant. It failed twice over: four other tables
(`message_details`, `receipt_device`, `receipt_user`, `reporting_info`) still marked
those rows undelivered, and more fundamentally, see the next item.

Reported symptom was "31 July to 6 August missing". They were not missing. Every row
inserted fresh was visible; every row updated in place was not.

### The root cause: `sort_id = _id`, always

**WhatsApp orders a thread by `sort_id`, not by timestamp, and sets `sort_id = _id` for
every message it creates.** Verified against 14 messages WhatsApp wrote itself after a
restore: all equal, no exceptions. In the original backup, 59,674 of 59,685 rows satisfy
it.

So display order is really **row ID order**, and any row with a low ID but a late
timestamp sits in the wrong place permanently.

Revived rows kept the backup's low IDs with late timestamps. The first fix renumbered
`sort_id` chronologically, which repaired existing threads and looked like a complete
success, but pushed 1,011 rows *above* the highest row ID. Every newly arriving message
then got an ID below them and landed underneath: invisible at the bottom of the thread,
while still appearing in the chat-list preview.

**Fixing an ordering symptom without restoring the underlying invariant produced a
subtler bug than the original.** The real fix: give the post-seam block IDs that increase
with time, above everything else, and set `sort_id = _id` everywhere. That required
rewriting **209 columns across the schema** that reference `message._id`, discovered
programmatically rather than by hand.

### Two sequencing errors that cost attempts

- **Placing the file while WhatsApp was installed.** The live app rewrote
  `msgstore.db.crypt15` over the staged file and the restore used WhatsApp's copy.
  Proven by SHA-256: the file on the phone was byte-identical to the phone's own backup.
- **Asserting that uninstalling preserves `Android/media/com.whatsapp`.** It does not.
  This was inferred from matching hashes rather than tested, and it contradicted a
  written note that turned out to be correct.

### Media: 23 columns of unreproducible values

`message_media` has 23 columns WhatsApp never leaves NULL. Three of them
(`media_key`, `enc_file_hash`, `direct_path`) are server-side artefacts that cannot be
reconstructed from a local file.

The first pass dropped media to text lines to avoid the risk entirely. The second
attached them properly: files copied into WhatsApp's media tree under its own naming
convention, real sizes, MIME types, computed base64 SHA-256, and true dimensions (JPEG
headers for images, `ffprobe` for video and audio; `mdls` was tried first but races
Spotlight indexing on freshly copied files). The three unreproducible fields got
correctly shaped substitutes; with `transferred = 1` and the file present, WhatsApp
renders from disk and never consults them. **840 of 908 media attached and confirmed
opening on the phone.**

## Result

**63,801 messages across 3,044 chats, 1 Nov 2018 → 6 Aug 2026.** Verified on the device:
threads in correct order, newly arriving messages landing at the bottom, media opening,
old chats intact.

| Year | Messages |
|---|---|
| 2018 | 2 |
| 2019 | 11,848 |
| 2020 | 6,930 |
| 2021 | 1,780 |
| 2022 | 5,799 |
| 2023 | 3,908 |
| 2024 | 24 |
| 2025 | 11,081 |
| 2026 | 22,428 |

Against Drive alone (59,685 messages ending 23 June), the merge recovered **4,116
messages plus 840 media files** across the six-week window that existed nowhere else.
July 2026 went from 69 messages to 3,273.

### Known losses

- **Reply/quote threading.** `ZPARENTMESSAGE` is NULL across every row in the iOS
  database. Not recoverable.
- **Reactions and edits.** No such columns exist in the Mac schema. Planned as zero.
- **68 media files** never downloaded to the Mac; no file exists anywhere.
- **86 messages** of unmappable type carrying no text.

### A pre-existing gap, not caused by this work

The Drive backup is missing **Aug 2023 to Jul 2025**, roughly 35 messages across 24
months against 300 to 3,000 per month either side. The Mac cannot fill it; its real
coverage starts Aug 2025.

Cause: the phone used in that period never had backup enabled. It broke in June 2025 and
was replaced, with backups beginning August 2025. Those messages only ever existed on
that handset. The one remaining path is chip-level recovery from it, if it still
physically exists.

**Check whether your backups actually cover what you think they cover.**

## What I would do differently

1. **Write the conformance checker before the first merge, not after the first crash.**
   It found 53 defects in minutes that three validation passes had called clean.
2. **Never revive a row in place. Delete and re-insert.** A fresh row inherits correct
   ordering by construction; an updated row carries its original identity forever.
3. **Establish the ordering invariant first.** `sort_id = _id` was discoverable from the
   original backup in one query. Knowing it up front would have removed the two most
   expensive bugs entirely.
4. **Test claims about platform behaviour; do not infer them from data.** The
   uninstall-deletes-media-folder question was answerable in one attempt and was instead
   guessed at, wrongly.
5. **Do the file work with the app uninstalled.** A live app competes for its own
   filenames.

## Scripts

| File | Role |
|---|---|
| `work/merge.py` | graft post-seam iOS rows into the Android backup |
| `work/conform.py` | compare generated rows against WhatsApp's own distributions |
| `work/fixsort.py` | chronological ordering and chat-list repair |
| `work/attach_media.py` | copy media into WhatsApp's tree and link it to messages |
| `work/fixids.py` | remap `message._id` chronologically across 209 referencing columns; optional third argument = seam in ms |
| `work/jidcheck.py`, `work/lidmap.py` | diagnostics for matching contacts across schemas |
| `work/wa_extract2.py` | render an iOS container to readable transcripts, CSVs and media |

Point them at a **copy** of the iOS container:

```bash
export WA_IOS_CONTAINER=/path/to/group.net.whatsapp.WhatsApp.shared
python3 work/merge.py
python3 work/conform.py
```

## Tooling notes

```
wa-crypt-tools 0.1.0     wadecrypt / waencrypt / wacreatekey
Python 3.14 + sqlite3    merge, conformance, id remap
ffprobe                  video and audio dimensions and duration
rsync -aHE               container copy preserving mtimes (never Finder)
```

Typical cycle:

```bash
wacreatekey -y --hex <64-hex-digits>            # writes ./encrypted_backup.key
wadecrypt encrypted_backup.key msgstore.db.crypt15 msgstore.db
waencrypt --jid <last 2 digits of your number> \
    --reference msgstore.db.crypt15 encrypted_backup.key merged.db out.crypt15
```

Gotchas that cost real time:

- **`wacreatekey -o <path>` is broken in 0.1.0.** argparse declares `-o` as
  `FileType('wb')`, then `wacreatekey.py:86` calls `Path()` on the open handle.
  Unconditional bug, not Python-version specific. **Omit `-o` and `cd` to the target
  directory first.**
- `wainfo` takes only the encrypted file: `wainfo [-k] <encrypted>`.
- `waencrypt` self-reports as beta. Always pass `--reference` against a real crypt15
  from the same device.
- **SQLite `strftime('%s', …)` returns TEXT.** Comparing it against a numeric timestamp
  column silently matches nothing and returns zero rows.
- Apple epoch offset: `unix = ZMESSAGEDATE + 978307200`.

## Warnings

- **Never write to the live container.** Copy it out with `rsync -aHE` and work on the
  copy. Finder silently destroys every modification time.
- **Registering a phone kills a linked device instantly.** Capture everything first.
- **Restore only happens on first run after a fresh install.** One shot per install.
- **Do the file work with WhatsApp uninstalled.** A live app rewrites its own backup
  filenames and will win the race.
- Uninstalling deletes `Android/media/com.whatsapp`. Recreate the folder structure by
  hand afterwards, or extract an archive containing the full path.
- **Never tap "Delete backup" in Google Drive** if it is your only generation.

## Moving the result to an iPhone

Apple's Move to iOS app also depends on `sort_id = _id`. Run `work/fixids.py` before you
export, with your own seam: `python3 work/fixids.py IN.db OUT.db SEAM_MS`.

- **The export stops at the row with the highest `sort_id`.** WhatsApp walks `_id` from 1
  up to the `_id` of that row. A row above it is not exported, and nothing tells you.
- **The iPhone orders each chat by the Android `_id`.** The chat list uses the timestamp,
  so the preview looks right, but rows with a high `_id` show at the bottom of the chat.

Both checks must return 0 before the export:

```sql
-- rows the export would skip
SELECT count(*) FROM message
WHERE _id > (SELECT _id FROM available_message_view ORDER BY sort_id DESC LIMIT 1);

-- places where _id order and time order differ inside a chat
SELECT count(*) FROM (
  SELECT timestamp t,
         lag(timestamp) OVER (PARTITION BY chat_row_id ORDER BY _id) p
  FROM message
) WHERE t < p;
```

The second check counts every chat. If rows before your seam were already out of order,
it stays above 0 after `fixids.py`; only the rows after the seam are renumbered.

Push names, view-once media, calls, polls and most system messages do not transfer.

## Not included

No message data, no media, no encryption key. The scripts are here; the contents of one
person's chat history are not.

## Credits

Built with [Claude Code](https://claude.com/claude-code), starting in plan mode: the
irreversible steps were identified and every input captured before anything was
registered or deleted.
