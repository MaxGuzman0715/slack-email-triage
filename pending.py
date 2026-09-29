"""Post a summary of every card still waiting on you.

    python pending.py          post the digest to #interviews
    python pending.py --print  print it here instead

Run it whenever, or on a schedule (Task Scheduler, each weekday morning).
"""

import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from slack_sdk import WebClient  # noqa: E402

import cards  # noqa: E402

KIND_ICON = {
    "SCHEDULING": ":calendar:",
    "RESCHEDULE": ":arrows_counterclockwise:",
    "NEXT_ROUND": ":fast_forward:",
    "SCREENING_QUESTION": ":grey_question:",
    "DOCUMENT_REQUEST": ":paperclip:",
    "ASSESSMENT": ":pencil:",
    "OFFER": ":tada:",
}

rows = cards.pending()
if not rows:
    print("Nothing outstanding.")
    sys.exit(0)

lines = []
for ts, c in rows:
    age = (datetime.now() - datetime.fromisoformat(c["at"])).days
    icon = KIND_ICON.get(c.get("kind") or "", ":envelope:")
    who = " - ".join(x for x in (c.get("company"), c.get("role")) if x) or c.get("subject") or "?"
    bits = [f"{icon} <{c['permalink']}|{who}>"]
    if c.get("asked"):
        bits.append(f"- {c['asked']}")
    tail = []
    if c.get("deadline"):
        tail.append(f":alarm_clock: {c['deadline']}")
    tail.append(f"{age}d old" if age else "today")
    lines.append(" ".join(bits) + "  _(" + ", ".join(tail) + ")_")

blocks = [
    {
        "type": "header",
        "text": {"type": "plain_text", "text": f"{len(rows)} still waiting on you", "emoji": True},
    },
    {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)[:2900]}},
    {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "Hit *Mark done* on a card to clear it from this list."}],
    },
]

if "--print" in sys.argv:
    for line in lines:
        print(" ", line)
    sys.exit(0)

WebClient(token=os.environ["SLACK_BOT_TOKEN"]).chat_postMessage(
    channel=os.environ["HITS_CHANNEL_ID"],
    text=f"{len(rows)} interview items still waiting on you",
    blocks=blocks,
    unfurl_links=False,
)
print(f"Posted digest: {len(rows)} outstanding.")
