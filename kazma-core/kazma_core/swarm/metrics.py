"""Per-worker metrics collection for the swarm engine.

Tracks token usage, cost, duration, and success/failure counts per
worker.  Aggregates into daily rows in the ``swarm_worker_metrics``
table (via :class:`~kazma_core.swarm.task_store.TaskStore`).

Queryable programmatically via :meth:`MetricsCollector.get_worker_metrics`
and :meth:`MetricsCollector.get_all_metrics`, and via the REST API at
``GET /api/swarm/workers/{name}/metrics``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kazma_core.swarm.task_store import TaskStore

logger = logging.getLogger(__name__)


def _utc_today() -> str:
    """Return the current UTC date as YYYY-MM-DD."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass
class WorkerMetricSnapshot:
    """Point-in-time snapshot of a single worker's metrics for one day."""

    worker: str
    date: str
    tasks_completed: int = 0
    tasks_failed: int = 0
    avg_latency: float = 0.0
    total_tokens: int = 0
    total_cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dict."""
        return {
            "worker": self.worker,
            "date": self.date,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "avg_latency": round(self.avg_latency, 4),
            "total_tokens": self.total_tokens,
            "total_cost": round(self.total_cost, 6),
        }


class MetricsCollector:
    """Collects and aggregates per-worker metrics for the swarm engine.

    Thread-safe in-memory accumulator backed by an optional
    :class:`~kazma_core.swarm.task_store.TaskStore` for persistent
    storage.  When a *task_store* is provided, metrics are flushed
    to SQLite on every record call.

    Usage::

        collector = MetricsCollector(task_store=store)
        collector.record(worker="analyst", tokens=150, cost=0.003,
                         duration=2.1, success=True)
        snapshot = collector.get_worker_metrics("analyst")
    """

    def __init__(self, task_store: TaskStore | None = None) -> None:
        self._task_store: TaskStore | None = task_store
        self._lock = threading.Lock()
        # In-memory accumulators keyed by (worker, date).
        self._metrics: dict[tuple[str, str], WorkerMetricSnapshot] = {}

    def record(
        self,
        *,
        worker: str,
        tokens: int = 0,
        cost: float = 0.0,
        duration: float = 0.0,
        success: bool = True,
        date: str | None = None,
    ) -> None:
        """Record a completed worker dispatch.

        Args:
            worker:   Worker name.
            tokens:   Token count consumed by this dispatch.
            cost:     Dollar cost of this dispatch.
            duration: Wall-clock seconds of the dispatch.
            success:  ``True`` if the dispatch succeeded.
            date:     Override date (defaults to today UTC).
        """
        metric_date = date or _utc_today()
        key = (worker, metric_date)

        with self._lock:
            snapshot = self._metrics.get(key)
            if snapshot is None:
                snapshot = WorkerMetricSnapshot(
                    worker=worker, date=metric_date
                )
                self._metrics[key] = snapshot

            prev_tasks = snapshot.tasks_completed + snapshot.tasks_failed
            if success:
                snapshot.tasks_completed += 1
            else:
                snapshot.tasks_failed += 1
            new_total = snapshot.tasks_completed + snapshot.tasks_failed

            # Weighted average latency.
            if new_total > 0:
                snapshot.avg_latency = (
                    (snapshot.avg_latency * prev_tasks) + duration
                ) / new_total

            snapshot.total_tokens += tokens
            snapshot.total_cost += cost

        # Flush to TaskStore if available.
        if self._task_store is not None:
            try:
                self._task_store.record_worker_metric(
                    worker=worker,
                    tasks_completed=1 if success else 0,
                    tasks_failed=0 if success else 1,
                    latency=duration,
                    tokens=tokens,
                    cost=cost,
                    date=metric_date,
                )
            except Exception:
                logger.exception(
                    "[MetricsCollector] failed to flush metrics for '%s'",
                    worker,
                )

    def record_worker_result(self, worker_result: Any) -> None:
        """Record metrics from a :class:`WorkerResult` object.

        Convenience method that extracts ``tokens_used``, ``cost``,
        ``duration_seconds``, and ``status`` from the result.
        """
        self.record(
            worker=worker_result.worker,
            tokens=getattr(worker_result, "tokens_used", 0),
            cost=getattr(worker_result, "cost", 0.0),
            duration=getattr(worker_result, "duration_seconds", 0.0),
            success=getattr(worker_result, "status", "") == "success",
        )

    def get_worker_metrics(
        self, worker: str, date: str | None = None
    ) -> WorkerMetricSnapshot | None:
        """Return the metric snapshot for *worker* on a given day.

        If *date* is ``None``, returns today's snapshot.  Returns
        ``None`` when no data exists for the worker/date pair.
        """
        metric_date = date or _utc_today()
        key = (worker, metric_date)
        with self._lock:
            return self._metrics.get(key)

    def get_worker_aggregate(self, worker: str) -> dict[str, Any]:
        """Return aggregated metrics for *worker* across all dates.

        Combines in-memory snapshots.  If a *task_store* is configured,
        the persisted data is used as the source of truth.
        """
        if self._task_store is not None:
            rows = self._task_store.get_worker_metrics(worker)
            if rows:
                total_completed = sum(r.get("tasks_completed", 0) for r in rows)
                total_failed = sum(r.get("tasks_failed", 0) for r in rows)
                total_tokens = sum(r.get("total_tokens", 0) for r in rows)
                total_cost = sum(r.get("total_cost", 0.0) for r in rows)
                avg_latency = 0.0
                total_tasks = total_completed + total_failed
                if total_tasks > 0:
                    latencies = [
                        r.get("avg_latency", 0.0)
                        * (r.get("tasks_completed", 0) + r.get("tasks_failed", 0))
                        for r in rows
                    ]
                    avg_latency = sum(latencies) / total_tasks
                return {
                    "worker": worker,
                    "tasks_completed": total_completed,
                    "tasks_failed": total_failed,
                    "avg_latency": round(avg_latency, 4),
                    "total_tokens": total_tokens,
                    "total_cost": round(total_cost, 6),
                }

        # Fallback to in-memory aggregation.
        with self._lock:
            snapshots = [
                s for (w, _d), s in self._metrics.items() if w == worker
            ]
        if not snapshots:
            return {
                "worker": worker,
                "tasks_completed": 0,
                "tasks_failed": 0,
                "avg_latency": 0.0,
                "total_tokens": 0,
                "total_cost": 0.0,
            }
        total_completed = sum(s.tasks_completed for s in snapshots)
        total_failed = sum(s.tasks_failed for s in snapshots)
        total_tasks = total_completed + total_failed
        total_tokens = sum(s.total_tokens for s in snapshots)
        total_cost = sum(s.total_cost for s in snapshots)
        avg_latency = 0.0
        if total_tasks > 0:
            avg_latency = sum(
                s.avg_latency * (s.tasks_completed + s.tasks_failed)
                for s in snapshots
            ) / total_tasks
        return {
            "worker": worker,
            "tasks_completed": total_completed,
            "tasks_failed": total_failed,
            "avg_latency": round(avg_latency, 4),
            "total_tokens": total_tokens,
            "total_cost": round(total_cost, 6),
        }

    def get_all_metrics(self) -> list[dict[str, Any]]:
        """Return aggregated metrics for all tracked workers.

        Uses the *task_store* when available, otherwise falls back to
        in-memory snapshots.
        """
        if self._task_store is not None:
            return self._task_store.get_all_worker_metrics()

        workers: set[str] = set()
        with self._lock:
            for w, _d in self._metrics:
                workers.add(w)
        return [self.get_worker_aggregate(w) for w in sorted(workers)]

    def get_task_totals(self, worker_results: list[Any]) -> dict[str, Any]:
        """Compute aggregate totals from a list of WorkerResult objects.

        Returns a dict with ``total_tokens``, ``total_cost``, and
        ``duration_seconds`` (sum of individual durations).
        """
        total_tokens = sum(
            getattr(r, "tokens_used", 0) for r in worker_results
        )
        total_cost = sum(
            getattr(r, "cost", 0.0) for r in worker_results
        )
        duration = sum(
            getattr(r, "duration_seconds", 0.0) for r in worker_results
        )
        return {
            "total_tokens": total_tokens,
            "total_cost": round(total_cost, 6),
            "duration_seconds": round(duration, 4),
        }

    def reset(self) -> None:
        """Clear all in-memory metrics (useful for tests)."""
        with self._lock:
            self._metrics.clear()
