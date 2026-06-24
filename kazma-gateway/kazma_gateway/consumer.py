"""Gateway Consumer Agent Handler — Bridges the gateway to the agent brain.

Registered via ``GatewayManager.on_message(handler)``.  Every time a
platform adapter enqueues an ``IncomingMessage``, this handler receives
it, runs it through ``agent.run()``, and returns an ``OutboundMessage``
with the response.

The agent brain sees **only** ``msg.text`` — it has zero awareness of
platforms, protocols, or transport layers.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from kazma_gateway import IncomingMessage, OutboundMessage

logger = logging.getLogger(__name__)


def make_agent_handler(agent: Any) -> Any:
    """Create a GatewayManager-compatible message handler.

    The returned callable has the signature:
        async def handler(msg: IncomingMessage) -> OutboundMessage | None

    It calls ``agent.run(msg.text)`` and wraps the response in an
    ``OutboundMessage`` addressed back to the original sender.

    Args:
        agent: A KazmaAgent instance with an ``run(text) -> str`` method.

    Returns:
        An async callable suitable for ``GatewayManager.on_message()``.
    """

    async def handle_message(msg: IncomingMessage) -> OutboundMessage | None:
        start_ts = time.monotonic()

        logger.info(
            "[Consumer] ← %s: %.100s",
            msg.sender_id,
            msg.text,
        )

        # ── Run through agent brain (platform agnostic) ────────────
        try:
            response = await agent.run(msg.text)
        except Exception as exc:
            logger.exception("[Consumer] agent.run() failed for %s", msg.sender_id)
            response = "⚠️ عذراً، حدث خطأ أثناء معالجة رسالتك."

        # ── Build reply ────────────────────────────────────────────
        out = OutboundMessage(
            target_id=msg.sender_id,
            text=response,
            context_metadata=msg.context_metadata,
        )

        elapsed = (time.monotonic() - start_ts) * 1000
        logger.info(
            "[Consumer] → %s: done in %.0fms",
            msg.sender_id,
            elapsed,
        )

        return out

    return handle_message
