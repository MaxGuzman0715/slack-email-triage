# Interview email triage bot

Forwarded email lands in one Slack channel. Rules kill the obvious noise for
free; a cheap model judges what's left; the interview ones get reposted into a
second channel.

```
Gmail / Outlook  --forward-->  #inbox-raw
                                   |
                            prefilter.py     (regex, free, ~55% dropped)
                                   |
                            classifier.py    (OpenRouter, ~$0.0002/email)
                                   |
                              #interviews
```

## Two stages, on purpose

**Stage 1 — `prefilter.py`, no AI.** Rejections, application confirmations and
job alerts have stable, mechanical vocabulary. They get dropped before a single
token is spent.

**The ordering is the whole design.** Rules may only drop an email when
*nothing* in it suggests a live conversation:

1. **Interview signals are checked first and win outright.** Anything asking
   for your time goes to the model, whatever else it says.
2. Only then rejections, confirmations and bulk/alerts.

This ordering exists because of a real bug. With rejections checked first,
*"Unfortunately I can't do Tuesday, would Wednesday work?"* was dropped as a
rejection — it is a reschedule. So were three other live-thread cases,
including an offer. All six are now regression tests.

The asymmetry is deliberate: a wrongly-kept email costs you one glance, a
wrongly-dropped one costs you an interview.

**Stage 2 — `classifier.py`, the model.** Only the survivors. The real question
is never "does it say interview" — it's *"is someone waiting on me to reply?"*
That covers scheduling, but also rate questions, clearance and work-auth
questions, notice period, document requests, background checks, assessments
and offers. All of those are live processes that stall if you miss them.

Never filter by sender domain: Greenhouse, Lever, Workday and iCIMS send both
confirmations and real invites from the same address.

## Tuning the rules

```bash
python test_prefilter.py
```

27 cases taken from real inboxes, including the traps. `KEEP` means it must
reach the model; `DROP` means rules should kill it. Add your own misses as you
find them — this is the file to edit when something slips through.

## Setup

### 1. Slack channels

Create two private channels, e.g. `#inbox-raw` and `#interviews`. Mute
`#inbox-raw` — it's the firehose, you never read it.

In `#inbox-raw`: **Settings → Integrations → Send emails to this channel**.
Copy the generated `…@yourworkspace.slack.com` address. *Paid Slack plan.*

Channel IDs (for `.env`) start with `C` — click the channel name, the ID is at
the bottom of the About tab. A `D…` id is a DM and won't work.

### 2. Forward your mail

- **Gmail** — Settings → Forwarding and POP/IMAP → *Add a forwarding address* →
  paste the Slack address. Gmail sends a confirmation code **to that address**,
  so it appears as a message in `#inbox-raw`. Paste it back, switch forwarding
  on. Repeat per account.
- **Outlook** — Rules → new rule → *Redirect to* the Slack address. Redirect,
  not forward — it preserves the original sender.

### 3. Slack app

api.slack.com/apps → **Create New App → From scratch**.

- **Socket Mode → Enable.** Gives you an app-level token (`xapp-…`). No public
  URL, no hosting.
- **OAuth & Permissions → Bot Token Scopes:** `channels:history`,
  `channels:read`, `groups:history`, `groups:read`, `chat:write`,
  `files:read`, `reactions:write`
- **Event Subscriptions → Enable → Subscribe to bot events:**
  `message.channels` (add `message.groups` for private channels)
- Install, copy the bot token (`xoxb-…`), then `/invite @yourbot` in **both**
  channels.

### 4. Run

```bash
cd C:\Projects\slack-email-triage
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env     # fill it in
python app.py
```

## Model

OpenRouter, set by `OPENROUTER_MODEL`. **Verify the exact slug at
openrouter.ai/models before running** — slugs change and a wrong one fails at
the first email.

Default is `deepseek/deepseek-chat`. This task is short, structured and
temperature-0, so a small model is genuinely enough — the hard part is the
prompt, not the model. With ~55% dropped by the rules first, expect well under
$1/month at 200 emails/day. If you see it getting borderline emails wrong,
swap in a stronger model before you touch the prompt.

## What you see

| Verdict | Action | Reaction on `#inbox-raw` |
|---|---|---|
| `ACTION` | posts to `#interviews` | 🎯 |
| `MAYBE` | posts to `#interviews` as 🟡 | 🟡 |
| `NO` | nothing | ➖ |
| killed by rules | nothing, no API call | ➖ |
| error | nothing — **retried on next run** | ⚠️ |

`ACTION` is not only scheduling. Each card is labelled with its kind:
Scheduling, Reschedule, Next round, Question to answer, Sends something,
Assessment, Offer — and carries a **You need to:** line saying what to do.

**Rejections are dropped**, on purpose — they need no reply. They're still
recorded with ➖ in `#inbox-raw` if you want to count them. To see them
instead, delete the `REJECTION` block from `prefilter.py` and let the model
call them `NO`.

Read the `MAYBE` bucket for the first week; it shows you where the line sits
wrong. The prompt is told to prefer `MAYBE` over `NO` when unsure.

The reactions are your audit trail. A message in `#inbox-raw` with **no**
reaction was never processed — that's how you spot downtime.

## Downtime is handled automatically

Socket Mode only delivers **live** events, so a bot that isn't running misses
mail. Rather than ask you to remember a command, **every start runs a catch-up
first**: it re-reads the last `CATCHUP_DAYS` (default 7) of `#inbox-raw` and
triages anything not already done, then begins watching.

So the whole recovery procedure is: **start it again.** Laptop rebooted,
crashed, closed the window by accident — just run `run.bat`.

```
run.bat                        catch up, then watch
install-autostart.bat          run once: start on Windows login
python backfill.py 30          manual sweep, further back than CATCHUP_DAYS
python backfill.py 30 --dry    list what it would do, post nothing
```

`processed.json` records every message that reached a verdict, so catch-up is
idempotent — run it any number of times, nothing is posted twice. Messages
that **errored** are deliberately left out, so the next run retries them.

Gone longer than 7 days? Raise `CATCHUP_DAYS` in `.env`, or run `backfill.py 30`
once.

## Known limits

- **It won't be 100%.** Recruiters phrase things unpredictably. The design
  choice throughout is to fail toward showing you too much.
- **Slack mangles HTML email.** ATS templates can arrive with thin or empty
  `plain_text`. The bot falls back to `preview_plain_text`, then the subject
  alone — so some emails are judged on a subject line only.
- **The bot must stay running.** Socket Mode is a live connection and does not
  catch up on start — run `backfill.py` after any downtime.
- **Free Slack drops messages after 90 days.** Harmless — the real emails are
  still in your mailbox.
