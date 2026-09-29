"""What the triage bot has decided, and what it cost.

    python stats.py          all time
    python stats.py 1        last 1 day
"""

import collections
import json
import pathlib
import sys
from datetime import datetime, timedelta

rows = []
path = pathlib.Path(__file__).with_name("triage.log")
if not path.exists():
    sys.exit("No triage.log yet - the bot hasn't classified anything since logging was added.")

since = None
if len(sys.argv) > 1 and sys.argv[1].replace(".", "").isdigit():
    since = datetime.now() - timedelta(days=float(sys.argv[1]))

for line in path.read_text(encoding="utf-8").splitlines():
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        continue
    if since and datetime.fromisoformat(r["at"]) < since:
        continue
    rows.append(r)

if not rows:
    sys.exit("Nothing in that window.")

ai = [r for r in rows if r.get("source") == "ai"]
rules = [r for r in rows if r.get("source") == "rules"]
costs = [r["cost_usd"] for r in ai if r.get("cost_usd") is not None]
total = sum(costs)

print(f"{len(rows)} emails  ({rows[0]['at'][:16]} -> {rows[-1]['at'][:16]})\n")
print(f"  killed by rules (free)   {len(rules):4}   {len(rules)/len(rows):5.0%}")
print(f"  sent to the model        {len(ai):4}   {len(ai)/len(rows):5.0%}\n")

by = collections.Counter(r["verdict"] for r in rows)
for verdict, n in by.most_common():
    print(f"  {verdict:12} {n:4}")

if rules:
    print("\nwhy rules dropped them:")
    for reason, n in collections.Counter(r.get("reason") for r in rules).most_common():
        print(f"  {str(reason):26} {n:4}")

print("\ncost:")
if costs:
    tok_in = sum(r.get("prompt_tokens") or 0 for r in ai)
    tok_out = sum(r.get("completion_tokens") or 0 for r in ai)
    print(f"  total                    ${total:.4f}")
    print(f"  per model call           ${total/len(costs):.6f}")
    print(f"  per email received       ${total/len(rows):.6f}")
    print(f"  tokens                   {tok_in:,} in / {tok_out:,} out")
    print(f"  projected at 200/day     ${total/len(rows)*200*30:.2f}/month")
else:
    print("  no cost data returned by the provider yet")

print("\nemails the model was asked about:")
for r in ai[-25:]:
    c = f"${r['cost_usd']:.6f}" if r.get("cost_usd") is not None else "   -     "
    print(f"  {r['at'][11:16]}  {r['verdict']:8} {c}  {str(r.get('subject'))[:52]}")
