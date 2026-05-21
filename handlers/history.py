"""Workflow 3 - Audit trail + the Update modal.

`easybot_ask_why_updated` is the action_id on the [Ask Why Updated] button in
views.match_card.  Clicking it replaces the ephemeral match card with the
full chronological history of that decision (from `decision_history`).

We also offer an [Update decision] button on the history card; only the
channel owner can use it.  Submitting the modal calls db.update_decision
which writes a row to `decision_history` with the actor and remark - that
remark is exactly what later [Ask Why Updated] clicks will show.
"""
from __future__ import annotations

import logging

from slack_bolt import App

import db
import permissions
import views

logger = logging.getLogger("easybot.history")


def register(app: App) -> None:

    # ----- [Ask Why Updated] button on an intercepted match card ------- #
    @app.action("easybot_ask_why_updated")
    def handle_ask_why_updated(ack, body, respond):
        ack()
        decision_id = int(body["actions"][0]["value"])
        decision = db.get_decision(decision_id)
        if not decision:
            respond({"response_type": "ephemeral", "replace_original": True,
                     "text": "That decision no longer exists."})
            return

        history_rows = db.list_history(decision_id)
        # history_card now includes Update + Delete action buttons
        blocks = views.history_card(decision, history_rows)
        respond({
            "response_type": "ephemeral",
            "replace_original": True,
            "text": f"History for Entry #{decision_id}",
            "blocks": blocks,
        })

    # ----- "Open thread" passthrough action (URL is in the button) ----- #
    @app.action("easybot_open_source")
    def handle_open_source(ack):
        ack()    # noop - Slack handles URL nav natively

    # ----- Owner clicks "Update decision" -> open the modal ------------ #
    @app.action("easybot_open_update_modal")
    def handle_open_update(ack, body, client, respond):
        ack()
        decision_id = int(body["actions"][0]["value"])
        decision = db.get_decision(decision_id)
        if not decision:
            respond({"response_type": "ephemeral", "text": "That decision no longer exists."})
            return

        info = permissions.conversation_info(client, decision["channel_id"])
        if not permissions.is_channel_owner(client, info, body["user"]["id"]):
            respond({"response_type": "ephemeral",
                     "text": ":lock: Only the channel owner can update this decision."})
            return

        client.views_open(
            trigger_id=body["trigger_id"],
            view=views.update_modal(decision),
        )

    # ----- Modal submit ------------------------------------------------ #
    @app.view("easybot_submit_update")
    def handle_submit_update(ack, body, view, client):
        decision_id = int(view["private_metadata"])
        values = view["state"]["values"]
        new_summary = values["summary"]["v"]["value"].strip()
        remark      = values["remark"]["v"]["value"].strip()

        # Modal renders either an 'answer' block (doubts) or a 'reason' block (decisions)
        new_answer = None
        new_reason = None
        if "answer" in values:
            new_answer = (values["answer"]["v"]["value"] or "").strip() or None
        else:
            new_reason = (values["reason"]["v"]["value"] or "").strip() or None

        if not remark:
            ack(response_action="errors",
                errors={"remark": "Please give a brief reason for the update."})
            return

        ack()
        actor_id = body["user"]["id"]
        updated = db.update_decision(
            decision_id,
            new_summary=new_summary,
            new_reason=new_reason,
            new_answer=new_answer,
            actor_id=actor_id,
            update_reason=remark,
        )
        if not updated:
            return

        # Announce the update in-channel
        try:
            client.chat_postMessage(
                channel=updated["channel_id"],
                text=(
                    f":pencil2: Entry *#{decision_id}* updated by <@{actor_id}> "
                    f"— _{remark}_"
                ),
            )
        except Exception as exc:
            logger.warning("post-update announce failed: %s", exc)

    # ----- Owner clicks "Delete entry" --------------------------------- #
    @app.action("easybot_delete_decision")
    def handle_delete_decision(ack, body, client, respond):
        ack()
        decision_id = int(body["actions"][0]["value"])
        decision = db.get_decision(decision_id)
        if not decision:
            respond({"response_type": "ephemeral", "text": "That entry no longer exists."})
            return

        info = permissions.conversation_info(client, decision["channel_id"])
        if not permissions.is_channel_owner(client, info, body["user"]["id"]):
            respond({"response_type": "ephemeral",
                     "text": ":lock: Only the channel owner can delete entries."})
            return

        actor_id = body["user"]["id"]
        deleted = db.delete_decision(decision_id, actor_id)
        if not deleted:
            respond({"response_type": "ephemeral",
                     "text": ":warning: Could not delete — entry may already be deleted."})
            return

        respond({"response_type": "ephemeral", "replace_original": True,
                 "text": f":wastebasket: Entry #{decision_id} deleted."})

        try:
            client.chat_postMessage(
                channel=decision["channel_id"],
                text=f":wastebasket: Entry *#{decision_id}* was removed from the knowledge base by <@{actor_id}>.",
            )
        except Exception as exc:
            logger.warning("delete announce failed: %s", exc)
