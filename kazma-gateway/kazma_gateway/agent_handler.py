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
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage, SessionStore

logger = logging.getLogger(__name__)

# Maximum number of entries retained in per-handler in-memory dicts.
# When exceeded the least-recently-used entry is evicted (LRU via
# OrderedDict).  This bounds memory usage for long-running gateways
# that see many distinct senders / threads.
_MAX_DICT_ENTRIES = 10_000

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
# Swarm slash-command interceptor
# ══════════════════════════════════════════════════════════════════════════


async def _try_swarm_command(
    msg: IncomingMessage,
    state: dict[str, Any],
    store: SessionStore,
    manager: Any,
    thread_id: str,
) -> bool:
    """Check if the message is a swarm slash-command and handle it.

    Supported commands:
        /swarm <worker> <prompt>           — dispatch to one worker
        /swarm pipeline <w1,w2,...> <p>    — pipeline pattern
        /swarm consult <w1,w2,...> <p>     — consult pattern
        /swarm fanout <w1,w2,...> <p>      — fan-out pattern
        /swarm broadcast <prompt>          — broadcast to all workers
        /swarm status                      — show swarm status
        /swarm list                        — list registered workers

    Returns ``True`` if the message was handled (swarm command detected),
    ``False`` to continue with normal graph processing.
    """
    text = (msg.text or "").strip()
    if not text.lower().startswith("/swarm"):
        return False

    # Lazy import — skip if swarm engine isn't available
    try:
        from kazma_core.swarm.engine import get_swarm_engine
        engine = get_swarm_engine()
    except Exception:
        return False  # swarm not initialized, fall through to graph

    parts = text.split(None, 2)  # ["/swarm", subcommand, rest]
    if len(parts) < 2:
        await _send_swarm_reply(msg, store, manager, thread_id,
            "🐝 **Swarm Commands**\n\n"
            "/swarm `<worker>` `<task>` — dispatch to one worker\n"
            "/swarm pipeline `<w1,w2,...>` `<task>` — sequential pipeline\n"
            "/swarm consult `<w1,w2,...>` `<task>` — parallel consult\n"
            "/swarm fanout `<w1,w2,...>` `<task>` — parallel fan-out\n"
            "/swarm broadcast `<task>` — all workers\n"
            "/swarm status — show swarm status\n"
            "/swarm list — list workers"
        )
        return True

    sub = parts[1].lower()

    # ── /swarm status ──────────────────────────────────────────
    if sub == "status":
        try:
            worker_names = engine.worker_names
            status_lines = [f"🐝 Swarm Status\n", f"Workers: {len(worker_names)}"]
            for name in worker_names:
                w = engine.get_worker(name)
                busy = " (busy)" if w and getattr(w, "busy", False) else ""
                model = f" [{getattr(w, 'model', '?')}]" if w and getattr(w, "model", "") else ""
                status_lines.append(f"  • {name}{busy}{model}")
            await _send_swarm_reply(msg, store, manager, thread_id, "\n".join(status_lines))
        except Exception as exc:
            await _send_swarm_reply(msg, store, manager, thread_id, f"⚠️ Status error: {exc}")
        return True

    # ── /swarm list ────────────────────────────────────────────
    if sub == "list":
        try:
            names = engine.worker_names
            if not names:
                await _send_swarm_reply(msg, store, manager, thread_id, "No workers registered.")
            else:
                lines = [f"🐝 Workers ({len(names)}):"]
                for name in names:
                    w = engine.get_worker(name)
                    role = getattr(w, "role", "") or ""
                    model = getattr(w, "model", "") or ""
                    lines.append(f"  • {name}" + (f" ({role})" if role else "") + (f" [{model}]" if model else ""))
                await _send_swarm_reply(msg, store, manager, thread_id, "\n".join(lines))
        except Exception as exc:
            await _send_swarm_reply(msg, store, manager, thread_id, f"⚠️ List error: {exc}")
        return True

    # ── /swarm broadcast <prompt> ──────────────────────────────
    if sub == "broadcast":
        prompt = parts[2] if len(parts) > 2 else ""
        if not prompt:
            await _send_swarm_reply(msg, store, manager, thread_id, "⚠️ Usage: /swarm broadcast <prompt>")
            return True
        return await _dispatch_swarm_from_chat(
            msg, store, manager, thread_id, engine,
            workers=[], task=prompt, pattern="broadcast",
        )

    # ── Pattern commands: /swarm pipeline|consult|fanout <workers> <prompt> ──
    if sub in ("pipeline", "consult", "fanout", "dispatch"):
        if len(parts) < 3:
            await _send_swarm_reply(msg, store, manager, thread_id,
                f"⚠️ Usage: /swarm {sub} <workers> <prompt>")
            return True

        # For "dispatch", workers is the worker name and the rest is the prompt
        if sub == "dispatch":
            worker_parts = parts[2].split(None, 1)
            if len(worker_parts) < 2:
                await _send_swarm_reply(msg, store, manager, thread_id,
                    "⚠️ Usage: /swarm dispatch <worker> <prompt>")
                return True
            worker_name = worker_parts[0]
            prompt = worker_parts[1]
            return await _dispatch_swarm_from_chat(
                msg, store, manager, thread_id, engine,
                workers=[worker_name], task=prompt, pattern="dispatch",
            )

        # pipeline/consult/fanout: <w1,w2,...> <prompt>
        rest = parts[2]
        split_idx = _find_worker_prompt_split(rest)
        if split_idx is None:
            await _send_swarm_reply(msg, store, manager, thread_id,
                f"⚠️ Usage: /swarm {sub} <worker1,worker2,...> <prompt>")
            return True

        workers_str = rest[:split_idx].strip()
        prompt = rest[split_idx:].strip()
        workers = [w.strip() for w in workers_str.split(",") if w.strip()]

        if not workers or not prompt:
            await _send_swarm_reply(msg, store, manager, thread_id,
                f"⚠️ Usage: /swarm {sub} <worker1,worker2,...> <prompt>")
            return True

        return await _dispatch_swarm_from_chat(
            msg, store, manager, thread_id, engine,
            workers=workers, task=prompt, pattern=sub,
        )

    # Unknown subcommand
    await _send_swarm_reply(msg, store, manager, thread_id,
        f"⚠️ Unknown swarm command: '{sub}'. Try /swarm for help."
    )
    return True


