"""llm.py - LLM wrapper for EasyBot.

Two responsibilities:

1. `summarize_thread(thread)` - turn a Slack thread into a structured decision
   record: { summary, author_hint, reason }.
2. `find_match(question, candidates)` - given a fresh channel message and the
   list of approved summaries for that channel, decide whether any of them
   answers the message (semantic match). Returns the matching candidate id
   or None.

>>> EDIT THE PROMPT CONSTANTS BELOW TO TUNE THE BOT'S BEHAVIOR <<<
>>> SET YOUR API KEY IN .env (GEMINI_API_KEY or OPENAI_API_KEY) <<<

The module supports two providers via the LLM_PROVIDER env var:
    - "gemini" (default) - uses google-genai
    - "openai"           - uses openai>=1.0

If you prefer a different provider, swap the body of `_chat_json` and keep
the signature the same; the rest of the file (and the handlers) will work
unchanged.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("easybot.llm")


# --------------------------------------------------------------------------- #
# PROMPTS  --  edit freely
# --------------------------------------------------------------------------- #
SUMMARIZE_SYSTEM_PROMPT = """You are EasyBot, a Slack knowledge-capture
assistant. You will be given one Slack message OR a full thread that contains
a team decision, established practice, or fact the team wants to remember.
Extract a SINGLE concrete piece of knowledge.

A "decision" includes ANY of the following:
  - A choice the team explicitly debated and resolved.
  - A statement of established practice (e.g. "we use Postgres in prod",
    "we don't run migrations on Fridays").
  - A confirmation of how something works in this team / project.
  - A standalone declarative announcement of a choice, even with no thread
    discussion around it.

Output JSON ONLY with this shape:
{
  "summary":     "<one or two crisp sentences describing the decision or fact>",
  "author_hint": "<display name of the person who stated it, as shown in the
                   thread; empty string if unclear>",
  "reason":      "<one sentence on WHY, if stated; empty if not>"
}

Rules:
- Be faithful. Do NOT invent facts not present in the input.
- A single declarative sentence IS a valid decision/fact - capture it.
- Only set summary to "" if the input is pure chitchat with no knowledge to
  capture (e.g. "hi everyone", "lol", "good morning", "wfh today").
- Do not include any text outside the JSON object.
"""


MATCH_SYSTEM_PROMPT = """You are a semantic matcher inside a Slack bot.
You will get:
  (a) a new question or statement a user just posted in a channel, and
  (b) a numbered list of previously-approved items from the same channel.
      Each item is either a decision/fact, or a question with its answer.

Decide whether any single item directly answers or covers (a).

Output JSON ONLY:
{
  "match_index": <integer index from the list, or -1 if no good match>,
  "confidence":  <number between 0 and 1>,
  "rationale":   "<one short sentence>"
}

Be strict. Only return a match_index >= 0 if confidence is at least 0.6 and
the item clearly speaks to the user's question. Otherwise return -1.
"""


SUMMARIZE_DOUBT_PROMPT = """You are EasyBot, a Slack knowledge-capture
assistant.  You will be given a Slack thread where someone asked a question
and someone else answered it.  Your job is to extract the Q & A so it can
be looked up later.

Output JSON ONLY:
{
  "question": "<the question being asked, rewritten as a clear single sentence>",
  "answer":   "<the resolution / answer given in the thread, in one or two sentences>",
  "author_hint": "<display name of the person who answered, if visible; else empty>"
}

Rules:
- Be faithful. Do NOT invent answers.
- The thread you see was triggered by a reaction on the ANSWER message, so
  the answer is usually the most recent / triggering message; the question
  is usually earlier in the thread (or the thread root).
- If you cannot find both a clear question and a clear answer, return
  question="" and answer="".  The caller handles that.
- Do not include any text outside the JSON object.
"""


CLASSIFY_AND_EXTRACT_PROMPT = """You are EasyBot. You will be given a Slack
thread (or a single Slack message). The channel owner reacted on one
message in this thread to capture knowledge.

Classify what they want to capture and extract it.

Output JSON ONLY:
{
  "kind":       "decision" | "doubt",
  "summary":    "<for kind='decision': one or two sentences describing the
                  fact / decision.
                  For kind='doubt': the clear question being asked.>",
  "answer":     "<for kind='doubt': the answer/resolution given in the
                  thread, in one or two sentences. Empty string for
                  kind='decision'.>",
  "reason":     "<for kind='decision': one sentence on WHY if stated.
                  Empty for kind='doubt'.>",
  "author_hint":"<display name of the person who stated the decision
                  OR answered the question, if visible. Empty otherwise.>"
}

