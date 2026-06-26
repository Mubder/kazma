"""MessageDispatcher — The Reply Contract + Slash Command Router + Markdown Renderer.

The agent brain is platform-agnostic.  When it generates a response,
it calls ``dispatcher.reply(message, response_text)`` which wraps
the response in an ``OutboundMessage`` and sends it through the
``GatewayManager.send()`` method, routing via the correct adapter.

Before reaching the agent, the dispatcher intercepts slash commands
(/help, /reset, /status, /undo, /edit, …) and resolves them instantly
(<50ms) without any LLM call.

Message tracking:
  The dispatcher maintains a lightweight message map
  ``{chat_id: [(user_msg_id, bot_tracking_id), ...]}`` so that
  /undo and /edit can operate on the most recent exchange.

Markdown rendering is applied per platform:
  - Telegram → parse_mode="MarkdownV2" (built-in)
  - Discord → native Markdown (no parse_mode needed)
  - Slack    → mrkdwn=true
  - Fallback → markdown→HTML conversion
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage
from kazma_gateway.slash_commands import (  # noqa: F401 — re-exported
    CMD_EDIT,
    CMD_UNDO,
    is_slash_command,
    resolve_slash_command,
)

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


# ── Error formatting ──────────────────────────────────────────────────


def _friendly_error(exc: Exception) -> str:
    """Return a user-friendly error snippet for a given exception.

    Maps common exception types to clear, actionable messages.
    """
    msg = str(exc).strip()
    name = type(exc).__name__

    # Rate-limit / timeout patterns
    if "429" in msg or "rate" in msg.lower() or "too many" in msg.lower():
        return "⏳ Rate limited — please wait a moment and try again."
    if "timeout" in msg.lower() or name == "TimeoutError":
        return "🤔 The model is taking longer than expected. Retrying..."
    if "connection" in msg.lower() or name in ("ConnectError", "ConnectionError"):
        return "📡 Connection issue. I'll retry automatically."
    if "tool" in msg.lower():
        return f"🔧 Tool execution issue — {msg[:120]}"

    # Generic fallback — truncate for safety
    return f"{name}: {msg[:150]}"


# ── Message tracking ──────────────────────────────────────────────────


class MessageTracker:
    """Lightweight message-pair store per chat.

    Maps ``chat_id -> [(user_msg_id, bot_tracking_id), ...]`` so that
    /undo and /edit can find the most recent exchange.

    Thread-safe for asyncio (single-threaded context)."""

    def __init__(self) -> None:
        self._map: dict[str, list[tuple[int, str]]] = {}

    def record(self, chat_id: str, user_msg_id: int, bot_tracking_id: str) -> None:
        """Append a new exchange to the chat's history."""
        if chat_id not in self._map:
            self._map[chat_id] = []
        self._map[chat_id].append((user_msg_id, bot_tracking_id))

    def pop_last(self, chat_id: str) -> tuple[int, str] | None:
        """Pop and return the most recent exchange for a chat.

        Returns:
            (user_msg_id, bot_tracking_id) or None if no history.
        """
        stack = self._map.get(chat_id)
        if not stack:
            return None
        return stack.pop()

    def peek_last(self, chat_id: str) -> tuple[int, str] | None:
        """Return the most recent exchange without removing it."""
        stack = self._map.get(chat_id)
        if not stack:
            return None
        return stack[-1]

    def history_length(self, chat_id: str) -> int:
        """Return how many exchanges are tracked for a chat."""
        return len(self._map.get(chat_id, []))


# ── Main dispatcher ──────────────────────────────────────────────────


