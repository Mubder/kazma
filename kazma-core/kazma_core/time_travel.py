"""Time Travel — snapshot recording and replay for agent execution.

Provides two main components:

- **SnapshotRecorder**: captures a SupervisorState snapshot after supervisor
  iterations and stores it in ``snapshots.db`` (SQLite). The database is the
  only source of truth: there is no in-memory copy (Time Travel audit
  TT-01/TT-05, 2026-10-14). The old per-process cache keyed snapshots by
  ``(thread_id, iteration)`` and disagreed with the database and with every
  other recorder; a cleared or evicted step stayed restorable from it.

- **ReplayEngine**: loads a recorded snapshot (by its id) and compares two.

Identity (TT-01). A snapshot is addressed by its id (a uuid). It also carries
its turn: ``turn_key`` (the turn's ``created_at``, set once per turn by
``initial_supervisor_state``), ``turn_index`` (the user messages it holds,
``memory.consolidator.user_turn_index``'s count) and ``seq``, its capture
order. ``iteration`` restarts at 0 on every turn, so it is a step number
inside a turn, never an address. Restore, fork, detail and compare take a
snapshot id on every surface.

Bounds (TT-06). A snapshot is the whole state, so the stored bytes grew with
the square of a turn's length. Declared budgets, enforced in SQLite in the
same write transaction that inserts each captured step (a crash leaves the
step stored with its eviction, or not at all), oldest first by capture order:

- ``max_snapshot_bytes`` -- one snapshot; a larger state is stored with its
  long texts cut, marked ``_time_travel_truncated``, and cannot be restored
  or forked (it can be read);
- ``max_per_turn`` -- steps kept per turn (its first and its newest);
- ``max_snapshots`` / ``max_thread_bytes`` -- per conversation;
- ``max_global_snapshots`` / ``max_total_bytes`` -- the whole database.

Captures are thinned inside a long turn (:func:`_capture_due`): every step up
to ``CAPTURE_ALL_UNTIL``, then every ``CAPTURE_EVERY``-th, and always the
turn's last step.

Maintenance:
    ``maintain_snapshots()`` prunes rows older than ``retention_days`` and
    VACUUMs the file only when its free pages justify it.
    ``start_snapshot_maintenance_loop()`` runs that daily, reading
    ``time_travel.auto_maintain`` and ``time_travel.retention_days`` live.

Restore and fork go through ``kazma_ui.thread_rewind`` (one service for every
surface: the per-thread lock, the refusals, the transcript, the audit row).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

__all__ = [
    "CAPTURE_ALL_UNTIL",
    "CAPTURE_EVERY",
    "DEFAULT_DB_PATH",
    "DEFAULT_MAX_GLOBAL_SNAPSHOTS",
    "DEFAULT_MAX_PER_TURN",
    "DEFAULT_MAX_SNAPSHOTS",
    "DEFAULT_MAX_SNAPSHOT_BYTES",
    "DEFAULT_MAX_THREAD_BYTES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "TRUNCATION_MARK",
    "ReplayEngine",
    "SnapshotRecord",
    "SnapshotRecorder",
    "SnapshotStore",
    "SnapshotStoreError",
    "apply_snapshot_to_thread",
    "capture_after_supervisor",
    "checkpoint_payload_from_snapshot",
    "create_recorder",
    "forget_thread_snapshots",
    "forget_turn_snapshots",
    "maintain_snapshots",
    "process_recorder",
    "set_process_recorder",
    "start_snapshot_maintenance_loop",
    "turn_boundary_payload",
]

logger = logging.getLogger(__name__)

# Default paths / limits
from kazma_core.paths import snapshots_db as _snapshots_db

#: Resolved via paths so KAZMA_DATA_DIR is honoured.
DEFAULT_DB_PATH = str(_snapshots_db())
DEFAULT_MAX_SNAPSHOTS = 50
DEFAULT_MAX_GLOBAL_SNAPSHOTS = 2000
#: One snapshot's JSON; a larger state is stored truncated (TT-06).
DEFAULT_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
#: Every snapshot of one conversation together.
DEFAULT_MAX_THREAD_BYTES = 32 * 1024 * 1024
#: The whole database (before SQLite's own page overhead).
DEFAULT_MAX_TOTAL_BYTES = 512 * 1024 * 1024
#: Steps kept per turn: its first and its newest.
DEFAULT_MAX_PER_TURN = 30

#: Every supervisor step up to this iteration is captured.
CAPTURE_ALL_UNTIL = 20
#: Beyond it, every CAPTURE_EVERY-th step and the turn's last step.
CAPTURE_EVERY = 10

#: The key a truncated snapshot carries; restore and fork refuse it.
TRUNCATION_MARK = "_time_travel_truncated"


class SnapshotStoreError(RuntimeError):
    """The snapshot store could not be read or written.

    Raised to the surfaces (503 on the routes, a plain sentence in the chat)
    instead of reading as "no steps saved" (TT-11): a corrupt or locked
    database used to look exactly like an empty one.
    """


def _resolve_db_path(db_path: str | Path | None) -> str:
    """Resolve a snapshots DB location to an absolute, cwd-independent path (H19).

    - None/empty -> ``paths.snapshots_db()`` (the data-dir default).
    - Absolute path -> unchanged.
    - Relative path (e.g. the shipped ``kazma.yaml`` ``db_path:``
      ``kazma-data/snapshots.db`` literal) -> anchored into the data dir by
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


def _role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role") or "")
    return str(getattr(message, "type", "") or "")


def _user_turns(messages: Any) -> int:
    """User messages held: ``memory.consolidator.user_turn_index``'s count,
    so a snapshot's turn and a memory's ``turn_number`` are the same number."""
    if not isinstance(messages, list):
        return 0
    return sum(1 for m in messages if _role(m) in ("user", "human"))


def _record_capture(result: str) -> None:
    from kazma_core.metrics import record_time_travel_capture

    record_time_travel_capture(result)  # a no-op without prometheus; never raises


# ══════════════════════════════════════════════════════════════════════════
# Snapshot data model
# ══════════════════════════════════════════════════════════════════════════


