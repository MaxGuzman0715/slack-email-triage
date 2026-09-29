"""Email -> interview verdict, via OpenRouter.

Only emails that survive prefilter.py get here, so this runs on roughly a
quarter of your mail.
"""

import json
import logging
import os
import re
import threading
import time

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

log = logging.getLogger("triage")

MODEL = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat")

# OpenRouter's shared pools get rate-limited upstream (429 "temporarily
# rate-limited"). Listing alternates lets OpenRouter route around a busy
# provider instead of failing the request. Verify slugs at openrouter.ai/models.
MAX_ATTEMPTS = int(os.environ.get("CLASSIFY_RETRIES", "6"))

# Requests are serialised and spaced out rather than routed to another model.
# One call at a time, at least MIN_INTERVAL apart, is what keeps us under the
# shared pool's limit; a 429 then only means "slow down further", not "switch".
MIN_INTERVAL = float(os.environ.get("CLASSIFY_MIN_INTERVAL", "2.0"))

_gate = threading.Lock()
_last_call = 0.0
_penalty = 0.0  # grows on 429, decays on success


def _wait_turn() -> None:
    """Hold the queue until enough time has passed since the last call."""
    global _last_call
    gap = MIN_INTERVAL + _penalty
    delay = gap - (time.monotonic() - _last_call)
    if delay > 0:
        time.sleep(delay)
    _last_call = time.monotonic()

_client = None


def _get_client() -> OpenAI:
    """Built on first use, so importing this module never needs the key."""
    global _client
    if _client is None:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Check that .env sits next to "
                "app.py and is named exactly .env -- Notepad likes to save it "
                "as .env.txt. Verify with:  dir /a .env"
            )
        _client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
    return _client

SYSTEM = """You triage a job-seeker's email. Answer with JSON only.

The question is NOT "does this mention an interview". It is:

    Is this an ACTIVE hiring conversation where a real person or system is
    waiting on this recipient to reply or do something?

ACTION means the recipient must respond or act. All of these count:
  - scheduling: proposing an interview/call/screen, asking availability,
    sending a scheduling link or calendar invite, confirming a time
  - rescheduling or cancelling a booked conversation
  - advancing a round: "we'd like to move you to the next round"
  - screening questions a recruiter needs answered before proceeding:
    expected rate or salary, notice period, work authorization or
    sponsorship, security clearance, relocation, "are you still interested"
  - requests for material: an updated resume, a form to complete, portfolio,
    references, a background check
  - assessments or take-homes, especially with a deadline
  - an offer, or negotiation of one
  - a recruiter about a specific role asking to connect or talk

NO means nothing is being asked of the recipient:
  - "thank you for applying", "we received your application"
  - any rejection, however politely worded, even one describing an
    interview that already happened
  - pure status updates with no ask ("your take-home is under review")
  - job alerts, digests, newsletters, marketing, billing, notifications

MAYBE means you genuinely cannot tell. Prefer MAYBE over NO whenever a real
hiring conversation might be at stake: a missed reply costs the recipient far
more than one extra email to glance at.

Common traps:
  - "Thanks for interviewing with us. Unfortunately..." is NO.
  - "Unfortunately I can't do Tuesday, would Wednesday work?" is ACTION --
    a reschedule, not a rejection.
  - An ask need not contain the word "interview". "Would Tuesday or Thursday
    work for a quick chat?" is ACTION.
  - A screening question wrapped in "thank you for your interest" is ACTION.
  - Greenhouse, Lever, Workday and iCIMS send BOTH confirmations and real
    asks. Judge the content, never the sender.

Fields:
  verdict  ACTION | MAYBE | NO
  kind     SCHEDULING | RESCHEDULE | NEXT_ROUND | SCREENING_QUESTION |
           DOCUMENT_REQUEST | ASSESSMENT | OFFER | OTHER   (null if NO)
  company  the hiring company, not the agency, or null
  role     or null
  reason   one short line
  deadline any date or time they must respond by, else null
  asked    what the recipient must actually do, in a few words, or null"""