class MessageDispatcher:
    """Routes outbound replies through the correct adapter.

    Also handles slash command resolution before the LLM is called.

    Message tracking:
        Every reply() call records the exchange in a per-chat map so
        /undo and /edit can locate the last response.

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
        self._tracker = MessageTracker()

    # ── Chat-ID extraction ───────────────────────────────────────────

    @staticmethod
    def _extract_chat_id(
        sender_id: str,
        context_metadata: dict[str, Any] | None,
    ) -> str:
        """Derive a stable chat-scoped key from sender_id + metadata.

        Prefers ``chat_id`` from context_metadata; falls back to sender_id.
        """
        if context_metadata and "chat_id" in context_metadata:
            return str(context_metadata["chat_id"])
        return sender_id

    # ── Slash command resolution ─────────────────────────────────────

    def resolve(self, text: str, context: dict[str, Any] | None = None) -> str | None:
        """Check if *text* is a slash command and return an instant response.

        For /undo and /edit the dispatcher injects itself + chat_id into the
        context so the command handler can operate on the message map.

        Args:
            text: The raw message text.
            context: Optional session context for status/model/cost commands.

        Returns:
            Response string if it's a known command, None otherwise.
        """
        if not is_slash_command(text):
            return None

        # Inject dispatcher reference + chat_id for undo/edit
        ctx = dict(context or {})
        raw_chat_id = ctx.get("chat_id", ctx.get("sender_id", "unknown"))
        ctx["_dispatcher"] = self
        ctx["_chat_id"] = str(raw_chat_id)

        result = resolve_slash_command(text, ctx)
        if result is not None:
            logger.info("[Dispatcher] Slash command resolved: %.50s", text)
        return result

    # ── Reply (with tracking) ────────────────────────────────────────

    async def reply(
        self,
        original_sender_id: str,
        content: str,
        context_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Send a reply, routing through the correct adapter.

        Applies platform-appropriate Markdown rendering and records the
        exchange in the message tracker.

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

        # ── Track the exchange ───────────────────────────────────────
        chat_id = self._extract_chat_id(original_sender_id, context_metadata)
        user_msg_id = (context_metadata or {}).get("message_id", 0)
        bot_tracking_id = f"msg-{uuid.uuid4().hex[:8]}"

        # Inject tracking id so the adapter can return it (future-proof)
        meta = dict(context_metadata or {})
        meta["_bot_tracking_id"] = bot_tracking_id

        out = OutboundMessage(
            target_id=original_sender_id,
            text=content,
            context_metadata={
                **meta,
                "parse_mode": parse_mode,
            },
        )

        try:
            result = await self._gateway.send(out)
        except TimeoutError:
            logger.warning("[Dispatcher] Send timed out for %s", original_sender_id)
            return "🤔 The model is taking longer than expected. Retrying..."
        except ConnectionError:
            logger.warning("[Dispatcher] Connection issue for %s", original_sender_id)
            return "📡 Connection issue. I'll retry automatically."
        except Exception as exc:
            logger.exception("[Dispatcher] Send failed for %s", original_sender_id)
            return f"⚠️ Failed to send: {_friendly_error(exc)}"

        # Record the exchange
        self._tracker.record(chat_id, user_msg_id, bot_tracking_id)
        logger.debug(
            "[Dispatcher] Tracked %s: user_msg=%s bot_track=%s (stack=%d)",
            chat_id,
            user_msg_id,
            bot_tracking_id,
            self._tracker.history_length(chat_id),
        )

        return str(result)

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

    # ── Undo / Edit support ──────────────────────────────────────────

    def undo_last(self, chat_id: str) -> tuple[int, str] | None:
        """Pop the last exchange from the tracker.

        Args:
            chat_id: The chat-scoped key (from _extract_chat_id).

        Returns:
            (user_msg_id, bot_tracking_id) or None if no history.
        """
        return self._tracker.pop_last(chat_id)

    def get_last_tracking_id(self, chat_id: str) -> str | None:
        """Return the bot tracking id of the last response (without popping)."""
        pair = self._tracker.peek_last(chat_id)
        return pair[1] if pair else None

    @property
    def tracker(self) -> MessageTracker:
        """Expose the tracker for slash commands that need direct access."""
        return self._tracker


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
