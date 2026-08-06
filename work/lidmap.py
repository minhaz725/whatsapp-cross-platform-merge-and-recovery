import os
import sqlite3, re, unicodedata

# point this at the copied iOS container directory before running.
# never run against ~/Library directly; work on a copy.
IOS_CONTAINER = os.environ.get('WA_IOS_CONTAINER', '<IOS_CONTAINER>')

C=f'{IOS_CONTAINER}'
mac=sqlite3.connect(f'{IOS_CONTAINER}/ChatStorage.sqlite')
base=sqlite3.connect('BASE.db'); cts=sqlite3.connect(f'{C}/ContactsV2.sqlite')
SEAM=803891102

# authoritative lid -> phone from address book
book={}
for lid,phone in cts.execute("SELECT ZLID,ZPHONENUMBER FROM ZWAADDRESSBOOKCONTACT WHERE ZLID IS NOT NULL AND ZPHONENUMBER IS NOT NULL"):
    book[str(lid).split('@')[0]] = re.sub(r'\D','',str(phone))
print(f"address-book lid->phone pairs: {len(book)}")

basejids={r[0]:r[1] for r in base.execute("SELECT raw_string,_id FROM jid WHERE raw_string IS NOT NULL")}
baseusers={r[0]:r[1] for r in base.execute("SELECT user,_id FROM jid WHERE server='s.whatsapp.net'")}

rows=mac.execute("""SELECT cs.ZCONTACTJID, cs.ZPARTNERNAME, COUNT(*)
 FROM ZWAMESSAGE m JOIN ZWACHATSESSION cs ON m.ZCHATSESSION=cs.Z_PK
 WHERE m.ZMESSAGEDATE>? AND cs.ZCONTACTJID LIKE '%@lid'
 GROUP BY cs.ZCONTACTJID ORDER BY COUNT(*) DESC""",(SEAM,)).fetchall()

res=0; unres=0
for jid,name,n in rows:
    lid=jid.split('@')[0]
    phone=book.get(lid)
    src='addressbook'
    if not phone and name:
        d=re.sub(r'\D','',unicodedata.normalize('NFKC',name))
        if 8<=len(d)<=15: phone,src=d,'partner-name'
    hit=baseusers.get(phone) if phone else None
    if hit: res+=n; print(f"  OK   {n:4d}  {lid[:18]:18} -> {phone}  ({src})")
    else:   unres+=n; print(f"  MISS {n:4d}  {lid[:18]:18} -> {phone}  ({src}) not in BASE")
print(f"\nresolved into existing BASE chats: {res} msgs; still new: {unres} msgs")
