"""Agent message handler — bridges IncomingMessage to the LangGraph supervisor.

Platform isolation contract:
    The Brain (LangGraph graph) NEVER sees platform-specific identifiers.
    chat_id, user_id, message_id, update_id, chat_type are stored in a
    SessionStore OUTSIDE the graph state and restored only when
    constructing the OutboundMessage for the return path.

    Graph state["_gateway"] contains ONLY:
        - thread_id:    stable session identifier
        - display_name: sender's display name (platform-agnostic)
        - platform:     "telegram" / "discord" (string, not an ID)

    Everything else lives in the SessionStore and is fetched back
    onto the OutboundMessage.context_metadata when replying.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage, SessionStore

logger = logging.getLogger(__name__)

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
# Standardized thread_id resolver
# ══════════════════════════════════════════════════════════════════════════


def _resolve_thread(msg: IncomingMessage) -> str:
    """Resolve or generate a stable thread_id for a message.

    Resolution order:
        1. Existing thread_id in context_metadata (from session store)
        2. Platform-prefixed deterministic ID from sender_id
        3. Fresh UUID4 (last resort)

    Args:
        msg: The incoming message.

    Returns:
        A stable thread_id string.
    """
    ctx = msg.context_metadata

    # 1. Already resolved (e.g. from a previous message in the session)
    if ctx.get("thread_id"):
        return ctx["thread_id"]

    # 2. Deterministic from sender_id (e.g. "telegram:12345" → "gw-telegram-12345")
    if msg.sender_id and ":" in msg.sender_id:
        platform, sender = msg.sender_id.split(":", 1)
        return f"gw-{platform}-{sender}"

    # 3. Fallback UUID
    return f"gw-{uuid.uuid4().hex[:12]}"


# ══════════════════════════════════════════════════════════════════════════
# In-memory fallback store (for when no persistent store is provided)
# ══════════════════════════════════════════════════════════════════════════


class _InMemoryStore(SessionStore):
    """Trivial in-memory store — no persistence, for testing/fallback.

    Tracks a monotonic timestamp per entry so that TTL-based eviction
    (``evict_older_than``) works the same way as the SQLite backend.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._timestamps: dict[str, float] = {}

    async def get(self, thread_id: str) -> dict[str, Any]:
        return dict(self._data.get(thread_id, {}))

    async def put(self, thread_id: str, context: dict[str, Any]) -> None:
        self._data[thread_id] = dict(context)
        self._timestamps[thread_id] = time.monotonic()

    async def delete(self, thread_id: str) -> None:
        self._data.pop(thread_id, None)
        self._timestamps.pop(thread_id, None)

    async def evict_older_than(self, seconds: float) -> int:
        """Remove entries whose last ``put`` is older than ``seconds`` ago."""
        cutoff = time.monotonic() - seconds
        stale = [tid for tid, ts in self._timestamps.items() if ts < cutoff]
        for tid in stale:
            self._data.pop(tid, None)
            self._timestamps.pop(tid, None)
        return len(stale)


# ══════════════════════════════════════════════════════════════════════════
# State builder — platform-agnostic
# ══════════════════════════════════════════════════════════════════════════


