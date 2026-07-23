"""Time Travel — snapshot recording and replay for agent execution.

Provides two main components:

- **SnapshotRecorder**: Captures SupervisorState snapshots after each
  supervisor iteration.  Stores them in both an in-memory LRU cache
  (for the current session) and a persistent SQLite database.

- **ReplayEngine**: Loads a recorded snapshot and replays the agent
  execution from that point, enabling "what if" analysis and
  side-by-side comparison of original vs. replayed runs.

Configuration (kazma.yaml):
    time_travel:
        enabled: true          # master switch
        max_snapshots: 50      # per-thread LRU cap
        db_path: kazma-data/snapshots.db

Design notes:
    - Snapshots are keyed by ``(thread_id, iteration)``.
    - The in-memory store is the source of truth for the current session;
      SQLite is a durable write-ahead log for cross-session replay.
    - ``compare_replays`` produces a structured diff of two replay runs
      (message count, final iteration, model used, cost delta).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

__all__ = ["DEFAULT_DB_PATH", "DEFAULT_MAX_SNAPSHOTS", "ReplayEngine", "SnapshotRecord", "SnapshotRecorder", "SnapshotStore", "create_recorder"]

logger = logging.getLogger(__name__)

# Default paths / limits
DEFAULT_DB_PATH = "kazma-data/snapshots.db"
DEFAULT_MAX_SNAPSHOTS = 50


# ══════════════════════════════════════════════════════════════════════════
# Snapshot data model
# ══════════════════════════════════════════════════════════════════════════


class SnapshotRecord:
    """A single captured SupervisorState snapshot.

    Attributes:
        id: Unique snapshot UUID.
        thread_id: Conversation thread this snapshot belongs to.
        iteration: Supervisor iteration index at capture time.
        state_json: JSON-serialised SupervisorState.
        timestamp: ISO-8601 UTC capture time.
        model_used: Model name from the state's ``last_model`` field.
    """

    __slots__ = ("id", "thread_id", "iteration", "state_json", "timestamp", "model_used")

    def __init__(
        self,
        *,
        id: str | None = None,
        thread_id: str,
        iteration: int,
        state_json: str,
        timestamp: str | None = None,
        model_used: str = "",
    ) -> None:
        self.id = id or str(uuid.uuid4())
        self.thread_id = thread_id
        self.iteration = iteration
        self.state_json = state_json
        self.timestamp = timestamp or datetime.now(UTC).isoformat()
        self.model_used = model_used

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "iteration": self.iteration,
            "state_json": self.state_json,
            "timestamp": self.timestamp,
            "model_used": self.model_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SnapshotRecord:
        """Deserialise from a plain dict."""
        return cls(
            id=data.get("id"),
            thread_id=data["thread_id"],
            iteration=data["iteration"],
            state_json=data["state_json"],
            timestamp=data.get("timestamp"),
            model_used=data.get("model_used", ""),
        )

    def get_state(self) -> dict[str, Any]:
        """Parse the stored state JSON back into a dict."""
        return json.loads(self.state_json)


# ══════════════════════════════════════════════════════════════════════════
# SQLite persistence layer
# ══════════════════════════════════════════════════════════════════════════

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id            TEXT PRIMARY KEY,
    thread_id     TEXT NOT NULL,
    iteration     INTEGER NOT NULL,
    state_json    TEXT NOT NULL,
    timestamp     TEXT NOT NULL,
    model_used    TEXT DEFAULT ''
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_snapshots_thread
ON snapshots (thread_id, iteration)
"""


