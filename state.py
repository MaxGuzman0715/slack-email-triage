"""Which Slack messages have already been triaged.

Kept on disk, not in memory, so a restart doesn't re-post everything and
backfill.py can tell what it still owes you.
"""

import json
import pathlib
import threading

PATH = pathlib.Path(__file__).with_name("processed.json")
_lock = threading.Lock()


def load() -> set[str]:
    try:
        return set(json.loads(PATH.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def mark(ts: str, done: set[str]) -> None:
    """Record one COMPLETED message. Write-through, so a crash loses nothing.

    `done` must contain only messages that actually reached a verdict. It is
    deliberately separate from the in-flight guard in app.py: persisting a
    merely-started message would make a crash look like success, and the
    message would never be retried.
    """
    with _lock:
        done.add(ts)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(done)), encoding="utf-8")
        tmp.replace(PATH)  # atomic, so a kill mid-write can't corrupt it
