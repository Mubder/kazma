"""What Kazma's Discord connection received, and what became of each message.

Found 2026-09-29: no message from the owner had reached Kazma on Discord for
at least eight days, and nothing said so. The adapter dropped a message that
arrived without text, from a server outside its list, or from a bot, without
a line in the log, and the connector's Test only checked that the token signs
in. This is Discord's side of the shared record (``kazma_gateway.receive_log``):
its reasons, and what READY and a raw MESSAGE_CREATE carry.
"""

from __future__ import annotations

from typing import Any

from kazma_gateway.receive_log import COMMON_REASONS, ReceiveLog

__all__ = ["DROP_REASONS", "DiscordReceiveLog"]

#: Why a message was not answered, in words the Test and the log show.
DROP_REASONS: dict[str, str] = {
    **COMMON_REASONS,
    "from_a_bot": "sent by a bot (Kazma's own posts included)",
    "no_text": (
        "arrived without text or attachment -- in a server channel Discord "
        "sends a bot the text only while its Message Content Intent is on, or "
        "when the message mentions the bot"
    ),
    "no_channel": "named no channel",
    "empty_event": "was empty",
    "server_not_allowed": "came from a server that is not the Guild ID set for Kazma",
}


class DiscordReceiveLog(ReceiveLog):
    """The shared record, fed from Discord's gateway events."""

    def __init__(self) -> None:
        super().__init__(DROP_REASONS)

    def ready(self, data: Any) -> None:
        self.connected_now(new_session=True)
        if isinstance(data, dict):
            guilds = data.get("guilds")
            self.extra["guilds_at_ready"] = len(guilds) if isinstance(guilds, list) else None
            self.extra["bot_user_id"] = str((data.get("user") or {}).get("id") or "") or None

    def drop_event(self, reason: str, data: Any) -> dict[str, Any]:
        """``drop`` for a raw MESSAGE_CREATE payload."""
        d = data if isinstance(data, dict) else {}
        guild = str(d.get("guild_id") or "") or None
        return self.drop(
            reason,
            message_id=d.get("id"),
            author_id=(d.get("author") or {}).get("id"),
            channel_id=d.get("channel_id"),
            where=f"server {guild}" if guild else "a direct message",
            person=reason != "from_a_bot",
        ) | {"guild_id": guild}
