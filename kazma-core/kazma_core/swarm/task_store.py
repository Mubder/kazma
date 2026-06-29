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
    """SQLite-backed persistence for swarm tasks and worker metrics.

    Thread-safe via a reentrant lock.  The database file is created
    automatically if it does not exist.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = str(db_path or _DEFAULT_DB)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Return (or create) the SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        with self._lock:
            conn = self._get_conn()
            conn.executescript(_SCHEMA)
            conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def clear(self) -> None:
        """Delete all rows from both tables. Useful for test isolation."""
        with self._lock:
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

        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT OR REPLACE INTO swarm_tasks
                   (id, type, prompt, status, workers, result,
                    created_at, started_at, completed_at, cost, tokens, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.id,
                    task.type.value if isinstance(task.type, TaskType) else str(task.type),
                    task.prompt,
                    task.status.value if isinstance(task.status, TaskStatus) else str(task.status),
                    json.dumps(task.workers, ensure_ascii=False),
                    result_json,
                    task.created_at,
                    task.started_at,
                    task.completed_at,
                    cost,
                    tokens,
                    json.dumps(task.metadata, ensure_ascii=False, default=str),
                ),
            )
            conn.commit()

        # Record per-worker metrics for terminal tasks.
        if task.result is not None and task.result.status in ("success", "partial"):
            for wr in task.result.worker_results:
                self.record_worker_metric(
                    worker=wr.worker,
                    tasks_completed=1 if wr.status == "success" else 0,
                    tasks_failed=1 if wr.status in ("error", "timeout") else 0,
                    latency=wr.duration_seconds,
                    tokens=wr.tokens_used,
                    cost=wr.cost,
                )
        elif task.result is not None and task.result.status in ("failed",):
            for wr in task.result.worker_results:
                self.record_worker_metric(
                    worker=wr.worker,
                    tasks_completed=0,
                    tasks_failed=1,
                    latency=wr.duration_seconds,
                    tokens=wr.tokens_used,
                    cost=wr.cost,
                )

    def get_task(self, task_id: str) -> SwarmTask | None:
        """Retrieve a persisted task by its id, or ``None``."""
        with self._lock:
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

        Returns:
            A list of :class:`SwarmTask` objects, or a tuple of
            ``(tasks, total_count)`` when *include_count* is ``True``.
        """
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        offset = (page - 1) * page_size

        conditions: list[str] = []
        params: list[Any] = []

        if status:
            conditions.append("status = ?")
            params.append(status)
        if task_type:
            conditions.append("type = ?")
            params.append(task_type)
        if worker:
            # Workers stored as JSON array — use LIKE for simple matching.
            conditions.append("workers LIKE ?")
            params.append(f'%"{worker}"%')

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        with self._lock:
            conn = self._get_conn()

            # Total count.
            total = 0
            if include_count:
                count_row = conn.execute(
                    f"SELECT COUNT(*) AS cnt FROM swarm_tasks {where_clause}",
                    params,
                ).fetchone()
                total = count_row["cnt"] if count_row else 0

            # Paginated results.
            rows = conn.execute(
                f"""SELECT * FROM swarm_tasks {where_clause}
                    ORDER BY completed_at DESC
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
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT * FROM swarm_tasks WHERE status = ? ORDER BY completed_at DESC",
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

                # Weighted average latency.
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
                        new_completed,
                        new_failed,
                        new_avg_latency,
                        prev_tokens + tokens,
                        prev_cost + cost,
                        worker,
                        metric_date,
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

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> SwarmTask:
        """Convert a SQLite row into a :class:`SwarmTask`."""
        result_data = json.loads(row["result"]) if row["result"] else None
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        workers = json.loads(row["workers"]) if row["workers"] else []

        return SwarmTask(
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
