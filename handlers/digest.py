"""Weekly digest — background thread that DMs channel owners every Sunday.

The thread wakes every 30 minutes and checks:
  1. Is it Sunday (UTC)?
  2. Has a digest already been sent for each channel in the last 6 days?

If both conditions are met for a channel, it sends a digest DM to the owner
and records the timestamp in digest_state so it won't re-send.

Configure via env vars:
  EASYBOT_DIGEST_WEEKDAY   0=Mon … 6=Sun  (default 6)
  EASYBOT_DIGEST_HOUR      UTC hour to send (default 9)
  EASYBOT_STALE_DAYS       Days before an entry is flagged stale (default 90)
"""
from __future__ import annotations

import datetime
import logging
import os
import threading
import time

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import db
import views

logger = logging.getLogger("easybot.digest")

_DIGEST_WEEKDAY = int(os.environ.get("EASYBOT_DIGEST_WEEKDAY", "6"))   # Sunday
_DIGEST_HOUR    = int(os.environ.get("EASYBOT_DIGEST_HOUR", "9"))
_STALE_DAYS     = int(os.environ.get("EASYBOT_STALE_DAYS", "90"))
_MIN_INTERVAL   = 6 * 86400   # don't re-send within 6 days


def start_digest_scheduler(token: str) -> None:
    """Spawn the digest background thread. Call once at bot startup."""
    t = threading.Thread(
        target=_digest_loop,
        args=(token,),
        daemon=True,
        name="easybot-digest",
    )
    t.start()
    logger.info("digest scheduler started (weekday=%d hour=%d UTC)", _DIGEST_WEEKDAY, _DIGEST_HOUR)


def _digest_loop(token: str) -> None:
    client = WebClient(token=token)
    while True:
        try:
            now = datetime.datetime.utcnow()
            if now.weekday() == _DIGEST_WEEKDAY and now.hour == _DIGEST_HOUR:
                _send_all_digests(client)
        except Exception:
            logger.exception("digest loop error")
        time.sleep(1800)   # check every 30 minutes


def _send_all_digests(client: WebClient) -> None:
    week_ago = int(time.time()) - 7 * 86400
    for channel_id in db.get_channel_ids_with_decisions():
        last = db.get_last_digest_ts(channel_id)
        if last and (time.time() - last) < _MIN_INTERVAL:
            continue   # already sent this week

        new_decisions   = db.get_recent_decisions(channel_id, week_ago)
        stale_decisions = db.get_stale_decisions(channel_id, _STALE_DAYS)

        if not new_decisions and not stale_decisions:
            continue   # nothing worth reporting

        try:
            info     = client.conversations_info(channel=channel_id)["channel"]
            owner_id = info.get("creator")
            if not owner_id:
                continue

            dm = client.conversations_open(users=owner_id)["channel"]["id"]
            client.chat_postMessage(
                channel=dm,
                text=views.digest_message(channel_id, new_decisions, stale_decisions),
            )
            db.set_last_digest_ts(channel_id, int(time.time()))
            logger.info("digest sent for channel %s to owner %s", channel_id, owner_id)
        except SlackApiError as e:
            logger.warning("digest failed for %s: %s", channel_id, e.response.get("error"))
