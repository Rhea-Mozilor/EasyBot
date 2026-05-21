"""Feedback handlers — 👍 / 👎 buttons on match cards.

Records one vote per user per decision (upsert). The response is always
ephemeral so only the voter sees it; the public match card stays unchanged.
"""
from __future__ import annotations

import logging

from slack_bolt import App

import db

logger = logging.getLogger("easybot.feedback")


def register(app: App) -> None:

    @app.action("easybot_feedback_helpful")
    def handle_helpful(ack, body, respond):
        ack()
        decision_id = int(body["actions"][0]["value"])
        user_id     = body["user"]["id"]
        db.record_feedback(decision_id, user_id, 1)
        respond({
            "response_type": "ephemeral",
            "replace_original": False,
            "text": ":thumbsup: Thanks — good to know this helped!",
        })

    @app.action("easybot_feedback_unhelpful")
    def handle_unhelpful(ack, body, respond):
        ack()
        decision_id = int(body["actions"][0]["value"])
        user_id     = body["user"]["id"]
        db.record_feedback(decision_id, user_id, -1)
        respond({
            "response_type": "ephemeral",
            "replace_original": False,
            "text": ":thumbsdown: Thanks for the feedback — we'll note that.",
        })