Rules for picking `kind`:
- If the reacted-on message (or the thread root) is a QUESTION and the
  thread contains an ANSWER from someone else, return kind="doubt".
- Otherwise return kind="decision" — that covers facts, established
  practices ("we use Postgres in prod"), and explicit choices.

Rules for content:
- Be faithful. Never invent facts or answers not in the input.
- For doubts, if you can't find both a clear Q and a clear A, you may set
  answer="" and the caller will handle it.
- Trivial chitchat (hi / lol / ok / wfh today) -> kind="decision" with
  summary="".
- Do not include any text outside the JSON object.
"""


# --------------------------------------------------------------------------- #
# Provider plumbing
# --------------------------------------------------------------------------- #
_PROVIDER = (os.environ.get("LLM_PROVIDER") or "gemini").lower()


def _chat_json(system_prompt: str, user_prompt: str) -> dict:
    """Send a prompt and parse a JSON response. Returns {} on any failure.

    Retries on 429 (rate-limit) up to 3 times with exponential backoff so a
    brief throttle doesn't make the bot silently swallow a capture or match.
    """
    import time as _time

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            if _PROVIDER == "openai":
                return _openai_call(system_prompt, user_prompt)
            return _gemini_call(system_prompt, user_prompt)
        except Exception as exc:
            last_exc = exc
            msg = str(exc)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                # Honour retry hint if present, else exponential backoff
                delay = 2 ** attempt   # 1s, 2s, 4s
                logger.warning(
                    "LLM rate-limited (attempt %d/3); retrying in %ds. Tip: "
                    "switch GEMINI_MODEL to gemini-1.5-flash for a larger free tier.",
                    attempt + 1, delay,
                )
                _time.sleep(delay)
                continue
            logger.exception("LLM call failed: %s", exc)
            return {}
    logger.error("LLM gave up after 3 retries: %s", last_exc)
    return {}


def _gemini_call(system_prompt: str, user_prompt: str) -> dict:
    # >>> INSERT YOUR GEMINI API KEY VIA THE GEMINI_API_KEY ENV VAR <<<
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=api_key)
    # gemini-1.5-flash has the largest free-tier daily quota; safe default.
    model = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")

    resp = client.models.generate_content(
        model=model,
        contents=[user_prompt],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    text = (resp.text or "").strip()
    return json.loads(text) if text else {}


def _openai_call(system_prompt: str, user_prompt: str) -> dict:
    # >>> INSERT YOUR OPENAI API KEY VIA THE OPENAI_API_KEY ENV VAR <<<
    from openai import OpenAI

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    return json.loads(text) if text else {}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def summarize_thread(thread_messages: list[dict]) -> dict:
    """Return {summary, author_hint, reason}. Empty `summary` means
    'no decision detected'."""
    rendered = _render_thread(thread_messages)
    user_prompt = f"Thread:\n{rendered}\n\nExtract the decision now."
    out = _chat_json(SUMMARIZE_SYSTEM_PROMPT, user_prompt)
    return {
        "summary":     out.get("summary", "").strip(),
        "author_hint": out.get("author_hint", "").strip(),
        "reason":      out.get("reason", "").strip(),
    }


def classify_and_extract(thread_messages: list[dict], triggering_ts: str | None = None) -> dict:
    """Single-call classifier + extractor used by the 🧠 capture flow.

    Returns:
      {
        "kind":        "decision" | "doubt",
        "summary":     str,                  # decision text OR question
        "answer":      str,                  # only meaningful for doubts
        "reason":      str,                  # only meaningful for decisions
        "author_hint": str,
      }

    Empty `summary` means "no knowledge worth capturing" - caller falls back
    to the raw triggering message.
    """
    rendered = _render_thread(thread_messages)
    hint = ""
    if triggering_ts:
        hint = (
            f"\n(The owner reacted on the message with ts={triggering_ts}.)"
        )
    user_prompt = f"Thread:\n{rendered}{hint}\n\nClassify and extract."
    out = _chat_json(CLASSIFY_AND_EXTRACT_PROMPT, user_prompt)
    kind = (out.get("kind") or "decision").strip().lower()
    if kind not in ("decision", "doubt"):
        kind = "decision"
    return {
        "kind":        kind,
        "summary":     (out.get("summary") or "").strip(),
        "answer":      (out.get("answer") or "").strip(),
        "reason":      (out.get("reason") or "").strip(),
        "author_hint": (out.get("author_hint") or "").strip(),
    }


def summarize_doubt(thread_messages: list[dict], triggering_ts: str | None = None) -> dict:
    """Extract a question + answer pair from a thread.

    Returns {question, answer, author_hint}.  Empty `question` OR empty
    `answer` means we couldn't find a complete Q&A and the caller should
    fall back (e.g. ask the owner to fill it in manually).
    """
    rendered = _render_thread(thread_messages)
    hint = ""
    if triggering_ts:
        hint = (
            f"\nThe reaction was placed on the message with ts={triggering_ts}, "
            "which is the ANSWER. Find the matching question earlier in the thread."
        )
    user_prompt = f"Thread:\n{rendered}{hint}\n\nExtract the Q & A now."
    out = _chat_json(SUMMARIZE_DOUBT_PROMPT, user_prompt)
    return {
        "question":    out.get("question", "").strip(),
        "answer":      out.get("answer", "").strip(),
        "author_hint": out.get("author_hint", "").strip(),
    }


def find_match(
    question: str,
    candidates: list[dict],
    threshold: Optional[float] = None,
) -> Optional[dict]:
    """Return the matching candidate dict (from `candidates`) or None.

    `candidates` is the list of approved items from db.list_approved().
    Each item is either a decision (kind='decision', has summary + optional
    reason) or a doubt (kind='doubt', has summary=question + answer).  We
    show the LLM the FULL content of each so it can judge whether any
    actually answers the user's message.

    `threshold` overrides the EASYBOT_MATCH_THRESHOLD env var when provided.
    """
    if not candidates:
        return None

    lines = []
    for i, c in enumerate(candidates):
        kind = c.get("kind") or "decision"
        if kind == "doubt":
            answer = (c.get("answer") or "").strip() or "(no answer recorded)"
            lines.append(
                f"[{i}] Q&A — Q: {c['summary_text']}  A: {answer}"
            )
        else:
            reason = (c.get("reason_for_decision") or "").strip()
            text = f"[{i}] FACT/DECISION — {c['summary_text']}"
            if reason:
                text += f"  (rationale: {reason})"
            lines.append(text)
    enumerated = "\n".join(lines)
    user_prompt = (
        f"New user message:\n{question}\n\n"
        f"Approved decisions for this channel:\n{enumerated}\n\n"
        "Now produce the JSON object."
    )
    out = _chat_json(MATCH_SYSTEM_PROMPT, user_prompt)
    logger.info(
        "find_match: q=%r  candidates=%d  llm=%s",
        question[:80], len(candidates), out,
    )
    try:
        idx = int(out.get("match_index", -1))
    except (TypeError, ValueError):
        idx = -1
    if idx < 0 or idx >= len(candidates):
        return None
    conf = float(out.get("confidence", 0) or 0)
    _threshold = threshold if threshold is not None else float(
        os.environ.get("EASYBOT_MATCH_THRESHOLD", "0.5")
    )
    if conf < _threshold:
        return None
    matched = dict(candidates[idx])
    matched["_match_confidence"] = conf
    matched["_match_rationale"] = out.get("rationale", "")
    return matched


def find_duplicate(summary: str, candidates: list[dict]) -> Optional[dict]:
    """Return an existing candidate if it is very similar to `summary` (threshold=0.85).

    Used during capture to warn the owner about near-identical existing entries.
    """
    return find_match(summary, candidates, threshold=0.85)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _render_thread(messages: list[dict]) -> str:
    """Render a Slack thread payload into plain text for the LLM."""
    lines: list[str] = []
    for m in messages:
        if m.get("subtype") in {"bot_message", "channel_join"}:
            continue
        user = m.get("user") or m.get("username") or "unknown"
        # `user_profile.real_name` may be present when conversations.replies
        # is called with include_all_metadata=True; otherwise fall back to id.
        profile = (m.get("user_profile") or {})
        name = profile.get("real_name") or profile.get("display_name") or user
        lines.append(f"{name}: {m.get('text', '')}")
    return "\n".join(lines)