def _find_worker_prompt_split(text: str) -> int | None:
    """Find the split point between comma-separated worker names and the prompt.

    Worker names may contain forward slashes (e.g. "meta/llama-3.1-8b-instruct")
    but not spaces. The first space after the last comma or the first space
    if no comma exists marks the start of the prompt.

    Returns the character index of the prompt start, or None if ambiguous.
    """
    if "," in text:
        last_comma = text.rfind(",")
        rest_after_comma = text[last_comma + 1:].lstrip()
        if rest_after_comma:
            # Find first space in the segment after the last comma
            space_in_segment = rest_after_comma.find(" ")
            if space_in_segment != -1:
                actual_idx = last_comma + 1 + (len(text[last_comma + 1:]) - len(rest_after_comma)) + space_in_segment
                return actual_idx + 1
        return None
    else:
        # No comma — first space splits worker name from prompt
        space = text.find(" ")
        if space != -1:
            return space + 1
        return None


async def _dispatch_swarm_from_chat(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
    engine: Any,
    workers: list[str],
    task: str,
    pattern: str,
) -> bool:
    """Dispatch a swarm task from a chat message and send the result back."""
    from kazma_core.swarm.task import SwarmTask, TaskType

    # Map pattern names to TaskType
    type_map = {
        "dispatch": TaskType.DISPATCH,
        "pipeline": TaskType.PIPELINE,
        "consult": TaskType.CONSULT,
        "fanout": TaskType.FAN_OUT,
        "broadcast": TaskType.BROADCAST,
    }
    task_type = type_map.get(pattern, TaskType.DISPATCH)

    # Tag with source platform metadata
    ctx = msg.context_metadata
    metadata = {
        "source_platform": msg.platform,
        "source_chat_id": ctx.get("chat_id", ""),
        "source_thread_id": thread_id,
        "source_user": ctx.get("username", ""),
    }

    swarm_task = SwarmTask(
        prompt=task,
        workers=workers,
        type=task_type,
        metadata=metadata,
    )

    # Send "dispatching" notification
    worker_label = ", ".join(workers) if workers else "all workers"
    await _send_swarm_reply(msg, store, manager, thread_id,
        f"🐝 Dispatching to {worker_label} ({pattern})...")

    # Dispatch synchronously (the engine handles concurrency internally)
    try:
        result = await engine.dispatch(swarm_task)

        # Format result for chat
        if result and result.aggregated_output:
            reply = result.aggregated_output
        elif result and result.worker_results:
            lines = []
            for wr in result.worker_results:
                status_icon = "✅" if wr.status == "success" else "❌"
                output = (wr.output or "")[:500] if wr.output else wr.error or "no output"
                lines.append(f"{status_icon} **{wr.worker}**: {output}")
            reply = "\n\n".join(lines)
        elif result and result.error:
            reply = f"⚠️ Task failed: {result.error}"
        else:
            reply = "⚠️ No result returned from swarm."

        await _send_swarm_reply(msg, store, manager, thread_id, reply)

    except Exception as exc:
        logger.exception("[agent-handler] Swarm dispatch failed for thread %s", thread_id)
        await _send_swarm_reply(msg, store, manager, thread_id,
            f"⚠️ Swarm dispatch failed: {exc}")

    return True