class SnapshotRecord:
    """A single captured SupervisorState snapshot.

    Attributes:
        id: Unique snapshot UUID -- the snapshot's address.
        thread_id: Conversation thread this snapshot belongs to.
        iteration: Supervisor iteration at capture time (restarts every turn).
        state_json: JSON-serialised SupervisorState ("" in a listing).
        timestamp: ISO-8601 UTC capture time.
        model_used: Model name from the state's ``last_model`` field.
        turn_key: The turn's identity (its ``created_at``).
        turn_index: User messages the snapshot holds (the turn number).
        message_count: Messages the snapshot holds (counted at write time).
        nbytes: Stored JSON size.
        truncated: True when the state was too large to keep whole.
        seq: Capture order (larger is newer).
    """

    __slots__ = (
        "id", "thread_id", "iteration", "state_json", "timestamp", "model_used",
        "turn_key", "turn_index", "message_count", "nbytes", "truncated", "seq",
    )

    def __init__(
        self,
        *,
        id: str | None = None,
        thread_id: str,
        iteration: int,
        state_json: str,
        timestamp: str | None = None,
        model_used: str = "",
        turn_key: str = "",
        turn_index: int = 0,
        message_count: int = 0,
        nbytes: int = 0,
        truncated: bool = False,
        seq: int = 0,
    ) -> None:
        self.id = id or str(uuid.uuid4())
        self.thread_id = thread_id
        self.iteration = int(iteration or 0)
        self.state_json = state_json
        self.timestamp = timestamp or datetime.now(UTC).isoformat()
        self.model_used = model_used or ""
        self.turn_key = turn_key or ""
        self.turn_index = int(turn_index or 0)
        self.message_count = int(message_count or 0)
        self.nbytes = int(nbytes or len(state_json or ""))
        self.truncated = bool(truncated)
        self.seq = int(seq or 0)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "iteration": self.iteration,
            "state_json": self.state_json,
            "timestamp": self.timestamp,
            "model_used": self.model_used,
            "turn_key": self.turn_key,
            "turn_index": self.turn_index,
            "message_count": self.message_count,
            "nbytes": self.nbytes,
            "truncated": self.truncated,
            "seq": self.seq,
        }

    def summary(self) -> dict[str, Any]:
        """What a listing shows: everything but the state itself."""
        out = self.to_dict()
        out.pop("state_json", None)
        return out

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
            turn_key=data.get("turn_key", ""),
            turn_index=data.get("turn_index", 0),
            message_count=data.get("message_count", 0),
            nbytes=data.get("nbytes", 0),
            truncated=data.get("truncated", False),
            seq=data.get("seq", 0),
        )

    def get_state(self) -> dict[str, Any]:
        """Parse the stored state JSON back into a dict."""
        return json.loads(self.state_json)


def _bounded_state_json(state: dict[str, Any], limit: int) -> tuple[str, bool, int]:
    """The state as JSON within *limit* bytes: ``(json, truncated, original)``.

    ``json.dumps`` escapes every non-ASCII character, so the string's length
    is its size in bytes. A state over the limit has its long texts cut
    (message contents, tool results, scratchpad values), harder each pass;
    one that still does not fit keeps its routing fields and no messages.
    Either way it carries :data:`TRUNCATION_MARK`, and restore and fork
    refuse it: writing cut texts back into a conversation would lose them.
    """
    text = json.dumps(state, default=str)
    original = len(text)
    if original <= limit:
        return text, False, original

    def _cut(value: Any, cap: int) -> Any:
        if isinstance(value, str) and len(value) > cap:
            return value[:cap] + f"... [cut by Time Travel: {len(value) - cap} more characters]"
        return value

    for cap in (4000, 1000, 200, 40):
        slim = dict(state)
        messages = []
        for m in state.get("messages") or []:
            if isinstance(m, dict):
                m = dict(m)
                m["content"] = _cut(m.get("content"), cap)
            messages.append(m)
        slim["messages"] = messages
        for key in ("tool_results", "scratchpad"):
            value = state.get(key)
            if isinstance(value, dict):
                slim[key] = {k: _cut(v, cap) for k, v in value.items()}
        slim[TRUNCATION_MARK] = {"original_bytes": original, "limit": limit, "cut_to": cap}
        text = json.dumps(slim, default=str)
        if len(text) <= limit:
            return text, True, original
    skeleton = {
        k: state.get(k)
        for k in ("thread_id", "iteration", "created_at", "next_node", "last_model", "tenant_id")
        if k in state
    }
    skeleton["messages"] = []
    skeleton[TRUNCATION_MARK] = {"original_bytes": original, "limit": limit, "cut_to": 0}
    return json.dumps(skeleton, default=str), True, original


def _capture_due(iteration: Any, next_node: Any) -> bool:
    """Whether a supervisor step is captured (TT-06): every step up to
    :data:`CAPTURE_ALL_UNTIL`, then every :data:`CAPTURE_EVERY`-th, and the
    turn's last step (the supervisor routes anywhere but the tool worker or
    itself). A 2,000-iteration mission turn wrote 2,000 whole states."""
    try:
        it = int(iteration or 0)
    except (TypeError, ValueError):
        return True
    if it <= CAPTURE_ALL_UNTIL:
        return True
    if str(next_node or "") not in ("tool_worker", "supervisor"):
        return True
    return it % CAPTURE_EVERY == 0


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
    model_used    TEXT DEFAULT '',
    turn_key      TEXT NOT NULL DEFAULT '',
    turn_index    INTEGER NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    nbytes        INTEGER NOT NULL DEFAULT 0,
    truncated     INTEGER NOT NULL DEFAULT 0,
    seq           INTEGER NOT NULL DEFAULT 0
)
"""

#: Columns an older snapshots.db lacks (TT-01/TT-06).
_ADDED_COLUMNS = (
    ("turn_key", "TEXT NOT NULL DEFAULT ''"),
    ("turn_index", "INTEGER NOT NULL DEFAULT 0"),
    ("message_count", "INTEGER NOT NULL DEFAULT 0"),
    ("nbytes", "INTEGER NOT NULL DEFAULT 0"),
    ("truncated", "INTEGER NOT NULL DEFAULT 0"),
    ("seq", "INTEGER NOT NULL DEFAULT 0"),
)

_CREATE_INDEXES_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_snapshots_thread ON snapshots (thread_id, iteration)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_thread_seq ON snapshots (thread_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_seq ON snapshots (seq)",
)

#: A row written before the identity columns: nbytes 0. Its size, capture
#: order (rowid), turn and message count come from its own JSON.
_BACKFILL_SQL = """
UPDATE snapshots SET
    nbytes = length(state_json),
    seq = rowid,
    turn_key = CASE WHEN json_valid(state_json)
        THEN COALESCE(json_extract(state_json, '$.created_at'), '') ELSE '' END,
    message_count = CASE WHEN json_valid(state_json)
        THEN COALESCE(json_array_length(state_json, '$.messages'), 0) ELSE 0 END,
    turn_index = CASE WHEN json_valid(state_json)
        THEN (SELECT count(*) FROM json_each(snapshots.state_json, '$.messages')
              WHERE json_extract(value, '$.role') IN ('user', 'human'))
        ELSE 0 END
