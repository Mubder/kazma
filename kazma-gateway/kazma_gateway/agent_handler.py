"""Agent message handler — bridges IncomingMessage to the LangGraph supervisor.

Platform isolation contract:
    The Brain (LangGraph graph) NEVER sees platform-specific identifiers.
    chat_id, user_id, message_id, update_id, chat_type are stored in a
    side-cache (_session_map) OUTSIDE the graph state and restored only
    when constructing the OutboundMessage for the return path.

    Graph state["_gateway"] contains ONLY:
        - thread_id:    stable session identifier
        - display_name: sender's display name (platform-agnostic)
        - platform:     "telegram" / "discord" (string, not an ID)

    Everything else lives in _session_map[thread_id] and is popped
    back onto the OutboundMessage.context_metadata when replying.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
# Side-cache — platform IDs live HERE, never in the graph state
# ══════════════════════════════════════════════════════════════════════════

_session_map: dict[str, dict[str, Any]] = {}

# Platform-specific keys that must NEVER enter graph state
_PLATFORM_KEYS = frozenset(
    {
        "chat_id",
        "user_id",
        "message_id",
        "update_id",
        "chat_type",
    }
)


# ══════════════════════════════════════════════════════════════════════════
# State builder — platform-agnostic
# ══════════════════════════════════════════════════════════════════════════


def _build_initial_state(msg: IncomingMessage) -> dict[str, Any]:
    """Build a platform-agnostic graph state from an IncomingMessage.

    Side-effects:
        - Stores full context_metadata in _session_map[thread_id]
        - The graph state's _gateway block contains ZERO platform IDs

    Args:
        msg: The incoming message from the gateway queue.

    Returns:
        LangGraph-compatible initial state dict.
    """
    ctx = msg.context_metadata

    # Resolve or generate a stable thread_id
    thread_id = ctx.get("thread_id") or str(uuid.uuid4())

    # Store full platform context in side-cache (NEVER enters the graph)
    _session_map[thread_id] = dict(ctx)

    # Build graph state with ONLY platform-agnostic fields
    try:
        from kazma_core.agent.state import initial_supervisor_state

        state = initial_supervisor_state(thread_id=thread_id)
    except ImportError:
        state = {"thread_id": thread_id, "messages": []}

    state["_gateway"] = {
        "thread_id": thread_id,
        "display_name": ctx.get("username") or "unknown",
        "platform": msg.platform,
    }

    # Attach the user message
    state["messages"] = [{"role": "user", "content": msg.text}]

    return state


# ══════════════════════════════════════════════════════════════════════════
# Handler factory
# ══════════════════════════════════════════════════════════════════════════


def create_graph_handler(
    graph: Any,
    manager: Any,  # GatewayManager (avoid circular import)
    system_prompt: str = "",
    cost_breaker: Any = None,
) -> Callable[[IncomingMessage], Awaitable[None]]:
    """Create an async handler that processes messages through LangGraph.

    Args:
        graph:          Compiled LangGraph supervisor graph.
        manager:        GatewayManager instance (for send() routing).
        system_prompt:  System prompt for the agent.
        cost_breaker:   Optional CostCircuitBreaker for budget control.

    Returns:
        Async handler function compatible with manager.on_message().
    """
    # Per-sender session tracking (sender_id → thread_id)
    _sessions: dict[str, str] = {}

    async def handler(msg: IncomingMessage) -> None:
        """Process a single IncomingMessage through the agent graph."""
        sender = msg.sender_id

        # Resolve or create session
        if sender not in _sessions:
            chat_id = msg.context_metadata.get("chat_id", sender)
            _sessions[sender] = f"gateway-{msg.platform}-{chat_id}"

        thread_id = _sessions[sender]

        # Inject the resolved thread_id into context_metadata
        # so _build_initial_state can pick it up
        msg.context_metadata["thread_id"] = thread_id

        # Cost breaker gate
        if cost_breaker and cost_breaker.should_halt():
            # Restore platform context for the reply
            ctx = _session_map.get(thread_id, msg.context_metadata)
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text="⚠️ ميزانية الجلسة انتهت. (Budget exceeded)",
                    context_metadata=ctx,
                )
            )
            return

        if cost_breaker:
            cost_breaker.record_user_interaction()

        # ── Build platform-agnostic state ──────────────────────────
        state = _build_initial_state(msg)

        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        # ── Invoke graph ───────────────────────────────────────────
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

            # ── Restore platform IDs from side-cache ───────────────
            ctx = _session_map.pop(thread_id, {})

            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=assistant_text,
                    context_metadata=ctx,
                )
            )

        except Exception:
            logger.exception("[agent-handler] Graph invocation failed for %s", sender)
            ctx = _session_map.pop(thread_id, msg.context_metadata)
            await manager.send(
                OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text="⚠️ حدث خطأ أثناء معالجة رسالتك. (Processing error)",
                    context_metadata=ctx,
                )
            )

    return handler


def _build_target_id(platform: str, ctx: dict[str, Any]) -> str:
    """Build a platform-prefixed target ID from context_metadata.

    Args:
        platform: "telegram", "discord", etc.
        ctx: The restored context_metadata (may be empty on error).

    Returns:
        e.g. "telegram:12345" or "telegram:unknown"
    """
    # Telegram uses chat_id
    chat_id = ctx.get("chat_id")
    if chat_id is not None:
        return f"{platform}:{chat_id}"

    # Discord would use channel_id, etc.
    return f"{platform}:unknown"
