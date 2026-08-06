# whatsapp-cross-platform-merge-and-recovery

Reconstructing a complete WhatsApp history by merging an **iOS-schema companion
database** into an **Android backup**, when neither source was complete on its own.

**Result:** 63,801 messages across 3,044 chats, 1 Nov 2018 → 6 Aug 2026, restored to an
Android phone with media attached. Recovered 4,116 messages and 840 media files that
existed in no backup anywhere.

Written up in [`RECOVERY-WRITEUP.md`](RECOVERY-WRITEUP.md), including every bug and how
each was diagnosed.

---

## The problem

A phone was bricked, destroying all local WhatsApp data. Two partial sources survived:

| Source | Format | Coverage | Catch |
|---|---|---|---|
| Google Drive backup | Android `msgstore.db.crypt15` | Nov 2018 → 23 Jun 2026 | Stops six weeks early |
| MacBook, linked device | iOS `ChatStorage.sqlite` | ~Aug 2025 → present | Dies the instant any phone registers |

Two limits shaped the whole exercise:

- **A linked device carries only about a year of history.** Older chats appear in the
  sidebar but hold nothing. Measured directly: real coverage began ~12 months back.
- **Linked devices log out 14 days after the primary phone goes offline.** The phone
  died 31 July, so the companion had until roughly 14 August before signing itself out
  and taking the unique six weeks with it.

And registering any phone kills the companion instantly and permanently, so restoring
the history would destroy the only copy of the missing part.

## Why this needed building

`natario1/whatsapp-database-merger` merges Android to Android. `whatsapp-chat-exporter`
reads iOS but exports to HTML, not to a database. **Nothing converts an iOS
`ChatStorage.sqlite` into an Android `msgstore.db`.**

## The prerequisite most guides skip

Local Android backups are encrypted with a key in `/data/data/com.whatsapp/files/`,
which requires root. The way around it is to **turn on end-to-end encrypted backup and
choose the 64-digit key, not a password.** That is the only supported way to hold a key
you can actually use. Without it, no decrypt/modify/re-encrypt cycle is possible.

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

Four ideas did most of the work:

1. **Find the seam empirically.** Query the last message carrying real text. Do not infer
   it from the backup's displayed date; both intuitive answers were wrong.
2. **Graft only what postdates the seam.** Six weeks, not ten months. The ambiguous
   overlap shrank to 472 messages, small enough to check by eye.
3. **Build rows from templates.** For every column, take the modal value WhatsApp itself
   uses across comparable rows, then override only what carries meaning.
4. **Verify by conformance, not validity.** `conform.py` compares every generated row
   against WhatsApp's own distributions. It caught 53 defects that `integrity_check`
   called perfectly fine.

## Scripts

| File | Role |
|---|---|
| `work/merge.py` | graft post-seam iOS rows into the Android backup |
| `work/conform.py` | compare generated rows against WhatsApp's own distributions |
| `work/fixsort.py` | chronological ordering and chat-list repair |
| `work/attach_media.py` | copy media into WhatsApp's tree and link it to messages |
| `work/fixids.py` | remap `message._id` chronologically across 209 referencing columns |
| `work/jidcheck.py`, `work/lidmap.py` | diagnostics for matching contacts across schemas |
| `wa_extract2.py` | render an iOS container to readable transcripts, CSVs and media |

Point them at a **copy** of the iOS container:

```bash
export WA_IOS_CONTAINER=/path/to/group.net.whatsapp.WhatsApp.shared
python3 work/merge.py
python3 work/conform.py
```

Requires `wa-crypt-tools`, `ffprobe`, and Python 3. See
[`TOOLING-NOTES.md`](TOOLING-NOTES.md) for version-specific gotchas, including a real
bug in `wa-crypt-tools 0.1.0`.

## Three findings worth knowing

- **iOS and Android swap audio and video type codes.** iOS `2 = video`, Android
  `3 = video`. A direct copy silently turns every video into an audio message.
- **`sort_id = _id`, always.** WhatsApp orders a thread by row ID, not timestamp, and
  sets `sort_id = _id` on every message it creates. Any row with a low ID and a late
  timestamp sits in the wrong place permanently. This was the root cause of three
  separate visible bugs.
- **`ZGROUPEVENTTYPE` is not a system-message flag.** Value `2` appears on ordinary
  messages of every kind.

## Warnings

- **Never write to the live container.** Copy it out with `rsync -aHE` and work on the
  copy. Finder silently destroys every modification time.
- **Registering a phone kills a linked device instantly.** Capture everything first.
- **Restore only happens on first run after a fresh install.** One shot per install.
- **Do the file work with WhatsApp uninstalled.** A live app rewrites its own backup
  filenames and will win the race.
- Uninstalling deletes `Android/media/com.whatsapp`. Recreate the folder structure by
  hand afterwards, or extract an archive that contains the full path.

## Not included

No message data, no media, and no encryption key. The scripts are here; the contents of
one person's chat history are not.

## Credits

Built with [Claude Code](https://claude.com/claude-code), starting in plan mode: the
irreversible steps were identified and every input captured before anything was
registered or deleted.
