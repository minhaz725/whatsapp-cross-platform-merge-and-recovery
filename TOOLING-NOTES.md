# Tooling notes — verified 6 Aug 2026 (Phase 0.8)

Venv: `<HOME>/Downloads/untitled folder/.wa-venv` (Python 3.14.6)
Installed: `wa-crypt-tools 0.1.0`, `whatsapp-chat-exporter 0.13.0`
Also present: OpenJDK 21 (for `natario1/whatsapp-database-merger`), sqlite3, rsync.

## Verified working

- `wadecrypt`, `waencrypt`, `wainfo`, `wacreatekey` all launch.
- Encrypt → decrypt round-trip is **byte-identical**, with both a generated key and a 64-hex-digit key.

```bash
V="<HOME>/Downloads/untitled folder/.wa-venv/bin"
"$V/wacreatekey" -y --hex <64-hex-digits>       # writes ./encrypted_backup.key
"$V/wadecrypt" encrypted_backup.key msgstore.db.crypt15 msgstore.db
"$V/waencrypt" --jid 70 --reference msgstore.db.crypt15 encrypted_backup.key merged.db msgstore-new.db.crypt15
```

## Gotchas found

- **`wacreatekey -o <path>` is broken.** argparse declares `-o` as `FileType('wb')`, then
  `wacreatekey.py:86` calls `Path(args.output)` on the open handle → `TypeError`.
  Unconditional bug in 0.1.0, not Python-version-specific.
  **Workaround: omit `-o` and `cd` to the target directory first.** It writes
  `./encrypted_backup.key` (or `./key` with `-c14`).
- `wainfo` takes only the encrypted file: `wainfo [-k] <encrypted>`. Passing a keyfile
  positionally fails.
- `--jid` is the **last two digits of the phone number**. Number is `<YOUR_NUMBER>` → **`70`**.
- `waencrypt` self-reports beta. Always pass `--reference` against a real crypt15 from the
  same device for the production run (round-trip above was tested without one).

## sqlite gotcha (cost me a wrong answer today)

`strftime('%s', ...)` returns **TEXT**. Comparing it against the numeric `ZMESSAGEDATE`
silently matches nothing and returns 0 rows. Use precomputed integers.

Apple epoch offset: `unix = ZMESSAGEDATE + 978307200`.

Seam constant (23 Jun 2026 04:59 UTC = 10:59 Dhaka) in Apple epoch: **`803883540`**

## Measured against the cold copy

| Query | Result |
|---|---|
| Total messages | 28,767 (2018-11-01 → 2026-08-06) |
| After seam (Mac-only, to be grafted) | **5,156** |
| Distinct chats touched after seam | **97** |
| 22–24 Jun dedupe window | **472** |
| `PRAGMA integrity_check` | ok |
