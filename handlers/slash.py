"""Slash command handler — /easybot.

Subcommands:
  /easybot list                         list all saved entries in this channel
  /easybot search <query>               keyword search in this channel
  /easybot search --global <query>      keyword search across all channels
  /easybot help  (or anything else)     show usage

The slash command must be registered in the Slack App Configuration
(Slash Commands section) before it will reach this handler.
"""
from __future__ import annotations

import logging

from slack_bolt import App
from slack_sdk.errors import SlackApiError

import db
import views

logger = logging.getLogger("easybot.slash")


def register(app: App) -> None:

    @app.command("/easybot")
    def handle_easybot(ack, body, client):
        ack()
        text       = (body.get("text") or "").strip()
        channel_id = body.get("channel_id", "")
        user_id    = body.get("user_id", "")

        lower = text.lower()

        # ── /easybot list ──────────────────────────────────────────────── #
        if lower.startswith("list"):
            decisions = db.list_approved(channel_id)
            _ephemeral(client, channel_id, user_id,
                       "📚 EasyBot Knowledge Base",
                       views.list_card(decisions))

        # ── /easybot search [--global] <query> ─────────────────────────── #
        elif lower.startswith("search"):
            rest = text[6:].strip()            # strip the word "search"
            cross_channel = "--global" in rest
            query = rest.replace("--global", "").strip()

            if not query:
                _ephemeral(client, channel_id, user_id,
                           "Usage",
                           [{"type": "section", "text": {"type": "mrkdwn", "text": (
                               "*Usage:*\n"
                               "• `/easybot search <query>` — search this channel\n"
                               "• `/easybot search --global <query>` — search all channels"
                           )}}])
                return

            results = db.search_decisions(
                query,
                channel_id=None if cross_channel else channel_id,
            )
            _ephemeral(client, channel_id, user_id,
                       f"Search: {query}",
                       views.search_results_card(results, query, cross_channel=cross_channel))

        # ── /easybot help (default) ─────────────────────────────────────── #
        else:
            _ephemeral(client, channel_id, user_id,
                       "EasyBot Help",
                       [{"type": "section", "text": {"type": "mrkdwn", "text": (
                           "*EasyBot commands:*\n"
                           "• `/easybot list` — show all saved entries in this channel\n"
                           "• `/easybot search <query>` — keyword search in this channel\n"
                           "• `/easybot search --global <query>` — search across all channels\n\n"
                           "To *save* a new entry, the channel owner reacts :brain: on any message."
                       )}}])


def _ephemeral(client, channel_id: str, user_id: str, text: str, blocks: list) -> None:
    try:
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=text,
            blocks=blocks,
        )
    except SlackApiError as e:
        logger.warning("slash ephemeral failed: %s", e.response.get("error"))