WHERE nbytes = 0
"""

_COLUMNS = (
    "id, thread_id, iteration, state_json, timestamp, model_used, "
    "turn_key, turn_index, message_count, nbytes, truncated, seq"
)
_SUMMARY_COLUMNS = (
    "id, thread_id, iteration, '' AS state_json, timestamp, model_used, "
    "turn_key, turn_index, message_count, nbytes, truncated, seq"
)


def _row_to_record(row: Any) -> SnapshotRecord:
    return SnapshotRecord(
        id=row[0], thread_id=row[1], iteration=row[2], state_json=row[3] or "",
        timestamp=row[4], model_used=row[5] or "", turn_key=row[6] or "",
        turn_index=row[7] or 0, message_count=row[8] or 0, nbytes=row[9] or 0,
        truncated=bool(row[10]), seq=row[11] or 0,
    )


class SnapshotStore:
    """Persistent SQLite-backed snapshot store.

    Safe for use from multiple threads: the connection is created with
    ``check_same_thread=False`` (capture runs inside ``asyncio.to_thread``)
    and every use is serialized by an in-process lock.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        # H19: resolve through the data dir so the path is cwd-independent.
        self._db_path = _resolve_db_path(db_path)
        self._conn_lock = threading.RLock()
        self._last_seq = 0
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # Autocommit: every write here is one statement (or an explicit
        # BEGIN IMMEDIATE ... COMMIT), and one that raises cannot leave
        # snapshots.db's write lock held (tests/test_sqlite_kept_connections.py).
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False, isolation_level=None)
        try:
            with self._conn_lock:
                apply_sqlite_pragmas(self._conn)
                self._conn.execute(_CREATE_TABLE_SQL)
                from kazma_core.db.sqlite_columns import add_missing_columns

                add_missing_columns(self._conn, "snapshots", _ADDED_COLUMNS)
                for sql in _CREATE_INDEXES_SQL:
                    self._conn.execute(sql)
                self._backfill_legacy_rows()
        except sqlite3.Error:
            self._conn.close()
            raise

    def _backfill_legacy_rows(self) -> None:
        try:
            self._conn.execute(_BACKFILL_SQL)
            return
        except sqlite3.OperationalError:
            # No JSON1 functions in this SQLite: read the rows in Python.
            logger.info("[TimeTravel] JSON1 unavailable; backfilling snapshot identity in Python")
        rows = self._conn.execute(
            "SELECT rowid, state_json FROM snapshots WHERE nbytes = 0"
        ).fetchall()
        for rowid, text in rows:
            try:
                state = json.loads(text or "{}")
            except ValueError:
                state = {}
            messages = state.get("messages") if isinstance(state, dict) else None
            self._conn.execute(
                "UPDATE snapshots SET nbytes = ?, seq = ?, turn_key = ?, message_count = ?, "
                "turn_index = ? WHERE rowid = ?",
                (
                    len(text or "") or 1, rowid,
                    str((state or {}).get("created_at") or "") if isinstance(state, dict) else "",
                    len(messages) if isinstance(messages, list) else 0,
                    _user_turns(messages), rowid,
                ),
            )

    @property
    def db_path(self) -> str:
        """The resolved (absolute) database file path."""
        return self._db_path

    def _next_seq(self) -> int:
        """Capture order: wall-clock nanoseconds, never below the last one."""
        with self._conn_lock:
            self._last_seq = max(time.time_ns(), self._last_seq + 1)
            return self._last_seq

    def _insert(self, record: SnapshotRecord) -> None:
        """Write *record*'s row. The caller holds the lock; from
        :meth:`enforce_bounds` it also holds the write transaction."""
        if not record.seq:
            record.seq = self._next_seq()
        self._conn.execute(
            f"INSERT OR REPLACE INTO snapshots ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id, record.thread_id, record.iteration, record.state_json,
                record.timestamp, record.model_used, record.turn_key, record.turn_index,
                record.message_count, record.nbytes, int(record.truncated), record.seq,
            ),
        )

    def save(self, record: SnapshotRecord) -> None:
        """Insert a snapshot record on its own (raises on failure).

        Not the capture path: a row saved here is held to no budget until
        the next capture of its chat. A capture writes its step and its
        eviction in one transaction (:meth:`enforce_bounds` with
        ``insert=True``). Kept for tests and tools that seed a store.
        """
        with self._conn_lock:
            self._insert(record)

    def get_by_id(self, snapshot_id: str) -> SnapshotRecord | None:
        """One snapshot, with its state."""
        with self._conn_lock:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM snapshots WHERE id = ?", (str(snapshot_id),)
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    def ids_with_prefix(self, thread_id: str, prefix: str, limit: int = 3) -> list[str]:
        """Ids of *thread_id*'s snapshots starting with *prefix* (a chat
        command names a step by the first characters of its id)."""
        with self._conn_lock:
            rows = self._conn.execute(
                "SELECT id FROM snapshots WHERE thread_id = ? AND substr(id, 1, ?) = ? "
                "ORDER BY seq LIMIT ?",
                (thread_id, len(prefix), prefix, int(limit)),
            ).fetchall()
        return [str(r[0]) for r in rows]

    def list_for_thread(self, thread_id: str) -> list[SnapshotRecord]:
        """A thread's snapshots in capture order, WITHOUT their states (a
        listing must not decode every state, TT-06)."""
        with self._conn_lock:
            rows = self._conn.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM snapshots WHERE thread_id = ? ORDER BY seq, timestamp",
                (thread_id,),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def clear_threads(self, thread_ids: list[str]) -> int:
        """Delete every snapshot of these threads. Returns rows deleted."""
        ids = [t for t in dict.fromkeys(thread_ids) if t]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        with self._conn_lock:
            return self._conn.execute(
                f"DELETE FROM snapshots WHERE thread_id IN ({marks})", ids
            ).rowcount

    def clear_thread(self, thread_id: str) -> int:
        """Delete all snapshots for a thread.  Returns count deleted."""
        return self.clear_threads([thread_id])

    def delete_turns_from(self, thread_ids: list[str], turn_index: int) -> int:
        """Delete the snapshots of these threads holding turn *turn_index* or
        later (a snapshot holds every message before it)."""
        ids = [t for t in dict.fromkeys(thread_ids) if t]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        with self._conn_lock:
            return self._conn.execute(
                f"DELETE FROM snapshots WHERE thread_id IN ({marks}) AND turn_index >= ?",
                (*ids, int(turn_index)),
            ).rowcount

    def delete_after(self, thread_id: str, seq: int) -> int:
        """Delete *thread_id*'s snapshots captured after *seq* (a rewind)."""
        with self._conn_lock:
            return self._conn.execute(
                "DELETE FROM snapshots WHERE thread_id = ? AND seq > ?", (thread_id, int(seq))
            ).rowcount

    def list_distinct_threads(self) -> list[str]:
        """Return all distinct thread_ids that have at least one snapshot."""
        with self._conn_lock:
            rows = self._conn.execute(
                "SELECT DISTINCT thread_id FROM snapshots ORDER BY thread_id"
            ).fetchall()
        return [r[0] for r in rows]

    def totals(self, thread_id: str | None = None) -> tuple[int, int]:
        """``(rows, bytes)`` of one thread, or of the whole database."""
        with self._conn_lock:
            if thread_id is None:
                row = self._conn.execute("SELECT count(*), COALESCE(SUM(nbytes), 0) FROM snapshots").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT count(*), COALESCE(SUM(nbytes), 0) FROM snapshots WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
        return int(row[0] or 0), int(row[1] or 0)

    def evict_beyond(self, thread_id: str, max_count: int) -> int:
        """Keep only the newest ``max_count`` snapshots of a thread, by
        capture order. (It kept the highest ITERATION numbers: after one long
        turn, every later short turn's snapshot was deleted as it was written,
        TT-01.)"""
        with self._conn_lock:
            return self._conn.execute(
                "DELETE FROM snapshots WHERE thread_id = ? AND id NOT IN ("
                "SELECT id FROM snapshots WHERE thread_id = ? ORDER BY seq DESC LIMIT ?)",
                (thread_id, thread_id, max(0, int(max_count))),
            ).rowcount

    def enforce_bounds(
        self,
        record: SnapshotRecord,
        *,
        insert: bool = False,
        max_per_turn: int,
        max_snapshots: int,
        max_thread_bytes: int,
        max_global_snapshots: int,
        max_total_bytes: int,
    ) -> dict[str, int]:
        """Evict, oldest first by capture order, until every budget holds.

        Never deletes *record* itself (the step just written). One write
        transaction, so a reader never sees a half-evicted thread. With
        ``insert=True`` the record's row is written first, inside that same
        transaction: a crash, or an error anywhere in it, leaves the step
        stored with its eviction or not at all. As two transactions (the
        insert committed alone, then the eviction) a kill between them left
        the turn one step over its cap (found by the TT-G3 soak), and a
        failed eviction left a step stored that the capture said was not.
        """
        out = {"per_turn": 0, "thread_count": 0, "thread_bytes": 0, "total_count": 0, "total_bytes": 0}
        thread = record.thread_id
        with self._conn_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if insert:
                    self._insert(record)
                if record.turn_key and max_per_turn > 0:
                    # The turn's first step stays (where it started), and its newest.
                    rows = self._conn.execute(
                        "SELECT id FROM snapshots WHERE thread_id = ? AND turn_key = ? ORDER BY seq",
                        (thread, record.turn_key),
                    ).fetchall()
                    extra = len(rows) - max_per_turn
                    if extra > 0:
                        doomed = [r[0] for r in rows[1:1 + extra] if r[0] != record.id]
                        out["per_turn"] = self._delete_ids(doomed)
                out["thread_count"] = self._conn.execute(
                    "DELETE FROM snapshots WHERE thread_id = ? AND id NOT IN ("
                    "SELECT id FROM snapshots WHERE thread_id = ? ORDER BY seq DESC LIMIT ?)",
                    (thread, thread, max(1, int(max_snapshots))),
                ).rowcount
                out["thread_bytes"] = self._trim_bytes(
                    "WHERE thread_id = ? AND id != ?", (thread, record.id), max_thread_bytes,
                    "WHERE thread_id = ?", (thread,),
                )
                out["total_count"] = self._conn.execute(
                    "DELETE FROM snapshots WHERE id NOT IN ("
                    "SELECT id FROM snapshots ORDER BY seq DESC LIMIT ?)",
                    (max(1, int(max_global_snapshots)),),
                ).rowcount
                out["total_bytes"] = self._trim_bytes(
                    "WHERE id != ?", (record.id,), max_total_bytes, "", (),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return out

    def _delete_ids(self, ids: list[str]) -> int:
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        return self._conn.execute(f"DELETE FROM snapshots WHERE id IN ({marks})", ids).rowcount

    def _trim_bytes(
        self, candidates: str, cparams: tuple[Any, ...], budget: int,
        scope: str, sparams: tuple[Any, ...],
    ) -> int:
        """Delete the oldest rows matching *candidates* while the bytes in
        *scope* exceed *budget*. Caller holds the lock and the transaction."""
        total = int(self._conn.execute(
            f"SELECT COALESCE(SUM(nbytes), 0) FROM snapshots {scope}", sparams
        ).fetchone()[0] or 0)
        if total <= budget:
            return 0
        doomed: list[str] = []
        for sid, nbytes in self._conn.execute(
            f"SELECT id, nbytes FROM snapshots {candidates} ORDER BY seq", cparams
        ).fetchall():
            if total <= budget:
                break
            doomed.append(sid)
            total -= int(nbytes or 0)
        return self._delete_ids(doomed)

    def close(self) -> None:
        """Close the underlying connection."""
        with self._conn_lock:
            self._conn.close()


# ══════════════════════════════════════════════════════════════════════════
# SnapshotRecorder
# ══════════════════════════════════════════════════════════════════════════


class SnapshotRecorder:
    """Captures SupervisorState snapshots and reads them back.

    Every read and write goes to the database: one source of truth for every
    surface and every recorder object (TT-01). Read failures raise
    :class:`SnapshotStoreError`; a capture whose durable write fails returns
    None (TT-11).
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
        max_global_snapshots: int = DEFAULT_MAX_GLOBAL_SNAPSHOTS,
        db_path: str | Path | None = None,
        store: SnapshotStore | None = None,
        max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
        max_thread_bytes: int = DEFAULT_MAX_THREAD_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_per_turn: int = DEFAULT_MAX_PER_TURN,
    ) -> None:
        self._enabled = enabled
        self._max_snapshots = max(1, int(max_snapshots))
        self._max_global_snapshots = max(1, int(max_global_snapshots))
        self._max_snapshot_bytes = max(4096, int(max_snapshot_bytes))
        self._max_thread_bytes = max(self._max_snapshot_bytes, int(max_thread_bytes))
        self._max_total_bytes = max(self._max_thread_bytes, int(max_total_bytes))
        self._max_per_turn = max(2, int(max_per_turn))
        # H19: resolve once (data-dir anchored, cwd-independent).
        self._db_path = _resolve_db_path(db_path)
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

    @property
    def max_snapshots(self) -> int:
        """Snapshots kept per conversation (TT-08: Settings reads the live value)."""
        return self._max_snapshots

    @property
    def max_global_snapshots(self) -> int:
        """Snapshots kept in the whole database."""
        return self._max_global_snapshots

    @property
    def limits(self) -> dict[str, int]:
        """Every budget this recorder enforces (TT-06, TT-08)."""
        return {
            "max_snapshots": self._max_snapshots,
            "max_global_snapshots": self._max_global_snapshots,
            "max_snapshot_bytes": self._max_snapshot_bytes,
            "max_thread_bytes": self._max_thread_bytes,
            "max_total_bytes": self._max_total_bytes,
            "max_per_turn": self._max_per_turn,
            "capture_all_until": CAPTURE_ALL_UNTIL,
            "capture_every": CAPTURE_EVERY,
        }

    def _get_store(self, db_path: str | Path | None = None) -> SnapshotStore:
        """Lazy-init the SQLite store; :class:`SnapshotStoreError` when it
        cannot be opened (a corrupt file, a locked or read-only folder)."""
        if self._store is None:
            with self._store_lock:
                if self._store is None:
                    try:
                        self._store = SnapshotStore(db_path if db_path else self._db_path)
                    except (sqlite3.Error, OSError) as exc:
                        raise SnapshotStoreError(f"snapshot store unavailable: {exc}") from exc
        return self._store

    def capture(
        self,
        state: dict[str, Any],
        *,
        db_path: str | Path | None = None,
    ) -> SnapshotRecord | None:
        """Capture a snapshot of the current SupervisorState.

        Returns the stored record; None when time travel is off or the durable
        write failed. A failed write used to return the record anyway, so the
        turn stamped its id and the stream announced a step that was never
        stored (TT-11). The step's insert and its eviction are one write
        transaction (:meth:`SnapshotStore.enforce_bounds`, ``insert=True``):
        None means nothing was stored, and a crash leaves the store as it was
        or holding the step with every budget kept.
        """
        if not self._enabled:
            return None

        thread_id = str(state.get("thread_id") or "unknown")
        iteration = state.get("iteration", 0)
        messages = state.get("messages")
        state_json, truncated, original = _bounded_state_json(state, self._max_snapshot_bytes)
        turn_index = _user_turns(messages)
        record = SnapshotRecord(
            thread_id=thread_id,
            iteration=iteration,
            state_json=state_json,
            model_used=str(state.get("last_model") or ""),
            turn_key=str(state.get("created_at") or "") or f"turn-{turn_index}",
            turn_index=turn_index,
            message_count=len(messages) if isinstance(messages, list) else 0,
            nbytes=len(state_json),
            truncated=truncated,
        )
        try:
            store = self._get_store(db_path)
            evicted = store.enforce_bounds(
                record,
                insert=True,
                max_per_turn=self._max_per_turn,
                max_snapshots=self._max_snapshots,
                max_thread_bytes=self._max_thread_bytes,
                max_global_snapshots=self._max_global_snapshots,
                max_total_bytes=self._max_total_bytes,
            )
        except (SnapshotStoreError, sqlite3.Error, OSError) as exc:
            logger.warning(
                "[TimeTravel] snapshot NOT saved (thread=%s iter=%s): %s; no snapshot announced",
                thread_id[:12], iteration, exc,
            )
            _record_capture("failed")
            return None
        if truncated:
            logger.warning(
                "[TimeTravel] snapshot of thread=%s iter=%s was %d bytes, over the %d-byte limit: "
                "stored truncated (it can be read, not restored)",
                thread_id[:12], iteration, original, self._max_snapshot_bytes,
            )
        _record_capture("truncated" if truncated else "stored")
        if any(evicted.values()):
            logger.debug("[TimeTravel] evicted %s for thread=%s", evicted, thread_id[:12])
        return record

    def get(self, snapshot_id: str, *, thread_id: str | None = None) -> SnapshotRecord | None:
        """One snapshot by id (with its state); None when there is none, or
        it belongs to another thread than *thread_id*."""
        try:
            rec = self._get_store().get_by_id(snapshot_id)
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot store unreadable: {exc}") from exc
        if rec is None or (thread_id is not None and rec.thread_id != thread_id):
            return None
        return rec

    def resolve_ref(self, thread_id: str, ref: str) -> str | None:
        """The id of *thread_id*'s snapshot named by *ref*: the whole id, or
        its first characters (at least 6) when only one snapshot starts so.
        None when no snapshot (or more than one) matches."""
        ref = str(ref or "").strip().lower()
        if len(ref) < 6:
            return None
        try:
            ids = self._get_store().ids_with_prefix(thread_id, ref)
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot store unreadable: {exc}") from exc
        return ids[0] if len(ids) == 1 else None

    def list_snapshots(
        self,
        thread_id: str,
        *,
        db_path: str | Path | None = None,
    ) -> list[SnapshotRecord]:
        """A thread's snapshots, oldest first by capture order, without their
        states (``get`` reads one with its state)."""
        try:
            return self._get_store(db_path).list_for_thread(thread_id)
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot store unreadable: {exc}") from exc

    def clear_snapshots(
        self,
        thread_id: str,
        *,
        db_path: str | Path | None = None,
    ) -> int:
        """Delete every snapshot of a thread. Returns rows deleted."""
        return self.forget_threads([thread_id], db_path=db_path)

    def forget_threads(self, thread_ids: list[str], *, db_path: str | Path | None = None) -> int:
        """Delete every snapshot of these thread keys. Returns rows deleted."""
        try:
            return self._get_store(db_path).clear_threads(list(thread_ids))
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot store unwritable: {exc}") from exc

    def forget_turns(self, thread_ids: list[str], turn_index: int) -> int:
        """Delete the snapshots holding turn *turn_index* or later."""
        try:
            return self._get_store().delete_turns_from(list(thread_ids), turn_index)
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot store unwritable: {exc}") from exc

    def forget_after(self, thread_id: str, seq: int) -> int:
        """Delete *thread_id*'s snapshots captured after *seq*."""
        try:
            return self._get_store().delete_after(thread_id, seq)
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot store unwritable: {exc}") from exc

    def list_distinct_threads(self, *, db_path: str | Path | None = None) -> list[str]:
        """Return all distinct thread_ids that have at least one snapshot."""
        try:
            return self._get_store(db_path).list_distinct_threads()
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot store unreadable: {exc}") from exc

    def totals(self, thread_id: str | None = None) -> tuple[int, int]:
        """``(rows, bytes)`` of one thread or of the whole database."""
        try:
            return self._get_store().totals(thread_id)
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot store unreadable: {exc}") from exc

    def close(self) -> None:
        """Close the SQLite store if open."""
        if self._store is not None:
            self._store.close()
            self._store = None


# ══════════════════════════════════════════════════════════════════════════
# ReplayEngine
# ══════════════════════════════════════════════════════════════════════════


class ReplayEngine:
    """Loads a recorded snapshot by its id and compares two."""

    def __init__(self, recorder: SnapshotRecorder) -> None:
        self._recorder = recorder

    def replay_from(self, thread_id: str, snapshot_id: str) -> dict[str, Any] | None:
        """The state of *thread_id*'s snapshot *snapshot_id*, or None.

        Raises :class:`SnapshotStoreError` when the store cannot be read.
        """
        record = self._recorder.get(snapshot_id, thread_id=thread_id)
        if record is None:
            logger.info("[ReplayEngine] No snapshot %s on thread=%s", snapshot_id, thread_id)
            return None
        return record.get_state()

    @staticmethod
    def compare_replays(
        original: dict[str, Any],
        replayed: dict[str, Any],
    ) -> dict[str, Any]:
        """Diff two snapshot states: message count, iteration, model, cost,
        tool calls and the next routing step."""
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


#: The setting each budget reads (Settings writes the first; the rest are
#: operator overrides). Read when the recorder is built.
_LIMIT_KEYS = (
    ("max_snapshots", "time_travel.max_snapshots"),
    ("max_global_snapshots", "time_travel.max_global_snapshots"),
    ("max_snapshot_bytes", "time_travel.max_snapshot_bytes"),
    ("max_thread_bytes", "time_travel.max_thread_bytes"),
    ("max_total_bytes", "time_travel.max_total_bytes"),
    ("max_per_turn", "time_travel.max_per_turn"),
)


def create_recorder(
    *,
    config: dict[str, Any] | None = None,
    db_path: str | None = None,
    max_snapshots: int | None = None,
    max_global_snapshots: int | None = None,
    store: SnapshotStore | None = None,
) -> SnapshotRecorder:
    """Create a SnapshotRecorder from kazma.yaml config and the settings store.

    Resolution per budget: explicit argument > settings store > kazma.yaml >
    default. The recorder is built once per process at startup, so a saved
    change applies at the next restart (Settings says so, TT-08).
    """
    tt_cfg = (config or {}).get("time_travel", {}) or {}
    enabled = tt_cfg.get("enabled", True)
    explicit = {"max_snapshots": max_snapshots, "max_global_snapshots": max_global_snapshots}
    limits: dict[str, int] = {}
    stored: dict[str, Any] = {}
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        for name, key in _LIMIT_KEYS:
            stored[name] = cs.get(key, None)
    except Exception:  # noqa: BLE001 - never break startup over the override
        logger.debug("[TimeTravel] ConfigStore override unavailable; using yaml/default", exc_info=True)
    for name, _key in _LIMIT_KEYS:
        for source in (explicit.get(name), stored.get(name), tt_cfg.get(name)):
            if source is None:
                continue
            try:
                limits[name] = int(source)
                break
            except (TypeError, ValueError):
                logger.warning("[TimeTravel] %s=%r is not a number; ignored", name, source)
    _db = db_path or tt_cfg.get("db_path") or None
    return SnapshotRecorder(enabled=enabled, db_path=_db, store=store, **limits)


_process_lock = threading.Lock()
_process_recorder: SnapshotRecorder | None = None
#: True when process_recorder() built it (no server registered one).
_process_built_here = False


def set_process_recorder(recorder: SnapshotRecorder | None) -> None:
    """Make *recorder* the one every surface of this process uses (the server
    registers the recorder its graphs capture with)."""
    global _process_recorder, _process_built_here
    with _process_lock:
        _process_recorder = recorder
        _process_built_here = False


def process_recorder() -> SnapshotRecorder:
    """The process's recorder: the server's, else one built from the settings.

    The gateway built a fresh recorder per ``/replay`` and ``/fork`` command
    and the slash resolver kept a third (TT-01/TT-05). The database is the
    source of truth either way; one object keeps one connection. One built
    here follows the data dir (a test, a CLI that moved it): it is rebuilt
    when the default path changed.
    """
    global _process_recorder, _process_built_here
    with _process_lock:
        if (
            _process_recorder is not None
            and _process_built_here
            and _process_recorder.db_path != _resolve_db_path(None)
        ):
            _process_recorder.close()
            _process_recorder = None
        if _process_recorder is None:
            _process_recorder = create_recorder()
            _process_built_here = True
        return _process_recorder


def forget_thread_snapshots(thread_keys: list[str], *, recorder: SnapshotRecorder | None = None) -> int:
    """Delete every snapshot of a conversation, under every key it has
    (TT-05): chat delete, forgetting the chat, ``/replay clear``."""
    keys = [str(k) for k in dict.fromkeys(thread_keys or []) if k]
    if not keys:
        return 0
    rec = recorder or process_recorder()
    n = rec.forget_threads(keys)
    if n:
        logger.info("[TimeTravel] deleted %d snapshot(s) of %s", n, ",".join(k[:12] for k in keys))
    return n


def forget_turn_snapshots(
    thread_keys: list[str], turn_index: int, *, recorder: SnapshotRecorder | None = None,
) -> int:
    """Delete the snapshots holding a forgotten turn: every snapshot of the
    conversation from that turn on (TT-05; a snapshot holds the whole
    conversation up to its step)."""
    keys = [str(k) for k in dict.fromkeys(thread_keys or []) if k]
    if not keys or int(turn_index) < 1:
        return 0
    rec = recorder or process_recorder()
    n = rec.forget_turns(keys, int(turn_index))
    if n:
        logger.info("[TimeTravel] deleted %d snapshot(s) holding turn %d of %s",
                    n, int(turn_index), ",".join(k[:12] for k in keys))
    return n


# ══════════════════════════════════════════════════════════════════════════
# Capture hook (graph_builder) — one function, so a test graph runs the
# product's capture path
# ══════════════════════════════════════════════════════════════════════════


async def capture_after_supervisor(
    recorder: SnapshotRecorder | None,
    state: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Capture the state after a supervisor step and stamp the result.

    A snapshot is observability: it never fails the turn it observes (audit
    2026-09-22). The capture runs off the event loop (M35); a step
    :func:`_capture_due` skips, or a write that failed, stamps nothing.
    """
    if recorder is None or not recorder.enabled:
        return result
    merged = {**state, **result}
    if not _capture_due(merged.get("iteration", 0), merged.get("next_node")):
        _record_capture("skipped")
        return result
    try:
        record = await asyncio.to_thread(recorder.capture, merged)
    except Exception:
        logger.warning("[TimeTravel] snapshot capture failed; turn continues", exc_info=True)
        record = None
    if record is not None:
        result["snapshot_id"] = record.id
        result["snapshot_iteration"] = merged.get("iteration", 0)
    return result


# ══════════════════════════════════════════════════════════════════════════
# Maintenance — TTL prune + VACUUM (dashboard button + daily auto-loop)
# ══════════════════════════════════════════════════════════════════════════

#: VACUUM only when this share of the file is free pages ...
_VACUUM_FREE_RATIO = 0.25
#: ... and at least this many bytes would come back. On the live install the
#: daily VACUUM rewrote a 288 MB file to reclaim 0.0 % (KNOWN_GAPS), and a
#: capture during it waited the busy timeout and was lost (TT-06).
_VACUUM_MIN_FREE_BYTES = 8 * 1024 * 1024


def _free_pages(db: Path) -> tuple[int, int, int]:
    """``(page_size, page_count, freelist_count)`` of *db*."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        free = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        return size, count, free
    finally:
        conn.close()


def _disk_bytes(db: Path) -> int:
    """What the store holds on disk: the file and its WAL.

    In WAL mode a VACUUM writes the rewritten file into the WAL; the file is
    cut only at a checkpoint, and the WAL keeps its size until truncated.
    Counting the file alone, the TT-G3 soak saw maintenance report 27.5 MB
    reclaimed while the disk use went from 81.6 MB to 108.2 MB.

    The shared-memory index (``-shm``) is not counted: it is a fixed 32 KB a
    connection keeps, and a read-only reader leaves it behind after closing.
    Counted, a prune of a store that had none yet reported its disk use
    growing (8 KB to 40 KB) while the file itself shrank.
    """
    return sum(p.stat().st_size for p in (db, Path(f"{db}-wal")) if p.exists())


def _truncate_wal(conn: sqlite3.Connection) -> str:
    """Checkpoint the VACUUM's WAL into the file and truncate it to zero.

    A reader holding an older snapshot (the recorder between statements does
    not) keeps part of it; that is said, never hidden: the next checkpoint
    finishes it.
    """
    busy, wal_pages, moved = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if busy:
        return f"ok; WAL not truncated yet ({moved} of {wal_pages} pages checkpointed, a reader is busy)"
    return "ok"


def maintain_snapshots(
    db_path: str | Path | None = None,
    retention_days: int = 30,
) -> dict[str, Any]:
    """Prune snapshots older than ``retention_days``; VACUUM when worth it.

    SQLite never shrinks a file on DELETE; the freed pages are reclaimed by
    VACUUM, which rewrites the whole file -- so it runs only when the free
    pages are at least a quarter of the file and 8 MB. Safe to run while the
    server is live (WAL mode, short write lock).

    Returns:
        Stats dict: ``deleted``, ``size_before``, ``size_after``,
        ``reclaimed`` (bytes), ``retention_days``, and per-step status.
    """
    db = Path(_resolve_db_path(db_path))
    size_before = _disk_bytes(db)
    deleted = 0
    cutoff = (datetime.now(UTC) - timedelta(days=max(1, int(retention_days)))).isoformat()

    try:
        conn = sqlite3.connect(str(db))
        try:
            apply_sqlite_pragmas(conn)
            if db.exists() and db.stat().st_size > 0:
                cur = conn.execute("DELETE FROM snapshots WHERE timestamp < ?", (cutoff,))
                deleted = cur.rowcount
                # COMMIT. Python's sqlite3 opens an implicit transaction for a
                # DML statement, and close() without commit ROLLS IT BACK — so
                # this pruned 3,000+ rows, reported success, and undid it, on
                # every run, for weeks (2026-09-13/14). snapshots.db reached
                # 864 MB with its oldest row 53 days past a 30-day retention.
                conn.commit()
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

    vacuum_status = "skipped"
    try:
        page_size, page_count, free = _free_pages(db) if db.exists() else (0, 0, 0)
        free_bytes = page_size * free
        ratio = (free / page_count) if page_count else 0.0
        if free_bytes >= _VACUUM_MIN_FREE_BYTES and ratio >= _VACUUM_FREE_RATIO:
            conn = sqlite3.connect(str(db))
            try:
                apply_sqlite_pragmas(conn)
                conn.execute("VACUUM")
                vacuum_status = _truncate_wal(conn)
            finally:
                conn.close()
        else:
            vacuum_status = f"skipped: {ratio:.0%} free ({free_bytes} bytes)"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TimeTravel] snapshot VACUUM failed: %s", exc)
        vacuum_status = f"failed: {exc}"

    # Verify with the read that can fail the same way the write did. `rowcount`
    # is what the DELETE *intended*; it reported 3,079 rows removed while the
    # transaction was being rolled back underneath it. Count what is actually
    # left, and say so loudly when the prune did not stick.
    remaining_old = -1
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            remaining_old = conn.execute(
                "SELECT count(*) FROM snapshots WHERE timestamp < ?", (cutoff,)
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        logger.debug("[TimeTravel] prune verification skipped", exc_info=True)

    size_after = _disk_bytes(db)
    if remaining_old > 0:
        logger.error(
            "[TimeTravel] prune did NOT stick: %d rows older than %dd are still "
            "there after deleting %d. Snapshots will grow without bound.",
            remaining_old,
            int(retention_days),
            deleted,
        )
        try:
            from kazma_core.observability.ops_alerts import alert

            alert(
                "time_travel.prune_not_sticking",
                "Time Travel snapshots are not being pruned",
                f"{remaining_old} snapshot(s) older than {int(retention_days)} days are still in "
                "snapshots.db after the daily clean-up deleted them; the file will grow without bound.",
                severity="warn",
            )
        except Exception:  # noqa: BLE001 - the ERROR line above stands
            logger.debug("[TimeTravel] prune alert skipped", exc_info=True)
    logger.info(
        "[TimeTravel] maintenance: deleted=%d remaining_old=%d before=%d after=%d "
        "(retention=%dd, vacuum=%s)",
        deleted,
        remaining_old,
        size_before,
        size_after,
        int(retention_days),
        vacuum_status,
    )
    return {
        "deleted": deleted,
        "size_before": size_before,
        "size_after": size_after,
        "reclaimed": max(0, size_before - size_after),
        "retention_days": int(retention_days),
        "prune": "ok" if remaining_old <= 0 else f"did not stick: {remaining_old} old rows remain",
        "remaining_old": remaining_old,
        "vacuum": vacuum_status,
    }


# Platform ids stay out of graph state (AGENTS.md §2). Both rewind and fork
# strip them so a snapshot that captured a routing sub-dict cannot put
# chat_id back into the checkpoint.
_SNAPSHOT_PLATFORM_IDS = ("chat_id", "user_id", "message_id")


def checkpoint_payload_from_snapshot(
    state: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    """Copy a snapshot into the dict a restore and a fork both persist.

    Every field on the snapshot travels, including fields other than
    ``messages`` (scratchpad, counters, summaries). The destination
    ``thread_id`` replaces the id stored in the snapshot, so a rewind keeps
    the live thread and a fork does not keep the source id.
    """
    if not isinstance(state, dict):
        raise TypeError("snapshot state must be a dict")
    dest = str(thread_id or "").strip()
    if not dest:
        raise ValueError("thread_id is required")
    payload = dict(state)
    messages = payload.get("messages")
    if isinstance(messages, list):
        payload["messages"] = list(messages)
    scratchpad = payload.get("scratchpad")
    if isinstance(scratchpad, dict):
        payload["scratchpad"] = dict(scratchpad)
    payload["thread_id"] = dest
    raw_gw = payload.get("_gateway")
    gateway = dict(raw_gw) if isinstance(raw_gw, dict) else {}
    gateway["thread_id"] = dest
    for key in _SNAPSHOT_PLATFORM_IDS:
        gateway.pop(key, None)
    payload["_gateway"] = gateway
    payload.pop(TRUNCATION_MARK, None)
    return payload


def turn_boundary_payload(state: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """:func:`checkpoint_payload_from_snapshot`, made a quiescent state (TT-02).

    A snapshot is taken after a supervisor step, often with tool calls the
    supervisor had just decided on. Written back as it was, the restored
    thread was left runnable (``next == ('tool_worker',)``) with the
    snapshot's pending calls -- and an Approve on a still-live card ran one
    of them, a call the human had denied in the original timeline. Here:

    - nothing is pending or done, and routing ends the turn;
    - an assistant tool call with no result, and a result with no call, are
      dropped (``graph_helpers.sanitize_tool_chains``: strict providers reject
      both);
    - the scratchpad replaces the live one: ``SCRATCHPAD_CLEAR`` first, since
      the channel's merge reducer otherwise kept every later entry (TT-03);
    - no snapshot or iteration counters of the old timeline stay live.

    The writer adds ``as_node="respond"``, so the checkpoint has no next step.
    """
    from kazma_core.agent.graph_helpers import sanitize_tool_chains
    from kazma_core.agent.state import SCRATCHPAD_CLEAR

    payload = checkpoint_payload_from_snapshot(state, thread_id)
    messages = payload.get("messages")
    if isinstance(messages, list):
        payload["messages"] = sanitize_tool_chains([m for m in messages if isinstance(m, dict)])
    payload["tool_calls_pending"] = []
    payload["tool_calls_done"] = []
    payload["next_node"] = "end"
    scratchpad = payload.get("scratchpad") if isinstance(payload.get("scratchpad"), dict) else {}
    payload["scratchpad"] = {SCRATCHPAD_CLEAR: "1", **scratchpad}
    return payload


async def apply_snapshot_to_thread(
    graph: Any,
    state: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    """Write a snapshot onto ``thread_id`` as a quiescent, turn-boundary state.

    Called by ``kazma_ui.thread_rewind`` only (the gate in
    ``tests/test_time_travel_rewind.py``), which holds the thread's mutation
    lock and has refused a running turn or a pending approval. Callers pass
    the live thread for a restore and a new id for a fork.
    """
    payload = turn_boundary_payload(state, thread_id)
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    # The state being replaced: its later turns leave memory.
    before = await graph.aget_state(config)
    old_messages = list(((before.values if before is not None else None) or {}).get("messages") or [])
    nodes = getattr(graph, "nodes", None) or {}
    if "respond" in nodes:
        # As the turn's last node: the checkpoint has no next step.
        await graph.aupdate_state(config, payload, as_node="respond")
    else:
        await graph.aupdate_state(config, payload)
    # A rewind takes the turns after the restored point back from memory too
    # (Memory audit M09); a fork's new thread holds none.
    from kazma_core.memory.forget import rewind_memory

    await rewind_memory(thread_id, old_messages, list(payload.get("messages") or []),
                        tenant_id=str(payload.get("tenant_id") or state.get("tenant_id") or "default"))
    return payload


_MAINTENANCE_INTERVAL_HOURS = 24
_MAINTENANCE_FIRST_DELAY_SECONDS = 120


def live_maintenance_config() -> dict[str, Any]:
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
                cfg = await asyncio.to_thread(live_maintenance_config)
                if not cfg["auto_maintain"]:
                    logger.debug("[TimeTravel] auto-maintain disabled; skipping")
                else:
                    await asyncio.to_thread(maintain_snapshots, db_path, cfg["retention_days"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - loop must survive any failure
                logger.warning("[TimeTravel] maintenance loop iteration failed: %s", exc)
            await asyncio.sleep(max(1, interval_hours) * 3600)

    from kazma_core.background import spawn_background

    return spawn_background(
        _loop(), name="snapshot-maintenance", never_completes=True
    )
