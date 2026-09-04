"""Discord send helpers — channel resolve + chunking (Telegram-style)."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DISCORD_MAX_MESSAGE_LEN",
    "chunk_message",
    "resolve_channel_id",
    "sanitize_outbound",
]

DISCORD_MAX_MESSAGE_LEN = 2000

# Audit G9b: agent/tool output is posted verbatim to Discord, which interprets
# mention/broadcast markup. A reply containing "@everyone" / "@here" (or raw
# <@&role> / <#channel> / <@user> snowflake mentions) would ping/broadcast.
# These come from untrusted content (knowledge library, tool/web output) that
# flows into the assistant reply, so we neutralize them before posting.
_EVERYONE_HERE_RE = re.compile(r"@(?=(everyone|here)\b)", re.IGNORECASE)
_DISCORD_MENTION_RE = re.compile(r"<@[&!#]?(\d+)>", re.IGNORECASE)


def sanitize_outbound(text: str) -> str:
    """Neutralize Discord mention/broadcast markup in outbound agent text.

    - ``@everyone`` / ``@here`` → ``@​everyone`` / ``@​here`` (zero-width space
      breaks the ping; renders visibly to the user without notifying anyone).
    - Raw ``<@user>`` / ``<@&role>`` / ``<#channel>`` snowflake mentions are
      stripped to a harmless placeholder so agent output can't ping arbitrary
      users/roles/channels.
    """
    if not text:
        return text
    # Break @everyone/@here by inserting a zero-width space after the @.
    text = _EVERYONE_HERE_RE.sub("@\u200b", text)
    # Strip raw snowflake-mention markup.
    text = _DISCORD_MENTION_RE.sub("@user", text)
    return text


def resolve_channel_id(
    context_metadata: dict[str, Any],
    target_id: str,
) -> str | None:
    """Resolve Discord channel snowflake from metadata or target_id."""
    channel_id = context_metadata.get("channel_id")
    if channel_id:
        return str(channel_id)
    if ":" in (target_id or ""):
        # LAST segment: inbound DM sender ids are dual-colon
        # (discord:{user_id}:{channel_id}) — split(":", 1)[1] used to yield
        # "user_id:channel_id", an invalid snowflake (2026-09-04 audit).
        return target_id.split(":")[-1] or None
    return target_id or None


def chunk_message(text: str, limit: int = DISCORD_MAX_MESSAGE_LEN) -> list[str]:
    """Split *text* into Discord-safe chunks.

    Empty text yields NO chunks — the old ``[""]`` made Discord 400 on empty
    content and blocked the attachments sent after the text.
    """
    if not text:
        return []
    return [text[i : i + limit] for i in range(0, len(text), limit)]
