"""Discord send helpers — channel resolve + chunking (Telegram-style)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DISCORD_MAX_MESSAGE_LEN",
    "chunk_message",
    "resolve_channel_id",
]

DISCORD_MAX_MESSAGE_LEN = 2000


def resolve_channel_id(
    context_metadata: dict[str, Any],
    target_id: str,
) -> str | None:
    """Resolve Discord channel snowflake from metadata or target_id."""
    channel_id = context_metadata.get("channel_id")
    if channel_id:
        return str(channel_id)
    if ":" in (target_id or ""):
        return target_id.split(":", 1)[1] or None
    return target_id or None


def chunk_message(text: str, limit: int = DISCORD_MAX_MESSAGE_LEN) -> list[str]:
    """Split *text* into Discord-safe chunks."""
    if not text:
        return [""]
    return [text[i : i + limit] for i in range(0, len(text), limit)]
