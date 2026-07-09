"""Telegram send helpers — chat_id resolve + message chunking (S5 extract)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LEN = 4096


def resolve_chat_id(
    context_metadata: dict[str, Any],
    target_id: str,
) -> int | None:
    """Resolve a numeric Telegram chat_id from metadata or target_id."""
    chat_id = context_metadata.get("chat_id")
    if chat_id:
        try:
            return int(chat_id)
        except (TypeError, ValueError):
            logger.error("[telegram] Invalid chat_id in metadata: %s", chat_id)
            return None
    if ":" in target_id:
        try:
            return int(target_id.split(":", 1)[1])
        except (ValueError, IndexError):
            logger.error(
                "[telegram] Cannot parse chat_id from target_id: %s",
                target_id,
            )
            return None
    logger.error("[telegram] No chat_id available for send()")
    return None


def chunk_message(text: str, max_len: int = TELEGRAM_MAX_MESSAGE_LEN) -> list[str]:
    """Split *text* into Telegram-safe message chunks."""
    if not text:
        return [""]
    return [text[i : i + max_len] for i in range(0, len(text), max_len)]
