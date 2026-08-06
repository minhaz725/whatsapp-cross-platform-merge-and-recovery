# CLAUDE.md

**This project is COMPLETE (6 Aug 2026). There is no outstanding work.**

The WhatsApp recovery finished successfully: 63,801 messages, 1 Nov 2018 → 6 Aug 2026,
running on the Samsung S25 Ultra with media attached and Google Drive backup re-enabled.

Read `WA-RECOVERY-BRIEF.md` for the outcome and known boundaries, and
`RECOVERY-WRITEUP.md` for the full technical account.

This repo is now a **public record and script archive**, not an active operation.

---

## If work resumes here

- **`work/encrypted_backup.key` must never be committed or shared.** It is the 64-digit
  end-to-end backup key. It is in `.gitignore`; keep it there.
- **No message data belongs in this repo.** Databases, media and transcripts are all
  gitignored. Scripts and documentation only.
- **Never write to `~/Library/`.** Read-only, always. Copy out first, work on the copy.
- **Never copy a WhatsApp container with Finder.** It destroys every mtime. Use
  `rsync -aHE`.
- **Never send message data over the network.** Generic technical web searches are fine
  and were used during this work.
- **Ask before anything destructive or irreversible.** Default to additive.

## Scripts

Point them at a copy of an iOS container:

```bash
export WA_IOS_CONTAINER=/path/to/group.net.whatsapp.WhatsApp.shared
python3 work/merge.py && python3 work/conform.py
```

`wa_extract2.py` regenerates a readable archive from any cold copy:

```bash
python3 wa_extract2.py --container "$WA_IOS_CONTAINER" --out ~/Desktop/WA-ARCHIVE \
    --timezone Pacific/Auckland
```

Keep `--timezone Pacific/Auckland` if comparing against the original archives. The Mac
clock is `Asia/Dhaka`; without the pin every timestamp shifts six hours.

## Data state

All source data was deleted on 6 Aug 2026 at the user's explicit instruction, after
being told twice that the Mac capture could not be regenerated. **The phone and Google
Drive are now the only copies.** Nothing in this repo can rebuild that history.

## Note

`../CLAUDE.md` in the parent directory belongs to an unrelated project (FlightPulse) and
will be auto-loaded alongside this file. Ignore it. Do not delete it.
