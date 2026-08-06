import os
import sqlite3

# point this at the copied iOS container directory before running.
# never run against ~/Library directly; work on a copy.
IOS_CONTAINER = os.environ.get('WA_IOS_CONTAINER', '<IOS_CONTAINER>')

SEAM_APPLE = 803891102
mac = sqlite3.connect(f'{IOS_CONTAINER}/ChatStorage.sqlite')
base = sqlite3.connect('BASE.db')

rows = mac.execute("""
  SELECT cs.ZCONTACTJID, cs.ZPARTNERNAME, COUNT(*)
  FROM ZWAMESSAGE m JOIN ZWACHATSESSION cs ON m.ZCHATSESSION = cs.Z_PK
  WHERE m.ZMESSAGEDATE > ? GROUP BY cs.ZCONTACTJID ORDER BY COUNT(*) DESC
""", (SEAM_APPLE,)).fetchall()

basejids = {r[0]: r[1] for r in base.execute("SELECT raw_string,_id FROM jid WHERE raw_string IS NOT NULL")}

matched = unmatched = 0; mm = um = 0
missing = []
for jid, name, n in rows:
    if jid in basejids: matched += 1; mm += n
    else: unmatched += 1; um += n; missing.append((jid, name, n))

print(f"post-seam chats: {len(rows)}")
print(f"  JID found in BASE   : {matched} chats, {mm} messages")
print(f"  JID NOT in BASE     : {unmatched} chats, {um} messages")
print()
if missing:
    print("chats needing new jid/chat rows:")
    for jid, name, n in missing[:20]: print(f"   {n:5d}  {name or '(no name)'}  {jid}")
