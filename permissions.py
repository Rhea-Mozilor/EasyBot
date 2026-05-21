"""permissions.py - channel-type gate and channel-owner check.

Rule of thumb used everywhere:

    if not allowed_conversation(client, channel_id):
        return                       # silently drop the event

    if not is_channel_owner(...):
        respond(...)                 # ephemeral error
        return
"""
from __future__ import annotations

import logging
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger("easybot.permissions")


def conversation_info(client: WebClient, channel_id: str) -> dict:
    try:
        return client.conversations_info(channel=channel_id).get("channel", {}) or {}
    except SlackApiError as e:
        logger.warning("conversations.info failed for %s: %s", channel_id, e.response.get("error"))
        return {}


def is_dm(info: dict) -> bool:
    return bool(info.get("is_im"))


def is_allowed_conversation(info: dict) -> bool:
    """True only for public_channel, private_channel, or mpim.
    Returns False for 1:1 DMs and for empty / unknown payloads.
    """
    if not info:
        return False
    if info.get("is_im"):
        return False
    return bool(
        info.get("is_channel")
        or info.get("is_group")           # private channel
        or info.get("is_private")
        or info.get("is_mpim")
    )


def channel_owner_id(info: dict) -> Optional[str]:
    """Return the channel's creator (the closest thing Slack has to 'owner').
    For mpim Slack returns no creator - callers should treat that as a
    "first user to engage is owner" case if they want."""
    return info.get("creator")


def is_workspace_admin(client: WebClient, user_id: str) -> bool:
    try:
        u = client.users_info(user=user_id).get("user", {})
        return bool(u.get("is_admin") or u.get("is_owner") or u.get("is_primary_owner"))
    except SlackApiError:
        return False


def is_channel_owner(client: WebClient, info: dict, user_id: str) -> bool:
    """The strict gate used by Workflow 1. True if the user is the
    channel's creator OR a workspace admin/owner."""
    creator = channel_owner_id(info)
    if creator and creator == user_id:
        return True
    return is_workspace_admin(client, user_id)
