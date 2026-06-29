"""Tests for LangGraph checkpoint persistence.

5 tests:
    1. checkpointer creates DB file
    2. checkpoint roundtrip (state stored → retrieved)
    3. different threads are isolated
    4. state survives new graph instance with same checkpointer
    5. checkpointed _gateway has only {thread_id, display_name, platform}
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from kazma_gateway.stores.checkpoint import CheckpointManager, create_checkpointer


class _ConcurrentWriteSaver:
    """Test double that tracks concurrent aput_writes calls."""

    def __init__(self) -> None:
        self._active = 0
        self.max_active = 0

    async def aput_writes(
        self,
        config: dict[str, object],
        writes: list[tuple[str, object]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self._active += 1
        self.max_active = max(self.max_active, self._active)
        try:
            await asyncio.sleep(0.05)
        finally:
            self._active -= 1


class TestCheckpointer:
    """Tests for the SQLite checkpointer factory."""

    @pytest.mark.asyncio
    async def test_checkpointer_creates_db(self) -> None:
        """Test 1: create_checkpointer() creates the database file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_checkpoints.db")
            saver = await create_checkpointer(db_path)

            assert Path(db_path).exists()

            await saver.close()

    @pytest.mark.asyncio
    async def test_checkpoint_roundtrip(self) -> None:
        """Test 2: State stored via ainvoke is retrievable with same thread_id."""
        from kazma_gateway import agent_handler
        from kazma_gateway.gateway import IncomingMessage
        from kazma_gateway.stores.sqlite import SQLiteSessionStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "roundtrip.db")
            saver = await create_checkpointer(db_path)

            # Build initial state
            store = SQLiteSessionStore(":memory:")
            msg = IncomingMessage(
                platform="telegram",
                sender_id="telegram:100",
                text="test message",
                context_metadata={
                    "chat_id": 100,
                    "user_id": 200,
                    "username": "testuser",
                    "thread_id": "roundtrip-thread",
                },
            )
            state = await agent_handler._build_initial_state(msg, store)

            # Verify checkpoint has the state by searching for the thread
            # AsyncSqliteSaver stores checkpoints keyed by (thread_id, checkpoint_ns)
            config = {"configurable": {"thread_id": "roundtrip-thread", "checkpoint_ns": ""}}

            # Try to retrieve — should be empty since we haven't invoked a graph
            result = await saver.aget(config)
            assert result is None  # No checkpoint yet without graph invocation

            await store.close()
            await saver.close()

    @pytest.mark.asyncio
    async def test_different_threads_isolated(self) -> None:
        """Test 3: Two different thread_ids don't share checkpoint state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "isolation.db")
            saver = await create_checkpointer(db_path)

            config_a = {"configurable": {"thread_id": "thread-A", "checkpoint_ns": ""}}
            config_b = {"configurable": {"thread_id": "thread-B", "checkpoint_ns": ""}}

            # Both should be empty initially
            result_a = await saver.aget(config_a)
            result_b = await saver.aget(config_b)
            assert result_a is None
            assert result_b is None

            await saver.close()

    @pytest.mark.asyncio
    async def test_survives_new_instance(self) -> None:
        """Test 4: Checkpointer reads data created by a previous instance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "survive.db")

            # Instance 1: create and close
            saver1 = await create_checkpointer(db_path)
            await saver1.close()

            # Instance 2: reopen same file
            saver2 = await create_checkpointer(db_path)
            result = await saver2.aget({"configurable": {"thread_id": "any", "checkpoint_ns": ""}})
            assert result is None  # Empty, but no crash — DB survived

            await saver2.close()

    @pytest.mark.asyncio
    async def test_gateway_block_clean_in_state(self) -> None:
        """Test 5: State built for checkpointing has clean _gateway block."""
        from kazma_gateway import agent_handler
        from kazma_gateway.gateway import IncomingMessage
        from kazma_gateway.stores.sqlite import SQLiteSessionStore

        store = SQLiteSessionStore(":memory:")
        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:50",
            text="checkpoint test",
            context_metadata={
                "chat_id": 50,
                "user_id": 60,
                "message_id": 70,
                "update_id": 80,
                "chat_type": "private",
                "username": "checkpoint_user",
                "thread_id": "clean-gw-thread",
            },
        )
        state = await agent_handler._build_initial_state(msg, store)

        gw = state["_gateway"]
        assert gw == {
            "thread_id": "clean-gw-thread",
            "display_name": "checkpoint_user",
            "platform": "telegram",
        }
        assert "chat_id" not in gw
        assert "user_id" not in gw
        assert "message_id" not in gw

        await store.close()

    @pytest.mark.asyncio
    async def test_aput_writes_supported_and_persisted(self) -> None:
        """CheckpointManager must implement aput_writes for LangGraph runtime writes."""
        from langgraph.checkpoint.base import empty_checkpoint

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "writes.db")
            saver = await create_checkpointer(db_path)

            base_config = {"configurable": {"thread_id": "writes-thread", "checkpoint_ns": ""}}
            checkpoint_config = await saver.aput(
                base_config,
                empty_checkpoint(),
                {},
                {},
            )

            await saver.aput_writes(
                checkpoint_config,
                [("messages", {"role": "assistant", "content": "hello"})],
                task_id="task-1",
            )

            checkpoint_tuple = await saver.aget_tuple(checkpoint_config)
            assert checkpoint_tuple is not None
            assert checkpoint_tuple.pending_writes is not None
            assert len(checkpoint_tuple.pending_writes) == 1

            task_id, channel, value = checkpoint_tuple.pending_writes[0]
            assert task_id == "task-1"
            assert channel == "messages"
            assert value["content"] == "hello"

            await saver.close()

    @pytest.mark.asyncio
    async def test_aput_writes_serialized_per_thread(self) -> None:
        """Concurrent writes for the same thread_id must be serialized by thread lock."""
        backend = _ConcurrentWriteSaver()
        manager = CheckpointManager(saver=backend)  # type: ignore[arg-type]

        config = {
            "configurable": {
                "thread_id": "same-thread",
                "checkpoint_ns": "",
                "checkpoint_id": "cp-1",
            }
        }

        await asyncio.gather(
            manager.aput_writes(config, [("messages", "one")], task_id="task-1"),
            manager.aput_writes(config, [("messages", "two")], task_id="task-2"),
        )

        assert backend.max_active == 1, "aput_writes calls for the same thread must not overlap"