async def _build_initial_state(msg: IncomingMessage, store: SessionStore) -> dict[str, Any]:
    """Build a platform-agnostic graph state from an IncomingMessage.

    Side-effects:
        - Stores full context_metadata in SessionStore via store.put()
        - The graph state's _gateway block contains ZERO platform IDs

    Args:
        msg:   The incoming message from the gateway queue.
        store: SessionStore for persisting platform context.

    Returns:
        LangGraph-compatible initial state dict.
    """
    ctx = msg.context_metadata

    # Resolve thread_id using standardized resolver
    thread_id = _resolve_thread(msg)

    # Store full platform context in SessionStore (NEVER enters the graph)
    await store.put(thread_id, dict(ctx))

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
    store: SessionStore | None = None,
) -> Callable[[IncomingMessage], Awaitable[None]]:
    """Create an async handler that processes messages through LangGraph.

    Args:
        graph:          Compiled LangGraph supervisor graph.
        manager:        GatewayManager instance (for send() routing).
        system_prompt:  System prompt for the agent.
        cost_breaker:   Optional CostCircuitBreaker for budget control.
        store:          SessionStore for platform context persistence.
                        Falls back to in-memory store if not provided.

    Returns:
        Async handler function compatible with manager.on_message().
    """
    # Use provided store or fall back to in-memory
    _store = store or _InMemoryStore()

    # Per-sender session tracking (sender_id → thread_id).
    # Guarded by _sessions_lock because concurrent handler invocations for
    # different senders read/write this shared dict.
    _sessions: dict[str, str] = {}
    _sessions_lock = asyncio.Lock()

    # Per-thread_id serialization lock. Two concurrent messages for the same
    # thread_id must not interleave graph.ainvoke() / checkpoint writes, or
    # the LangGraph state and SQLite checkpoint will corrupt. Each distinct
    # thread_id gets its own asyncio.Lock so unrelated threads stay parallel.
    _thread_locks: dict[str, asyncio.Lock] = {}
    _thread_locks_lock = asyncio.Lock()

    # Session TTL: entries survive agent replies (for crash-recovery routing)
    # and are evicted lazily by this many seconds of inactivity.
    _session_ttl_seconds = 300  # 5 minutes

    async def _get_thread_lock(thread_id: str) -> asyncio.Lock:
        """Return (creating if needed) the serialization lock for a thread_id."""
        async with _thread_locks_lock:
            lock = _thread_locks.get(thread_id)
            if lock is None:
                lock = asyncio.Lock()
                _thread_locks[thread_id] = lock
            return lock

    async def handler(msg: IncomingMessage) -> None:
        """Process a single IncomingMessage through the agent graph."""
        sender = msg.sender_id

        # Resolve thread_id using standardized resolver (synchronized)
        async with _sessions_lock:
            if sender not in _sessions:
                _sessions[sender] = _resolve_thread(msg)
            thread_id = _sessions[sender]

        # Inject the resolved thread_id into context_metadata
        # so _build_initial_state can pick it up
        msg.context_metadata["thread_id"] = thread_id

        # Cost breaker gate
        if cost_breaker and cost_breaker.should_halt():
            # Restore platform context for the reply
            ctx = await _store.get(thread_id)
            if not ctx:
                ctx = msg.context_metadata
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
        state = await _build_initial_state(msg, _store)

        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        # ── Serialize per thread_id ────────────────────────────────
        # Two concurrent messages for the same thread_id must NOT interleave
        # graph.ainvoke() calls, or LangGraph checkpoints and messages will
        # corrupt. Different thread_ids use different locks and stay parallel.
        thread_lock = await _get_thread_lock(thread_id)

        async with thread_lock:
            # ── Invoke graph ───────────────────────────────────────
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

                # ── Restore platform IDs from SessionStore ─────────
                # The entry is intentionally NOT deleted here. It must persist
                # so crash-recovery routing can rehydrate the platform context
                # (chat_id, user_id) on the next inbound message. Stale
                # entries are evicted lazily by TTL below.
                ctx = await _store.get(thread_id)

                await manager.send(
                    OutboundMessage(
                        target_id=_build_target_id(msg.platform, ctx),
                        text=assistant_text,
                        context_metadata=ctx,
                    )
                )

            except Exception:
                logger.exception("[agent-handler] Graph invocation failed for %s", sender)
                ctx = await _store.get(thread_id)
                if not ctx:
                    ctx = msg.context_metadata
                # Keep the session entry for recovery routing; only evict via TTL.
                await manager.send(
                    OutboundMessage(
                        target_id=_build_target_id(msg.platform, ctx),
                        text="⚠️ حدث خطأ أثناء معالجة رسالتك. (Processing error)",
                        context_metadata=ctx,
                    )
                )

        # ── Lazy TTL eviction ──────────────────────────────────────
        # Opportunistically prune sessions that have been inactive longer than
        # the TTL. This bounds the store size over time without deleting live
        # entries that crash recovery still needs.
        try:
            await _store.evict_older_than(_session_ttl_seconds)
        except Exception:
            logger.debug("[agent-handler] TTL eviction skipped (store may not support it)")

    # ── Register telegram backend with core's send_message dispatcher ──
    try:
        from kazma_core.tools.send_message import register_message_backend

        async def _telegram_backend_handler(target_id: str, text: str) -> str:
            ctx = await _store.get(target_id)
            if not ctx:
                ctx = {"thread_id": target_id}
            outbound = OutboundMessage(target_id=target_id, text=text, context_metadata=ctx)
            await manager.send(outbound)
            return f"sent:{target_id}"

        register_message_backend("telegram", _telegram_backend_handler)
    except ImportError:
        logger.debug("[agent-handler] kazma_core not available — backend registration skipped")

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
