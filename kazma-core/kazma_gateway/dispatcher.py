"""MessageDispatcher — The Reply Contract.

The agent brain is platform-agnostic.  When it generates a response,
it calls ``dispatcher.reply(message, response_text)`` which routes
the reply back through the correct adapter based on the original
message's ``sender_id`` prefix (e.g. 'telegram:12345' → TelegramAdapter).

This is the **only** interface the agent brain uses for outbound
communication.  No platform-specific code ever enters the agent loop.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kazma_gateway.base import Message

if TYPE_CHECKING:
    from kazma_gateway.gateway import GatewayManager

logger = logging.getLogger(__name__)


class MessageDispatcher:
    """Routes outbound replies through the correct adapter.

    Usage:
        dispatcher = MessageDispatcher(gateway)
        await dispatcher.reply(incoming_msg, "Hello, human!")
        # → calls gateway.send("telegram:12345", "Hello, human!")
        #   which routes to TelegramAdapter.send()
    """

    def __init__(self, gateway: "GatewayManager") -> None:
        self._gateway = gateway

    async def reply(
        self,
        original_sender_id: str,
        content: str,
        **kwargs: Any,
    ) -> str:
        """Send a reply back to the sender, routing through the correct adapter.

        Args:
            original_sender_id: The ``sender_id`` from the incoming Message
                (e.g. ``'telegram:123456'``).  The dispatcher extracts the
                platform prefix to find the right adapter.
            content: The response text.
            **kwargs: Additional parameters forwarded to the adapter's
                ``send()`` method (e.g. ``parse_mode``).

        Returns:
            Platform message ID or error string.
        """
        result = await self._gateway.send(original_sender_id, content, **kwargs)
        logger.debug(
            "[Dispatcher] reply to %s: %.80s → %s",
            original_sender_id,
            content,
            result[:60],
        )
        return result

    async def reply_to_message(
        self,
        message: Message,
        content: str,
        **kwargs: Any,
    ) -> str:
        """Convenience: reply to a Message object instead of a raw sender_id.

        Preserves the ``metadata`` as an opaque envelope (immutable by
        convention — the agent never reads it).
        """
        return await self.reply(message.sender_id, content, **kwargs)

    async def broadcast(self, content: str, platforms: list[str] | None = None) -> dict[str, str]:
        """Send the same message to all running adapters (or a subset)."""
        results: dict[str, str] = {}
        for name, adapter in self._gateway._adapters.items():
            if platforms and adapter.platform not in platforms:
                continue
            if adapter.status != "running":
                continue
            result = await self._gateway.send(f"{adapter.platform}:broadcast", content)
            results[name] = result
        return results
