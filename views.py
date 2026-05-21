"""views.py - Block Kit builders.

Every function here returns either a list of blocks or a full view payload.
Keeping the JSON out of the handlers makes the workflow logic readable.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time as _time
from typing import Optional

_STALE_DAYS = int(os.environ.get("EASYBOT_STALE_DAYS", "90"))
_CONFIDENT_THRESHOLD = float(os.environ.get("EASYBOT_CONFIDENT_THRESHOLD", "0.75"))


def _fmt_ts(ts: int) -> str:
    return _dt.datetime.utcfromtimestamp(ts).strftime("%b %d, %Y at %H:%M UTC")


def _pt(text: str) -> dict:
    """plain_text element with emoji rendering enabled."""
    return {"type": "plain_text", "text": text, "emoji": True}


def _staleness_warning(timestamp: int) -> Optional[dict]:
    """Returns a context block warning if the entry is older than _STALE_DAYS, else None."""
    age_days = (_time.time() - timestamp) / 86400
    if age_days < _STALE_DAYS:
        return None
    months = max(1, int(age_days / 30))
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": (
            f"⚠️ Last updated *{months} month{'s' if months != 1 else ''} ago* — "
            "verify this is still current before acting on it."
        )}],
    }


# --------------------------------------------------------------------------- #
# Workflow 1: Approval card sent privately to the channel owner
# --------------------------------------------------------------------------- #
def approval_card(
    *,
    decision_id: int,
    channel_id: str,
    summary: str,
    author_id: str,
    reason: Optional[str],
    source_permalink: Optional[str] = None,
    kind: str = "decision",
    answer: Optional[str] = None,
) -> list[dict]:
    if kind == "doubt":
        header = "❓ New Q&A to approve"
        content_blocks: list[dict] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Question*\n{summary}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Answer*\n{answer or '_Not provided_'}"}},
        ]
    else:
        header = "🧠 New decision to approve"
        content_blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Decision*\n{summary}"}},
        ]
        if reason:
            content_blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*Why*\n{reason}"}}
            )

    context_text = f"Submitted by <@{author_id}> in <#{channel_id}>"
    if source_permalink:
        context_text += f"  ·  <{source_permalink}|Open original message>"

    return [
        {"type": "header", "text": _pt(header)},
        {"type": "divider"},
        *content_blocks,
        {"type": "context", "elements": [{"type": "mrkdwn", "text": context_text}]},
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": f"easybot_approval_{decision_id}",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": _pt("✅  Approve & Save"),
                    "action_id": "easybot_approve_decision",
                    "value": str(decision_id),
                },
                {
                    "type": "button",
                    "style": "danger",
                    "text": _pt("✕  Discard"),
                    "action_id": "easybot_discard_decision",
                    "value": str(decision_id),
                },
            ],
        },
    ]


# --------------------------------------------------------------------------- #
# Workflow 2 (hit — confident): Match card posted publicly to the channel
# --------------------------------------------------------------------------- #
def match_card(decision: dict) -> list[dict]:
    kind = decision.get("kind") or "decision"
    decision_id = decision["id"]

    if kind == "doubt":
        header = "💡 EasyBot found a saved answer"
        content_blocks: list[dict] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Question*\n{decision['summary_text']}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Answer*\n{decision.get('answer') or '_Not recorded_'}"}},
        ]
    else:
        header = "🧠 EasyBot found a saved decision"
        content_blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Decision*\n{decision['summary_text']}"}},
        ]
        if decision.get("reason_for_decision"):
            content_blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*Why*\n{decision['reason_for_decision']}"}}
            )

    blocks: list[dict] = [
        {"type": "header", "text": _pt(header)},
        {"type": "divider"},
        *content_blocks,
    ]

    stale = _staleness_warning(decision["timestamp"])
    if stale:
        blocks.append(stale)

    blocks += [
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": (
                f"Saved by <@{decision['author_id']}> · "
                f"{_fmt_ts(decision['timestamp'])} · Entry #{decision_id}"
            )}],
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": _pt("📜  View history"),
                    "action_id": "easybot_ask_why_updated",
                    "value": str(decision_id),
                },
                {
                    "type": "button",
                    "text": _pt("👍  Helpful"),
                    "action_id": "easybot_feedback_helpful",
                    "value": str(decision_id),
                },
                {
                    "type": "button",
                    "text": _pt("👎  Not helpful"),
                    "action_id": "easybot_feedback_unhelpful",
                    "value": str(decision_id),
                },
            ],
        },
    ]
    return blocks


# --------------------------------------------------------------------------- #
# Workflow 2 (hit — uncertain): Suggestion card for medium-confidence matches
# --------------------------------------------------------------------------- #
def uncertain_match_card(decision: dict, confidence: float) -> list[dict]:
    kind = decision.get("kind") or "decision"
    decision_id = decision["id"]
    pct = int(confidence * 100)

    if kind == "doubt":
        snippet = decision.get("answer") or decision["summary_text"]
    else:
        snippet = decision["summary_text"]

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":thinking_face: *EasyBot found a possible match* ({pct}% confidence)\n\n"
                    f"_{snippet}_"
                ),
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": (
                f"Saved by <@{decision['author_id']}> · "
                f"{_fmt_ts(decision['timestamp'])} · Entry #{decision_id}"
            )}],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": _pt("📖  View full answer"),
                    "action_id": "easybot_ask_why_updated",
                    "value": str(decision_id),
                },
                {
                    "type": "button",
                    "text": _pt("👍  Yes, this helped"),
                    "action_id": "easybot_feedback_helpful",
                    "value": str(decision_id),
                },
                {
                    "type": "button",
                    "text": _pt("👎  Not what I needed"),
                    "action_id": "easybot_feedback_unhelpful",
                    "value": str(decision_id),
                },
            ],
        },
    ]


# --------------------------------------------------------------------------- #
# Workflow 2 (miss): Fallback card with "Request Owner to Save"
# --------------------------------------------------------------------------- #
def fallback_card(channel_id: str, message_ts: str, question: str) -> list[dict]:
    payload = json.dumps({"channel_id": channel_id, "message_ts": message_ts})
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    ":thinking_face: *No saved answer found.*\n"
                    f"You asked: _{question}_\n\n"
                    "If this question comes up often, the channel owner can save it for everyone."
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": _pt("📬  Request owner to save this"),
                    "action_id": "easybot_request_owner_save",
                    "value": payload,
                }
            ],
        },
    ]


# --------------------------------------------------------------------------- #
# Workflow 3: Audit-trail + history card (includes owner action buttons)
# --------------------------------------------------------------------------- #
def history_card(decision: dict, history_rows: list[dict]) -> list[dict]:
    kind = decision.get("kind") or "decision"
    decision_id = decision["id"]

    if kind == "doubt":
        current_text = (
            f"*Question*\n{decision['summary_text']}\n\n"
            f"*Answer*\n{decision.get('answer') or '_Not recorded_'}"
        )
    else:
        current_text = f"*Decision*\n{decision['summary_text']}"
        if decision.get("reason_for_decision"):
            current_text += f"\n\n*Why*\n{decision['reason_for_decision']}"

    _action_emoji = {"created": "🟢", "approved": "✅", "updated": "✏️", "deleted": "🗑️"}
    timeline_parts = []
    for h in history_rows:
        emoji = _action_emoji.get(h["action"], "•")
        line = f"{emoji} *{h['action'].upper()}* by <@{h['actor_id']}> on {_fmt_ts(h['timestamp'])}"
        if h.get("summary_text"):
            line += f"\n   _{h['summary_text']}_"
        if h.get("remark"):
            line += f"\n   :speech_balloon: _{h['remark']}_"
        timeline_parts.append(line)

    timeline_text = (
        "*Timeline*\n\n" + "\n\n".join(timeline_parts)
        if timeline_parts else "*No history recorded yet.*"
    )

    blocks: list[dict] = [
        {"type": "header", "text": _pt(f"📋  History — Entry #{decision_id}")},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": current_text}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Author: <@{decision['author_id']}>"}]},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": timeline_text}},
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": _pt("✏️  Update decision (owner)"),
                    "action_id": "easybot_open_update_modal",
                    "value": str(decision_id),
                },
                {
                    "type": "button",
                    "style": "danger",
                    "text": _pt("🗑️  Delete entry (owner)"),
                    "action_id": "easybot_delete_decision",
                    "value": str(decision_id),
                    "confirm": {
                        "title": _pt("Delete this entry?"),
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                "*This is permanent.* The entry will be removed from the "
                                "knowledge base.\n\nThe audit trail is preserved."
                            ),
                        },
                        "confirm": _pt("Yes, delete"),
                        "deny": _pt("Cancel"),
                        "style": "danger",
                    },
                },
            ],
        },
    ]
    return blocks


# --------------------------------------------------------------------------- #
# Update modal
# --------------------------------------------------------------------------- #
def update_modal(decision: dict) -> dict:
    is_doubt = decision.get("kind") == "doubt"

    if is_doubt:
        variable_block = {
            "type": "input",
            "block_id": "answer",
            "label": _pt("Updated answer"),
            "hint": _pt("This is shown to anyone who asks a similar question."),
            "element": {
                "type": "plain_text_input",
                "action_id": "v",
                "multiline": True,
                "initial_value": decision.get("answer") or "",
            },
        }
    else:
        variable_block = {
            "type": "input",
            "block_id": "reason",
            "optional": True,
            "label": _pt("Updated reason"),
            "hint": _pt("Why was this decision made? Optional but useful for the team."),
            "element": {
                "type": "plain_text_input",
                "action_id": "v",
                "multiline": True,
                "initial_value": decision.get("reason_for_decision") or "",
            },
        }

    return {
        "type": "modal",
        "callback_id": "easybot_submit_update",
        "private_metadata": str(decision["id"]),
        "title": _pt(f"Update #{decision['id']}"),
        "submit": _pt("Save changes"),
        "close": _pt("Cancel"),
        "blocks": [
            {
                "type": "input",
                "block_id": "summary",
                "label": _pt("Question" if is_doubt else "Updated summary"),
                "element": {
                    "type": "plain_text_input",
                    "action_id": "v",
                    "multiline": True,
                    "initial_value": decision["summary_text"],
                },
            },
            variable_block,
            {
                "type": "input",
                "block_id": "remark",
                "label": _pt("Reason for this update"),
                "hint": _pt("Shown in the history log and announced in the channel."),
                "element": {
                    "type": "plain_text_input",
                    "action_id": "v",
                    "multiline": False,
                    "placeholder": _pt("e.g. Policy changed after team meeting on May 19"),
                },
            },
        ],
    }


# --------------------------------------------------------------------------- #
# Search results card (/easybot search)
# --------------------------------------------------------------------------- #
def search_results_card(
    results: list[dict], query: str, cross_channel: bool = False
) -> list[dict]:
    scope = "all channels" if cross_channel else "this channel"
    if not results:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":mag: No results found for *{query}* in {scope}.",
                },
            }
        ]

    blocks: list[dict] = [
        {"type": "header", "text": _pt(f"🔍  Search: {query[:50]}")},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": (
                f"{len(results)} result{'s' if len(results) != 1 else ''} in {scope}"
            )}],
        },
        {"type": "divider"},
    ]

    for d in results[:10]:
        kind = d.get("kind") or "decision"
        if kind == "doubt":
            text = f"*Q:* {d['summary_text']}\n*A:* {d.get('answer') or '_No answer_'}"
        else:
            text = f"*{d['summary_text']}*"
            if d.get("reason_for_decision"):
                text += f"\n_Why: {d['reason_for_decision']}_"
        channel_suffix = f" · <#{d['channel_id']}>" if cross_channel else ""
        blocks += [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": (
                    f"Entry #{d['id']} · <@{d['author_id']}>{channel_suffix} · "
                    f"{_fmt_ts(d['timestamp'])}"
                )}],
            },
            {"type": "divider"},
        ]

    if len(results) > 10:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"_Showing 10 of {len(results)} results._"}],
        })

    return blocks


# --------------------------------------------------------------------------- #
# List card (/easybot list)
# --------------------------------------------------------------------------- #
def list_card(decisions: list[dict]) -> list[dict]:
    if not decisions:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":inbox_tray: No saved entries in this channel yet.",
                },
            }
        ]

    blocks: list[dict] = [
        {"type": "header", "text": _pt(f"📚  Knowledge Base ({len(decisions)} entries)")},
        {"type": "divider"},
    ]

    for d in decisions[:20]:
        kind = d.get("kind") or "decision"
        emoji = "💡" if kind == "doubt" else "🧠"
        stale_marker = " ⚠️" if (_time.time() - d["timestamp"]) / 86400 > _STALE_DAYS else ""
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{emoji}{stale_marker} *#{d['id']}* — {d['summary_text']}",
            },
        })

    if len(decisions) > 20:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"_Showing 20 of {len(decisions)} entries. Use `/easybot search` to find specific ones._"}],
        })

    return blocks


# --------------------------------------------------------------------------- #
# Weekly digest message (plain text — sent as DM to channel owner)
# --------------------------------------------------------------------------- #
def digest_message(
    channel_id: str,
    new_decisions: list[dict],
    stale_decisions: list[dict],
) -> str:
    lines = [f":calendar: *EasyBot Weekly Digest — <#{channel_id}>*\n"]

    if new_decisions:
        n = len(new_decisions)
        lines.append(f"*{n} new {'entry' if n == 1 else 'entries'} this week:*")
        for d in new_decisions[:5]:
            emoji = "💡" if d.get("kind") == "doubt" else "🧠"
            lines.append(f"  {emoji} _{d['summary_text']}_")
        if n > 5:
            lines.append(f"  _...and {n - 5} more._")
    else:
        lines.append("_No new entries this week._")

    if stale_decisions:
        n = len(stale_decisions)
        not_updated = "entry has not" if n == 1 else "entries have not"
        lines.append(
            f"\n⚠️ *{n} {not_updated} been updated in {_STALE_DAYS}+ days — worth reviewing:*"
        )
        for d in stale_decisions[:3]:
            lines.append(f"  • Entry #{d['id']}: _{d['summary_text'][:80]}_")
        if n > 3:
            lines.append(f"  _...and {n - 3} more._")

    lines.append("\n_Use `/easybot list` to browse all entries._")
    return "\n".join(lines)
