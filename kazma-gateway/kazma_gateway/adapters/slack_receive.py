"""Slack's side of the shared receive record (``kazma_gateway.receive_log``).

Before 2026-09-29 the Slack adapter logged every message it RECEIVED at DEBUG
only, and dropped one from a channel outside Allowed Channels, or one it
could not read, without a line at INFO -- so the log could not say whether a
Slack message ever arrived. These are its reasons, and how an event names
its message.
"""

from __future__ import annotations

from typing import Any

from kazma_gateway.receive_log import COMMON_REASONS

__all__ = ["SLACK_REASONS", "drop_reason", "event_key", "where_of"]

SLACK_REASONS: dict[str, str] = {
    **COMMON_REASONS,
    "duplicate": "was a repeated native command envelope already dispatched",
    "unsupported_command": "named an unsupported command; use /kazma help",
    "from_a_bot": "sent by a bot or an app (Kazma's own posts included)",
    "not_a_new_message": "was an edit, a deletion or a channel notice, not a new message",
    "no_text": "had no text or file",
    "no_channel": "named no channel",
    "team_not_allowed": "came from a workspace outside Allowed Teams",
    "channel_not_allowed": "came from a channel outside Allowed Channels",
}


def drop_reason(event: dict[str, Any] | None) -> str | None:
    """Why ``slack_parse.parse_message_event`` made nothing of *event*: a key
    of SLACK_REASONS, or None when the event is no message at all (a
    reaction, a member joining...). Mirrors its checks in their order."""
    if not event or event.get("type") not in ("message", "app_mention"):
        return None
    if "bot_id" in event:
        return "from_a_bot"
    subtype = event.get("subtype", "")
    if subtype and subtype != "bot_message":
        return "not_a_new_message"
    if not event.get("channel"):
        return "no_channel"
    if not event.get("user"):
        return "not_a_new_message"
    return "no_text"


def event_key(event: dict[str, Any]) -> str | None:
    """A message's id in the record: its channel and timestamp."""
    ts = event.get("ts") or (event.get("message") or {}).get("ts")
    channel = event.get("channel")
    return f"{channel}:{ts}" if channel and ts else None


def where_of(channel_id: Any) -> str:
    cid = str(channel_id or "")
    return "a direct message" if cid.startswith("D") else f"channel {cid or '?'}"
