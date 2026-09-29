"""Deterministic noise filter. Runs before the model, costs nothing.

Deliberately minimal: it only drops what is *provably* noise -- bulk job
alerts and unambiguous rejections. Everything else goes to the model, because
a wrongly-kept email costs one glance and a wrongly-dropped one costs an
interview. Measured on real mail, this sends ~98% to the model for well under
$2/month.

Order matters. Rejections are checked first because they often contain the
word "interview" ("thanks for interviewing with us... unfortunately").
Then strong interview signals rescue an email from every remaining rule,
because the expensive mistake is dropping a real invite.
"""

import os
import re

# Application confirmations are the highest-volume noise -- but they are also
# where both production misses came from: "Thanks for applying... finish your
# interview by Tuesday" and "Thank you for your application... one more step".
# So they go to the model by default. Set DROP_CONFIRMATIONS=true to trade
# ~$0.35/month for a slightly higher chance of missing one.
DROP_CONFIRMATIONS = os.environ.get("DROP_CONFIRMATIONS", "").lower() in ("1", "true", "yes")

def _any(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(patterns), re.IGNORECASE)


# 1. Rejections. Checked first -- these win over everything.
REJECTION = _any([
    r"\bunfortunately\b",
    r"we (?:have )?(?:regret|decided to (?:move forward|proceed) with)",
    r"(?:other|another) candidates?",
    r"not (?:be )?(?:moving|going) forward",
    r"no longer (?:under consideration|being considered)",
    r"(?:were|was) not selected",
    r"position has been filled",
    r"we will not be",
    r"keep your (?:resume|r\u00e9sum\u00e9|cv|profile) on file",
    r"pursuing other (?:applicants|candidates)",
])

# 2. Strong "someone wants something from you" signals. These rescue an email
#    from every rule below, and send it to the model for a real judgement.
INTERVIEW_SIGNAL = _any([
    r"\bavailabilit(?:y|ies)\b",
    r"\bschedul(?:e|ing)\b",
    r"\bbook (?:a |some )?time\b",
    r"\bpick a time\b",
    r"\bcalendly\.com|savvycal\.com|cal\.com/|hubspot\.com/meetings|goodtime\.io",
    r"\b(?:phone|video|technical|initial|final) (?:screen|interview|call)\b",
    r"\bnext steps?\b",
    r"\binterview (?:invitation|invite|request)\b",
    r"\binvit(?:e|ed|ation) (?:you )?to (?:an?|interview|chat|speak|meet)",
    r"\b(?:are|would) you (?:available|free)\b",
    r"\bdoes .{0,20}(?:work for you|suit you)\b",
    r"\blet me know .{0,30}(?:time|availability|works)\b",
    r"\bset up (?:a )?(?:call|chat|time|meeting)\b",
    r"\bconfirm(?:ing)? (?:your |the )?(?:time|slot|interview|appointment)\b",
    r"\bhiring manager\b",
    r"\bassessment\b|\btake[- ]home\b|\bcoding challenge\b",
    r"\breschedul(?:e|ing|ed)\b",
    r"\bworks? for you\b",
    r"\b(?:another|different|new) time\b",
    r"\b(?:would|does|is)\s+\w{0,10}day\b",
    r"\bcalendar invite\b|\bmeeting invite\b|\.ics\b",
    r"zoom\.us/|teams\.microsoft\.com/l/meetup|meet\.google\.com",
    r"\bextend(?:ing)? (?:you )?an offer\b|\boffer letter\b",
    r"\bfollow(?:ing)?[- ]up on (?:our|your|the) (?:call|interview|conversation|chat)\b",
    # Screening questions -- a live process, just not a scheduling one.
    r"\b(?:expected|desired|current)\s+(?:\w+\s+){0,2}(?:rate|salary|compensation|pay)\b",
    r"\b(?:rate|salary|compensation)\s+expectation",
    r"\bsecurity clearance\b|\bclearance\b",
    r"\bauthorized to work\b|\bwork authorization\b|\bsponsorship\b|\bvisa status\b",
    r"\bnotice period\b|\bhow soon can you (?:start|join)\b|\bavailable to start\b",
    r"\bstill interested\b|\bconfirm your interest\b",
    r"\bupdated (?:resume|r\u00e9sum\u00e9|cv)\b|\bsend (?:me |us )?your (?:resume|r\u00e9sum\u00e9|cv)\b",
    r"\bbackground check\b|\breference check\b|\bprovide references\b",
    r"\bnext round\b|\bmove you (?:forward|to the)\b|\bmoving you forward\b",
    r"\bopen to relocat|\bwilling to relocate\b",
    r"\bplease (?:complete|fill (?:out|in)|submit)\b",
    r"\bcould you (?:send|share|confirm|provide)\b|\bcan you (?:send|share|confirm|provide)\b",
    # Asks hidden behind a polite opener. Both of these were real misses:
    # "Thanks for applying... finish your interview by Tuesday" and
    # "Thank you for your application... one more step, confirm your identity".
    r"\b(?:finish|complete|continue|resume|start|begin)\s+your\b[^.!?]{0,40}\b(?:interview|assessment|application|profile)\b",
    r"\bpick up where you left off\b",
    r"\baction (?:needed|required)\b|\bresponse requested\b",
    r"\b(?:one|just one) more step\b",
    r"\bconfirm your identity\b",
    r"\byour interview\b",
    r"\bback by\b|\bdue by\b|\bcloses? on\b|\bdeadline\b",
    r"\bopen the link below\b|\bclick the link below to (?:begin|start|continue)\b",
    r"\bfinal reminder\b|\blast chance\b",
])

