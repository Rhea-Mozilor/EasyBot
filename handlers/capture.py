"""Workflow 1 - Capture & Approve.

Trigger: user reacts with the configured emoji (default 🧠 / `:brain:`) on a
channel message.

Gatekeeper:
    * conversation must NOT be a 1:1 DM (we silently drop those)
    * conversation must be a public channel, private channel, or mpim
    * the reacting user must be the channel creator (or a workspace admin)

Action: fetch the thread, ask the LLM to extract the decision, stage as
'pending' in SQLite, DM the owner with an [Approve & Save] / [Discard] card.

On approval the row flips to 'approved' and we add a :white_check_mark:
reaction to the original message so everyone in the channel sees that it
was captured.
"""
from __future__ import annotations

import logging
import os

from slack_bolt import App
from slack_sdk.errors import SlackApiError

import db
import llm
import permissions
import views

logger = logging.getLogger("easybot.capture")

CAPTURE_EMOJI  = os.environ.get("EASYBOT_CAPTURE_EMOJI", "brain")        # 🧠
DOUBT_EMOJI    = os.environ.get("EASYBOT_DOUBT_EMOJI",   "question")     # ❓
APPROVED_EMOJI = os.environ.get("EASYBOT_APPROVED_EMOJI", "white_check_mark")


