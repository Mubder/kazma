"""Slack send helpers — channel resolve + chunking."""

from __future__ import annotations

from typing import Any

__all__ = [
    "SLACK_MAX_MESSAGE_LEN",
    "chunk_message",
    "resolve_channel_id",
]

SLACK_MAX_MESSAGE_LEN = 3900  # leave headroom under 4000


def resolve_channel_id(
    context_metadata: dict[str, Any],
    target_id: str,
) -> str | None:
    channel_id = context_metadata.get("channel_id")
    if channel_id:
        return str(channel_id)
    if ":" in (target_id or ""):
        return target_id.split(":", 1)[1] or None
    return target_id or None


def chunk_message(text: str, limit: int = SLACK_MAX_MESSAGE_LEN) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + limit] for i in range(0, len(text), limit)]