class SnapshotStore:
    """Persistent SQLite-backed snapshot store.

    Thread-safe for sequential use (SQLite single-writer model).
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        apply_sqlite_pragmas(self._conn)
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.execute(_CREATE_INDEX_SQL)
        self._conn.commit()

    def save(self, record: SnapshotRecord) -> None:
        """Insert or replace a snapshot record."""
        self._conn.execute(
            "INSERT OR REPLACE INTO snapshots (id, thread_id, iteration, state_json, timestamp, model_used) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record.id, record.thread_id, record.iteration, record.state_json, record.timestamp, record.model_used),
        )
        self._conn.commit()

    def get(self, thread_id: str, iteration: int) -> SnapshotRecord | None:
        """Retrieve a single snapshot by thread + iteration."""
        row = self._conn.execute(
            "SELECT id, thread_id, iteration, state_json, timestamp, model_used "
            "FROM snapshots WHERE thread_id = ? AND iteration = ?",
            (thread_id, iteration),
        ).fetchone()
        if row is None:
            return None
        return SnapshotRecord(
            id=row[0], thread_id=row[1], iteration=row[2],
            state_json=row[3], timestamp=row[4], model_used=row[5],
        )

    def list_for_thread(self, thread_id: str) -> list[SnapshotRecord]:
        """List all snapshots for a thread, ordered by iteration."""
        rows = self._conn.execute(
            "SELECT id, thread_id, iteration, state_json, timestamp, model_used "
            "FROM snapshots WHERE thread_id = ? ORDER BY iteration",
            (thread_id,),
        ).fetchall()
        return [
            SnapshotRecord(
                id=r[0], thread_id=r[1], iteration=r[2],
                state_json=r[3], timestamp=r[4], model_used=r[5],
            )
            for r in rows
        ]

    def clear_thread(self, thread_id: str) -> int:
        """Delete all snapshots for a thread.  Returns count deleted."""
        cursor = self._conn.execute(
            "DELETE FROM snapshots WHERE thread_id = ?",
            (thread_id,),
        )
        self._conn.commit()
        return cursor.rowcount

    def evict_beyond(self, thread_id: str, max_count: int) -> int:
        """Keep only the latest ``max_count`` snapshots for a thread.

        Deletes the oldest iterations that exceed the cap.
        Returns the number of records deleted.
        """
        rows = self._conn.execute(
            "SELECT id FROM snapshots WHERE thread_id = ? ORDER BY iteration DESC",
            (thread_id,),
        ).fetchall()
        if len(rows) <= max_count:
            return 0
        ids_to_delete = [r[0] for r in rows[max_count:]]
        placeholders = ",".join("?" * len(ids_to_delete))
        cursor = self._conn.execute(
            f"DELETE FROM snapshots WHERE id IN ({placeholders})",
            ids_to_delete,
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()


# ══════════════════════════════════════════════════════════════════════════
# SnapshotRecorder
# ══════════════════════════════════════════════════════════════════════════


class SnapshotRecorder:
    """Captures SupervisorState snapshots after each iteration.

    Maintains an in-memory LRU cache (``max_snapshots`` per thread) and
    writes through to SQLite for durable storage.

    Usage::

        recorder = SnapshotRecorder()
        # Inside supervisor_node, after iteration:
        recorder.capture(state)
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
        db_path: str = DEFAULT_DB_PATH,
        store: SnapshotStore | None = None,
    ) -> None:
        self._enabled = enabled
        self._max_snapshots = max_snapshots
        # In-memory LRU: key=(thread_id, iteration) → SnapshotRecord
        self._memory: OrderedDict[tuple[str, int], SnapshotRecord] = OrderedDict()
        # SQLite store (lazily created or injected)
        self._store = store

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def _get_store(self, db_path: str = DEFAULT_DB_PATH) -> SnapshotStore:
        """Lazy-init the SQLite store."""
        if self._store is None:
            self._store = SnapshotStore(db_path)
        return self._store

    def capture(
        self,
        state: dict[str, Any],
        *,
        db_path: str = DEFAULT_DB_PATH,
    ) -> SnapshotRecord | None:
        """Capture a snapshot of the current SupervisorState.

        Args:
            state: The SupervisorState dict (or any dict with the expected keys).
            db_path: Path to the SQLite snapshots database.

        Returns:
            The captured SnapshotRecord, or None if time travel is disabled.
        """
        if not self._enabled:
            return None

        thread_id = state.get("thread_id", "unknown")
        iteration = state.get("iteration", 0)
        model_used = state.get("last_model", "")

        record = SnapshotRecord(
            thread_id=thread_id,
            iteration=iteration,
            state_json=json.dumps(state, default=str),
            model_used=model_used,
        )

        # In-memory LRU
        key = (thread_id, iteration)
        self._memory[key] = record
        self._memory.move_to_end(key)

        # Evict oldest if over cap (per-thread)
        thread_keys = [k for k in self._memory if k[0] == thread_id]
        while len(thread_keys) > self._max_snapshots:
            oldest_key = thread_keys.pop(0)
            del self._memory[oldest_key]

        # Write-through to SQLite
        try:
            store = self._get_store(db_path)
            store.save(record)
            store.evict_beyond(thread_id, self._max_snapshots)
        except Exception as exc:
            logger.warning("[TimeTravel] SQLite write failed (non-fatal): %s", exc)

        logger.debug(
            "[TimeTravel] Captured snapshot thread=%s iter=%d model=%s",
            thread_id, iteration, model_used,
        )
        return record

    def get_snapshot(
        self,
        thread_id: str,
        iteration: int,
        *,
        db_path: str = DEFAULT_DB_PATH,
    ) -> SnapshotRecord | None:
        """Retrieve a snapshot, preferring in-memory over SQLite."""
        key = (thread_id, iteration)
        if key in self._memory:
            return self._memory[key]
        # Fall back to SQLite
        try:
            store = self._get_store(db_path)
            return store.get(thread_id, iteration)
        except Exception as exc:
            logger.debug("[SnapshotStore] SQLite fallback failed for thread %s, iteration %d: %s", thread_id, iteration, exc)
            return None

    def list_snapshots(
        self,
        thread_id: str,
        *,
        db_path: str = DEFAULT_DB_PATH,
    ) -> list[SnapshotRecord]:
        """List all available snapshots for a thread."""
        # Merge in-memory and SQLite, dedup by (thread_id, iteration)
        seen: dict[tuple[str, int], SnapshotRecord] = {}

        # In-memory first
        for key, rec in self._memory.items():
            if key[0] == thread_id:
                seen[key] = rec

        # SQLite
        try:
            store = self._get_store(db_path)
            for rec in store.list_for_thread(thread_id):
                key = (rec.thread_id, rec.iteration)
                if key not in seen:
                    seen[key] = rec
        except Exception as exc:
            logger.debug("Failed to load snapshots for thread %s: %s", thread_id, exc)

        return sorted(seen.values(), key=lambda r: r.iteration)

    def clear_snapshots(
        self,
        thread_id: str,
        *,
        db_path: str = DEFAULT_DB_PATH,
    ) -> int:
        """Clear all snapshots for a thread from both stores.

        Returns the total count of deleted records (memory + SQLite).
        """
        mem_count = sum(1 for k in list(self._memory) if k[0] == thread_id)
        for k in list(self._memory):
            if k[0] == thread_id:
                del self._memory[k]

        db_count = 0
        try:
            store = self._get_store(db_path)
            db_count = store.clear_thread(thread_id)
        except Exception as exc:
            logger.debug("Failed to clear DB snapshots for thread %s: %s", thread_id, exc)

        return mem_count + db_count

    def close(self) -> None:
        """Close the SQLite store if open."""
        if self._store is not None:
            self._store.close()
            self._store = None


