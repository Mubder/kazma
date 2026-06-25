"""MessageDispatcher — The Reply Contract + Slash Command Router + Markdown Renderer.

The agent brain is platform-agnostic.  When it generates a response,
it calls ``dispatcher.reply(message, response_text)`` which wraps
the response in an ``OutboundMessage`` and sends it through the
``GatewayManager.send()`` method, routing via the correct adapter.

Before reaching the agent, the dispatcher intercepts slash commands
(/help, /reset, /status, …) and resolves them instantly (<50ms)
without any LLM call.

Markdown rendering is applied per platform:
  - Telegram → parse_mode="MarkdownV2" (built-in)
  - Discord → native Markdown (no parse_mode needed)
  - Slack    → mrkdwn=true
  - Fallback → markdown→HTML conversion
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage
from kazma_gateway.slash_commands import is_slash_command, resolve_slash_command

if TYPE_CHECKING:
    from kazma_gateway.gateway import GatewayManager

logger = logging.getLogger(__name__)


# ── Markdown rendering ───────────────────────────────────────────────


def _markdown_to_html(text: str) -> str:
    """Convert basic Markdown to HTML for platforms that don't support it natively.

    Handles: bold, italic, inline code, code blocks, links.
    """
    # Code blocks first (preserve inner content)
    text = re.sub(
        r"```(\w*)\n(.*?)```",
        lambda m: f"<pre><code>{m.group(2)}</code></pre>",
        text,
        flags=re.DOTALL,
    )
    # Inline code
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    # Links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    # Newlines to <br> for non-block content
    text = text.replace("\n", "<br>")
    return text


def _platform_parse_mode(platform: str) -> str | None:
    """Return the parse_mode appropriate for a given platform.

    Returns:
        "MarkdownV2" for Telegram, None for Discord/Slack (native),
        "HTML" for fallback.
    """
    mapping = {
        "telegram": "MarkdownV2",
        "discord": None,  # native Markdown
        "slack": None,    # mrkdwn=true is set at adapter level
    }
    return mapping.get(platform, "HTML")


# ── Main dispatcher ──────────────────────────────────────────────────


class MessageDispatcher:
    """Routes outbound replies through the correct adapter.

    Also handles slash command resolution before the LLM is called.

    Usage:
        dispatcher = MessageDispatcher(gateway)
        # Check and resolve slash commands (returns None if not a command)
        response = dispatcher.resolve(text, context)
        if response:
            await dispatcher.reply(incoming_msg, response)
        else:
            result = await agent.run(text)
            await dispatcher.reply(incoming_msg, result)
    """

    def __init__(self, gateway: GatewayManager) -> None:
        self._gateway = gateway

    def resolve(self, text: str, context: dict[str, Any] | None = None) -> str | None:
        """Check if *text* is a slash command and return an instant response.

        Args:
            text: The raw message text.
            context: Optional session context for status/model/cost commands.

        Returns:
            Response string if it's a known command, None otherwise.
        """
        if not is_slash_command(text):
            return None
        result = resolve_slash_command(text, context)
        if result is not None:
            logger.info("[Dispatcher] Slash command resolved: %.50s", text)
        return result

    async def reply(
        self,
        original_sender_id: str,
        content: str,
        context_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Send a reply, routing through the correct adapter.

        Applies platform-appropriate Markdown rendering.

        Args:
            original_sender_id: The ``sender_id`` from the incoming message.
            content: The response text.
            context_metadata: Opaque envelope carried through from the
                original IncomingMessage.

        Returns:
            Result string from the adapter.
        """
        # Determine platform from sender_id
        platform = original_sender_id.split(":")[0] if ":" in original_sender_id else "unknown"

        # Apply Markdown rendering
        parse_mode = _platform_parse_mode(platform)
        if parse_mode == "HTML":
            content = _markdown_to_html(content)

        out = OutboundMessage(
            target_id=original_sender_id,
            text=content,
            context_metadata={
                **(context_metadata or {}),
                "parse_mode": parse_mode,
            },
        )
        result = await self._gateway.send(out)
        return result

    async def reply_to_message(
        self,
        message: IncomingMessage,
        content: str,
    ) -> str:
        """Convenience: reply to an IncomingMessage."""
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
