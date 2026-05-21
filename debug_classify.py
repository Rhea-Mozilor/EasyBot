"""debug_classify.py - prove the auto-classifier works on a Q+A thread.

Run:  python3 debug_classify.py

Reproduces exactly what the bot does when you 🧠 on an answer message:
  - synthesizes a tiny thread (question + answer)
  - calls llm.classify_and_extract
  - prints the structured result

If the result is kind='doubt' with both summary and answer populated,
the LLM is doing the right thing and the bug is elsewhere (probably the
bot wasn't restarted, so the new code isn't running).
"""
from __future__ import annotations

import json

from dotenv import load_dotenv
load_dotenv()

import llm

# Sanity: confirm the function exists in the loaded module
print("classify_and_extract present:", hasattr(llm, "classify_and_extract"))

fake_thread = [
    {"user": "U_deepak", "user_profile": {"real_name": "Deepak"},
     "text": "what's our retry strategy?", "ts": "1.0"},
    {"user": "U_rhea",   "user_profile": {"real_name": "rhea"},
     "text": "exponential backoff, jitter, max 5 retries.", "ts": "2.0"},
]

print("\nCalling classify_and_extract on a Q+A thread...\n")
result = llm.classify_and_extract(fake_thread, triggering_ts="2.0")
print(json.dumps(result, indent=2))

print()
if result.get("kind") == "doubt" and result.get("summary") and result.get("answer"):
    print("CLASSIFIER OK — running bot should store this as kind='doubt'.")
else:
    print("CLASSIFIER MISCLASSIFIED — the LLM didn't recognize this as a Q&A.")
    print("Tell me what the JSON above says and I'll tighten the prompt.")
