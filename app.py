"""Slack bot: reads forwarded email in a raw channel, posts the interview ones.

Emails arrive in RAW_CHANNEL via Slack's channel email address. Every message
there is classified; INTERVIEW and MAYBE get reposted to HITS_CHANNEL. Runs in
Socket Mode, so it needs no public URL.
"""

import json
import logging
import os
import pathlib
from http.client import IncompleteRead
from logging.handlers import RotatingFileHandler
import sys
import threading
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()  # must run before classifier is imported -- it reads env at import

# Email subjects carry accents and emoji; the Windows console is cp1252 and
# raises UnicodeEncodeError on them, which would kill the bot mid-triage.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from slack_bolt import App  # noqa: E402
from slack_bolt.adapter.socket_mode import SocketModeHandler  # noqa: E402
from slack_sdk import WebClient  # noqa: E402
from slack_sdk.http_retry import RetryHandler, RetryState  # noqa: E402
from slack_sdk.http_retry.builtin_handlers import (  # noqa: E402
    RateLimitErrorRetryHandler,
)

from classifier import classify  # noqa: E402
from prefilter import prefilter  # noqa: E402
import state  # noqa: E402
import cards  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("triage")

# Also write to disk, so problems can be diagnosed after the window is closed.
_logfile = RotatingFileHandler(
    pathlib.Path(__file__).with_name("bot.log"),
    maxBytes=2_000_000, backupCount=3, encoding="utf-8",
)
_logfile.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s"))
logging.getLogger().addHandler(_logfile)

# The Slack SDK's transport warnings are noise once retries handle them.
logging.getLogger("slack_sdk.web.base_client").setLevel(logging.CRITICAL)



RAW_CHANNEL = os.environ["RAW_CHANNEL_ID"]    # where forwarded mail lands
HITS_CHANNEL = os.environ["HITS_CHANNEL_ID"]  # where the interview ones go

# This machine's connection to slack.com is intermittently slow (observed
# 1s-33s for the same call), so give requests room and retry transport
# failures rather than dying on one bad handshake.
class AnyNetworkErrorRetryHandler(RetryHandler):
    """Retry every transport failure, including bare socket timeouts.

    The built-in ConnectionErrorRetryHandler only matches URLError and
    ConnectionError, so a raw TimeoutError escapes it -- which is exactly
    what was killing this bot on a slow connection.
    """

    # Handshake/connection failures happen BEFORE the request is sent, so
    # replaying them is always safe. A read timeout means the request WAS
    # sent and only the response was lost -- safe to replay for a read, but
    # replaying a write posts the card twice, which is what went wrong.
    WRITES = ("chat.postmessage", "chat.update", "chat.delete", "reactions.add")

    def _can_retry(self, *, state: RetryState, request, response=None, error=None) -> bool:
        if error is None:
            return False
        if isinstance(error, IncompleteRead):
            return True  # truncated download; re-requesting is always safe
        if not isinstance(error, (OSError, EOFError)):
            return False
        if "read operation timed out" not in str(error).lower():
            return True  # never reached the server; replay freely
        url = str(getattr(request, "url", "")).lower()
        return not any(w in url for w in self.WRITES)


_web = WebClient(token=os.environ["SLACK_BOT_TOKEN"], timeout=90)
_web.retry_handlers.append(AnyNetworkErrorRetryHandler(max_retry_count=6))
_web.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=3))

# token_verification_enabled=False skips the auth.test that Bolt otherwise
# runs at import; a single slow call there used to kill startup outright.
app = App(client=_web, token_verification_enabled=False)

_bot_user_id = None


def bot_user_id() -> str | None:
    """Resolved on first use, with retries, and cached."""
    global _bot_user_id
    if _bot_user_id is None:
        for attempt in range(5):
            try:
                _bot_user_id = app.client.auth_test()["user_id"]
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("auth.test failed (%s/5): %s", attempt + 1, exc)
                time.sleep(2 ** attempt)
    return _bot_user_id

done: set[str] = state.load()  # completed, persisted across restarts
inflight: set[str] = set()     # started this run; never written to disk

EMOJI = {"ACTION": "dart", "MAYBE": "large_yellow_circle"}
REACT_RULES = "heavy_minus_sign"  # killed by prefilter, no API call
REACT_AI_NO = "white_circle"      # model looked at it and said no
REACT_ERROR = "warning"           # failed; will be retried
KIND_LABEL = {
    "SCHEDULING": "Scheduling",
    "RESCHEDULE": "Reschedule",
    "NEXT_ROUND": "Next round",
    "SCREENING_QUESTION": "Question to answer",
    "DOCUMENT_REQUEST": "Sends something",
    "ASSESSMENT": "Assessment",
    "OFFER": "Offer",
}


