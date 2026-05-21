"""debug_match.py - find out exactly why the matcher isn't matching.

Calls Gemini directly with:
  1. The current DB candidates
  2. A test question

Prints the raw LLM response so we can see why no match is being made.

Run:  python3 debug_match.py "what's our retry strategy?"
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv
load_dotenv()

import db
import llm

q = sys.argv[1] if len(sys.argv) > 1 else "what's our retry strategy?"

# Pull ALL approved candidates from every channel (we don't know the channel id
# in this script - that's fine for a diagnostic).
import sqlite3, os
con = sqlite3.connect(os.environ.get("KB_DB_PATH", os.environ.get("EASYBOT_DB_PATH", "easybot.sqlite3")))
con.row_factory = sqlite3.Row
candidates = [dict(r) for r in con.execute(
    "SELECT * FROM decisions WHERE status = 'approved' ORDER BY id DESC"
)]
con.close()

print(f"\nQuestion under test: {q!r}")
print(f"Candidates fed to the matcher ({len(candidates)}):")
for c in candidates:
    kind = c.get("kind") or "decision"
    if kind == "doubt":
        print(f"  #{c['id']} DOUBT  Q={c['summary_text']!r}  A={c.get('answer')!r}")
    else:
        print(f"  #{c['id']} DECISION  {c['summary_text']!r}")
print()

import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

result = llm.find_match(q, candidates)
print()
if result:
    print("MATCH FOUND:")
    print(f"  id={result['id']}  kind={result.get('kind')}")
    print(f"  summary={result['summary_text']!r}")
    print(f"  answer ={result.get('answer')!r}")
    print(f"  confidence={result.get('_match_confidence')}")
    print(f"  rationale={result.get('_match_rationale')!r}")
else:
    print("NO MATCH (the matcher rejected all candidates).")
    print("Scroll up to the `find_match: ...` log line above to see what Gemini returned.")