# ══════════════════════════════════════════════════════════════════════════
# ReplayEngine
# ══════════════════════════════════════════════════════════════════════════


class ReplayEngine:
    """Replays agent execution from a recorded snapshot.

    Loads a checkpoint state and provides it for re-execution through
    the graph.  Also supports comparing two replay runs.
    """

    def __init__(self, recorder: SnapshotRecorder) -> None:
        self._recorder = recorder

    def replay_from(
        self,
        thread_id: str,
        iteration: int,
        *,
        db_path: str = DEFAULT_DB_PATH,
    ) -> dict[str, Any] | None:
        """Load the state snapshot for a specific thread + iteration.

        The caller (graph integration) takes this state and feeds it
        back into the supervisor node to resume execution.

        Args:
            thread_id: The conversation thread to replay.
            iteration: The iteration number to rewind to.

        Returns:
            The deserialized SupervisorState dict, or None if no
            snapshot exists for the given thread/iteration.
        """
        record = self._recorder.get_snapshot(thread_id, iteration, db_path=db_path)
        if record is None:
            logger.warning(
                "[ReplayEngine] No snapshot found for thread=%s iter=%d",
                thread_id, iteration,
            )
            return None

        state = record.get_state()
        logger.info(
            "[ReplayEngine] Loaded snapshot thread=%s iter=%d (recorded at %s)",
            thread_id, iteration, record.timestamp,
        )
        return state

    @staticmethod
    def compare_replays(
        original: dict[str, Any],
        replayed: dict[str, Any],
    ) -> dict[str, Any]:
        """Diff two replay runs.

        Produces a structured comparison covering:
          - message count delta
          - iteration count delta
          - model used (original vs. replayed)
          - cost delta
          - tool call count delta
          - whether the final node routing differed

        Args:
            original: The original SupervisorState (or snapshot).
            replayed: The replayed SupervisorState.

        Returns:
            A dict with diff details.
        """
        def _msg_count(state: dict) -> int:
            return len(state.get("messages", []))

        def _tool_call_count(state: dict) -> int:
            return len(state.get("tool_calls_pending", [])) + len(state.get("tool_calls_done", []))

        return {
            "original_iteration": original.get("iteration", 0),
            "replayed_iteration": replayed.get("iteration", 0),
            "iteration_delta": replayed.get("iteration", 0) - original.get("iteration", 0),
            "original_message_count": _msg_count(original),
            "replayed_message_count": _msg_count(replayed),
            "message_count_delta": _msg_count(replayed) - _msg_count(original),
            "original_model": original.get("last_model", ""),
            "replayed_model": replayed.get("last_model", ""),
            "model_changed": original.get("last_model", "") != replayed.get("last_model", ""),
            "original_cost_usd": original.get("last_cost_usd", 0.0),
            "replayed_cost_usd": replayed.get("last_cost_usd", 0.0),
            "cost_delta_usd": replayed.get("last_cost_usd", 0.0) - original.get("last_cost_usd", 0.0),
            "original_tool_calls": _tool_call_count(original),
            "replayed_tool_calls": _tool_call_count(replayed),
            "tool_calls_delta": _tool_call_count(replayed) - _tool_call_count(original),
            "original_next_node": original.get("next_node", ""),
            "replayed_next_node": replayed.get("next_node", ""),
            "routing_changed": original.get("next_node", "") != replayed.get("next_node", ""),
            "identical": original == replayed,
        }


