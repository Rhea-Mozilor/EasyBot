"""cleanup.py - one-off DB cleaner.

Removes rows where summary_text looks like a question but no answer is
stored.  These got created earlier when the user reacted 🧠 on questions,
which the LLM didn't know to treat as Q&A pairs.  They pollute the
semantic matcher with question-shaped 'facts'.

Run:  python3 cleanup.py
"""
from __future__ import annotations

import os
import sqlite3
import re

DB_PATH = os.environ.get("EASYBOT_DB_PATH", "easybot.sqlite3")

QUESTION_RE = re.compile(
    r"(\?|^\s*(what|whats|when|where|why|how|who|whose|which|"
    r"should|shall|can|could|would|may|might|"
    r"do|does|did|is|are|was|were|will|wont|has|have|had|am)\b)",
    re.IGNORECASE,
)

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

print("Scanning decisions table for question-shaped rows with no answer...\n")
to_delete: list[int] = []
for r in con.execute("SELECT id, kind, summary_text, answer FROM decisions"):
    is_question = bool(QUESTION_RE.search((r["summary_text"] or "").strip()))
    if is_question and not r["answer"]:
        to_delete.append(r["id"])
        print(f"  will delete #{r['id']}  kind={r['kind']:8}  {r['summary_text']!r}")

if not to_delete:
    print("Nothing to clean up.")
    con.close()
    raise SystemExit

resp = input(f"\nDelete {len(to_delete)} row(s)? [y/N] ").strip().lower()
if resp != "y":
    print("Aborted.")
    con.close()
    raise SystemExit

for did in to_delete:
    con.execute("DELETE FROM decision_history WHERE decision_id = ?", (did,))
    con.execute("DELETE FROM decisions WHERE id = ?", (did,))
con.commit()
con.close()
print(f"Deleted {len(to_delete)} rows. Done.")
