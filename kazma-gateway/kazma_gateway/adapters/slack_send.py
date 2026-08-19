"""Slack send helpers — channel resolve + chunking."""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "SLACK_MAX_MESSAGE_LEN",
    "chunk_message",
    "resolve_channel_id",
    "sanitize_outbound",
]

SLACK_MAX_MESSAGE_LEN = 3900  # leave headroom under 4000

# Audit G9b: agent/tool output is posted verbatim to Slack with mrkdwn=True, so
# broadcast/mention markup is interpreted. A reply containing <!everyone> /
# <!here> / <!channel> or <@U123> mentions would broadcast/ping. These come
# from untrusted content (knowledge library, tool/web output) flowing into the
# assistant reply, so we neutralize them before posting.
_SLACK_BROADCAST_RE = re.compile(r"<!(everyone|here|channel)\b", re.IGNORECASE)
_SLACK_MENTION_RE = re.compile(r"<@[WUB]\w+>", re.IGNORECASE)
_SLACK_RAW_AT_RE = re.compile(r"@(?=(everyone|here|channel)\b)", re.IGNORECASE)


def sanitize_outbound(text: str) -> str:
    """Neutralize Slack broadcast/mention markup in outbound agent text.

    - ``<!everyone>`` / ``<!here>`` / ``<!channel>`` and bare ``@everyone`` /
      ``@here`` / ``@channel`` → de-pinged (zero-width space after ``@``).
    - ``<@U123>`` / ``<@W123>`` / ``<@B123>`` user mentions → ``@user``.
    """
    if not text:
        return text
    text = _SLACK_BROADCAST_RE.sub("<!\u200b\\1", text)
    text = _SLACK_MENTION_RE.sub("@user", text)
    text = _SLACK_RAW_AT_RE.sub("@\u200b", text)
    return text


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
    """Split *text* into Slack-safe chunks.

    Empty text yields NO chunks — the old ``[""]`` made Slack reject the
    message (``must_not_be_blank``) and the attachments after it were never
    sent (same fix as Telegram/Discord; deep-audit 2026-08-19).
    """
    if not text:
        return []
    return [text[i : i + limit] for i in range(0, len(text), limit)]