# 3. Application confirmations -- they ask nothing of you.
CONFIRMATION = _any([
    r"thank(?:s| you)\b[^.!?]{0,25}\bfor (?:applying|your application|your interest)",
    r"(?:we(?:'ve| have)? )?received your application",
    r"your application (?:has been |was )?(?:received|submitted)",
    r"application (?:received|submitted|confirmation)",
    r"we(?:'ve| have)? got your application",
    r"successfully applied",
])

# 4. Job alerts, digests, marketing.
BULK = _any([
    r"\bjob alert",
    r"\bnew jobs? (?:for you|matching|posted)",
    r"\bjobs? (?:you may|recommended)",
    r"\b\d+ new jobs?\b",
    r"\bjob recommendations?\b",
    r"\bwebinar\b",
    r"\bnewsletter\b",
    r"\bupgrade your\b",
])

BULK_SENDERS = _any([
    r"jobalerts-noreply@linkedin\.com",
    r"jobs-listings@linkedin\.com",
    r"@(?:indeed(?:email)?|ziprecruiter|glassdoor|jobright|monster)\.com",
    r"@.*\.(?:mailchimpapp|sendgrid|substack)\.",
])

# 5. Mechanical noise with no ask in it. Safe to drop ONLY because
#    INTERVIEW_SIGNAL is checked first -- Akamai's "Action Needed ... one more
#    step ... confirm your identity" carries a passcode but is rescued above,
#    while a bare "here is your code" lands here.
NOISE = _any([
    # one-time codes and account plumbing
    r"\b(?:verification|security|login|one[- ]time|access|confirmation) code\b",
    r"\byour code is\b|\bhere is your code\b",
    r"\bverify your (?:email|account|address|candidate account)\b",
    r"\bconfirm your email address\b",
    r"\bsign in to\b|\blog in to\b|\bpassword reset\b|\breset your password\b",
    r"\bwelcome to\b.{0,30}\bcareers\b",
    # social / network noise
    r"\bsearch appearances\b|\bviewed your profile\b|\bprofile is popular\b",
    r"\bpeople you may know\b|\bnew connections?\b|\bendorsed you\b",
    r"\binvitation to connect\b|\bwants to connect\b",
    # content digests
    r"\btop (?:posts|stories|picks)\b|\bdaily digest\b|\bweekly digest\b",
    r"\btrending (?:in|now|this)\b|\brecommended for you\b",
    # commerce
    r"\breceipt\b|\binvoice\b|\byour order\b|\bpayment (?:received|failed)\b",
    r"\b\d{1,2}% off\b|\bsale ends\b|\blimited time offer\b",
])


def prefilter(sender: str, subject: str, body: str) -> str | None:
    """Return a drop reason, or None to pass the email to the model."""
    head = f"{subject}\n{body[:2000]}"  # the tell is near the top

    # Anything that asks for the recipient's time goes to the model, full stop.
    # "Unfortunately I can't do Tuesday, would Wednesday work?" is a reschedule,
    # not a rejection -- and dropping that is the one mistake this system
    # cannot afford. Rules may only drop when NOTHING suggests a live thread.
    if INTERVIEW_SIGNAL.search(head):
        return None

    if REJECTION.search(head):
        return "rejection"

    if DROP_CONFIRMATIONS and CONFIRMATION.search(head):
        return "application confirmation"

    if BULK_SENDERS.search(sender) or BULK.search(subject):
        return "bulk/alert"

    if NOISE.search(head):
        return "noise"

    return None
