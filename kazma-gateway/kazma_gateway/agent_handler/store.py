"""Store submodule — thread resolver, in-memory store, state builder, and context persistence."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from kazma_gateway.gateway import IncomingMessage, SessionStore

logger = logging.getLogger(__name__)

_MAX_DICT_ENTRIES = 10_000

_PLATFORM_KEYS = frozenset(
    {
        # Telegram
        "chat_id",
        "user_id",
        "message_id",
        "update_id",
        "chat_type",
        # Discord / Slack
        "channel_id",
        "guild_id",
        "team_id",
        "thread_ts",
        "message_ts",
    }
)


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

    # Store full platform context in SessionStore (NEVER enters the graph).
    # Adapters only set sender_id as the top-level IncomingMessage field,
    # never inside context_metadata — but hitl.py's cross-thread approval
    # ownership check reads original_sender from the persisted context, so
    # without this it always sees "" and the authz guard never fires.
    persisted_ctx = dict(ctx)
    persisted_ctx.setdefault("sender_id", msg.sender_id)
    await store.put(thread_id, persisted_ctx)

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

    # Defense-in-depth: strip any platform-specific identifiers that might
    # have leaked into the top-level state. ``_PLATFORM_KEYS`` is the
    # authoritative list of keys that must never enter the graph; the state
    # is built from scratch above, so this is a safety net against future
    # refactors that copy ``ctx`` wholesale.
    leaked = _PLATFORM_KEYS.intersection(state)
    for key in leaked:
        state.pop(key, None)

    return state


def _build_target_id(platform: str, ctx: dict[str, Any]) -> str:
    """Build a platform-prefixed target ID from context_metadata.

    Args:
        platform: "telegram", "discord", etc.
        ctx: The restored context_metadata (may be empty on error).

    Returns:
        e.g. "telegram:12345", "discord:98765", or "telegram:unknown"
    """
    # Telegram uses chat_id
    chat_id = ctx.get("chat_id")
    if chat_id is not None:
        return f"{platform}:{chat_id}"

    # Discord / Slack route on channel_id
    channel_id = ctx.get("channel_id")
    if channel_id is not None:
        return f"{platform}:{channel_id}"

    return f"{platform}:unknown"
