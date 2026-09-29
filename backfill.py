"""Catch up on emails that arrived while the bot was not running.

    python backfill.py          last 7 days
    python backfill.py 30       last 30 days
    python backfill.py 30 --dry show what it would do, post nothing

Safe to run repeatedly: anything already in processed.json is skipped.
"""

import sys
import time

from app import RAW_CHANNEL, app, done, triage

days = 7
dry = "--dry" in sys.argv
for arg in sys.argv[1:]:
    if arg.isdigit():
        days = int(arg)

oldest = time.time() - days * 86400
client = app.client

print(f"scanning #inbox-raw, last {days} days" + (" (dry run)" if dry else ""))

messages, cursor = [], None
while True:
    # Slack returns an empty page for an explicit cursor=None, so only send
    # the parameter once there is a real cursor to follow.
    args = {"channel": RAW_CHANNEL, "oldest": f"{oldest:.6f}", "limit": 50}
    if cursor:
        args["cursor"] = cursor
    page = client.conversations_history(**args)
    messages.extend(page["messages"])
    cursor = page.get("response_metadata", {}).get("next_cursor")
    if not cursor:
        break

messages.reverse()  # oldest first, so #interviews reads in arrival order
pending = [m for m in messages if m.get("ts") not in done]
print(f"{len(messages)} messages found, {len(pending)} not yet triaged\n")

counts = {}
for msg in pending:
    if dry:
        files = msg.get("files") or []
        subject = next(
            (f.get("subject") for f in files if f.get("filetype") == "email"), None
        )
        print(f"  would triage: {subject or '(not an email)'}")
        continue

    verdict = triage(msg, client) or "skipped"
    counts[verdict] = counts.get(verdict, 0) + 1
    time.sleep(1.2)  # Slack posts are rate limited to ~1/sec per channel

if not dry:
    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing to do")
