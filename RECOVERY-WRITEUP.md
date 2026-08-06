# Cross-Platform WhatsApp History Reconstruction

Merging an iOS-schema companion database into an Android backup to recover six weeks
of chat history that existed in no backup anywhere.

**Completed 6 Aug 2026.** Final state: 63,801 messages across 3,044 chats,
1 Nov 2018 → 6 Aug 2026, running on a Samsung S25 Ultra with media attached.

---

## 1. Scenario

A Redmi Note 10 Pro Max was bricked and flashed on 31 July 2026, destroying all local
WhatsApp data. Two partial sources survived, and neither was complete:

| Source | Format | Coverage | Problem |
|---|---|---|---|
| Google Drive backup | Android `msgstore.db.crypt15` | Nov 2018 → **23 Jun 2026** | Stops 6 weeks early |
| MacBook (linked companion) | iOS `ChatStorage.sqlite` | **28 Aug 2025** → 6 Aug 2026 | Dies the moment any phone registers |

The gap between them — **23 June to 6 August 2026** — existed only on the Mac, in
Apple's schema, on a device that would be destroyed by the very act of restoring the
phone. Restoring from Drive alone meant permanently losing those six weeks.

Constraints that shaped everything:

- **One-way door.** Registering any phone kills the Mac companion instantly and
  permanently. Everything Mac-side had to be captured first.
- **Expensive iterations.** Each attempt costs a full uninstall, reinstall, and SMS
  re-registration. Rapid retries risk anti-abuse flags.
- **No off-the-shelf tool.** `natario1/whatsapp-database-merger` merges Android to
  Android. `whatsapp-chat-exporter` reads iOS but exports to HTML, not to a database.
  Nothing converts iOS `ChatStorage.sqlite` into Android `msgstore.db`.

### The prerequisite most guides omit

Local Android backups are encrypted with a key held in `/data/data/com.whatsapp/files/`,
which needs root. The workaround: **enable end-to-end encrypted backup and choose the
64-digit key rather than a password.** That is the only supported way to obtain a key
you actually hold. Without it, no decrypt/modify/re-encrypt cycle is possible at all.

---

## 2. Approach

```
Google Drive crypt15 ──wadecrypt──► BASE.db (Android schema)
                                        │
Mac ChatStorage.sqlite (iOS) ───────────┤ graft rows after the seam
                                        ▼
                                    MERGED.db
                                        │ attach media files
                                        ▼
                                    remap ids ──waencrypt──► msgstore.db.crypt15
```

**Find the seam empirically.** The last message in BASE carrying real text was
23 Jun 2026 13:05:02. The Mac holds the *same message* at the same second, so the two
sources abut exactly. Both dates offered by intuition were wrong: 10:59 was the last
message sent from the old phone, 13:18 was when the backup finished uploading. Neither
is the seam. **Query it; never infer it from a displayed backup date.**

**Graft only what postdates the seam.** 5,124 messages across 97 chats, not ten months
of overlap. This is the single decision that made the problem tractable — the ambiguous
window shrank to 472 messages, small enough to verify by eye.

**Build rows from templates, not by hand.** For every column, take the modal value
WhatsApp itself uses across comparable rows, then override only the fields that carry
meaning. New rows become structurally indistinguishable from WhatsApp's own.

**Verify by conformance, not just validity.** A separate checker compares every column
of every generated row against WhatsApp's distributions for comparable rows, flagging
both nulls where WhatsApp is never null and values WhatsApp never uses in that context.
This caught 53 defects that `PRAGMA integrity_check` reported as perfectly fine.

---

## 3. Mistakes

Every one of these shipped to a phone and had to be diagnosed from the failure.

### 3.1 Structural validity is not acceptability

The first merge passed `integrity_check`, `foreign_key_check`, and a message-count
reconciliation. **The import crashed at 70% and the app then crashed on launch.**

Valid SQLite is not the same thing as a database WhatsApp will accept. Three defects:

- **LID addresses typed as phone numbers.** 13 new contacts written with `jid.type = 0`,
  meaning "phone number". WhatsApp uses **18** for `@lid`. It was parsing a 15-digit
  internal identifier as a phone number.
- **`chat.account_jid_row_id` left NULL** on all 15 new chats. WhatsApp populates it in
  every one of its own 2,419 chats — zero exceptions. For group, broadcast and `@lid`
  chats it equals `jid_row_id` (2,152/2,152).
- **48 more NULL columns** WhatsApp never leaves NULL: 25 in `chat`, 23 in `message_media`.

The fix was structural, not a patch: template-derived rows plus the conformance checker.

### 3.2 iOS and Android swap audio and video type codes

iOS: `2 = video`, `3 = audio`. Android: `3 = video`, `2 = audio`. A direct copy silently
converts every video into an audio message. Nothing errors. You find out by looking.

### 3.3 `ZGROUPEVENTTYPE` is not a system-message flag

Value `2` appears on **ordinary messages of every kind**, including 2,820 plain text
messages. Checking it before the type map relabelled 704 media messages and thousands
of texts as group system notices. Content type must win; the flag decides only leftovers.

### 3.4 Reviving a placeholder in place does not work

WhatsApp had already written ~900 empty "Waiting for this message" rows for 31 Jul –
6 Aug. Filling them via `UPDATE` seemed elegant. It failed twice over:

