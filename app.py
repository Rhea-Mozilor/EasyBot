"""app.py - EasyBot Slack Bolt app (Socket Mode entry point).

Run with:

    python db_setup.py        # one-time
    python app.py             # boots the bot

Required env vars (see .env.example):
    SLACK_BOT_TOKEN      xoxb- token
    SLACK_APP_TOKEN      xapp- token (Socket Mode, connections:write scope)
    SLACK_SIGNING_SECRET signing secret
    GEMINI_API_KEY       (or OPENAI_API_KEY if LLM_PROVIDER=openai)
"""
from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import db_setup
from handlers import capture, feedback, history, interception, slash
from handlers import digest as digest_handler

# Load env BEFORE we read any os.environ values that handlers use at import time.
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger("easybot")


def build_app() -> App:
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET")
    if not bot_token or not signing_secret:
        logger.error("SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET must be set.")
        sys.exit(1)

    app = App(token=bot_token, signing_secret=signing_secret)

    # Ensure schema exists every time we boot.
    db_setup.init_db()

    # Register every workflow's handlers.
    capture.register(app)        # Workflow 1: reaction_added + Approve/Discard
    interception.register(app)   # Workflow 2: message + fallback button
    history.register(app)        # Workflow 3: Ask Why Updated + Update modal + Delete
    slash.register(app)          # /easybot search | list
    feedback.register(app)       # 👍 / 👎 on match cards

    # @-mention handler — also handles "list" and "search" as a fallback
    # for teams that haven't registered the /easybot slash command yet.
    @app.event("app_mention")
    def handle_mention(event, client):
        import db, views
        from slack_sdk.errors import SlackApiError

        text       = (event.get("text") or "")
        channel_id = event["channel"]
        user_id    = event["user"]

        # Strip the bot mention token (e.g. "<@U123ABC> list")
        # Everything after the first word is the command text.
        parts = text.strip().split(None, 2)   # ["<@BOT>", "list"] or ["<@BOT>", "search", "query"]
        sub   = parts[1].lower() if len(parts) > 1 else ""
        rest  = parts[2] if len(parts) > 2 else ""

        def _reply(msg_text, blocks=None):
            try:
                kwargs = {"channel": channel_id, "user": user_id, "text": msg_text}
                if blocks:
                    kwargs["blocks"] = blocks
                client.chat_postEphemeral(**kwargs)
            except SlackApiError as e:
                logger.warning("mention reply failed: %s", e.response.get("error"))

        if sub == "list":
            decisions = db.list_approved(channel_id)
            _reply("📚 EasyBot Knowledge Base", views.list_card(decisions))

        elif sub == "search":
            cross_channel = "--global" in rest
            query = rest.replace("--global", "").strip()
            if not query:
                _reply("Usage: `@EasyBot search <query>` or `@EasyBot search --global <query>`")
                return
            results = db.search_decisions(
                query, channel_id=None if cross_channel else channel_id
            )
            _reply(f"Search: {query}", views.search_results_card(results, query, cross_channel))

        else:
            _reply(
                "*Hi! I'm EasyBot* — I capture team decisions and answer repeat questions.\n\n"
                ":brain: React with :brain: on any message to capture it *(channel owner only)*.\n"
                ":mag: I automatically reply when I recognise a repeat question.\n\n"
                "*Commands (mention or slash):*\n"
                "• `@EasyBot list` — browse all entries in this channel\n"
                "• `@EasyBot search <query>` — keyword search in this channel\n"
                "• `@EasyBot search --global <query>` — search across all channels\n"
                "• `/easybot list` / `/easybot search` — same, once slash command is set up"
            )

    # Generic error sink so a single handler failure doesn't kill the bot
    @app.error
    def global_error(error, body, logger):
        logger.exception("Unhandled error: %s\nbody=%s", error, body)
        return None

    return app


def main() -> None:
    app = build_app()
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        logger.error(
            "SLACK_APP_TOKEN is missing. Generate one at "
            "Basic Information -> App-Level Tokens (scope: connections:write)."
        )
        sys.exit(1)

    # Start weekly digest scheduler in a background daemon thread.
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    digest_handler.start_digest_scheduler(bot_token)

    logger.info("EasyBot starting in Socket Mode...")
    SocketModeHandler(app, app_token).start()


if __name__ == "__main__":
    main()
