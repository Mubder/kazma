"""Agent message handler — bridges UniversalMessage to the LangGraph supervisor.

This module creates an async handler that:
  1. Receives a UniversalMessage from the gateway queue.
  2. Builds a LangGraph-compatible state with the user message.
  3. Invokes the compiled supervisor graph.
  4. Sends the response back through the gateway's send() method.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from kazma_gateway.schemas import UniversalMessage

logger = logging.getLogger(__name__)


def create_graph_handler(
    graph: Any,
    manager: Any,  # GatewayManager (avoid circular import)
    system_prompt: str = "",
    cost_breaker: Any = None,
) -> Callable[[UniversalMessage], Awaitable[None]]:
    """Create an async handler that processes messages through LangGraph.

    Args:
        graph: Compiled LangGraph supervisor graph.
        manager: GatewayManager instance (for send() routing).
        system_prompt: System prompt for the agent.
        cost_breaker: Optional CostCircuitBreaker for budget control.

    Returns:
        Async handler function compatible with manager.on_message().
    """
    # Per-sender session tracking (sender_id → thread_id)
    _sessions: dict[str, str] = {}

    async def handler(msg: UniversalMessage) -> None:
        """Process a single UniversalMessage through the agent graph."""
        sender = msg.sender_id

        # Resolve or create session
        if sender not in _sessions:
            _sessions[sender] = f"gateway-{msg.platform}-{msg.metadata.get('chat_id', sender)}"

        thread_id = _sessions[sender]

        # Cost breaker gate
        if cost_breaker and cost_breaker.should_halt():
            await manager.send(msg.reply_to, "⚠️ ميزانية الجلسة انتهت. (Budget exceeded)")
            return

        if cost_breaker:
            cost_breaker.record_user_interaction()

        # Build state
        try:
            from kazma_core.agent.state import initial_supervisor_state
        except ImportError:
            logger.error("kazma_core not available — cannot process messages")
            await manager.send(msg.reply_to, "⚠️ Agent core not available.")
            return

        state = initial_supervisor_state(thread_id=thread_id)
        user_msg: dict[str, Any] = {"role": "user", "content": msg.content}
        user_msg["_gateway"] = msg.to_dict()
        state["messages"] = [user_msg]

        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        # Invoke graph
        start = time.monotonic()
        try:
            result_state = await graph.ainvoke(state, config)
            duration_ms = (time.monotonic() - start) * 1000

            messages = result_state.get("messages", [])
            assistant_text = ""
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "assistant" and m.get("content"):
                    assistant_text = m["content"]
                    break

            if not assistant_text:
                assistant_text = "(No response generated)"

            logger.info(
                "[agent-handler] Graph completed in %.0fms (thread=%s, platform=%s)",
                duration_ms,
                thread_id,
                msg.platform,
            )

            # Send response back through the gateway
            await manager.send(msg.reply_to, assistant_text)

        except Exception:
            logger.exception("[agent-handler] Graph invocation failed for %s", sender)
            await manager.send(msg.reply_to, "⚠️ حدث خطأ أثناء معالجة رسالتك. (Processing error)")

    return handler
