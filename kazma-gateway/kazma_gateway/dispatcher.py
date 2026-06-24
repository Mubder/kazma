"""MessageDispatcher — The Reply Contract.

The agent brain is platform-agnostic.  When it generates a response,
it calls ``dispatcher.reply(message, response_text)`` which wraps
the response in an ``OutboundMessage`` and sends it through the
``GatewayManager.send()`` method, routing via the correct adapter.

This is the **only** interface the agent brain uses for outbound
communication.  No platform-specific code ever enters the agent loop.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kazma_gateway import IncomingMessage, OutboundMessage

if TYPE_CHECKING:
    from kazma_gateway.gateway import GatewayManager

logger = logging.getLogger(__name__)


class MessageDispatcher:
    """Routes outbound replies through the correct adapter.

    Usage:
        dispatcher = MessageDispatcher(gateway)
        await dispatcher.reply(incoming_msg, "Hello, human!")
        # → creates OutboundMessage(target_id=..., text=...)
        # → calls gateway.send(outbound)
        # → routes to correct adapter.send()
    """

    def __init__(self, gateway: "GatewayManager") -> None:
        self._gateway = gateway

    async def reply(
        self,
        original_sender_id: str,
        content: str,
        context_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Send a reply, routing through the correct adapter.

        Args:
            original_sender_id: The ``sender_id`` from the incoming message.
            content: The response text.
            context_metadata: Opaque envelope carried through from the
                original IncomingMessage.  The adapter uses this to
                extract raw platform IDs.

        Returns:
            Result string from the adapter.
        """
        out = OutboundMessage(
            target_id=original_sender_id,
            text=content,
            context_metadata=context_metadata or {},
        )
        result = await self._gateway.send(out)
        return result

    async def reply_to_message(
        self,
        message: IncomingMessage,
        content: str,
    ) -> str:
        """Convenience: reply to an IncomingMessage.

        Preserves ``context_metadata`` as an opaque envelope
        (the agent never reads it).
        """
        return await self.reply(
            original_sender_id=message.sender_id,
            content=content,
            context_metadata=message.context_metadata,
        )


def make_send_message_tool(dispatcher: MessageDispatcher) -> dict[str, Any]:
    """Return a tool definition for ``send_message`` that the agent can call."""

    async def _handler(sender_id: str, content: str) -> str:
        return await dispatcher.reply(sender_id, content)

    return {
        "name": "send_message",
        "description": "Send a message to a user.  Use the original sender_id from the incoming message.",
        "parameters": {
            "type": "object",
            "properties": {
                "sender_id": {"type": "string", "description": "The original sender_id (e.g. 'telegram:123456')"},
                "content": {"type": "string", "description": "The message text to send"},
            },
            "required": ["sender_id", "content"],
        },
        "handler": _handler,
        "category": "communication",
    }
