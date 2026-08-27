"""Time Travel — snapshot recording and replay for agent execution.

Provides two main components:

- **SnapshotRecorder**: Captures SupervisorState snapshots after each
  supervisor iteration.  Stores them in both an in-memory LRU cache
  (for the current session) and a persistent SQLite database.

- **ReplayEngine**: Loads a recorded snapshot and replays the agent
  execution from that point, enabling "what if" analysis and
  side-by-side comparison of original vs. replayed runs.

Configuration (kazama.yaml):
    time_travel:
        enabled: true          # master switch
        max_snapshots: 50      # per-thread LRU cap
        db_path: kazma-data/snapshots.db

Maintenance:
    ``maintain_snapshots()`` prunes rows older than ``retention_days``
    and VACUUMs the DB to reclaim space.  ``start_snapshot_maintenance_loop()``
    runs that daily (24h cadence) reading ``time_travel.auto_maintain`` and
    ``time_travel.retention_days`` LIVE from the ConfigStore so Settings-UI
    changes apply without a restart (unlike max_snapshots which is read at
    recorder creation).

Design notes:
    - Snapshots are keyed by ``(thread_id, iteration)``.
    - The in-memory store is the source of truth for the current session;
      SQLite is a durable write-ahead log for cross-session replay.
    - ``compare_replays`` produces a structured diff of two replay runs
      (message count, final iteration, model used, cost delta).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import uuid
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

__all__ = ["DEFAULT_DB_PATH", "DEFAULT_MAX_SNAPSHOTS", "ReplayEngine", "SnapshotRecord", "SnapshotRecorder", "SnapshotStore", "create_recorder", "maintain_snapshots", "start_snapshot_maintenance_loop"]

logger = logging.getLogger(__name__)

# Default paths / limits
DEFAULT_DB_PATH = "kazma-data/snapshots.db"
DEFAULT_MAX_SNAPSHOTS = 50


def _resolve_db_path(db_path: str | Path | None) -> str:
    """Resolve a snapshots DB location to an absolute, cwd-independent path (H19).

    The legacy ``DEFAULT_DB_PATH`` literal (``kazma-data/snapshots.db``) is
    CWD-relative: two processes started from different working directories
    silently wrote DIFFERENT snapshot files. Resolution now goes through
    ``kazma_core.paths`` (which honors ``KAZMA_DATA_DIR``):

    - None/empty → ``paths.snapshots_db()`` (the data-dir default).
    - Absolute path → unchanged.
    - Relative path (e.g. the shipped ``kazma.yaml`` ``db_path:``
      ``kazma-data/snapshots.db`` literal) → anchored into the data dir by
      filename, so every process in one installation shares ONE database.
    """
    p = Path(db_path) if db_path else None
    if p is not None and p.is_absolute():
        return str(p)
    try:
        from kazma_core.paths import data_dir

        base = Path(data_dir())
        return str(base / p.name) if p is not None else str(base / "snapshots.db")
    except Exception:  # noqa: BLE001 - never break startup over path resolution
        logger.debug("[TimeTravel] data-dir resolution failed; using legacy default", exc_info=True)
        return str(p) if p is not None else DEFAULT_DB_PATH


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

    Safe for use from multiple threads: the connection is created with
    ``check_same_thread=False`` (capture now runs inside
    ``asyncio.to_thread`` — M35) and mutating operations are serialized
    by an in-process lock.
    """

    def __init__(self, db_path: str | Path | None = DEFAULT_DB_PATH) -> None:
        # H19: resolve through the data dir so the path is cwd-independent.
        self._db_path = _resolve_db_path(db_path)
        # Serializes ALL connection use: capture runs inside asyncio.to_thread
        # (M35), while replay/fork handlers may touch the same connection from
        # the event-loop thread. Unsynchronized cross-thread sqlite use can
        # corrupt the C-level handle (native access violations downstream).
        self._conn_lock = threading.RLock()
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        with self._conn_lock:
            apply_sqlite_pragmas(self._conn)
            self._conn.execute(_CREATE_TABLE_SQL)
            self._conn.execute(_CREATE_INDEX_SQL)
            self._conn.commit()

    @property
    def db_path(self) -> str:
        """The resolved (absolute) database file path."""
        return self._db_path

    def save(self, record: SnapshotRecord) -> None:
        """Insert or replace a snapshot record."""
        with self._conn_lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO snapshots (id, thread_id, iteration, state_json, timestamp, model_used) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (record.id, record.thread_id, record.iteration, record.state_json, record.timestamp, record.model_used),
            )
            self._conn.commit()

    def get(self, thread_id: str, iteration: int) -> SnapshotRecord | None:
        """Retrieve a single snapshot by thread + iteration."""
        with self._conn_lock:
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
        with self._conn_lock:
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
        with self._conn_lock:
            cursor = self._conn.execute(
                "DELETE FROM snapshots WHERE thread_id = ?",
                (thread_id,),
            )
            self._conn.commit()
            return cursor.rowcount

    def list_distinct_threads(self) -> list[str]:
        """Return all distinct thread_ids that have at least one snapshot."""
        with self._conn_lock:
            rows = self._conn.execute(
                "SELECT DISTINCT thread_id FROM snapshots ORDER BY thread_id"
            ).fetchall()
        return [r[0] for r in rows]

    def evict_beyond(self, thread_id: str, max_count: int) -> int:
        """Keep only the latest ``max_count`` snapshots for a thread.

        Deletes the oldest iterations that exceed the cap.
        Returns the number of records deleted.
        """
        with self._conn_lock:
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
        with self._conn_lock:
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
        db_path: str | Path | None = DEFAULT_DB_PATH,
        store: SnapshotStore | None = None,
    ) -> None:
        self._enabled = enabled
        self._max_snapshots = max_snapshots
        # H19: resolve once (data-dir anchored, cwd-independent). Relative
        # literals from legacy kazma.yaml configs normalize to the data dir.
        self._db_path = _resolve_db_path(db_path)
        # In-memory LRU: key=(thread_id, iteration) → SnapshotRecord
        self._memory: OrderedDict[tuple[str, int], SnapshotRecord] = OrderedDict()
        # SQLite store (lazily created or injected); guarded so two
        # concurrent to_thread captures cannot double-create it.
        self._store: SnapshotStore | None = store
        self._store_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def db_path(self) -> str:
        """The resolved (absolute) default snapshots DB path for this recorder."""
        return self._db_path

    def _get_store(self, db_path: str | Path | None = None) -> SnapshotStore:
        """Lazy-init the SQLite store."""
        if self._store is None:
            with self._store_lock:
                if self._store is None:
                    self._store = SnapshotStore(db_path if db_path else self._db_path)
        return self._store

    def capture(
        self,
        state: dict[str, Any],
        *,
        db_path: str | Path | None = None,
    ) -> SnapshotRecord | None:
        """Capture a snapshot of the current SupervisorState.

        Args:
            state: The SupervisorState dict (or any dict with the expected keys).
            db_path: Optional SQLite DB override; defaults to this
                recorder's resolved path (data-dir anchored).

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
        db_path: str | Path | None = None,
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
        db_path: str | Path | None = None,
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
        db_path: str | Path | None = None,
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

    def list_distinct_threads(self, *, db_path: str | Path | None = None) -> list[str]:
        """Return all distinct thread_ids that have at least one snapshot.

        Merges in-memory and SQLite thread sets.
        """
        threads: set[str] = set()
        for key in self._memory:
            threads.add(key[0])
        try:
            store = self._get_store(db_path)
            threads.update(store.list_distinct_threads())
        except Exception as exc:
            logger.debug("[SnapshotRecorder] list_distinct_threads DB failed: %s", exc)
        return sorted(threads)

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
        db_path: str | Path | None = None,
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

    # Effective resolution mirrors the embedder: ConfigStore override >
    # kazama.yaml > default. The Settings UI writes time_travel.max_snapshots
    # to the store; a restart is still required because the recorder is
    # created once per agent at startup.
    try:
        from kazma_core.config_store import get_config_store

        store_max = get_config_store().get("time_travel.max_snapshots", None)
        if store_max is not None:
            _max = int(store_max)
    except Exception:  # noqa: BLE001 - never break startup over the override
        logger.debug("[TimeTravel] ConfigStore override unavailable; using yaml/default", exc_info=True)

    return SnapshotRecorder(
        enabled=enabled,
        max_snapshots=_max,
        db_path=_db,
        store=store,
    )


# ══════════════════════════════════════════════════════════════════════════
# Maintenance — TTL prune + VACUUM (dashboard button + daily auto-loop)
# ══════════════════════════════════════════════════════════════════════════


def maintain_snapshots(
    db_path: str | Path | None = None,
    retention_days: int = 30,
) -> dict[str, Any]:
    """Prune snapshots older than ``retention_days`` and VACUUM the DB.

    SQLite never shrinks a file on DELETE; the freed pages are reclaimed
    here so ``snapshots.db`` stops growing without bound across threads.
    Safe to run while the server is live (WAL mode, short write lock).
    ``db_path=None`` resolves to the data-dir-anchored default (H19).

    Returns:
        Stats dict: ``deleted``, ``size_before``, ``size_after``,
        ``reclaimed`` (bytes), ``retention_days``, and per-step status.
    """
    db = Path(_resolve_db_path(db_path))
    size_before = db.stat().st_size if db.exists() else 0
    deleted = 0
    cutoff = (datetime.now(UTC) - timedelta(days=max(1, int(retention_days)))).isoformat()

    try:
        conn = sqlite3.connect(str(db))
        try:
            apply_sqlite_pragmas(conn)
            if db.exists() and db.stat().st_size > 0:
                cur = conn.execute("DELETE FROM snapshots WHERE timestamp < ?", (cutoff,))
                deleted = cur.rowcount
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - never let maintenance take the server down
        logger.warning("[TimeTravel] snapshot prune failed: %s", exc)
        return {
            "deleted": 0,
            "size_before": size_before,
            "size_after": size_before,
            "reclaimed": 0,
            "retention_days": int(retention_days),
            "prune": f"failed: {exc}",
            "vacuum": "skipped",
        }

    vacuum_status = "ok"
    try:
        conn = sqlite3.connect(str(db))
        try:
            apply_sqlite_pragmas(conn)
            conn.execute("VACUUM")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TimeTravel] snapshot VACUUM failed: %s", exc)
        vacuum_status = f"failed: {exc}"

    size_after = db.stat().st_size if db.exists() else 0
    logger.info(
        "[TimeTravel] maintenance: deleted=%d before=%d after=%d (retention=%dd)",
        deleted,
        size_before,
        size_after,
        int(retention_days),
    )
    return {
        "deleted": deleted,
        "size_before": size_before,
        "size_after": size_after,
        "reclaimed": max(0, size_before - size_after),
        "retention_days": int(retention_days),
        "prune": "ok",
        "vacuum": vacuum_status,
    }


_MAINTENANCE_INTERVAL_HOURS = 24
_MAINTENANCE_FIRST_DELAY_SECONDS = 120


def _live_maintenance_config() -> dict[str, Any]:
    """Live-read the maintenance knobs from the ConfigStore (Settings UI).

    Mirrors ``get_hitl_config``: a Settings change applies on the next loop
    run without a restart.  Falls back to yaml-free defaults on any error.
    """
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        auto = cs.get("time_travel.auto_maintain", True)
        retention = cs.get("time_travel.retention_days", 30)
        return {
            "auto_maintain": bool(auto),
            "retention_days": int(retention) if int(retention) >= 1 else 30,
        }
    except Exception:  # noqa: BLE001
        logger.debug("[TimeTravel] maintenance config unavailable; using defaults", exc_info=True)
        return {"auto_maintain": True, "retention_days": 30}


def start_snapshot_maintenance_loop(
    *,
    interval_hours: int = _MAINTENANCE_INTERVAL_HOURS,
    first_delay_seconds: int = _MAINTENANCE_FIRST_DELAY_SECONDS,
    db_path: str | Path | None = None,
) -> asyncio.Task:
    """Start the daily snapshot prune + VACUUM loop (fire-and-forget).

    Reads ``time_travel.auto_maintain`` / ``time_travel.retention_days`` live
    from the ConfigStore on every run, so Settings-UI changes apply without
    a restart.  Never raises — failures are logged and the loop continues.
    ``db_path=None`` resolves to the data-dir-anchored snapshots DB.
    """
    async def _loop() -> None:
        await asyncio.sleep(max(0, first_delay_seconds))
        while True:
            try:
                from kazma_core.shutdown import is_shutting_down

                if is_shutting_down():
                    logger.info("[TimeTravel] maintenance loop exiting (shutdown)")
                    return
                cfg = _live_maintenance_config()
                if not cfg["auto_maintain"]:
                    logger.debug("[TimeTravel] auto-maintain disabled; skipping")
                else:
                    await asyncio.to_thread(maintain_snapshots, db_path, cfg["retention_days"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - loop must survive any failure
                logger.warning("[TimeTravel] maintenance loop iteration failed: %s", exc)
            await asyncio.sleep(max(1, interval_hours) * 3600)

    return asyncio.create_task(_loop())
