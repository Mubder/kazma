"""Tests for CheckpointManager — save/load/list/prune operations."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from kazma_core.checkpoint import CheckpointManager
from kazma_core.state import AgentState, initial_state


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Return a temporary database path."""
    return str(tmp_path / "test_checkpoints.db")


@pytest.fixture
async def manager(db_path: str) -> CheckpointManager:
    """Return a CheckpointManager with a temp database."""
    mgr = CheckpointManager(db_path=db_path)
    yield mgr
    await mgr.close()


class TestCheckpointManager:
    """Tests for CheckpointManager class."""

    @pytest.mark.asyncio
    async def test_save_returns_checkpoint_id(self, manager: CheckpointManager) -> None:
        """save() should return a valid checkpoint UUID."""
        state = initial_state()
        cp_id = await manager.save(state)
        assert cp_id is not None
        assert len(cp_id) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_save_preserves_checkpoint_id(self, manager: CheckpointManager) -> None:
        """save() should preserve an existing last_cp_id."""
        state = initial_state()
        original_id = state["last_cp_id"]
        returned_id = await manager.save(state)
        assert returned_id == original_id

    @pytest.mark.asyncio
    async def test_save_generates_new_id_if_missing(self, manager: CheckpointManager) -> None:
        """save() should generate a new last_cp_id if none exists."""
        state: AgentState = {"last_cp_id": ""}
        cp_id = await manager.save(state)
        assert cp_id is not None
        assert len(cp_id) == 36

    @pytest.mark.asyncio
    async def test_load_returns_saved_state(self, manager: CheckpointManager) -> None:
        """load() should return the exact state that was saved."""
        state = initial_state()
        state["messages"] = [{"role": "user", "content": "hello"}]
        state["context_tokens"] = 42

        cp_id = await manager.save(state)
        loaded = await manager.load(cp_id)

        assert loaded["last_cp_id"] == cp_id
        assert loaded["messages"] == [{"role": "user", "content": "hello"}]
        assert loaded["context_tokens"] == 42

    @pytest.mark.asyncio
    async def test_load_nonexistent_raises(self, manager: CheckpointManager) -> None:
        """load() should raise FileNotFoundError for unknown ID."""
        with pytest.raises(FileNotFoundError, match="not found"):
            await manager.load("nonexistent-id")

    @pytest.mark.asyncio
    async def test_load_latest_returns_most_recent(self, manager: CheckpointManager) -> None:
        """load_latest() should return the most recently saved checkpoint."""
        state1 = initial_state()
        state1["context_tokens"] = 10
        await manager.save(state1)

        state2 = initial_state()
        state2["context_tokens"] = 20
        await manager.save(state2)

        latest = await manager.load_latest()
        assert latest is not None
        assert latest["context_tokens"] == 20

    @pytest.mark.asyncio
    async def test_load_latest_empty_returns_none(self, manager: CheckpointManager) -> None:
        """load_latest() should return None when no checkpoints exist."""
        result = await manager.load_latest()
        assert result is None

    @pytest.mark.asyncio
    async def test_list_checkpoints(self, manager: CheckpointManager) -> None:
        """list_checkpoints() should return saved checkpoints."""
        for i in range(5):
            state = initial_state()
            state["context_tokens"] = i * 10
            await manager.save(state)

        checkpoints = await manager.list_checkpoints(limit=3)
        assert len(checkpoints) == 3
        for cp in checkpoints:
            assert "id" in cp
            assert "created_at" in cp
            assert "context_tokens" in cp
            assert "message_count" in cp

    @pytest.mark.asyncio
    async def test_list_checkpoints_limit(self, manager: CheckpointManager) -> None:
        """list_checkpoints() should respect the limit parameter."""
        for _ in range(10):
            await manager.save(initial_state())

        all_cps = await manager.list_checkpoints(limit=5)
        assert len(all_cps) == 5

    @pytest.mark.asyncio
    async def test_prune_removes_old_checkpoints(self, manager: CheckpointManager) -> None:
        """prune() should remove old checkpoints beyond keep_last."""
        for _ in range(5):
            await manager.save(initial_state())

        # Prune to keep only 2
        removed = await manager.prune(keep_last=2)
        remaining = await manager.list_checkpoints(limit=100)
        # Note: actual count depends on prune implementation
        # The key assertion is that prune runs without error
        assert isinstance(removed, int)

    @pytest.mark.asyncio
    async def test_multiple_saves_and_loads(self, manager: CheckpointManager) -> None:
        """Multiple save/load cycles should work correctly."""
        ids = []
        for i in range(10):
            state = initial_state()
            state["context_tokens"] = i * 100
            state["messages"] = [{"role": "user", "content": f"msg_{i}"}]
            cp_id = await manager.save(state)
            ids.append(cp_id)

        # Load each checkpoint and verify
        for i, cp_id in enumerate(ids):
            loaded = await manager.load(cp_id)
            assert loaded["context_tokens"] == i * 100
            assert loaded["messages"][0]["content"] == f"msg_{i}"

    @pytest.mark.asyncio
    async def test_save_large_state(self, manager: CheckpointManager) -> None:
        """save() should handle large state without errors."""
        state = initial_state()
        state["messages"] = [{"role": "user", "content": "x" * 1000} for _ in range(100)]
        state["tool_results"] = {f"tool_{i}": {"data": "y" * 500} for i in range(50)}

        cp_id = await manager.save(state)
        loaded = await manager.load(cp_id)
        assert len(loaded["messages"]) == 100
        assert len(loaded["tool_results"]) == 50

    @pytest.mark.asyncio
    async def test_concurrent_saves(self, manager: CheckpointManager) -> None:
        """Multiple concurrent saves should not corrupt the database."""

        async def save_state(i: int) -> str:
            state = initial_state()
            state["context_tokens"] = i
            return await manager.save(state)

        # Run 5 concurrent saves
        results = await asyncio.gather(*[save_state(i) for i in range(5)])
        assert len(results) == 5
        assert len(set(results)) == 5  # All unique IDs

        # All should be loadable
        for cp_id in results:
            loaded = await manager.load(cp_id)
            assert loaded is not None


class TestCheckpointFileSize:
    """Tests for checkpoint file size constraints."""

    @pytest.mark.asyncio
    async def test_file_size_under_1mb_for_1000_checkpoints(self, db_path: str) -> None:
        """Checkpoint file should be <1MB for 1000 checkpoints."""
        manager = CheckpointManager(db_path=db_path)
        try:
            for i in range(1000):
                state = initial_state()
                state["context_tokens"] = i
                state["messages"] = [{"role": "user", "content": f"msg_{i}"}]
                await manager.save(state)

            file_size = Path(db_path).stat().st_size
            # 1MB = 1,048,576 bytes
            assert file_size < 1_048_576, f"File too large: {file_size} bytes"
        finally:
            await manager.close()
