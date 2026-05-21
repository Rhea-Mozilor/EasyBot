"""Workflow 2 - Proactive Interception.

On every channel message we:

1. Silently drop anything from a 1:1 DM, anything from a bot, edits/deletes.
2. Look up approved decisions for this channel.
3. Ask the LLM whether the message matches any of them.
4. HIT  -> ephemeral private card to the asker with the answer + buttons.
5. MISS -> if the message looks like a question, send an ephemeral fallback
   with the [Request Owner to Save This] button. (Statements / banter that
   don't look like questions are ignored entirely - the channel stays quiet.)

The fallback button DMs the channel owner asking them to take a look and
trigger 🧠 if appropriate.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

from slack_bolt import App
from slack_sdk.errors import SlackApiError

import db
import llm
import permissions
import views

logger = logging.getLogger("easybot.interception")

# Cache LLM match results for 5 minutes so repeat questions don't burn quota.
# Key: (channel_id, normalised_text) -> (matched_decision_id_or_None, expiry_epoch)
_match_cache: dict[tuple, tuple] = {}
_CACHE_TTL = 300  # seconds


def _cache_key(channel_id: str, text: str) -> tuple:
    return (channel_id, " ".join(text.lower().split()))


def _get_cached(channel_id: str, text: str) -> tuple[bool, object]:
    """Returns (hit, match_or_None). hit=False means cache miss."""
    key = _cache_key(channel_id, text)
    entry = _match_cache.get(key)
    if entry and time.time() < entry[1]:
        logger.info("intercept: cache hit for %r", text[:60])
        return True, entry[0]
    return False, None


def _set_cache(channel_id: str, text: str, match: object) -> None:
    _match_cache[_cache_key(channel_id, text)] = (match, time.time() + _CACHE_TTL)


# Broad "does this look like a question?" detector. Used ONLY to decide
# whether to show the "no match found" fallback - we don't want to nag the
# channel on every casual sentence. The LLM matcher runs on ALL substantive
# messages regardless, so true matches always surface even if the phrasing
# slips past this regex.
_QUESTION_RE = re.compile(
    r"("
    r"\?"                                                         # any ?
    r"|^\s*(what|whats|when|where|why|how|who|whose|which|"
    r"should|shall|can|could|would|may|might|"
    r"do|does|did|is|are|was|were|will|wont|"
    r"has|have|had|am)\b"                                         # wh + aux openers
    r"|\b(anyone\s+know|anybody\s+know|does\s+anyone|"
    r"do\s+we\s+have|do\s+we\s+know|how\s+do\s+(i|we|you)|"
    r"i\s+(want\s+to\s+know|need\s+to\s+know|wonder|"
    r"don'?t\s+know|am\s+not\s+sure|'?m\s+not\s+sure)|"
    r"not\s+sure\s+(if|whether|how|what|when))\b"
    r")",
    re.IGNORECASE,
)


def _looks_like_question(text: str) -> bool:
    return bool(text and _QUESTION_RE.search(text.strip()))


def _is_trivial(text: str) -> bool:
    """Skip messages too short / too noisy to bother matching against."""
    words = text.split()
    if len(words) < 2:
        return True
    if len(text) < 8:
        return True
    return False


def register(app: App) -> None:

    # ----- The main message listener ----------------------------------- #
    @app.event("message")
    def handle_message(event, client):
        # Drop edits, deletes, joins, bot echoes, ephemeral replies, etc.
        if event.get("subtype"):
            return
        if event.get("bot_id"):
            return
        if event.get("channel_type") == "im":
            return                              # silent DM drop per Rule 1

        text = (event.get("text") or "").strip()
        if not text or _is_trivial(text):
            return

        channel_id = event.get("channel")
        user_id    = event.get("user")
        ts         = event.get("ts")
        if not (channel_id and user_id and ts):
            return

        # Channel-type re-check via the API (defense in depth)
        info = permissions.conversation_info(client, channel_id)
        if not permissions.is_allowed_conversation(info):
            logger.info("intercept: dropped (not allowed conversation) channel=%s", channel_id)
            return

        # Only run LLM matching for question-shaped messages — preserves
        # free-tier quota and keeps the channel silent on statements/banter.
        if not _looks_like_question(text):
            return

        candidates = db.list_approved(channel_id)
        logger.info("intercept: channel=%s user=%s candidates=%d text=%r",
                    channel_id, user_id, len(candidates), text[:80])

        hit, match = _get_cached(channel_id, text)
        if not hit:
            match = llm.find_match(text, candidates) if candidates else None
            _set_cache(channel_id, text, match)

        if match:
            conf = match.get("_match_confidence", 1.0)
            confident_threshold = float(
                os.environ.get("EASYBOT_CONFIDENT_THRESHOLD", "0.75")
            )
            if conf >= confident_threshold:
                card = views.match_card(match)
                fallback_text = ":mag: EasyBot found a previous answer."
            else:
                card = views.uncertain_match_card(match, conf)
                fallback_text = ":thinking_face: EasyBot found a possible match."
            try:
                client.chat_postMessage(
                    channel=channel_id,
                    text=fallback_text,
                    blocks=card,
                )
            except SlackApiError as e:
                logger.warning("match post failed: %s", e.response.get("error"))
            return

        try:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="EasyBot couldn't find a saved answer.",
                blocks=views.fallback_card(channel_id, ts, text),
            )
        except SlackApiError as e:
            logger.warning("ephemeral miss failed: %s", e.response.get("error"))

    # ----- "Request Owner to Save This" fallback button --------------- #
    @app.action("easybot_request_owner_save")
    def handle_request_owner_save(ack, body, client, respond):
        ack()
        try:
            payload = json.loads(body["actions"][0]["value"])
        except (ValueError, KeyError):
            respond({"response_type": "ephemeral", "text": "Bad request payload."})
            return

        channel_id = payload.get("channel_id")
        message_ts = payload.get("message_ts")
        info = permissions.conversation_info(client, channel_id)
        owner_id = permissions.channel_owner_id(info)

        if not owner_id:
            respond({"response_type": "ephemeral", "replace_original": False,
                     "text": ":warning: No channel owner found - can't ping anyone."})
            return

        permalink = None
        try:
            permalink = client.chat_getPermalink(
                channel=channel_id, message_ts=message_ts
            ).get("permalink")
        except SlackApiError:
            pass

        try:
            dm = client.conversations_open(users=owner_id)["channel"]["id"]
            text = (
                f":bell: <@{body['user']['id']}> asked something in <#{channel_id}> "
                "that doesn't have a saved answer yet. If this is worth keeping, "
                f"react :brain: on the thread."
            )
            if permalink:
                text += f"\n<{permalink}|Open the message>"
            client.chat_postMessage(channel=dm, text=text)
            respond({"response_type": "ephemeral", "replace_original": True,
                     "text": ":mailbox_with_mail: Sent to the channel owner."})
        except SlackApiError as e:
            logger.warning("could not DM owner: %s", e.response.get("error"))
            respond({"response_type": "ephemeral", "replace_original": False,
                     "text": "Couldn't notify the owner. Try again later."})
