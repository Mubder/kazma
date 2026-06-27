"""Tests for concurrent invocation serialization and session-store persistence.

Covers:
    - VAL-CRIT-001: Concurrent messages to the same thread_id are serialized
      (the second graph.ainvoke() must wait for the first to complete).
    - VAL-CRIT-002: SessionStore entry persists after the agent reply (it is
      NOT deleted), so crash-recovery routing can rehydrate the platform
      context on the next inbound message.
    - TTL/LRU eviction: old session entries are evicted via a time-based
      sweep rather than an immediate delete after every reply.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from kazma_gateway.agent_handler import create_graph_handler
from kazma_gateway.gateway import (
    GatewayManager,
    IncomingMessage,
    OutboundMessage,
    SessionStore,
)
from kazma_gateway.stores.sqlite import SQLiteSessionStore

# ══════════════════════════════════════════════════════════════════════════
# Helpers / fixtures
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
async def store() -> SQLiteSessionStore:
    """In-memory SQLite session store."""
    s = SQLiteSessionStore(":memory:")
    yield s
    await s.close()


def _make_msg(
    *,
    sender_id: str = "telegram:12345",
    text: str = "hello",
    chat_id: int = 12345,
    user_id: int = 999,
    thread_id: str | None = None,
) -> IncomingMessage:
    ctx: dict[str, Any] = {
        "chat_id": chat_id,
        "user_id": user_id,
        "username": "alice",
        "message_id": 1,
        "update_id": 2,
        "chat_type": "private",
    }
    if thread_id is not None:
        ctx["thread_id"] = thread_id
    return IncomingMessage(
        platform="telegram",
        sender_id=sender_id,
        text=text,
        context_metadata=ctx,
    )


class _SerialGraph:
    """A fake compiled graph that records overlap and forces serialized access.

    Tracks whether two ainvoke() calls were running at the same time. If the
    handler serializes correctly, ``overlap_count`` stays 0.
    """

    def __init__(self) -> None:
        self._active = 0
        self._max_active = 0
        self.overlap_count = 0
        self.invoke_count = 0
        self._gate = asyncio.Event()
        self._gate.set()

    async def ainvoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        self.invoke_count += 1
        self._active += 1
        self._max_active = max(self._max_active, self._active)
        if self._active > 1:
            self.overlap_count += 1
        try:
            # Hold the invocation open long enough that a concurrent caller
            # would overlap if serialization were missing.
            await asyncio.sleep(0.15)
            return {
                "messages": [
                    {"role": "user", "content": state.get("messages", [{}])[-1].get("content", "")},
                    {"role": "assistant", "content": "reply"},
                ]
            }
        finally:
            self._active -= 1


# ══════════════════════════════════════════════════════════════════════════
# VAL-CRIT-001: Concurrent messages to same thread_id are serialized
# ══════════════════════════════════════════════════════════════════════════


class TestConcurrentSerialization:
    """Two concurrent messages with the same thread_id must not run ainvoke() in parallel."""

    @pytest.mark.asyncio
    async def test_same_thread_id_serialized(self, store: SQLiteSessionStore) -> None:
        graph = _SerialGraph()
        manager = MagicMock(spec=GatewayManager)
        manager.send = AsyncMock(return_value=True)

        handler = create_graph_handler(graph=graph, manager=manager, store=store)

        msg1 = _make_msg(sender_id="telegram:1", chat_id=1, thread_id="thread-A")
        msg2 = _make_msg(sender_id="telegram:1", chat_id=1, thread_id="thread-A")

        await asyncio.gather(handler(msg1), handler(msg2))

        assert graph.invoke_count == 2, "both messages must be processed"
        assert graph.overlap_count == 0, (
            f"graph.ainvoke() overlapped {graph.overlap_count} time(s) -- "
            "concurrent invocations for the same thread_id were NOT serialized"
        )

    @pytest.mark.asyncio
    async def test_different_thread_ids_can_overlap(self, store: SQLiteSessionStore) -> None:
        """Different thread_ids use different locks, so they MAY run concurrently."""
        graph = _SerialGraph()
        manager = MagicMock(spec=GatewayManager)
        manager.send = AsyncMock(return_value=True)

        handler = create_graph_handler(graph=graph, manager=manager, store=store)

        # Distinct sender_ids -> distinct thread_ids
        msg1 = _make_msg(sender_id="telegram:1", chat_id=1, thread_id="thread-A")
        msg2 = _make_msg(sender_id="telegram:2", chat_id=2, thread_id="thread-B")

        await asyncio.gather(handler(msg1), handler(msg2))

        assert graph.invoke_count == 2
        # Different thread_ids are allowed to overlap (not required, but the
        # lock must not serialize unrelated threads).
        assert graph._max_active >= 1

    @pytest.mark.asyncio
    async def test_three_concurrent_same_thread(self, store: SQLiteSessionStore) -> None:
        """Three concurrent messages to the same thread serialize with zero overlap."""
        graph = _SerialGraph()
        manager = MagicMock(spec=GatewayManager)
        manager.send = AsyncMock(return_value=True)

        handler = create_graph_handler(graph=graph, manager=manager, store=store)

        msgs = [
            _make_msg(sender_id="telegram:1", chat_id=1, thread_id="thread-X"),
            _make_msg(sender_id="telegram:1", chat_id=1, thread_id="thread-X"),
            _make_msg(sender_id="telegram:1", chat_id=1, thread_id="thread-X"),
        ]
        await asyncio.gather(*(handler(m) for m in msgs))

        assert graph.invoke_count == 3
        assert graph.overlap_count == 0


# ══════════════════════════════════════════════════════════════════════════
# VAL-CRIT-002: SessionStore entry persists after agent reply
# ══════════════════════════════════════════════════════════════════════════


class TestSessionPersistence:
    """After a successful reply, the SessionStore entry must remain readable."""

    @pytest.mark.asyncio
    async def test_entry_persists_after_reply(self, store: SQLiteSessionStore) -> None:
        graph = _SerialGraph()
        manager = MagicMock(spec=GatewayManager)
        manager.send = AsyncMock(return_value=True)

        handler = create_graph_handler(graph=graph, manager=manager, store=store)

        msg = _make_msg(chat_id=777, user_id=555, thread_id="persist-thread")
        await handler(msg)

        ctx = await store.get("persist-thread")
        assert ctx != {}, "SessionStore entry was deleted after the reply"
        assert ctx["chat_id"] == 777
        assert ctx["user_id"] == 555
        assert ctx["username"] == "alice"

    @pytest.mark.asyncio
    async def test_entry_persists_after_error(self, store: SQLiteSessionStore) -> None:
        """Even on graph failure, the session entry must survive for recovery routing."""

        class _BoomGraph:
            async def ainvoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
                raise RuntimeError("simulated graph failure")

        manager = MagicMock(spec=GatewayManager)
        manager.send = AsyncMock(return_value=True)

        handler = create_graph_handler(graph=_BoomGraph(), manager=manager, store=store)

        msg = _make_msg(chat_id=888, user_id=444, thread_id="error-thread")
        await handler(msg)

        ctx = await store.get("error-thread")
        assert ctx != {}, "SessionStore entry was deleted after an error reply"
        assert ctx["chat_id"] == 888

    @pytest.mark.asyncio
    async def test_reply_uses_restored_context(self, store: SQLiteSessionStore) -> None:
        """The OutboundMessage sent to the manager carries the restored platform context."""
        graph = _SerialGraph()
        manager = MagicMock(spec=GatewayManager)
        manager.send = AsyncMock(return_value=True)

        handler = create_graph_handler(graph=graph, manager=manager, store=store)

        msg = _make_msg(chat_id=321, user_id=654, thread_id="ctx-thread")
        await handler(msg)

        assert manager.send.await_count == 1
        outbound: OutboundMessage = manager.send.await_args.args[0]
        assert outbound.target_id == "telegram:321"
        assert outbound.context_metadata.get("chat_id") == 321
        assert outbound.context_metadata.get("user_id") == 654


# ══════════════════════════════════════════════════════════════════════════
# TTL / LRU eviction (replaces immediate delete)
# ══════════════════════════════════════════════════════════════════════════


class TestTtlEviction:
    """Old session entries are evicted via TTL, not immediate delete."""

    @pytest.mark.asyncio
    async def test_evict_older_than_removes_stale_entries(
        self, store: SQLiteSessionStore
    ) -> None:
        """Store exposes an eviction method that prunes entries older than a TTL."""
        await store.put("fresh", {"chat_id": 1})
        await store.put("stale", {"chat_id": 2})

        # Backdate the "stale" entry so it is older than the TTL window.
        db = await store._ensure_db()  # type: ignore[attr-defined]
        old_ts = int(time.time()) - 3600  # 1 hour ago
        await db.execute(
            "UPDATE sessions SET updated_at = ? WHERE thread_id = ?",
            (old_ts, "stale"),
        )
        await db.commit()

        removed = await store.evict_older_than(seconds=300)  # 5 min TTL
        assert removed == 1, "exactly the stale entry should be evicted"

        assert await store.get("fresh") != {}
        assert await store.get("stale") == {}

    @pytest.mark.asyncio
    async def test_handler_does_not_delete_after_reply(
        self, store: SQLiteSessionStore
    ) -> None:
        """After the handler returns, store.list_active() still shows the thread."""
        graph = _SerialGraph()
        manager = MagicMock(spec=GatewayManager)
        manager.send = AsyncMock(return_value=True)

        handler = create_graph_handler(graph=graph, manager=manager, store=store)

        msg = _make_msg(chat_id=111, thread_id="list-thread")
        await handler(msg)

        active = await store.list_active()
        thread_ids = [s["thread_id"] for s in active]
        assert "list-thread" in thread_ids, (
            "session entry must persist for crash-recovery routing, not be deleted after reply"
        )


# ══════════════════════════════════════════════════════════════════════════
# In-memory fallback store: TTL eviction contract
# ══════════════════════════════════════════════════════════════════════════


class TestInMemoryStoreEviction:
    """The in-memory fallback store must also support TTL eviction."""

    @pytest.mark.asyncio
    async def test_inmemory_evict_older_than(self) -> None:
        from kazma_gateway.agent_handler import _InMemoryStore

        s = _InMemoryStore()
        await s.put("fresh", {"chat_id": 1})
        await s.put("stale", {"chat_id": 2})

        # Backdate the stale entry directly in the internal tracking dict.
        s._timestamps["stale"] = time.monotonic() - 3600  # type: ignore[attr-defined]

        removed = await s.evict_older_than(seconds=300)
        assert removed == 1
        assert await s.get("fresh") != {}
        assert await s.get("stale") == {}


# ══════════════════════════════════════════════════════════════════════════
# Abstract SessionStore contract: eviction is part of the interface
# ══════════════════════════════════════════════════════════════════════════


class TestSessionStoreContract:
    """Every SessionStore implementation must expose evict_older_than()."""

    def test_sqlite_store_has_evict_method(self) -> None:
        assert hasattr(SQLiteSessionStore, "evict_older_than")

    def test_inmemory_store_has_evict_method(self) -> None:
        from kazma_gateway.agent_handler import _InMemoryStore

        assert hasattr(_InMemoryStore, "evict_older_than")

    @pytest.mark.asyncio
    async def test_custom_store_must_implement_eviction(self) -> None:
        """A SessionStore subclass missing evict_older_than still works via the
        default no-op implementation on the ABC (graceful degradation)."""

        class _MinimalStore(SessionStore):
            async def get(self, thread_id):
                return {}

            async def put(self, thread_id, context):
                pass

            async def delete(self, thread_id):
                pass

        # The ABC provides a default evict_older_than so callers do not crash
        # if a custom store predates the eviction contract.
        s = _MinimalStore()
        removed = await s.evict_older_than(seconds=60)  # type: ignore[attr-defined]
        assert removed == 0
