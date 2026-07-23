"""SQLite-backed persistence for swarm tasks and worker metrics.

Provides the TaskStore class for persisting terminal tasks, querying
task history with pagination and filtering, and aggregating per-worker
daily metrics.  Also supports HITL checkpoint state persistence so that
paused pipelines survive server restarts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kazma_core.swarm.task import (
    SwarmTask,
    TaskResult,
    TaskStatus,
    TaskType,
)

from kazma_core.config_store import apply_sqlite_pragmas

__all__ = ["TaskStore"]

logger = logging.getLogger(__name__)

_DEFAULT_DB = "kazma-data/swarm_tasks.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS swarm_tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    workers TEXT NOT NULL DEFAULT '[]',
    result TEXT,
    context TEXT DEFAULT '',
    dependencies TEXT DEFAULT '[]',
    fallback_chain TEXT DEFAULT '[]',
    validation_schema TEXT DEFAULT '',
    aggregation TEXT DEFAULT '',
    timeout REAL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    cost REAL DEFAULT 0.0,
    tokens INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_swarm_tasks_status ON swarm_tasks(status);
CREATE INDEX IF NOT EXISTS idx_swarm_tasks_type ON swarm_tasks(type);
CREATE INDEX IF NOT EXISTS idx_swarm_tasks_completed_at ON swarm_tasks(completed_at);
CREATE INDEX IF NOT EXISTS idx_swarm_tasks_created_at ON swarm_tasks(created_at);

CREATE TABLE IF NOT EXISTS swarm_worker_metrics (
    worker TEXT NOT NULL,
    date TEXT NOT NULL,
    tasks_completed INTEGER DEFAULT 0,
    tasks_failed INTEGER DEFAULT 0,
    avg_latency REAL DEFAULT 0.0,
    total_tokens INTEGER DEFAULT 0,
    total_cost REAL DEFAULT 0.0,
    PRIMARY KEY (worker, date)
);
"""


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


