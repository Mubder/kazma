"""Telegram's side of the shared receive record (``kazma_gateway.receive_log``).

Before 2026-09-29 the Telegram adapter dropped an update it could not read (a
sticker, a poll, a contact) without a word and logged a message from a user
outside Allowed User IDs at DEBUG only, so "why did Kazma not answer me on
Telegram" had no answer in the log. These are its reasons, and how an update
names its message.
"""

from __future__ import annotations

from typing import Any

from kazma_gateway.receive_log import COMMON_REASONS

__all__ = ["TELEGRAM_REASONS", "message_of", "update_kind", "where_of"]

TELEGRAM_REASONS: dict[str, str] = {
    **COMMON_REASONS,
    "other_bot": "was addressed to another bot (or Kazma's bot identity is unavailable)",
    "unsupported": "was a kind of message Kazma does not read (a sticker, poll, location or contact)",
    "voice_failed": "was a voice note that could not be transcribed",
    "media_failed": "had a photo or file that could not be downloaded",
    "duplicate": "was an edit or a repeat of a message already answered",
}


def update_kind(update: dict[str, Any]) -> str:
    """The update's type: its one key besides ``update_id``."""
    return next((k for k in update if k != "update_id"), "unknown")


def message_of(update: dict[str, Any]) -> dict[str, Any] | None:
    """The message an update carries (a message, an edit or a channel post)."""
    for key in ("message", "edited_message", "channel_post"):
        if isinstance(update.get(key), dict):
            return update[key]
    return None


def where_of(message: dict[str, Any]) -> str:
    chat = message.get("chat") or {}
    if chat.get("type") == "private":
        return "a private chat"
    title = chat.get("title") or chat.get("id")
    return f"the {chat.get('type') or 'chat'} «{title}»"