def _when(ts, fmt: str) -> str:
    """Format Slack's epoch timestamp, or return '' if it isn't there."""
    try:
        return time.strftime(fmt, time.localtime(float(ts)))
    except (TypeError, ValueError):
        return ""


def _bare(field) -> str:
    """Just the address, for building an Outlook search string."""
    if isinstance(field, list) and field:
        first = field[0]
        if isinstance(first, dict):
            return first.get("address", "")
        return str(first)
    return ""


def _address(field) -> str:
    """Slack gives `from` as a list of dicts, but not always."""
    if isinstance(field, list) and field:
        first = field[0]
        if isinstance(first, dict):
            name, addr = first.get("name"), first.get("address", "")
            return f"{name} <{addr}>" if name else addr
        return str(first)
    return str(field or "unknown")


def extract_email(client, files: list) -> dict | None:
    """Pull sender/subject/body out of Slack's email file object."""
    email = next((f for f in files if f.get("filetype") == "email"), None)
    if not email:
        return None

    body = email.get("plain_text") or email.get("preview_plain_text") or ""
    if not body.strip():
        # Slack truncates or omits plain_text on larger mail; go fetch it.
        try:
            full = client.files_info(file=email["id"])["file"]
            body = full.get("plain_text") or full.get("preview_plain_text") or ""
        except Exception as exc:  # noqa: BLE001 - subject alone still classifies
            log.warning("files_info failed for %s: %s", email.get("id"), exc)

    return {
        "sender": _address(email.get("from")),
        "sender_addr": _bare(email.get("from")),
        # Outlook's received: filter wants m/d/yyyy; the display copy is for humans.
        "received": _when(email.get("timestamp"), "%b %d, %I:%M %p"),
        "received_date": _when(email.get("timestamp"), "%m/%d/%Y"),
        "subject": email.get("subject") or "(no subject)",
        "body": body,
        "to": _address(email.get("to")),
    }


AUDIT_PATH = pathlib.Path(__file__).with_name("triage.log")


def audit(**row) -> None:
    """Append one JSON line per email: what was decided, and what it cost."""
    mail = row.pop("mail", {}) or {}
    usage = row.pop("usage", None) or {}
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        **row,
        "sender": mail.get("sender_addr") or mail.get("sender"),
        "subject": mail.get("subject"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cost_usd": usage.get("cost"),
    }
    try:
        with AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError as exc:  # noqa: BLE001 - logging must never break triage
        log.debug("audit write failed: %s", exc)


def _already_in_channel(client, raw_ts: str) -> bool:
    """Is a card for THIS EXACT email already sitting in #interviews?

    Covers the case where chat_postMessage succeeded but its response timed
    out, so no card was recorded locally; without it the next catch-up pass
    would post a second copy.

    Matching is on the raw message id carried in Slack message metadata, not
    on the subject line. Subject matching silently suppressed genuinely
    different emails that happened to share a subject -- three separate SMS
    forwards titled "Incoming SMS from: ...", and it would equally have
    swallowed a recruiter's second "Re: Next steps".
    """
    if not raw_ts:
        return False
    try:
        recent = client.conversations_history(
            channel=HITS_CHANNEL, limit=50, include_all_metadata=True
        )["messages"]
    except Exception:  # noqa: BLE001 - if we cannot check, fall through and post
        return False
    for m in recent:
        payload = (m.get("metadata") or {}).get("event_payload") or {}
        if payload.get("raw_ts") == raw_ts:
            return True
    return False


def _is_network(exc: BaseException) -> bool:
    """A transport failure on this flaky link, not a fault in the code."""
    return isinstance(exc, (TimeoutError, OSError, EOFError, IncompleteRead))


def _brief(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:80]}"


def _finish(ts: str) -> None:
    """Mark one message genuinely done: persisted, and out of the guard set."""
    state.mark(ts, done)
    inflight.discard(ts)


def _react(client, ts: str, name: str) -> None:
    try:
        client.reactions_add(channel=RAW_CHANNEL, timestamp=ts, name=name)
    except Exception as exc:  # noqa: BLE001 - already-reacted is common and harmless
        log.debug("reaction %s failed: %s", name, exc)


