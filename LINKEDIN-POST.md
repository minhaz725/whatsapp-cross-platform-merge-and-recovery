# LinkedIn post

---

My phone bricked on 31 July and took 7.5 years of WhatsApp with it. Nov 2018 onward. Every conversation, gone.

I had two partial copies, and neither one was complete.

The Google Drive backup covered Nov 2018 to 23 June 2026, then stopped. My MacBook was still logged in as a linked device and had everything up to the present, but only going back about a year, because a linked device does not carry your full history. It syncs roughly the last twelve months and shows the rest as empty chat headers.

So the six weeks between 23 June and 6 August existed in exactly one place: a laptop.

Then I found the clock I did not know about. When your main phone goes offline, WhatsApp logs out every linked device after 14 days. My phone died on 31 July. That gave me until roughly 14 August before the Mac quietly signed itself out and took those six weeks with it.

And there was a second trap. The moment any phone registers your number, the linked device dies instantly and permanently. So restoring my history would destroy the only copy of the missing part. One shot, no undo.

I worked through it with Claude Code. What actually made the difference was starting in plan mode instead of jumping straight to commands. We mapped the whole thing first, wrote down what was irreversible, and got every input safely captured before touching anything. Nothing got deleted or registered until the plan was on paper.

The hard part turned out to be that iPhone and Android store WhatsApp chats in completely different database formats. There is no tool that converts between them. We genuinely searched. Nothing exists.

So we built it. Read WhatsApp's own data closely enough to copy its conventions exactly, then wrote the missing messages in a form it would accept.

It did not work on the first try. Or the second. Or the third. Each failure taught us another undocumented rule, including one that took three separate attempts to fully understand: WhatsApp orders a conversation by internal row ID, not by the message timestamp. Get that wrong and your messages are all present but sitting in the wrong place, or invisible at the bottom of a thread.

Final result: 63,801 messages across 3,044 chats, Nov 2018 to Aug 2026, on my new Samsung, with photos and videos attached.

Two things worth passing on.

I used a cheap spare phone as a crash test dummy so I never had to experiment on the good one. That was useful, but not required. The same approach works with one phone. You just feel every failed attempt more.

And if you take one thing from this: check whether your backups actually cover what you think they cover. Mine had a two year hole in it from a phone where I never switched backups on, and I only found out while digging through the data. By then it was years too late.

Full technical write-up and the scripts are on my GitHub, including every mistake and how each one was diagnosed.

#DataRecovery #WhatsApp #ClaudeCode #Engineering