# ══════════════════════════════════════════════════════════════════════════
# Factory helpers
# ══════════════════════════════════════════════════════════════════════════


def create_recorder(
    *,
    config: dict[str, Any] | None = None,
    db_path: str | None = None,
    max_snapshots: int | None = None,
    store: SnapshotStore | None = None,
) -> SnapshotRecorder:
    """Create a SnapshotRecorder from kazma.yaml config.

    Args:
        config: Full kazma.yaml dict.  Reads ``time_travel.enabled``,
            ``time_travel.max_snapshots``, ``time_travel.db_path``.
        db_path: Override db_path (takes precedence over config).
        max_snapshots: Override max_snapshots (takes precedence over config).
        store: Inject a pre-built SnapshotStore (for testing).

    Returns:
        Configured SnapshotRecorder instance.
    """
    tt_cfg = (config or {}).get("time_travel", {})
    enabled = tt_cfg.get("enabled", True)
    _max = max_snapshots if max_snapshots is not None else tt_cfg.get("max_snapshots", DEFAULT_MAX_SNAPSHOTS)
    _db = db_path or tt_cfg.get("db_path", DEFAULT_DB_PATH)

    return SnapshotRecorder(
        enabled=enabled,
        max_snapshots=_max,
        db_path=_db,
        store=store,
    )
