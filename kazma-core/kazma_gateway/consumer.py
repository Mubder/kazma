"""Gateway Consumer — Background task that bridges the gateway queue to the agent brain.

Architecture
════════════

    TelegramAdapter ──┐
    DiscordAdapter  ──┤──→ asyncio.Queue ──→ GatewayConsumer ──→ agent.run(msg.content)
                       │                           │
                       │                    dispatcher.reply(sender_id, response)
                       │                           │
                       └──────────────────←── platform adapter.send()
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from kazma_gateway.base import Message
from kazma_gateway.dispatcher import MessageDispatcher

if TYPE_CHECKING:
    from kazma_gateway.gateway import GatewayManager

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.5


# ── Session mapping (sender_id → thread_id) ──────────────────────────

_sessions: dict[str, str] = {}
_reverse_sessions: dict[str, str] = {}


def _resolve_thread(sender_id: str) -> tuple[str, bool]:
    if sender_id in _sessions:
        return _sessions[sender_id], False
    thread_id = str(uuid.uuid4())
    _sessions[sender_id] = thread_id
    _reverse_sessions[thread_id] = sender_id
    return thread_id, True


def get_sender_for_thread(thread_id: str) -> str | None:
    return _reverse_sessions.get(thread_id)


# ── Consumer task ────────────────────────────────────────────────────


async def start_gateway_consumer(
    gateway: "GatewayManager",
    agent: Any,
    poll_interval: float = POLL_INTERVAL,
) -> None:
    """Run forever: pull messages from the gateway queue → agent → reply.

    This is the **only** place the agent brain receives messages from
    external platforms.
    """
    dispatcher = MessageDispatcher(gateway)
    logger.info("[Consumer] Gateway consumer started, waiting for messages...")

    while True:
        try:
            msg = await gateway.consume(timeout=poll_interval)
            if msg is None:
                continue

            await _handle_message(msg, agent, dispatcher)

        except asyncio.CancelledError:
            logger.info("[Consumer] Gateway consumer stopped")
            break
        except Exception as exc:
            logger.exception("[Consumer] Unhandled error: %s", exc)
            await asyncio.sleep(1)


async def _handle_message(
    msg: Message,
    agent: Any,
    dispatcher: MessageDispatcher,
) -> None:
    """Process a single Message through the agent brain and reply."""
    start_ts = time.monotonic()

    thread_id, is_new = _resolve_thread(msg.sender_id)

    logger.info(
        "[Consumer] ← %s: %.100s (thread=%s%s)",
        msg.sender_id,
        msg.content,
        thread_id,
        " NEW" if is_new else "",
    )

    try:
        response = await agent.run(msg.content)
    except Exception as exc:
        logger.exception("[Consumer] agent.run() failed for %s", msg.sender_id)
        response = "⚠️ عذراً، حدث خطأ أثناء معالجة رسالتك."

    result = await dispatcher.reply_to_message(msg, response)

    elapsed = (time.monotonic() - start_ts) * 1000
    logger.info(
        "[Consumer] → %s: done in %.0fms (result=%s)",
        msg.sender_id,
        elapsed,
        result[:60] if isinstance(result, str) else result,
    )


# ── Utility: create a send_message tool for the ReAct loop ──────────


def make_send_message_tool(dispatcher: MessageDispatcher) -> dict[str, Any]:
    """Return a tool definition for ``send_message`` that the agent can call."""

    async def _handler(sender_id: str, content: str, **kwargs: str) -> str:
        return await dispatcher.reply(sender_id, content, **kwargs)

    return {
        "name": "send_message",
        "description": "Send a message to a user.  Use the original sender_id from the incoming message.",
        "parameters": {
            "type": "object",
            "properties": {
                "sender_id": {
                    "type": "string",
                    "description": "The original sender_id (e.g. 'telegram:123456')",
                },
                "content": {
                    "type": "string",
                    "description": "The message text to send",
                },
            },
            "required": ["sender_id", "content"],
        },
        "handler": _handler,
        "category": "communication",
    }