async def _send_swarm_reply(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
    text: str,
) -> None:
    """Send a reply through the gateway manager (platform-agnostic)."""
    ctx = await store.get(thread_id)
    if not ctx:
        ctx = msg.context_metadata
    await manager.send(
        OutboundMessage(
            target_id=_build_target_id(msg.platform, ctx),
            text=text,
            context_metadata=ctx,
        )
    )


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
    #
    # Bounded LRU: evicts the least-recently-used sender when the store
    # exceeds _MAX_DICT_ENTRIES (default 10 000).
    _sessions: OrderedDict[str, str] = OrderedDict()
    _sessions_lock = asyncio.Lock()

    # Per-thread_id serialization lock. Two concurrent messages for the same
    # thread_id must not interleave graph.ainvoke() / checkpoint writes, or
    # the LangGraph state and SQLite checkpoint will corrupt. Each distinct
    # thread_id gets its own asyncio.Lock so unrelated threads stay parallel.
    #
    # Bounded LRU: evicts the least-recently-used lock when the store
    # exceeds _MAX_DICT_ENTRIES (default 10 000).
    _thread_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
    _thread_locks_lock = asyncio.Lock()

    # Session TTL: entries survive agent replies (for crash-recovery routing)
    # and are evicted lazily by this many seconds of inactivity.
    _session_ttl_seconds = 300  # 5 minutes

    async def _get_thread_lock(thread_id: str) -> asyncio.Lock:
        """Return (creating if needed) the serialization lock for a thread_id.

        Uses LRU ordering: existing entries are moved to the end
        (most-recently-used) and the oldest entry is evicted when the
        bound is exceeded.
        """
        async with _thread_locks_lock:
            lock = _thread_locks.get(thread_id)
            if lock is not None:
                _thread_locks.move_to_end(thread_id)
                return lock
            lock = asyncio.Lock()
            _thread_locks[thread_id] = lock
            while len(_thread_locks) > _MAX_DICT_ENTRIES:
                _thread_locks.popitem(last=False)
            return lock

    async def handler(msg: IncomingMessage) -> None:
        """Process a single IncomingMessage through the agent graph."""
        sender = msg.sender_id

        # Resolve thread_id using standardized resolver (synchronized)
        async with _sessions_lock:
            if sender in _sessions:
                # LRU: mark as most-recently-used.
                _sessions.move_to_end(sender)
                thread_id = _sessions[sender]
            else:
                _sessions[sender] = _resolve_thread(msg)
                while len(_sessions) > _MAX_DICT_ENTRIES:
                    _sessions.popitem(last=False)
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

        # ── Swarm slash-command intercept ──────────────────────────
        # If the message starts with /swarm, dispatch to the swarm engine
        # instead of the single-agent graph.
        swarm_handled = await _try_swarm_command(
            msg, state, _store, manager, thread_id,
        )
        if swarm_handled:
            return  # swarm dispatched, skip graph

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
