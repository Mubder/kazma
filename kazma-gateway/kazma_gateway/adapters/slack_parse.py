"""Slack event → IncomingMessage pure helpers (Telegram-style extract)."""

from __future__ import annotations

from typing import Any

from kazma_gateway.gateway import Attachment, IncomingMessage

__all__ = ["is_new_message", "parse_message_event"]

#: Message subtypes that are a new message to read. A file shared with or
#: without words is ``file_share``; any other subtype (an edit, a deletion,
#: a channel notice) is not new. Every file a person sent on Slack was left as
#: "not a new message" until 2026-11-01 (the same day a Telegram file sent
#: with a caption was found never reaching the agent).
_NEW_MESSAGE_SUBTYPES = frozenset({"", "bot_message", "file_share"})


def is_new_message(event: dict[str, Any]) -> bool:
    """True when the message event's subtype is one Kazma reads."""
    return (event.get("subtype") or "") in _NEW_MESSAGE_SUBTYPES


def parse_message_event(event: dict[str, Any] | None) -> IncomingMessage | None:
    """Parse a raw Slack event dict into an IncomingMessage.

    Returns None for events that should be skipped (bot messages,
    edits, empty text, non-message types, missing fields).
    """
    if event is None:
        return None

    event_type = event.get("type", "")
    if event_type not in ("message", "app_mention"):
        return None

    if "bot_id" in event:
        return None

    if not is_new_message(event):
        return None

    channel_id = event.get("channel")
    if not channel_id:
        return None

    user_id = event.get("user", "")
    if not user_id:
        return None

    text = event.get("text", "")
    raw_files = event.get("files") or []

    if not text and not raw_files:
        return None

    # Strip leading @bot mention on app_mention events
    if event_type == "app_mention" and text:
        parts = text.split(maxsplit=1)
        if parts and parts[0].startswith("<@"):
            text = parts[1] if len(parts) > 1 else ""

    ts = event.get("ts", "")
    team_id = event.get("team", "")
    username = event.get("username") or f"slack_{user_id}"

    attachments: list[Attachment] = []
    for f in raw_files:
        mime = (f.get("mimetype") or "").lower()
        if mime.startswith("image/"):
            kind = "image"
        elif mime.startswith("video/"):
            kind = "video"
        elif mime.startswith("audio/"):
            kind = "audio"
        else:
            kind = "file"
        attachments.append(
            Attachment(
                kind=kind,
                mime=mime or "application/octet-stream",
                filename=f.get("name", "") or f"slack_{f.get('id', 'file')}",
                url=f.get("url_private_download") or f.get("url_private"),
                meta={
                    "file_id": f.get("id"),
                    "source": "slack",
                    "size": f.get("size"),
                },
            )
        )

    msg_text = text or (f"[{attachments[0].kind}]" if attachments else "")
    thread_ts = event.get("thread_ts")
    meta: dict[str, Any] = {
        "channel_id": channel_id,
        "user_id": user_id,
        "team_id": team_id,
        "thread_ts": thread_ts,
        "message_ts": ts,
        "username": username,
        "media": bool(attachments),
    }
    # Replies in a Slack thread carry thread_ts ≠ this message's ts.
    if thread_ts and str(thread_ts) != str(ts or ""):
        safe = str(thread_ts).replace(".", "-")
        meta["thread_hint"] = f"gw-slack-{user_id}-thread-{safe}"
    return IncomingMessage(
        platform="slack",
        sender_id=f"slack:{user_id}",
        text=msg_text,
        attachments=attachments,
        context_metadata=meta,
    )