_JSON = re.compile(r"\{.*\}", re.DOTALL)


def classify(sender: str, subject: str, body: str) -> dict:
    """Classify one email, retrying through upstream rate limits."""
    global _penalty
    last = None

    # One request at a time. The lock is the queue: concurrent triages line up
    # here instead of hammering the shared pool in parallel.
    with _gate:
        for attempt in range(MAX_ATTEMPTS):
            _wait_turn()
            try:
                result = _classify_once(sender, subject, body)
                _penalty = max(0.0, _penalty - 1.0)  # ease back off after success
                return result
            except RateLimitError as exc:
                last = exc
                _penalty = min(_penalty + 2.0, 20.0)  # space every later call out
                wait = min(5 * 2 ** attempt, 120)  # 5s, 10s, 20s, 40s, 80s, 120s
                log.warning(
                    "rate limited (%s/%s); waiting %ss, spacing now %.1fs",
                    attempt + 1, MAX_ATTEMPTS, wait, MIN_INTERVAL + _penalty,
                )
                time.sleep(wait)
            except (APIConnectionError, APITimeoutError) as exc:
                last = exc
                wait = min(2 ** attempt * 2, 30)
                log.warning("connection issue (%s/%s); waiting %ss", attempt + 1, MAX_ATTEMPTS, wait)
                time.sleep(wait)
            except APIStatusError as exc:
                # A middlebox on this network (antivirus / firewall / ISP)
                # intercepts HTTPS and occasionally returns its own 403 with
                # {"success": false, "error": "Access denied by security
                # policy."} -- not OpenRouter's error shape. That is transient,
                # so retry it. Any other 4xx is a real error; re-raise at once.
                if not _is_middlebox_block(exc):
                    raise
                last = exc
                wait = min(2 ** attempt * 2, 30)
                log.warning("blocked by a network policy (%s/%s); waiting %ss",
                            attempt + 1, MAX_ATTEMPTS, wait)
                time.sleep(wait)

    raise last  # exhausted: triage marks it with a warning and retries later


def _is_middlebox_block(exc: APIStatusError) -> bool:
    """A local security appliance blocking the call, not a real API error."""
    if exc.status_code not in (401, 403):
        return False
    text = str(getattr(exc, "body", "") or exc).lower()
    return "security policy" in text or "access denied" in text


def _classify_once(sender: str, subject: str, body: str) -> dict:
    extra = {"usage": {"include": True}}
    response = _get_client().chat.completions.create(
        model=MODEL,
        max_tokens=300,
        temperature=0,
        response_format={"type": "json_object"},
        extra_body=extra,  # real cost, plus fallback routing
        messages=[
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": f"From: {sender}\nSubject: {subject}\n\n{(body or '')[:6000]}",
            },
        ],
    )

    u = getattr(response, "usage", None)
    usage = {
        "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
        # OpenRouter returns the real charge here; absent on some providers.
        "cost": getattr(u, "cost", None),
    }

    raw = response.choices[0].message.content or ""
    match = _JSON.search(raw)  # some models wrap JSON in prose or code fences
    if not match:
        raise ValueError(f"no JSON in model output: {raw[:200]!r}")

    data = json.loads(match.group(0))
    verdict = str(data.get("verdict", "")).upper()
    if verdict == "INTERVIEW":
        verdict = "ACTION"  # older prompt wording, still accepted
    if verdict not in ("ACTION", "MAYBE", "NO"):
        verdict = "MAYBE"  # unparseable verdict surfaces rather than vanishes

    return {
        "verdict": verdict,
        "usage": usage,
        "kind": data.get("kind"),
        "company": data.get("company"),
        "role": data.get("role"),
        "reason": data.get("reason") or "",
        "deadline": data.get("deadline"),
        "asked": data.get("asked"),
    }