# Calendar invites and ATS mail carry pages of machinery after the human part.
CRUFT = (
    "~~//~~",
    "Invitation from Google Calendar",
    "You are receiving this email because you are subscribed",
    "Forwarding this invitation could allow",
    "View all guest info",
    "This email was sent to",
    "To stop receiving these emails",
    "Unsubscribe",
)


def trim_body(body: str) -> str:
    """Cut the boilerplate tail so the thread shows what a person wrote."""
    text = (body or "").strip()
    cut = min(
        (text.find(marker) for marker in CRUFT if text.find(marker) > 120),
        default=-1,
    )
    if cut > 0:
        text = text[:cut].rstrip() + "\n\n[... calendar/footer boilerplate trimmed]"
    return text


def build_blocks(verdict: dict, mail: dict, permalink: str, done: str = "") -> list:
    action = verdict["verdict"] == "ACTION"
    kind = KIND_LABEL.get(verdict.get("kind") or "", "Needs a look")
    company = verdict.get("company") or "Unknown company"
    icon = ":white_check_mark:" if done else (":dart:" if action else ":large_yellow_circle:")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{icon} {kind} - {company}"[:150],
                "emoji": True,
            },
        }
    ]

    if verdict.get("role"):
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{verdict['role']}*"}}
        )

    # Metadata as a context block: small grey type, so it reads as a label
    # rather than competing with the content.
    meta = [f":envelope: {mail['subject']}", f":bust_in_silhouette: {mail['sender']}"]
    if mail.get("received"):
        meta.append(f":clock3: {mail['received']}")
    meta.append(f":inbox_tray: {mail['to']}")
    blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "  |  ".join(meta)[:3000]}]}
    )

    blocks.append({"type": "divider"})

    if verdict.get("reason"):
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": verdict["reason"][:2900]}}
        )

    # Side-by-side fields scan faster than stacked lines.
    fields = []
    if verdict.get("asked"):
        fields.append({"type": "mrkdwn", "text": "*:point_right: You need to*\n" + verdict["asked"]})
    if verdict.get("deadline"):
        fields.append({"type": "mrkdwn", "text": "*:alarm_clock: Deadline*\n" + str(verdict["deadline"])})
    if fields:
        blocks.append({"type": "section", "fields": fields})

    query = outlook_query(mail)
    if query:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*:mag: Find it in Outlook*\n```" + query + "```"},
            }
        )

    buttons = [
        {
            "type": "button",
            "action_id": "open_original",
            "text": {"type": "plain_text", "text": "Open email", "emoji": True},
            "url": permalink,
        }
    ]
    if done:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f":white_check_mark: *Done* by <@{done}>"}
                ],
            }
        )
        buttons.append(
            {
                "type": "button",
                "action_id": "reopen_card",
                "text": {"type": "plain_text", "text": "Reopen", "emoji": True},
                "value": "reopen",
            }
        )
    else:
        buttons.insert(
            0,
            {
                "type": "button",
                "action_id": "mark_done",
                "text": {"type": "plain_text", "text": "Mark done", "emoji": True},
                "style": "primary",
                "value": "done",
            },
        )

    blocks.append({"type": "actions", "elements": buttons})

    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": ":speech_balloon: full email in the thread below"}],
        }
    )
    return blocks


def outlook_query(mail: dict) -> str:
    """A search string narrow enough to land on exactly one message.

    Outlook cannot search by Message-ID, so the tightest key available is
    sender + full subject + received date. The subject must stay quoted --
    unquoted, Outlook treats each word as a separate term and returns half
    the inbox. The subject is no longer truncated: a cut-off subject inside
    quotes matches nothing at all.
    """
    bits = []
    if mail.get("sender_addr"):
        bits.append(f"from:{mail['sender_addr']}")
    subject = (mail.get("subject") or "").replace('"', "").strip()
    if subject and subject != "(no subject)":
        bits.append('subject:"' + subject[:230] + '"')
    if mail.get("received_date"):
        bits.append(f"received:{mail['received_date']}")
    return " ".join(bits)


def outlook_query(mail: dict) -> str:
    """A search string narrow enough to land on one message.

    Outlook cannot search by Message-ID, so the tightest key available is
    sender + full subject + the received date. The subject must be quoted or
    Outlook treats each word as a separate term and returns half the inbox.
    """
    bits = []
    if mail.get("sender_addr"):
        bits.append(f"from:{mail['sender_addr']}")
    subject = (mail.get("subject") or "").replace('"', "").strip()
    if subject and subject != "(no subject)":
        bits.append(f'subject:"{subject[:230]}"')
    if mail.get("received_date"):
        bits.append(f"received:{mail['received_date']}")
    return " ".join(bits)