- Four other tables (`message_details`, `receipt_device`, `receipt_user`,
  `reporting_info`) still marked those rows as undelivered.
- More fundamentally, see 3.5.

The user reported "31 July to 6 August missing". They were not missing. Every row
inserted fresh was visible; every row updated in place was not.

### 3.5 The root cause: `sort_id = _id`, always

**WhatsApp orders a thread by `sort_id`, not by timestamp — and sets `sort_id = _id`
for every message it creates.** Verified against 14 messages WhatsApp wrote itself
after a restore: all equal, no exceptions. In the original backup, 59,674 of 59,685
rows satisfy it.

So display order is really **row ID order**, and any row with a low ID but a late
timestamp sits in the wrong place permanently.

Revived rows kept BASE's low IDs with late timestamps. The first fix renumbered
`sort_id` chronologically — which repaired existing threads and looked like a complete
success, but pushed 1,011 rows *above* the highest row ID. Every newly arriving message
then got an ID below them and landed underneath, invisible at the bottom of the thread
while still appearing in the chat-list preview.

**Fixing an ordering symptom without restoring the underlying invariant produced a
subtler bug than the original.** The real fix: give the post-seam block IDs that
increase with time, above everything else, and set `sort_id = _id` everywhere. That
required rewriting **209 columns across the schema** that reference `message._id`,
discovered programmatically rather than by hand.

### 3.6 Two sequencing errors that cost attempts

- **Placing the file while WhatsApp was installed.** The live app rewrote
  `msgstore.db.crypt15` over the staged file; the restore used WhatsApp's copy. Proven
  by SHA-256: the file on the phone was byte-identical to the phone's own backup.
- **Asserting that uninstalling preserves `Android/media/com.whatsapp`.** It does not.
  This was inferred from matching hashes rather than tested, and it contradicted a
  written note that turned out to be correct. The folder must be recreated by hand
  after uninstalling — extracting a zip with the full path structure does this cleanly.

### 3.7 Media: 23 columns of unreproducible values

`message_media` has 23 columns WhatsApp never leaves NULL. Three — `media_key`,
`enc_file_hash`, `direct_path` — are server-side artefacts that cannot be reconstructed
from a local file.

First pass dropped media to text lines (📷 photo) to avoid the risk entirely. Second
pass attached them properly: files copied into WhatsApp's media tree under its own
naming convention, real sizes, MIME types, computed base64 SHA-256, and true dimensions
(JPEG headers for images, `ffprobe` for video and audio — `mdls` was tried first but
races Spotlight indexing on freshly-copied files). The three unreproducible fields got
correctly-shaped substitutes; with `transferred = 1` and the file present, WhatsApp
renders from disk and never consults them. **840 of 908 media attached and confirmed
opening on the phone.**

---

## 4. Result

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
- **68 media files** never downloaded to the Mac; no file exists anywhere. They remain
  as text lines.
- **86 messages** of unmappable type carrying no text.

### A pre-existing gap, not caused by this work

The Drive backup is missing **Aug 2023 → Jul 2025** — roughly 35 messages across 24
months, against 300–3,000 per month either side. The Mac cannot fill it; its real
coverage starts 28 Aug 2025.

Cause: the phone used in that period never had backup enabled. It broke in June 2025
and was replaced, with backups beginning August 2025. Those messages only ever existed
on that handset. The one remaining path is chip-level recovery from it, if it still
physically exists.

---

## 5. What would be done differently

1. **Write the conformance checker before the first merge, not after the first crash.**
   It found 53 defects in minutes that three validation passes had called clean.
2. **Never revive a row in place. Delete and re-insert.** A fresh row inherits correct
   ordering by construction; an updated row carries its original identity forever.
3. **Establish the ordering invariant first.** `sort_id = _id` was discoverable from the
   original backup in one query. Knowing it up front would have removed defects 3.4 and
   3.5 entirely, and both were the expensive ones.
4. **Test claims about platform behaviour; do not infer them from data.** The
   uninstall-deletes-media-folder question was answerable in one attempt and was instead
   guessed at, wrongly, from matching hashes.
5. **Do the file work with the app uninstalled.** A live app competes for its own
   filenames.

---

## 6. Tooling

```
wa-crypt-tools 0.1.0     wadecrypt / waencrypt / wacreatekey
Python 3.14 + sqlite3    merge, conformance, id remap
ffprobe                  video/audio dimensions and duration
rsync -aHE               container copy preserving mtimes (never Finder)
```

Scripts in `work/`:

| File | Role |
|---|---|
| `merge.py` | graft post-seam iOS rows into the Android backup |
| `conform.py` | compare generated rows against WhatsApp's own distributions |
| `fixsort.py` | chronological ordering and chat-list repair |
| `attach_media.py` | copy media into WhatsApp's tree and link it to messages |
| `fixids.py` | remap `message._id` chronologically across 209 referencing columns |

Known bug in `wa-crypt-tools 0.1.0`: `wacreatekey -o <path>` declares `-o` as
`argparse.FileType('wb')` then calls `Path()` on the handle. Omit `-o` and `cd` to the
target directory first.

`strftime('%s', …)` in SQLite returns **TEXT**. Comparing it against a numeric timestamp
column silently matches nothing and returns zero rows.
