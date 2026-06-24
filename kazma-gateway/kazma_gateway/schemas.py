"""Universal message schema for the unified message bus.

All platform adapters normalize incoming messages into UniversalMessage
before placing them on the GatewayManager's asyncio.Queue.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class UniversalMessage:
    """Normalized message from any platform.

    Every adapter converts its platform-specific payload into this schema
    so the agent loop only deals with one message type.

    Attributes:
        platform: Source platform identifier (e.g. "telegram", "discord").
        sender_id: Platform-specific sender ID (e.g. "telegram:12345").
        content: The message text content.
        metadata: Arbitrary platform metadata (chat_id, username, etc.).
        timestamp: Unix timestamp when the message was received.
        reply_to: Optional target for sending replies (e.g. "telegram:12345").
            Defaults to sender_id if not set.
    """

    platform: str
    sender_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    reply_to: str = ""

    def __post_init__(self) -> None:
        if not self.reply_to:
            self.reply_to = self.sender_id

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for logging/storage."""
        return {
            "platform": self.platform,
            "sender_id": self.sender_id,
            "content": self.content[:200],
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "reply_to": self.reply_to,
        }
