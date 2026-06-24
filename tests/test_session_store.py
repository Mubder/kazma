"""Tests for SessionStore and SQLiteSessionStore.

7 mandatory tests per gw-010 spec:
    1. put + get round-trip
    2. get returns empty dict for missing key
    3. put upserts (overwrite existing)
    4. delete removes entry
    5. delete is no-op for missing key
    6. platform-specific keys survive round-trip
    7. store is used by agent_handler._build_initial_state
"""

from __future__ import annotations

import pytest
from kazma_gateway import agent_handler
from kazma_gateway.gateway import IncomingMessage, SessionStore
from kazma_gateway.stores.sqlite import SQLiteSessionStore

# ══════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
async def store() -> SQLiteSessionStore:
    """In-memory SQLite store for testing."""
    s = SQLiteSessionStore(":memory:")
    yield s
    await s.close()


# ══════════════════════════════════════════════════════════════════════════
# 7 Mandatory Tests
# ══════════════════════════════════════════════════════════════════════════


class TestSQLiteSessionStore:
    """Mandatory tests for the SQLiteSessionStore."""

    @pytest.mark.asyncio
    async def test_put_get_roundtrip(self, store: SQLiteSessionStore) -> None:
        """Test 1: put then get returns the same data."""
        ctx = {"chat_id": 123, "user_id": 456, "username": "alice"}
        await store.put("thread-1", ctx)

        result = await store.get("thread-1")
        assert result == ctx

    @pytest.mark.asyncio
    async def test_get_missing_returns_empty(self, store: SQLiteSessionStore) -> None:
        """Test 2: get returns empty dict for unknown thread_id."""
        result = await store.get("nonexistent")
        assert result == {}

    @pytest.mark.asyncio
    async def test_put_upserts(self, store: SQLiteSessionStore) -> None:
        """Test 3: put overwrites existing context for same thread_id."""
        await store.put("thread-1", {"chat_id": 100})
        await store.put("thread-1", {"chat_id": 200, "new_field": True})

        result = await store.get("thread-1")
        assert result["chat_id"] == 200
        assert result["new_field"] is True

    @pytest.mark.asyncio
    async def test_delete_removes_entry(self, store: SQLiteSessionStore) -> None:
        """Test 4: delete removes the stored context."""
        await store.put("thread-1", {"chat_id": 100})
        await store.delete("thread-1")

        result = await store.get("thread-1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_delete_noop_for_missing(self, store: SQLiteSessionStore) -> None:
        """Test 5: delete on missing key does not raise."""
        # Should not raise
        await store.delete("nonexistent")

    @pytest.mark.asyncio
    async def test_platform_keys_survive_roundtrip(self, store: SQLiteSessionStore) -> None:
        """Test 6: All platform-specific keys survive put/get."""
        ctx = {
            "chat_id": 999,
            "user_id": 555,
            "message_id": 42,
            "update_id": 888,
            "chat_type": "private",
            "username": "test_user",
        }
        await store.put("thread-1", ctx)

        result = await store.get("thread-1")
        assert result["chat_id"] == 999
        assert result["user_id"] == 555
        assert result["message_id"] == 42
        assert result["update_id"] == 888
        assert result["chat_type"] == "private"
        assert result["username"] == "test_user"

    @pytest.mark.asyncio
    async def test_agent_handler_uses_store(self, store: SQLiteSessionStore) -> None:
        """Test 7: _build_initial_state persists context in the store."""
        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:123",
            text="hello",
            context_metadata={
                "chat_id": 123,
                "user_id": 456,
                "username": "bob",
                "message_id": 789,
                "chat_type": "private",
                "thread_id": "test-thread-store",
            },
        )

        state = await agent_handler._build_initial_state(msg, store)

        # Verify state is clean
        gw = state["_gateway"]
        assert gw["thread_id"] == "test-thread-store"
        assert gw["display_name"] == "bob"
        assert gw["platform"] == "telegram"
        assert "chat_id" not in gw

        # Verify store has the full context
        ctx = await store.get("test-thread-store")
        assert ctx["chat_id"] == 123
        assert ctx["user_id"] == 456
        assert ctx["message_id"] == 789

        # Cleanup
        await store.delete("test-thread-store")


# ══════════════════════════════════════════════════════════════════════════
# ABC contract
# ══════════════════════════════════════════════════════════════════════════


class TestSessionStoreABC:
    """Verify SessionStore is a proper ABC."""

    def test_cannot_instantiate(self) -> None:
        """SessionStore is abstract — cannot be instantiated directly."""
        with pytest.raises(TypeError):
            SessionStore()  # type: ignore[abstract]


class TestInMemoryStore:
    """Verify the in-memory fallback store works."""

    @pytest.mark.asyncio
    async def test_roundtrip(self) -> None:
        store = agent_handler._InMemoryStore()
        await store.put("t1", {"chat_id": 1})
        assert await store.get("t1") == {"chat_id": 1}
        await store.delete("t1")
        assert await store.get("t1") == {}