def register(app: App) -> None:

    # ----- reaction_added: the entry point for Workflows 1 & "doubt" --- #
    @app.event("reaction_added")
    def handle_reaction(event, client, logger):
        reaction = event.get("reaction")
        # 1. Branch on the trigger emoji: brain -> decision, question -> Q&A doubt
        if reaction == CAPTURE_EMOJI:
            kind = "decision"
        elif reaction == DOUBT_EMOJI:
            kind = "doubt"
        else:
            return

        item = event.get("item") or {}
        if item.get("type") != "message":
            return

        channel_id = item.get("channel")
        message_ts = item.get("ts")
        user_id    = event.get("user")
        if not (channel_id and message_ts and user_id):
            return

        # 2. Gatekeeper: channel type + DM filter
        info = permissions.conversation_info(client, channel_id)
        if permissions.is_dm(info):
            return                              # silently drop DMs
        if not permissions.is_allowed_conversation(info):
            return                              # silently drop anything weird

        # 3. Owner-only
        if not permissions.is_channel_owner(client, info, user_id):
            try:
                emoji_hint = ":brain:" if kind == "decision" else ":question:"
                client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=(
                        ":lock: Only the channel owner can trigger EasyBot "
                        f"capture. Ask the owner to react with {emoji_hint}."
                    ),
                )
            except SlackApiError as e:
                logger.warning("ephemeral failed: %s", e.response.get("error"))
            return

        # 4. De-dupe: if we've already captured this exact source message, skip
        existing = db.get_decision_by_source(channel_id, message_ts)
        if existing:
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text=f":information_source: Already captured (#{existing['id']}, "
                     f"{existing['kind']}, status {existing['status']}).",
            )
            return

        # 5. Pull the thread the message belongs to
        thread_ts = _root_ts(client, channel_id, message_ts)
        thread = _fetch_thread(client, channel_id, thread_ts)
        if not thread:
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text=":warning: Couldn't read this thread. Make sure I'm invited to the channel.",
            )
            return

        # 6. Single LLM call that BOTH classifies (decision vs doubt) AND
        #    extracts the right fields. The user doesn't have to choose
        #    between 🧠 and ❓ — the bot figures it out from the content.
        original_msg = next((m for m in thread if m.get("ts") == message_ts), thread[0])
        raw_text = (original_msg.get("text") or "").strip()

        # The ❓ emoji is still honored as an explicit "this is a Q&A" hint,
        # but 🧠 alone is enough — the classifier handles either case.
        if kind == "doubt":
            # Explicit ❓ trigger -> force Q&A extraction
            extracted = llm.summarize_doubt(thread, triggering_ts=message_ts)
            classified_kind = "doubt"
            summary = extracted["question"]
            answer  = extracted["answer"] or None
            reason  = None
        else:
            # 🧠 trigger -> auto-classify
            extracted = llm.classify_and_extract(thread, triggering_ts=message_ts)
            classified_kind = extracted["kind"]
            summary = extracted["summary"]
            answer  = (extracted["answer"] or None) if classified_kind == "doubt" else None
            reason  = (extracted["reason"] or None) if classified_kind == "decision" else None
            kind    = classified_kind   # carry forward into storage & view

        # ---- Fallbacks for whichever kind we ended up with ---- #
        if kind == "doubt":
            if not summary:
                root_text = (thread[0].get("text") or "").strip()
                if root_text and root_text != raw_text:
                    logger.info("LLM empty question; falling back to thread root.")
                    summary = root_text
            if not answer and raw_text:
                # If the reacted-to message IS the question (typical when
                # someone just hits 🧠 on a question), we have no answer yet.
                # Don't reuse the question as its own answer.
                if raw_text.strip() != (summary or "").strip():
                    logger.info("LLM empty answer; falling back to reacted message.")
                    answer = raw_text
            if not summary or not answer:
                client.chat_postEphemeral(
                    channel=channel_id, user=user_id,
                    text=(
                        ":no_entry_sign: This looks like a question that hasn't "
                        "been answered yet in the thread. Wait for an answer, "
                        "then react :brain: on the answer message (or :question:)."
                    ),
                )
                return
        else:  # decision
            if not summary:
                if not raw_text:
                    client.chat_postEphemeral(
                        channel=channel_id, user=user_id,
                        text=":no_entry_sign: That message is empty - nothing to capture.",
                    )
                    return
                logger.info("LLM empty; falling back to raw text for decision.")
                summary = raw_text

        # 7. author_id = the person who made the decision / asked the question.
        if kind == "doubt":
            author_id = (thread[0].get("user")
                         or original_msg.get("user") or user_id)
        else:
            author_id = original_msg.get("user") or user_id

        # 7b. Duplicate detection — warn the owner if a very similar entry exists.
        existing_decisions = db.list_approved(channel_id)
        if existing_decisions:
            duplicate = llm.find_duplicate(summary, existing_decisions)
            if duplicate:
                try:
                    dm = client.conversations_open(users=user_id)["channel"]["id"]
                    client.chat_postMessage(
                        channel=dm,
                        text=(
                            f":warning: *Possible duplicate detected!*\n"
                            f"The captured entry is very similar to existing Entry "
                            f"*#{duplicate['id']}:*\n_{duplicate['summary_text']}_\n\n"
                            "Please check whether it's already saved before approving."
                        ),
                    )
                except SlackApiError:
                    pass

        # 8. Stage as pending
        decision_id = db.create_pending_decision(
            channel_id=channel_id,
            message_ts=message_ts,
            summary_text=summary,
            author_id=author_id,
            reason_for_decision=reason,
            captured_by=user_id,
            kind=kind,
            answer=answer,
        )

        # 9. DM the owner with the Approve & Save card
        permalink = _permalink(client, channel_id, message_ts)
        try:
            dm = client.conversations_open(users=user_id)["channel"]["id"]
            client.chat_postMessage(
                channel=dm,
                text=f"New {kind} draft from <#{channel_id}>",
                blocks=views.approval_card(
                    decision_id=decision_id,
                    channel_id=channel_id,
                    summary=summary,
                    author_id=author_id,
                    reason=reason,
                    source_permalink=permalink,
                    kind=kind,
                    answer=answer,
                ),
            )
        except SlackApiError as e:
            logger.warning("could not DM owner: %s", e.response.get("error"))
            # fall back to ephemeral in the channel
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text=f"New {kind} draft (DM blocked - showing here):",
                blocks=views.approval_card(
                    decision_id=decision_id,
                    channel_id=channel_id,
                    summary=summary,
                    author_id=author_id,
                    reason=reason,
                    source_permalink=permalink,
                    kind=kind,
                    answer=answer,
                ),
            )

    # ----- [Approve & Save] button ------------------------------------- #
    @app.action("easybot_approve_decision")
    def handle_approve(ack, body, client, respond):
        ack()
        decision_id = int(body["actions"][0]["value"])
        decision = db.get_decision(decision_id)
        if not decision:
            respond({"replace_original": True, "text": "That draft no longer exists."})
            return

        # The approver must be the channel's owner (or workspace admin)
        info = permissions.conversation_info(client, decision["channel_id"])
        if not permissions.is_channel_owner(client, info, body["user"]["id"]):
            respond({"response_type": "ephemeral", "replace_original": False,
                     "text": ":lock: Only the channel owner can approve this."})
            return

        # Flip the row to approved + write an 'approved' history entry
        updated = db.approve_decision(decision_id, body["user"]["id"])

        # Confirm in the owner's DM
        respond({
            "replace_original": True,
            "text": f":white_check_mark: Approved & saved as Entry #{decision_id}.",
        })

        # Mark the source message in-channel with a tick reaction
        try:
            client.reactions_add(
                channel=updated["channel_id"],
                timestamp=updated["message_ts"],
                name=APPROVED_EMOJI,
            )
        except SlackApiError as e:
            if e.response.get("error") != "already_reacted":
                logger.warning("could not add :%s: reaction: %s", APPROVED_EMOJI, e.response.get("error"))

        # DM the original author so they know their message was captured.
        approver_id = body["user"]["id"]
        author_id   = updated.get("author_id")
        if author_id and author_id != approver_id:
            try:
                kind_label = "Q&A" if updated.get("kind") == "doubt" else "decision"
                author_dm = client.conversations_open(users=author_id)["channel"]["id"]
                client.chat_postMessage(
                    channel=author_dm,
                    text=(
                        f":white_check_mark: Your {kind_label} in <#{updated['channel_id']}> "
                        f"was saved to the team knowledge base by <@{approver_id}>.\n"
                        f"_{updated['summary_text']}_"
                    ),
                )
            except SlackApiError as e:
                logger.warning("could not DM author: %s", e.response.get("error"))

    # ----- [Discard] button -------------------------------------------- #
    @app.action("easybot_discard_decision")
    def handle_discard(ack, body, respond):
        ack()
        decision_id = int(body["actions"][0]["value"])
        db.discard_decision(decision_id)
        respond({"replace_original": True, "text": ":wastebasket: Draft discarded."})


# --------------------------------------------------------------------------- #
# Slack helpers
# --------------------------------------------------------------------------- #
def _root_ts(client, channel_id: str, message_ts: str) -> str:
    """Find the thread root for a message (the message itself if it isn't
    in a thread). Falls back to the message_ts on error."""
    try:
        resp = client.conversations_history(
            channel=channel_id, latest=message_ts, inclusive=True, limit=1
        )
        msgs = resp.get("messages") or []
        if msgs and msgs[0].get("thread_ts"):
            return msgs[0]["thread_ts"]
        return message_ts
    except SlackApiError:
        return message_ts


def _fetch_thread(client, channel_id: str, thread_ts: str) -> list[dict]:
    try:
        resp = client.conversations_replies(channel=channel_id, ts=thread_ts, limit=200)
        return resp.get("messages") or []
    except SlackApiError as e:
        logger.warning("conversations.replies failed: %s", e.response.get("error"))
        return []


def _permalink(client, channel_id: str, message_ts: str) -> str | None:
    try:
        return client.chat_getPermalink(channel=channel_id, message_ts=message_ts).get("permalink")
    except SlackApiError:
        return None