def _utc_today() -> str:
    """Return the current UTC date as YYYY-MM-DD."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


class TaskStore:
    """Persistence for swarm tasks and worker metrics.

    Uses SQLite by default, or Postgres (``kazma_swarm_tasks``) when
    ``KAZMA_DATABASE_URL`` is configured.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = str(db_path or _DEFAULT_DB)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._pg = False
        try:
            from kazma_core.db.pg_helpers import use_postgres

            self._pg = use_postgres()
        except Exception:
            self._pg = False
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Return (or create) the SQLite connection."""
        if self._pg:
            raise RuntimeError("SQLite connection requested while Postgres backend active")
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            apply_sqlite_pragmas(self._conn)
        return self._conn

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        if self._pg:
            from kazma_core.db.pg_helpers import get_pool

            get_pool()
            # Extra columns for full task fidelity on Postgres
            pool = get_pool()
            ddl_extra = """
            ALTER TABLE kazma_swarm_tasks ADD COLUMN IF NOT EXISTS dependencies JSONB DEFAULT '[]'::jsonb;
            ALTER TABLE kazma_swarm_tasks ADD COLUMN IF NOT EXISTS fallback_chain JSONB DEFAULT '[]'::jsonb;
            ALTER TABLE kazma_swarm_tasks ADD COLUMN IF NOT EXISTS validation_schema TEXT DEFAULT '';
            ALTER TABLE kazma_swarm_tasks ADD COLUMN IF NOT EXISTS aggregation TEXT DEFAULT '';
            ALTER TABLE kazma_swarm_tasks ADD COLUMN IF NOT EXISTS timeout DOUBLE PRECISION;
            CREATE TABLE IF NOT EXISTS kazma_swarm_worker_metrics (
                worker TEXT NOT NULL,
                date TEXT NOT NULL,
                tasks_completed INTEGER DEFAULT 0,
                tasks_failed INTEGER DEFAULT 0,
                avg_latency DOUBLE PRECISION DEFAULT 0.0,
                total_tokens INTEGER DEFAULT 0,
                total_cost DOUBLE PRECISION DEFAULT 0.0,
                PRIMARY KEY (worker, date)
            );
            """
            try:
                with pool.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(ddl_extra)
                    conn.commit()
            except Exception as exc:
                logger.debug("[TaskStore] pg schema ensure: %s", exc)
            logger.info("[TaskStore] using Postgres backend")
            return
        with self._lock:
            conn = self._get_conn()
            conn.executescript(_SCHEMA)
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(swarm_tasks)").fetchall()}
            migrations = [
                ("context", "TEXT DEFAULT ''"),
                ("dependencies", "TEXT DEFAULT '[]'"),
                ("fallback_chain", "TEXT DEFAULT '[]'"),
                ("validation_schema", "TEXT DEFAULT ''"),
                ("aggregation", "TEXT DEFAULT ''"),
                ("timeout", "REAL"),
            ]
            for col_name, col_def in migrations:
                if col_name not in existing_cols:
                    try:
                        conn.execute(f"ALTER TABLE swarm_tasks ADD COLUMN {col_name} {col_def}")
                    except Exception:
                        pass
            conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def clear(self) -> None:
        """Delete all rows from both tables. Useful for test isolation."""
        with self._lock:
            if self._pg:
                from kazma_core.db.pg_helpers import get_pool

                get_pool().execute("DELETE FROM kazma_swarm_tasks")
                get_pool().execute("DELETE FROM kazma_swarm_worker_metrics")
                return
            conn = self._get_conn()
            conn.execute("DELETE FROM swarm_tasks")
            conn.execute("DELETE FROM swarm_worker_metrics")
            conn.commit()

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    def persist_task(self, task: SwarmTask) -> None:
        """Persist a swarm task (INSERT or REPLACE).

        Should be called when a task reaches a terminal state
        (completed, failed, timeout) or a paused HITL state.
        """
        result_json: str | None = None
        if task.result is not None:
            result_json = task.result.to_json()

        cost = 0.0
        tokens = 0
        if task.result is not None:
            cost = task.result.total_cost
            tokens = task.result.total_tokens

        type_s = task.type.value if isinstance(task.type, TaskType) else str(task.type)
        status_s = task.status.value if isinstance(task.status, TaskStatus) else str(task.status)
        workers_j = json.dumps(task.workers, ensure_ascii=False)
        deps_j = json.dumps(getattr(task, "dependencies", []), ensure_ascii=False)
        fb_j = json.dumps(getattr(task, "fallback_chain", []), ensure_ascii=False)
        vs = (
            json.dumps(getattr(task, "validation_schema", None), ensure_ascii=False)
            if getattr(task, "validation_schema", None)
            else ""
        )
        meta_j = json.dumps(task.metadata, ensure_ascii=False, default=str)

        with self._lock:
            if self._pg:
                from kazma_core.db.pg_helpers import get_pool

                get_pool().execute(
                    """
                    INSERT INTO kazma_swarm_tasks (
                        id, type, prompt, status, workers, result, context,
                        dependencies, fallback_chain, validation_schema, aggregation,
                        timeout, created_at, started_at, completed_at, cost, tokens, metadata
                    ) VALUES (
                        %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s,
                        %s::jsonb, %s::jsonb, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s::jsonb
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        type = EXCLUDED.type,
                        prompt = EXCLUDED.prompt,
                        status = EXCLUDED.status,
                        workers = EXCLUDED.workers,
                        result = EXCLUDED.result,
                        context = EXCLUDED.context,
                        dependencies = EXCLUDED.dependencies,
                        fallback_chain = EXCLUDED.fallback_chain,
                        validation_schema = EXCLUDED.validation_schema,
                        aggregation = EXCLUDED.aggregation,
                        timeout = EXCLUDED.timeout,
                        created_at = EXCLUDED.created_at,
                        started_at = EXCLUDED.started_at,
                        completed_at = EXCLUDED.completed_at,
                        cost = EXCLUDED.cost,
                        tokens = EXCLUDED.tokens,
                        metadata = EXCLUDED.metadata
                    """,
                    (
                        task.id, type_s, task.prompt, status_s, workers_j,
                        result_json or "null",
                        task.context or "",
                        deps_j, fb_j, vs,
                        getattr(task, "aggregation", "") or "",
                        getattr(task, "timeout", None),
                        task.created_at, task.started_at, task.completed_at,
                        cost, tokens, meta_j,
                    ),
                )
                return
            conn = self._get_conn()
            conn.execute(
                """INSERT OR REPLACE INTO swarm_tasks
                   (id, type, prompt, status, workers, result,
                    context, dependencies, fallback_chain,
                    validation_schema, aggregation, timeout,
                    created_at, started_at, completed_at, cost, tokens, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.id, type_s, task.prompt, status_s, workers_j, result_json,
                    task.context or "", deps_j, fb_j, vs,
                    getattr(task, "aggregation", "") or "",
                    getattr(task, "timeout", None),
                    task.created_at, task.started_at, task.completed_at,
                    cost, tokens, meta_j,
                ),
            )
            conn.commit()

    def get_task(self, task_id: str) -> SwarmTask | None:
        """Retrieve a persisted task by its id, or ``None``."""
        with self._lock:
            if self._pg:
                from kazma_core.db.pg_helpers import get_pool

                row = get_pool().execute_one(
                    "SELECT * FROM kazma_swarm_tasks WHERE id = %s", (task_id,)
                )
                if row is None:
                    return None
                return self._dict_to_task(row)
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM swarm_tasks WHERE id = ?", (task_id,)
            ).fetchone()

        if row is None:
            return None
        return self._row_to_task(row)

    def list_tasks(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        task_type: str | None = None,
        worker: str | None = None,
        include_count: bool = False,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[SwarmTask] | tuple[list[SwarmTask], int]:
        """Return persisted tasks with optional filtering and pagination.

        Results are sorted by ``completed_at`` descending (most recent first).

        Args:
            page: 1-based page number.
            page_size: Number of items per page.
            status: Filter by task status (e.g. ``"completed"``).
            task_type: Filter by task type (e.g. ``"consult"``).
            worker: Filter to tasks involving this worker name.
            include_count: If ``True``, return ``(tasks, total_count)``.
            metadata_filter: Filter by metadata key-value pairs (e.g.
                ``{"kind": "research"}``). Uses ``json_extract`` on SQLite
                and ``@>`` JSONB containment on Postgres.

        Returns:
            A list of :class:`SwarmTask` objects, or a tuple of
            ``(tasks, total_count)`` when *include_count* is ``True``.
        """
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        offset = (page - 1) * page_size

        with self._lock:
            if self._pg:
                from kazma_core.db.pg_helpers import get_pool

                conditions: list[str] = []
                params: list[Any] = []
                if status:
                    conditions.append("status = %s")
                    params.append(status)
                if task_type:
                    conditions.append("type = %s")
                    params.append(task_type)
                if worker:
                    conditions.append("workers @> %s::jsonb")
                    params.append(json.dumps([worker]))
                if metadata_filter:
                    conditions.append("metadata @> %s::jsonb")
                    params.append(json.dumps(metadata_filter))
                where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
                total = 0
                if include_count:
                    count_row = get_pool().execute_one(
                        f"SELECT COUNT(*) AS cnt FROM kazma_swarm_tasks {where_clause}",
                        params,
                    )
                    total = int(count_row["cnt"]) if count_row else 0
                rows = get_pool().execute(
                    f"""SELECT * FROM kazma_swarm_tasks {where_clause}
                        ORDER BY COALESCE(completed_at, created_at) DESC
                        LIMIT %s OFFSET %s""",
                    params + [page_size, offset],
                )
                tasks = [self._dict_to_task(r) for r in rows]
                if include_count:
                    return tasks, total
                return tasks

            conditions = []
            params = []
            if status:
                conditions.append("status = ?")
                params.append(status)
            if task_type:
                conditions.append("type = ?")
                params.append(task_type)
            if worker:
                conditions.append(
                    "EXISTS (SELECT 1 FROM json_each(workers) WHERE value = ?)"
                )
                params.append(worker)
            if metadata_filter:
                for mkey, mval in metadata_filter.items():
                    conditions.append("json_extract(metadata, ?) = ?")
                    params.append(f"$.{mkey}")
                    params.append(mval)

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            conn = self._get_conn()
            total = 0
            if include_count:
                count_row = conn.execute(
                    f"SELECT COUNT(*) AS cnt FROM swarm_tasks {where_clause}",
                    params,
                ).fetchone()
                total = count_row["cnt"] if count_row else 0
            rows = conn.execute(
                f"""SELECT * FROM swarm_tasks {where_clause}
                    ORDER BY COALESCE(completed_at, created_at) DESC
                    LIMIT ? OFFSET ?""",
                params + [page_size, offset],
            ).fetchall()

        tasks = [self._row_to_task(row) for row in rows]
        if include_count:
            return tasks, total
        return tasks

    def get_paused_tasks(self) -> list[SwarmTask]:
        """Return all tasks with status='paused' (for HITL restore on restart)."""
        with self._lock:
            if self._pg:
                from kazma_core.db.pg_helpers import get_pool

                rows = get_pool().execute(
                    "SELECT * FROM kazma_swarm_tasks WHERE status = %s ORDER BY created_at DESC",
                    ("paused",),
                )
                return [self._dict_to_task(r) for r in rows]
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT * FROM swarm_tasks WHERE status = ? ORDER BY created_at DESC",
                ("paused",),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    # ------------------------------------------------------------------
    # Worker metrics
    # ------------------------------------------------------------------

    def record_worker_metric(
        self,
        *,
        worker: str,
        tasks_completed: int = 0,
        tasks_failed: int = 0,
        latency: float = 0.0,
        tokens: int = 0,
        cost: float = 0.0,
        date: str | None = None,
    ) -> None:
        """Record (or accumulate) worker metrics for a given day.

        The average latency is computed as a weighted running average
        across all tasks recorded for the same ``(worker, date)`` pair.
        """
        metric_date = date or _utc_today()
        total_tasks = tasks_completed + tasks_failed

        with self._lock:
            if self._pg:
                from kazma_core.db.pg_helpers import get_pool

                existing = get_pool().execute_one(
                    """SELECT tasks_completed, tasks_failed, avg_latency,
                              total_tokens, total_cost
                       FROM kazma_swarm_worker_metrics
                       WHERE worker = %s AND date = %s""",
                    (worker, metric_date),
                )
                if existing:
                    prev_completed = int(existing["tasks_completed"] or 0)
                    prev_failed = int(existing["tasks_failed"] or 0)
                    prev_avg_latency = float(existing["avg_latency"] or 0)
                    prev_tokens = int(existing["total_tokens"] or 0)
                    prev_cost = float(existing["total_cost"] or 0)
                    new_completed = prev_completed + tasks_completed
                    new_failed = prev_failed + tasks_failed
                    prev_total = prev_completed + prev_failed
                    new_total = prev_total + total_tasks
                    new_avg = (
                        ((prev_avg_latency * prev_total) + (latency * total_tasks)) / new_total
                        if new_total > 0
                        else 0.0
                    )
                    get_pool().execute(
                        """UPDATE kazma_swarm_worker_metrics
                           SET tasks_completed = %s, tasks_failed = %s,
                               avg_latency = %s, total_tokens = %s, total_cost = %s
                           WHERE worker = %s AND date = %s""",
                        (
                            new_completed, new_failed, new_avg,
                            prev_tokens + tokens, prev_cost + cost,
                            worker, metric_date,
                        ),
                    )
                else:
                    get_pool().execute(
                        """INSERT INTO kazma_swarm_worker_metrics
                           (worker, date, tasks_completed, tasks_failed,
                            avg_latency, total_tokens, total_cost)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (
                            worker, metric_date, tasks_completed, tasks_failed,
                            latency if total_tasks > 0 else 0.0, tokens, cost,
                        ),
                    )
                return

            conn = self._get_conn()
            existing = conn.execute(
                """SELECT tasks_completed, tasks_failed, avg_latency,
                          total_tokens, total_cost
                   FROM swarm_worker_metrics
                   WHERE worker = ? AND date = ?""",
                (worker, metric_date),
            ).fetchone()

            if existing:
                prev_completed = existing["tasks_completed"]
                prev_failed = existing["tasks_failed"]
                prev_avg_latency = existing["avg_latency"]
                prev_tokens = existing["total_tokens"]
                prev_cost = existing["total_cost"]

                new_completed = prev_completed + tasks_completed
                new_failed = prev_failed + tasks_failed
                prev_total_tasks = prev_completed + prev_failed
                new_total_tasks = prev_total_tasks + total_tasks

                if new_total_tasks > 0:
                    new_avg_latency = (
                        (prev_avg_latency * prev_total_tasks) + (latency * total_tasks)
                    ) / new_total_tasks
                else:
                    new_avg_latency = 0.0

                conn.execute(
                    """UPDATE swarm_worker_metrics
                       SET tasks_completed = ?, tasks_failed = ?,
                           avg_latency = ?, total_tokens = ?, total_cost = ?
                       WHERE worker = ? AND date = ?""",
                    (
                        new_completed, new_failed, new_avg_latency,
                        prev_tokens + tokens, prev_cost + cost,
                        worker, metric_date,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO swarm_worker_metrics
                       (worker, date, tasks_completed, tasks_failed,
                        avg_latency, total_tokens, total_cost)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (worker, metric_date, tasks_completed, tasks_failed,
                     latency if total_tasks > 0 else 0.0, tokens, cost),
                )
            conn.commit()

    def get_worker_metrics(self, worker: str) -> list[dict[str, Any]]:
        """Return daily metrics for a given worker, newest first."""
        with self._lock:
            if self._pg:
                from kazma_core.db.pg_helpers import get_pool

                rows = get_pool().execute(
                    """SELECT worker, date, tasks_completed, tasks_failed,
                              avg_latency, total_tokens, total_cost
                       FROM kazma_swarm_worker_metrics
                       WHERE worker = %s
                       ORDER BY date DESC""",
                    (worker,),
                )
                return [dict(r) for r in rows]
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT worker, date, tasks_completed, tasks_failed,
                          avg_latency, total_tokens, total_cost
                   FROM swarm_worker_metrics
                   WHERE worker = ?
                   ORDER BY date DESC""",
                (worker,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_all_worker_metrics(self) -> list[dict[str, Any]]:
        """Return aggregated metrics for all workers."""
        with self._lock:
            if self._pg:
                from kazma_core.db.pg_helpers import get_pool

                rows = get_pool().execute(
                    """SELECT worker, SUM(tasks_completed) AS tasks_completed,
                              SUM(tasks_failed) AS tasks_failed,
                              AVG(avg_latency) AS avg_latency,
                              SUM(total_tokens) AS total_tokens,
                              SUM(total_cost) AS total_cost
                       FROM kazma_swarm_worker_metrics
                       GROUP BY worker
                       ORDER BY worker""",
                )
                return [dict(r) for r in rows]
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT worker, SUM(tasks_completed) AS tasks_completed,
                          SUM(tasks_failed) AS tasks_failed,
                          AVG(avg_latency) AS avg_latency,
                          SUM(total_tokens) AS total_tokens,
                          SUM(total_cost) AS total_cost
                   FROM swarm_worker_metrics
                   GROUP BY worker
                   ORDER BY worker""",
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _dict_to_task(cls, row: dict[str, Any]) -> SwarmTask:
        """Convert a Postgres dict row into a SwarmTask."""
        def _j(key: str, default: Any = None) -> Any:
            val = row.get(key)
            if val is None:
                return default
            if isinstance(val, (dict, list)):
                return val
            if isinstance(val, str) and val:
                try:
                    return json.loads(val)
                except Exception:
                    return default
            return default

        result_data = _j("result")
        metadata = _j("metadata") or {}
        workers = _j("workers") or []
        task = SwarmTask(
            id=row["id"],
            type=row["type"],
            prompt=row["prompt"],
            status=row["status"],
            workers=workers if isinstance(workers, list) else [],
            result=TaskResult.from_dict(result_data) if isinstance(result_data, dict) else None,
            created_at=row.get("created_at") or "",
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            cost_estimate=float(row.get("cost") or 0),
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        task.context = row.get("context") or ""
        task.dependencies = _j("dependencies", []) or []
        task.fallback_chain = _j("fallback_chain", []) or []
        task.validation_schema = _j("validation_schema", None)
        task.aggregation = row.get("aggregation") or ""
        if row.get("timeout") is not None:
            task.timeout = float(row["timeout"])
        return task

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> SwarmTask:
        """Convert a SQLite row into a :class:`SwarmTask`."""
        result_data = json.loads(row["result"]) if row["result"] else None
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        workers = json.loads(row["workers"]) if row["workers"] else []

        def _safe_json(key: str, default: Any) -> Any:
            try:
                val = row[key] if key in row.keys() else None
                return json.loads(val) if val else default
            except (json.JSONDecodeError, TypeError, KeyError, IndexError):
                return default

        task = SwarmTask(
            id=row["id"],
            type=row["type"],
            prompt=row["prompt"],
            status=row["status"],
            workers=workers,
            result=TaskResult.from_dict(result_data) if result_data else None,
            created_at=row["created_at"] or "",
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            cost_estimate=float(row["cost"] or 0),
            metadata=metadata,
        )
        task.context = row["context"] if "context" in row.keys() else ""
        if "dependencies" in row.keys():
            task.dependencies = _safe_json("dependencies", [])
        if "fallback_chain" in row.keys():
            task.fallback_chain = _safe_json("fallback_chain", [])
        if "validation_schema" in row.keys():
            task.validation_schema = _safe_json("validation_schema", None)
        if "aggregation" in row.keys():
            task.aggregation = row["aggregation"] or ""
        if "timeout" in row.keys() and row["timeout"] is not None:
            task.timeout = float(row["timeout"])
        return task
