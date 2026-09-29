"""Which posted cards are still outstanding.

Slack has no persistent per-message unread, so the bot keeps its own: every
card starts pending, and clicking Done marks it handled. This is the same
model as flagged/unflagged in Outlook.
"""

import json
import pathlib
import threading
from datetime import datetime

PATH = pathlib.Path(__file__).with_name("cards.json")
_lock = threading.Lock()


def _read() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write(data: dict) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(PATH)  # atomic; a kill mid-write cannot corrupt it


def add(ts: str, verdict: dict, mail: dict, permalink: str, raw_ts: str = "") -> None:
    with _lock:
        data = _read()
        data[ts] = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "raw_ts": raw_ts,  # the #inbox-raw message this card came from
            "done": False,
            "verdict": verdict["verdict"],
            "kind": verdict.get("kind"),
            "company": verdict.get("company"),
            "role": verdict.get("role"),
            "reason": verdict.get("reason"),
            "asked": verdict.get("asked"),
            "deadline": verdict.get("deadline"),
            # Everything build_blocks needs, so Done/Reopen can re-render
            # the card without going back to Slack for the original.
            "subject": mail.get("subject"),
            "sender": mail.get("sender"),
            "sender_addr": mail.get("sender_addr"),
            "to": mail.get("to"),
            "received": mail.get("received"),
            "received_date": mail.get("received_date"),
            "permalink": permalink,
        }
        _write(data)


def has_raw(raw_ts: str) -> bool:
    """Has a card already been posted for this #inbox-raw message?"""
    with _lock:
        return any(c.get("raw_ts") == raw_ts for c in _read().values())


def mark_done(ts: str, by: str) -> dict | None:
    with _lock:
        data = _read()
        card = data.get(ts)
        if card is None:
            return None
        card["done"] = True
        card["done_by"] = by
        card["done_at"] = datetime.now().isoformat(timespec="seconds")
        _write(data)
        return card


def reopen(ts: str) -> dict | None:
    with _lock:
        data = _read()
        card = data.get(ts)
        if card is None:
            return None
        card["done"] = False
        card.pop("done_by", None)
        card.pop("done_at", None)
        _write(data)
        return card


def pending() -> list[tuple[str, dict]]:
    """Outstanding cards, oldest first."""
    return sorted(
        ((ts, c) for ts, c in _read().items() if not c.get("done")),
        key=lambda kv: float(kv[0]),
    )