def triage(event, client) -> str | None:
    """Classify one raw-channel message and act on it. Returns the verdict."""
    if event.get("subtype") in ("message_changed", "message_deleted"):
        return None
    if event.get("user") == bot_user_id():
        return None

    ts = event.get("ts")
    if not ts or ts in done or ts in inflight:
        return None
    inflight.add(ts)  # guards duplicate events; not a record of success

    try:
        return _triage_message(event, client, ts)
    except Exception as exc:  # noqa: BLE001
        if _is_network(exc):
            log.warning("triage retryable: %s (will retry on next sweep)", _brief(exc))
        else:
            log.exception("triage failed: %s", exc)
        _react(client, ts, REACT_ERROR)
        return "ERROR"
    finally:
        # `done` is the only record of success. A failure must never leave the
        # message sitting in `inflight`, or every later catch-up skips it as
        # "in progress" and the email is lost until the process restarts.
        inflight.discard(ts)


def _triage_message(event, client, ts: str) -> str | None:
    mail = extract_email(client, event.get("files") or [])
    if not mail:
        return None  # not an email — someone just typed in the channel

    dropped = prefilter(mail["sender"], mail["subject"], mail["body"])
    if dropped:
        log.info("%-9s %s | %s", dropped, mail["sender"], mail["subject"])
        _react(client, ts, REACT_RULES)
        audit(verdict="RULES_DROP", source="rules", reason=dropped, mail=mail)
        _finish(ts)
        return "FILTERED"

    try:
        verdict = classify(mail["sender"], mail["subject"], mail["body"])
    except Exception as exc:  # noqa: BLE001
        if _is_network(exc):
            log.warning("classify retryable: %s", _brief(exc))
        else:
            log.exception("classify failed: %s", exc)
        _react(client, ts, REACT_ERROR)
        return "ERROR"  # not marked done, so the next catch-up retries it

    log.info("%-9s %s | %s", verdict["verdict"], mail["sender"], mail["subject"])

    audit(verdict=verdict["verdict"], source="ai", reason=verdict.get("reason"),
          mail=mail, usage=verdict.get("usage"), kind=verdict.get("kind"))

    if verdict["verdict"] == "NO":
        _react(client, ts, REACT_AI_NO)
        _finish(ts)
        return "NO"

    if cards.has_raw(ts) or _already_in_channel(client, ts):
        # A card already exists for this email -- a retried post, or a
        # catch-up racing the live event. Don't post it twice.
        log.info("card already posted for %s, skipping", mail["subject"][:50])
        _finish(ts)
        return verdict["verdict"]

    permalink = client.chat_getPermalink(channel=RAW_CHANNEL, message_ts=ts)["permalink"]
    posted = client.chat_postMessage(
        channel=HITS_CHANNEL,
        text=f"{KIND_LABEL.get(verdict.get('kind') or '', 'Action')}: {mail['subject']}",  # notification fallback
        blocks=build_blocks(verdict, mail, permalink),
        unfurl_links=False,
        # Lets a later pass recognise this exact email, even if the response
        # to this call never made it back to us.
        metadata={"event_type": "triage_card", "event_payload": {"raw_ts": ts}},
    )

    cards.add(posted["ts"], {**verdict, "reason": verdict.get("reason")},
              {**mail, "permalink": permalink}, permalink, raw_ts=ts)

    # Full text goes in the thread. Slack collapses it to a "1 reply" link,
    # so the channel stays a scannable list and the body is one click away.
    body = trim_body(mail["body"])
    if body:
        try:
            client.chat_postMessage(
                channel=HITS_CHANNEL,
                thread_ts=posted["ts"],
                text=body[:2900],
                unfurl_links=False,
                unfurl_media=False,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("thread body not attached (%s); card is posted", _brief(exc))
    _react(client, ts, EMOJI[verdict["verdict"]])
    _finish(ts)
    return verdict["verdict"]


@app.event("message")
def on_message(event, client):
    if event.get("channel") != RAW_CHANNEL:
        return
    triage(event, client)


def _rebuild(card: dict, done_by: str = "") -> list:
    """Re-render a stored card, used when Done or Reopen is clicked."""
    verdict = {
        "verdict": card.get("verdict", "ACTION"),
        "kind": card.get("kind"),
        "company": card.get("company"),
        "role": card.get("role"),
        "reason": card.get("reason") or "",
        "asked": card.get("asked"),
        "deadline": card.get("deadline"),
    }
    mail = {
        "subject": card.get("subject") or "",
        "sender": card.get("sender") or "",
        "sender_addr": card.get("sender_addr") or "",
        "to": card.get("to") or "",
        "received": card.get("received") or "",
        "received_date": card.get("received_date") or "",
    }
    return build_blocks(verdict, mail, card.get("permalink") or "", done=done_by)


@app.action("mark_done")
def on_mark_done(ack, body, client):
    ack()
    ts = body["message"]["ts"]
    user = body["user"]["id"]
    card = cards.mark_done(ts, user)
    if card is None:
        return
    client.chat_update(
        channel=body["channel"]["id"],
        ts=ts,
        text=f"Done: {card.get('subject') or ''}",
        blocks=_rebuild(card, done_by=user),
    )


@app.action("reopen_card")
def on_reopen(ack, body, client):
    ack()
    ts = body["message"]["ts"]
    card = cards.reopen(ts)
    if card is None:
        return
    client.chat_update(
        channel=body["channel"]["id"],
        ts=ts,
        text=card.get("subject") or "",
        blocks=_rebuild(card),
    )

@app.action("open_original")
def ignore_link_click(ack):
    ack()  # URL buttons still fire an action; ack it so Bolt stops warning


def catch_up(days: float) -> None:
    """Triage anything that arrived while the bot was off.

    Socket Mode only delivers live events, so without this every restart
    leaves a hole. Runs on every start; processed.json makes it idempotent.
    """
    client = app.client
    oldest = time.time() - days * 86400
    messages, cursor = [], None
    while True:
        # Slack returns an empty page for an explicit cursor=None, so only
        # send the parameter once there is a real cursor to follow.
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
    if not pending:
        log.info("catch-up: nothing missed")
        return

    log.info("catch-up: %d message(s) missed while offline", len(pending))
    for msg in pending:
        try:
            triage(msg, client)
        except Exception as exc:  # noqa: BLE001
            # Leave it unmarked so the next pass retries just this one,
            # instead of losing the rest of the batch to one bad message.
            log.warning("catch-up: skipping one message (%s)", exc)
            inflight.discard(msg.get("ts", ""))
        time.sleep(1.2)  # Slack allows ~1 post/sec per channel


if __name__ == "__main__":
    # Newest source file, not just this one -- the stamp is how a stale
    # running process gets spotted, and edits often land in classifier.py
    # or prefilter.py rather than here.
    newest = max(f.stat().st_mtime for f in pathlib.Path(__file__).parent.glob("*.py"))
    log.info("=== started, code version %s ===",
             datetime.fromtimestamp(newest).isoformat(timespec="seconds"))
    days = float(os.environ.get("CATCHUP_DAYS", "7"))
    backoff = 5

    every = float(os.environ.get("CATCHUP_EVERY_MIN", "15"))

    def _catch_up_safely() -> None:
        """Catch up now, then keep re-scanning.

        Bolt drops an event outright if its auth middleware fails, and this
        machine's connection to Slack is flaky enough for that to happen.
        A periodic re-scan turns that from lost mail into a few minutes' delay.
        """
        while True:
            try:
                catch_up(days)
            except Exception as exc:  # noqa: BLE001 - never take down the watcher
                log.warning("catch-up failed: %s", exc)
            if every <= 0:
                return
            time.sleep(every * 60)

    # Started ONCE, outside the loop. It runs forever on its own thread, so
    # spawning it per reconnect stacked up duplicate sweepers -- three dropped
    # connections meant three threads all re-scanning every 15 minutes.
    #
    # It is also deliberately off the main path: catch-up used to run inline
    # before connecting, and a long backlog (39 messages, rate-limit backoffs,
    # 35 minutes) meant Socket Mode never connected at all, so every live
    # email in that window was missed. The backlog must never delay the
    # live watcher.
    threading.Thread(target=_catch_up_safely, name="catchup", daemon=True).start()

    while True:
        try:
            log.info("watching %s, posting hits to %s", RAW_CHANNEL, HITS_CHANNEL)
            SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
        except KeyboardInterrupt:
            log.info("stopped by user")
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("connection lost (%s); retrying in %ss", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)  # cap at 5 minutes
        else:
            backoff = 5
