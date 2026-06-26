"""Tests for Time Travel — snapshot recording and replay.

Covers:
  - SnapshotRecorder: capture, store, retrieve, max limit eviction
  - ReplayEngine: replay_from, compare_replays
  - SnapshotStore: SQLite persistence, listing, clearing
  - Integration: full capture-replay cycle through the graph
  - State: new snapshot_id / snapshot_iteration fields
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from kazma_core.agent.state import initial_supervisor_state
from kazma_core.time_travel import (
    DEFAULT_MAX_SNAPSHOTS,
    ReplayEngine,
    SnapshotRecord,
    SnapshotRecorder,
    SnapshotStore,
    create_recorder,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_state(
    thread_id: str = "test-thread",
    iteration: int = 0,
    last_model: str = "gpt-4o-mini",
    last_cost_usd: float = 0.01,
    message_count: int = 3,
    next_node: str = "tool_worker",
) -> dict[str, Any]:
    """Build a realistic SupervisorState dict for testing."""
    state = initial_supervisor_state(thread_id=thread_id)
    state["iteration"] = iteration
    state["last_model"] = last_model
    state["last_cost_usd"] = last_cost_usd
    state["next_node"] = next_node
    # Build exactly message_count messages
    base = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "user", "content": "do something"},
        {"role": "assistant", "content": "doing it"},
        {"role": "user", "content": "thanks"},
    ]
    state["messages"] = (base * ((message_count // len(base)) + 1))[:message_count]
    state["tool_calls_pending"] = []
    state["tool_calls_done"] = []
    return state


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    """Return a temporary SQLite db path."""
    return str(tmp_path / "test_snapshots.db")


@pytest.fixture
def store(tmp_db: str) -> SnapshotStore:
    """Return a fresh SnapshotStore backed by a temp file."""
    return SnapshotStore(db_path=tmp_db)


@pytest.fixture
def recorder(tmp_db: str) -> SnapshotRecorder:
    """Return a SnapshotRecorder with a temp SQLite store."""
    return SnapshotRecorder(enabled=True, max_snapshots=50, db_path=tmp_db)


# ═══════════════════════════════════════════════════════════════════
# 1. test_snapshot_capture — snapshot recorded after supervisor iteration
# ═══════════════════════════════════════════════════════════════════


class TestSnapshotCapture:
    """Verify snapshots are captured correctly."""

    def test_capture_returns_record(self, recorder: SnapshotRecorder, tmp_db: str):
        """capture() returns a SnapshotRecord with correct metadata."""
        state = _make_state(thread_id="t1", iteration=0, last_model="gpt-4o")
        record = recorder.capture(state, db_path=tmp_db)

        assert record is not None
        assert record.thread_id == "t1"
        assert record.iteration == 0
        assert record.model_used == "gpt-4o"
        assert len(record.id) == 36  # UUID

    def test_capture_disabled_returns_none(self, tmp_db: str):
        """capture() returns None when time travel is disabled."""
        recorder = SnapshotRecorder(enabled=False, db_path=tmp_db)
        state = _make_state()
        record = recorder.capture(state, db_path=tmp_db)
        assert record is None

    def test_capture_stores_state_json(self, recorder: SnapshotRecorder, tmp_db: str):
        """The captured record contains valid JSON of the state."""
        state = _make_state(thread_id="t2", iteration=5)
        record = recorder.capture(state, db_path=tmp_db)

        parsed = json.loads(record.state_json)
        assert parsed["thread_id"] == "t2"
        assert parsed["iteration"] == 5


# ═══════════════════════════════════════════════════════════════════
# 2. test_snapshot_store_retrieve — snapshot stored and retrievable
# ═══════════════════════════════════════════════════════════════════


class TestSnapshotStoreRetrieve:
    """Verify snapshots round-trip through SQLite."""

    def test_store_and_get(self, store: SnapshotStore):
        """Save a record and retrieve it by (thread_id, iteration)."""
        record = SnapshotRecord(
            thread_id="thread-A",
            iteration=3,
            state_json=json.dumps({"iteration": 3, "messages": []}),
            model_used="claude-3",
        )
        store.save(record)

        retrieved = store.get("thread-A", 3)
        assert retrieved is not None
        assert retrieved.id == record.id
        assert retrieved.thread_id == "thread-A"
        assert retrieved.iteration == 3
        assert retrieved.model_used == "claude-3"

    def test_get_nonexistent_returns_none(self, store: SnapshotStore):
        """get() returns None for missing snapshots."""
        assert store.get("no-such-thread", 99) is None

    def test_recorder_get_from_memory(self, recorder: SnapshotRecorder, tmp_db: str):
        """recorder.get_snapshot prefers in-memory over SQLite."""
        state = _make_state(thread_id="mem-test", iteration=1)
        recorder.capture(state, db_path=tmp_db)

        snap = recorder.get_snapshot("mem-test", 1, db_path=tmp_db)
        assert snap is not None
        assert snap.iteration == 1

    def test_recorder_get_falls_back_to_db(self, recorder: SnapshotRecorder, tmp_db: str):
        """recorder.get_snapshot falls back to SQLite when not in memory."""
        # Write directly to store
        store = SnapshotStore(db_path=tmp_db)
        record = SnapshotRecord(
            thread_id="db-only",
            iteration=7,
            state_json=json.dumps({"iteration": 7}),
        )
        store.save(record)
        store.close()

        # Not in memory — should hit SQLite
        snap = recorder.get_snapshot("db-only", 7, db_path=tmp_db)
        assert snap is not None
        assert snap.iteration == 7


# ═══════════════════════════════════════════════════════════════════
# 3. test_snapshot_max_limit — old snapshots evicted when over max
# ═══════════════════════════════════════════════════════════════════


class TestSnapshotMaxLimit:
    """Verify LRU eviction at the per-thread cap."""

    def test_memory_eviction(self, tmp_db: str):
        """In-memory store evicts oldest snapshots beyond max_snapshots."""
        recorder = SnapshotRecorder(enabled=True, max_snapshots=3, db_path=tmp_db)

        for i in range(5):
            state = _make_state(thread_id="evict-test", iteration=i)
            recorder.capture(state, db_path=tmp_db)

        # Only iterations 2,3,4 should remain in memory
        memory_keys = [k for k in recorder._memory if k[0] == "evict-test"]
        iterations = sorted(k[1] for k in memory_keys)
        assert iterations == [2, 3, 4]

    def test_db_eviction(self, store: SnapshotStore):
        """SQLite store evicts oldest beyond max_count."""
        for i in range(10):
            store.save(SnapshotRecord(
                thread_id="evict-db",
                iteration=i,
                state_json=json.dumps({"iteration": i}),
            ))

        deleted = store.evict_beyond("evict-db", max_count=5)
        assert deleted == 5

        remaining = store.list_for_thread("evict-db")
        assert len(remaining) == 5
        assert remaining[0].iteration == 5  # oldest kept is 5

    def test_eviction_respects_per_thread_isolation(self, tmp_db: str):
        """Evicting thread A does not affect thread B."""
        recorder = SnapshotRecorder(enabled=True, max_snapshots=2, db_path=tmp_db)

        for i in range(4):
            recorder.capture(_make_state(thread_id="A", iteration=i), db_path=tmp_db)
            recorder.capture(_make_state(thread_id="B", iteration=i), db_path=tmp_db)

        a_keys = [k for k in recorder._memory if k[0] == "A"]
        b_keys = [k for k in recorder._memory if k[0] == "B"]
        assert len(a_keys) == 2
        assert len(b_keys) == 2


# ═══════════════════════════════════════════════════════════════════
# 4. test_replay_from_snapshot — replay loads correct state
# ═══════════════════════════════════════════════════════════════════


class TestReplayFromSnapshot:
    """Verify ReplayEngine loads the right state."""

    def test_replay_returns_state(self, recorder: SnapshotRecorder, tmp_db: str):
        """replay_from returns the captured state dict."""
        state = _make_state(thread_id="replay-t", iteration=3, last_model="gpt-4o")
        recorder.capture(state, db_path=tmp_db)

        engine = ReplayEngine(recorder)
        loaded = engine.replay_from("replay-t", 3, db_path=tmp_db)

        assert loaded is not None
        assert loaded["thread_id"] == "replay-t"
        assert loaded["iteration"] == 3
        assert loaded["last_model"] == "gpt-4o"

    def test_replay_nonexistent_returns_none(self, recorder: SnapshotRecorder, tmp_db: str):
        """replay_from returns None for missing snapshot."""
        engine = ReplayEngine(recorder)
        assert engine.replay_from("nope", 0, db_path=tmp_db) is None

    def test_replay_state_is_independent_copy(self, recorder: SnapshotRecorder, tmp_db: str):
        """Mutating the replayed state does not affect the stored snapshot."""
        state = _make_state(thread_id="copy-test", iteration=0)
        recorder.capture(state, db_path=tmp_db)

        engine = ReplayEngine(recorder)
        loaded = engine.replay_from("copy-test", 0, db_path=tmp_db)
        loaded["iteration"] = 999  # mutate

        # Original should be unchanged
        loaded_again = engine.replay_from("copy-test", 0, db_path=tmp_db)
        assert loaded_again["iteration"] == 0


# ═══════════════════════════════════════════════════════════════════
# 5. test_compare_replays_diff — detects differences between replays
# ═══════════════════════════════════════════════════════════════════


class TestCompareReplays:
    """Verify compare_replays produces accurate diffs."""

    def test_identical_states(self):
        """Two identical states produce an 'all zero' diff."""
        state = _make_state(iteration=3, last_model="gpt-4o", last_cost_usd=0.05)
        diff = ReplayEngine.compare_replays(state, state)

        assert diff["identical"] is True
        assert diff["iteration_delta"] == 0
        assert diff["message_count_delta"] == 0
        assert diff["model_changed"] is False
        assert diff["cost_delta_usd"] == 0.0

    def test_different_iterations(self):
        """Diff detects iteration mismatch."""
        original = _make_state(iteration=2)
        replayed = _make_state(iteration=5)
        diff = ReplayEngine.compare_replays(original, replayed)

        assert diff["original_iteration"] == 2
        assert diff["replayed_iteration"] == 5
        assert diff["iteration_delta"] == 3
        assert diff["identical"] is False

    def test_different_models(self):
        """Diff detects model change."""
        original = _make_state(last_model="gpt-4o-mini")
        replayed = _make_state(last_model="gpt-4o")
        diff = ReplayEngine.compare_replays(original, replayed)

        assert diff["model_changed"] is True
        assert diff["original_model"] == "gpt-4o-mini"
        assert diff["replayed_model"] == "gpt-4o"

    def test_different_costs(self):
        """Diff computes cost delta."""
        original = _make_state(last_cost_usd=0.01)
        replayed = _make_state(last_cost_usd=0.03)
        diff = ReplayEngine.compare_replays(original, replayed)

        assert diff["cost_delta_usd"] == pytest.approx(0.02)

    def test_different_message_counts(self):
        """Diff detects message count changes."""
        original = _make_state(message_count=2)
        replayed = _make_state(message_count=5)
        diff = ReplayEngine.compare_replays(original, replayed)

        assert diff["message_count_delta"] == 3


# ═══════════════════════════════════════════════════════════════════
# 6. test_list_snapshots — lists available checkpoints
# ═══════════════════════════════════════════════════════════════════


class TestListSnapshots:
    """Verify list_snapshots returns all snapshots for a thread."""

    def test_list_returns_all_captured(self, recorder: SnapshotRecorder, tmp_db: str):
        """All captured iterations appear in list_snapshots."""
        for i in range(5):
            recorder.capture(_make_state(thread_id="list-test", iteration=i), db_path=tmp_db)

        snapshots = recorder.list_snapshots("list-test", db_path=tmp_db)
        assert len(snapshots) == 5
        iterations = [s.iteration for s in snapshots]
        assert iterations == [0, 1, 2, 3, 4]

    def test_list_empty_thread(self, recorder: SnapshotRecorder, tmp_db: str):
        """list_snapshots returns [] for thread with no snapshots."""
        assert recorder.list_snapshots("empty-thread", db_path=tmp_db) == []

    def test_list_merges_memory_and_db(self, recorder: SnapshotRecorder, tmp_db: str):
        """list_snapshots merges in-memory and SQLite records."""
        # Put some in memory
        recorder.capture(_make_state(thread_id="merge", iteration=0), db_path=tmp_db)
        recorder.capture(_make_state(thread_id="merge", iteration=1), db_path=tmp_db)

        # Put one only in DB
        store = SnapshotStore(db_path=tmp_db)
        store.save(SnapshotRecord(
            thread_id="merge", iteration=2,
            state_json=json.dumps({"iteration": 2}),
        ))
        store.close()

        snapshots = recorder.list_snapshots("merge", db_path=tmp_db)
        assert len(snapshots) == 3


# ═══════════════════════════════════════════════════════════════════
# 7. test_clear_snapshots — clears all snapshots for a thread
# ═══════════════════════════════════════════════════════════════════


class TestClearSnapshots:
    """Verify clear_snapshots removes all records for a thread."""

    def test_clear_removes_all(self, recorder: SnapshotRecorder, tmp_db: str):
        """After clear, no snapshots remain for the thread."""
        for i in range(5):
            recorder.capture(_make_state(thread_id="clear-test", iteration=i), db_path=tmp_db)

        deleted = recorder.clear_snapshots("clear-test", db_path=tmp_db)
        assert deleted >= 5  # memory + DB

        remaining = recorder.list_snapshots("clear-test", db_path=tmp_db)
        assert remaining == []

    def test_clear_other_thread_unaffected(self, recorder: SnapshotRecorder, tmp_db: str):
        """Clearing thread A does not affect thread B."""
        recorder.capture(_make_state(thread_id="keep", iteration=0), db_path=tmp_db)
        recorder.capture(_make_state(thread_id="drop", iteration=0), db_path=tmp_db)

        recorder.clear_snapshots("drop", db_path=tmp_db)

        assert len(recorder.list_snapshots("keep", db_path=tmp_db)) == 1
        assert len(recorder.list_snapshots("drop", db_path=tmp_db)) == 0

    def test_clear_empty_thread_returns_zero(self, recorder: SnapshotRecorder, tmp_db: str):
        """Clearing a thread with no snapshots returns 0."""
        assert recorder.clear_snapshots("never-existed", db_path=tmp_db) == 0


# ═══════════════════════════════════════════════════════════════════
# 8. test_snapshot_integration — full capture-replay cycle
# ═══════════════════════════════════════════════════════════════════


class TestSnapshotIntegration:
    """End-to-end capture → list → replay → compare cycle."""

    def test_full_capture_replay_cycle(self, tmp_db: str):
        """Simulate a full agent run, capture snapshots, replay and compare."""
        recorder = SnapshotRecorder(enabled=True, max_snapshots=10, db_path=tmp_db)
        engine = ReplayEngine(recorder)

        # Simulate 5 supervisor iterations
        thread_id = "integration-thread"
        for i in range(5):
            state = _make_state(
                thread_id=thread_id,
                iteration=i,
                last_model="gpt-4o-mini" if i < 3 else "gpt-4o",
                last_cost_usd=0.01 * (i + 1),
            )
            recorder.capture(state, db_path=tmp_db)

        # List all snapshots
        snapshots = recorder.list_snapshots(thread_id, db_path=tmp_db)
        assert len(snapshots) == 5

        # Replay from iteration 2
        replayed = engine.replay_from(thread_id, 2, db_path=tmp_db)
        assert replayed is not None
        assert replayed["iteration"] == 2
        assert replayed["last_model"] == "gpt-4o-mini"

        # Get the "final" state (iteration 4)
        final = engine.replay_from(thread_id, 4, db_path=tmp_db)
        assert final is not None
        assert final["iteration"] == 4
        assert final["last_model"] == "gpt-4o"

        # Compare replay point vs final
        diff = ReplayEngine.compare_replays(replayed, final)
        assert diff["iteration_delta"] == 2
        assert diff["model_changed"] is True
        assert diff["cost_delta_usd"] == pytest.approx(0.02)

    def test_create_recorder_from_config(self, tmp_db: str):
        """create_recorder reads from kazma.yaml config structure."""
        config = {
            "time_travel": {
                "enabled": True,
                "max_snapshots": 25,
                "db_path": tmp_db,
            }
        }
        recorder = create_recorder(config=config, db_path=tmp_db)
        assert recorder.enabled is True
        assert recorder._max_snapshots == 25

    def test_create_recorder_defaults(self):
        """create_recorder with no config uses sensible defaults."""
        recorder = create_recorder()
        assert recorder.enabled is True
        assert recorder._max_snapshots == DEFAULT_MAX_SNAPSHOTS

    def test_state_has_snapshot_fields(self):
        """SupervisorState includes snapshot_id and snapshot_iteration."""
        state = initial_supervisor_state()
        assert "snapshot_id" in state
        assert "snapshot_iteration" in state
        assert state["snapshot_id"] == ""
        assert state["snapshot_iteration"] == -1
