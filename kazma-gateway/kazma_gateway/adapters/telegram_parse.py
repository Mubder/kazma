"""Telegram update parsing — pure helpers (S5 extract)."""

from __future__ import annotations

from typing import Any

from kazma_gateway.gateway import IncomingMessage

__all__ = [
    "advance_offset",
    "extract_message",
    "parse_text_update",
]


def extract_message(update: dict[str, Any]) -> dict[str, Any] | None:
    """Return the message object from various Telegram update shapes."""
    return (
        update.get("message")
        or update.get("channel_post")
        or update.get("edited_message")
    )


def parse_text_update(update: dict[str, Any]) -> IncomingMessage | None:
    """Parse a Telegram Update into an IncomingMessage for text/caption.

    Returns None if not a usable text message (voice handled elsewhere).
    """
    message = extract_message(update)
    if not message:
        return None

    chat_id = message.get("chat", {}).get("id")
    if not chat_id:
        return None

    text = (message.get("text") or message.get("caption") or "").strip()
    if not text:
        return None

    from_user = message.get("from", {})
    user_id = from_user.get("id", 0)
    username = (
        from_user.get("username", "")
        or from_user.get("first_name", "")
        or f"tg_{user_id}"
    )
    sender_id = f"telegram:{user_id}" if user_id else f"telegram:{chat_id}"

    return IncomingMessage(
        platform="telegram",
        sender_id=sender_id,
        text=text,
        context_metadata={
            "chat_id": chat_id,
            "user_id": user_id,
            "username": username,
            "message_id": message.get("message_id", 0),
            "chat_type": message.get("chat", {}).get("type", "private"),
            "update_id": update.get("update_id", 0),
        },
    )


def advance_offset(updates: list[dict[str, Any]], current: int | None) -> int | None:
    """Return the next getUpdates offset after processing *updates*."""
    if not updates:
        return current
    return max(u["update_id"] for u in updates) + 1
