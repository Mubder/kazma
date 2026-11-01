"""Telegram update parsing — pure helpers (S5 extract)."""

from __future__ import annotations

from typing import Any

from kazma_gateway.gateway import IncomingMessage

__all__ = [
    "advance_offset",
    "extract_message",
    "is_edit_only",
    "message_metadata",
    "parse_text_update",
    "sender_id_of",
]


def extract_message(update: dict[str, Any]) -> dict[str, Any] | None:
    """Return the message object from various Telegram update shapes."""
    return (
        update.get("message")
        or update.get("channel_post")
        or update.get("edited_message")
    )


def is_edit_only(update: dict[str, Any]) -> bool:
    """True for an update that only edits a message already received.

    An edit is not a new agent turn: Telegram fires a second update when the
    user edits, which used to start a second graph invoke and a duplicate
    HITL card on the same thread.
    """
    return "edited_message" in update and "message" not in update and "channel_post" not in update


def message_metadata(update: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    """The routing metadata of *message*, the same for text, voice and files.

    The voice and file paths built their own copy without the forum topic
    (``message_thread_id`` / ``thread_hint``), so a file sent in a topic was
    answered in the chat's main thread, and with ``update_id`` read from the
    message, which has none.
    """
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    user_id = from_user.get("id", 0)
    chat_id = chat.get("id", 0)
    meta: dict[str, Any] = {
        "chat_id": chat_id,
        "user_id": user_id,
        "username": (
            from_user.get("username", "")
            or from_user.get("first_name", "")
            or f"tg_{user_id}"
        ),
        "message_id": message.get("message_id", 0),
        "chat_type": chat.get("type", "private"),
        "update_id": update.get("update_id", 0),
    }
    topic_id = message.get("message_thread_id")
    if topic_id not in (None, "", 0, "0"):
        meta["message_thread_id"] = topic_id
        sender = user_id or chat_id
        meta["thread_hint"] = f"gw-telegram-{sender}-topic-{topic_id}"
    return meta


def sender_id_of(meta: dict[str, Any]) -> str:
    """The gateway's sender id: the user, else the chat (a channel post)."""
    return f"telegram:{meta['user_id']}" if meta.get("user_id") else f"telegram:{meta.get('chat_id')}"


def parse_text_update(update: dict[str, Any]) -> IncomingMessage | None:
    """Parse a Telegram Update into an IncomingMessage for text/caption.

    Returns None if not a usable text message, and for an edit
    (:func:`is_edit_only`). A message carrying a file or a voice note is
    read by the adapter's media path first; this sees its caption only when
    the file could not be read.
    """
    if is_edit_only(update):
        return None
    message = extract_message(update)
    if not message:
        return None

    if not (message.get("chat") or {}).get("id"):
        return None

    text = (message.get("text") or message.get("caption") or "").strip()
    if not text:
        return None

    meta = message_metadata(update, message)
    return IncomingMessage(
        platform="telegram",
        sender_id=sender_id_of(meta),
        text=text,
        context_metadata=meta,
    )


def advance_offset(updates: list[dict[str, Any]], current: int | None) -> int | None:
    """Return the next getUpdates offset after processing *updates*."""
    if not updates:
        return current
    return max(u["update_id"] for u in updates) + 1
