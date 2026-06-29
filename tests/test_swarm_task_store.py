"""Tests for the swarm TaskStore — SQLite-backed persistence."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kazma_core.swarm.task import (
    SwarmTask,
    TaskResult,
    TaskStatus,
    TaskType,
    WorkerResult,
)
from kazma_core.swarm.task_store import TaskStore


@pytest.fixture
def store(tmp_path: Path) -> TaskStore:
    """Create a TaskStore with a temporary SQLite database."""
    db_path = str(tmp_path / "test_swarm_tasks.db")
    return TaskStore(db_path=db_path)


@pytest.fixture
def sample_task() -> SwarmTask:
    """Create a sample completed SwarmTask."""
    return SwarmTask(
        prompt="Fix the auth bug",
        id="task-abc123",
        type=TaskType.DISPATCH,
        context="auth module",
        workers=["worker-a"],
        status=TaskStatus.COMPLETED,
        result=TaskResult(
            task_id="task-abc123",
            status="success",
            worker_results=[
                WorkerResult(
                    worker="worker-a",
                    task_id="task-abc123",
                    status="success",
                    output="Fixed the auth bug by updating the token validation.",
                    tokens_used=150,
                    cost=0.003,
                    duration_seconds=2.5,
                )
            ],
            total_cost=0.003,
            total_tokens=150,
            duration_seconds=2.5,
        ),
        created_at="2026-06-29T10:00:00+00:00",
        started_at="2026-06-29T10:00:01+00:00",
        completed_at="2026-06-29T10:00:03+00:00",
        cost_estimate=0.003,
        metadata={"tracing": {"spans": []}},
    )


@pytest.fixture
def sample_consult_task() -> SwarmTask:
    """Create a sample completed consult SwarmTask."""
    return SwarmTask(
        prompt="What architecture for X?",
        id="task-consult-1",
        type=TaskType.CONSULT,
        workers=["worker-a", "worker-b"],
        status=TaskStatus.COMPLETED,
        result=TaskResult(
            task_id="task-consult-1",
            status="success",
            worker_results=[
                WorkerResult(
                    worker="worker-a",
                    task_id="task-consult-1",
                    status="success",
                    output="Use microservices.",
                    tokens_used=100,
                    cost=0.002,
                    duration_seconds=1.5,
                ),
                WorkerResult(
                    worker="worker-b",
                    task_id="task-consult-1",
                    status="success",
                    output="Use monolith first.",
                    tokens_used=120,
                    cost=0.0025,
                    duration_seconds=1.8,
                ),
            ],
            individual_opinions=[
                WorkerResult(
                    worker="worker-a",
                    task_id="task-consult-1",
                    status="success",
                    output="Use microservices.",
                    tokens_used=100,
                    cost=0.002,
                    duration_seconds=1.5,
                ),
                WorkerResult(
                    worker="worker-b",
                    task_id="task-consult-1",
                    status="success",
                    output="Use monolith first.",
                    tokens_used=120,
                    cost=0.0025,
                    duration_seconds=1.8,
                ),
            ],
            synthesized_output="Worker A suggests microservices while Worker B recommends monolith first. Consider starting monolith.",
            total_cost=0.0045,
            total_tokens=220,
            duration_seconds=3.3,
        ),
        created_at="2026-06-29T11:00:00+00:00",
        completed_at="2026-06-29T11:00:03+00:00",
    )


# ---------------------------------------------------------------
# VAL-PERSIST-001: Completed tasks persisted to SQLite
# ---------------------------------------------------------------


class TestTaskPersistence:
    """Tests for basic task persistence (VAL-PERSIST-001)."""

    def test_store_creates_database(self, tmp_path: Path) -> None:
        """TaskStore creates the SQLite database file and tables on init."""
        db_path = str(tmp_path / "new_tasks.db")
        store = TaskStore(db_path=db_path)
        assert Path(db_path).exists()
        store.close()

    def test_store_persisted_task(self, store: TaskStore, sample_task: SwarmTask) -> None:
        """Terminal tasks are stored in swarm_tasks table with full TaskResult JSON."""
        store.persist_task(sample_task)

        conn = sqlite3.connect(store._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM swarm_tasks WHERE id = ?", (sample_task.id,)
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["id"] == "task-abc123"
        assert row["type"] == "dispatch"
        assert row["prompt"] == "Fix the auth bug"
        assert row["status"] == "completed"
        workers = json.loads(row["workers"])
        assert workers == ["worker-a"]
        result_data = json.loads(row["result"])
        assert result_data["status"] == "success"
        assert result_data["total_cost"] == 0.003

    def test_persist_updates_on_duplicate(self, store: TaskStore, sample_task: SwarmTask) -> None:
        """Persisting the same task id twice updates the existing row."""
        store.persist_task(sample_task)
        sample_task.status = TaskStatus.FAILED
        sample_task.result.status = "failed"
        store.persist_task(sample_task)

        conn = sqlite3.connect(store._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM swarm_tasks WHERE id = ?", (sample_task.id,)
        ).fetchone()
        conn.close()

        assert row["status"] == "failed"

    def test_get_task_by_id(self, store: TaskStore, sample_task: SwarmTask) -> None:
        """Retrieve a persisted task by its id."""
        store.persist_task(sample_task)
        retrieved = store.get_task("task-abc123")
        assert retrieved is not None
        assert retrieved.id == "task-abc123"
        assert retrieved.prompt == "Fix the auth bug"
        assert retrieved.result is not None
        assert retrieved.result.status == "success"

    def test_get_task_returns_none_for_unknown(self, store: TaskStore) -> None:
        """Unknown task id returns None."""
        assert store.get_task("nonexistent") is None


# ---------------------------------------------------------------
# VAL-PERSIST-002: Task history queryable with pagination
# ---------------------------------------------------------------


class TestTaskHistoryPagination:
    """Tests for paginated task history (VAL-PERSIST-002)."""

    def _populate(self, store: TaskStore, count: int) -> list[str]:
        """Insert N tasks into the store and return their ids."""
        ids = []
        for i in range(count):
            task = SwarmTask(
                prompt=f"Task {i}",
                id=f"task-{i:04d}",
                type=TaskType.DISPATCH,
                status=TaskStatus.COMPLETED,
                result=TaskResult(
                    task_id=f"task-{i:04d}",
                    status="success",
                    total_cost=float(i) * 0.001,
                    total_tokens=i * 10,
                    duration_seconds=float(i),
                ),
                created_at=f"2026-06-29T{10 + i % 14:02d}:00:00+00:00",
                completed_at=f"2026-06-29T{10 + i % 14:02d}:00:01+00:00",
            )
            store.persist_task(task)
            ids.append(task.id)
        return ids

    def test_list_tasks_default(self, store: TaskStore) -> None:
        """Default listing returns all tasks, most-recent-first."""
        ids = self._populate(store, 5)
        result = store.list_tasks()
        assert len(result) == 5

    def test_list_tasks_pagination(self, store: TaskStore) -> None:
        """Pagination returns correct pages with non-overlapping items."""
        self._populate(store, 10)
        page1 = store.list_tasks(page=1, page_size=3)
        page2 = store.list_tasks(page=2, page_size=3)
        page3 = store.list_tasks(page=3, page_size=3)

        assert len(page1) == 3
        assert len(page2) == 3
        assert len(page3) == 3
        # No overlap
        page1_ids = {t.id for t in page1}
        page2_ids = {t.id for t in page2}
        page3_ids = {t.id for t in page3}
        assert page1_ids.isdisjoint(page2_ids)
        assert page2_ids.isdisjoint(page3_ids)

    def test_list_tasks_pagination_last_page(self, store: TaskStore) -> None:
        """Last page may have fewer items than page_size."""
        self._populate(store, 7)
        page3 = store.list_tasks(page=3, page_size=3)
        assert len(page3) == 1

    def test_list_tasks_total_count(self, store: TaskStore) -> None:
        """list_tasks returns total count alongside results."""
        self._populate(store, 5)
        tasks, total = store.list_tasks(page=1, page_size=3, include_count=True)
        assert total == 5
        assert len(tasks) == 3


# ---------------------------------------------------------------
# VAL-PERSIST-003: Task history filterable by status, type, worker
# ---------------------------------------------------------------


class TestTaskHistoryFiltering:
    """Tests for task history filtering (VAL-PERSIST-003)."""

    def _populate_mixed(self, store: TaskStore) -> None:
        """Insert a mix of tasks for filtering tests."""
        tasks = [
            SwarmTask(
                prompt="Dispatch task",
                id="task-d1",
                type=TaskType.DISPATCH,
                status=TaskStatus.COMPLETED,
                workers=["worker-a"],
                result=TaskResult(task_id="task-d1", status="success"),
                completed_at="2026-06-29T10:00:00+00:00",
            ),
            SwarmTask(
                prompt="Consult task",
                id="task-c1",
                type=TaskType.CONSULT,
                status=TaskStatus.COMPLETED,
                workers=["worker-a", "worker-b"],
                result=TaskResult(task_id="task-c1", status="success"),
                completed_at="2026-06-29T11:00:00+00:00",
            ),
            SwarmTask(
                prompt="Failed dispatch",
                id="task-d2",
                type=TaskType.DISPATCH,
                status=TaskStatus.FAILED,
                workers=["worker-b"],
                result=TaskResult(task_id="task-d2", status="failed"),
                completed_at="2026-06-29T12:00:00+00:00",
            ),
            SwarmTask(
                prompt="Pipeline task",
                id="task-p1",
                type=TaskType.PIPELINE,
                status=TaskStatus.COMPLETED,
                workers=["worker-a", "worker-b"],
                result=TaskResult(task_id="task-p1", status="success"),
                completed_at="2026-06-29T13:00:00+00:00",
            ),
        ]
        for task in tasks:
            store.persist_task(task)

    def test_filter_by_status(self, store: TaskStore) -> None:
        """Filtering by status returns matching tasks."""
        self._populate_mixed(store)
        completed, _ = store.list_tasks(status="completed", include_count=True)
        failed, _ = store.list_tasks(status="failed", include_count=True)
        assert len(completed) == 3
        assert len(failed) == 1
        assert failed[0].id == "task-d2"

    def test_filter_by_type(self, store: TaskStore) -> None:
        """Filtering by type returns matching tasks."""
        self._populate_mixed(store)
        consult, _ = store.list_tasks(task_type="consult", include_count=True)
        pipeline, _ = store.list_tasks(task_type="pipeline", include_count=True)
        assert len(consult) == 1
        assert consult[0].id == "task-c1"
        assert len(pipeline) == 1
        assert pipeline[0].id == "task-p1"

    def test_filter_by_worker(self, store: TaskStore) -> None:
        """Filtering by worker returns tasks involving that worker."""
        self._populate_mixed(store)
        worker_a, _ = store.list_tasks(worker="worker-a", include_count=True)
        worker_b, _ = store.list_tasks(worker="worker-b", include_count=True)
        assert len(worker_a) == 3  # d1, c1, p1
        assert len(worker_b) == 3  # c1, d2, p1

    def test_combined_filters(self, store: TaskStore) -> None:
        """Filters are combinable (AND logic)."""
        self._populate_mixed(store)
        result, _ = store.list_tasks(
            status="completed", task_type="consult", include_count=True
        )
        assert len(result) == 1
        assert result[0].id == "task-c1"

    def test_empty_result_returns_valid_list(self, store: TaskStore) -> None:
        """No matches returns an empty list, not an error."""
        self._populate_mixed(store)
        result, total = store.list_tasks(
            status="nonexistent", include_count=True
        )
        assert result == []
        assert total == 0


# ---------------------------------------------------------------
# VAL-PERSIST-004: Task detail retrievable by id
# ---------------------------------------------------------------


class TestTaskDetail:
    """Tests for task detail retrieval (VAL-PERSIST-004)."""

    def test_get_task_returns_full_result(
        self, store: TaskStore, sample_task: SwarmTask
    ) -> None:
        """GET /api/swarm/tasks/{id} returns the full TaskResult."""
        store.persist_task(sample_task)
        retrieved = store.get_task("task-abc123")
        assert retrieved is not None
        assert retrieved.result is not None
        assert retrieved.result.worker_results[0].output == "Fixed the auth bug by updating the token validation."
        assert retrieved.result.total_cost == 0.003

    def test_get_task_unknown_returns_none(self, store: TaskStore) -> None:
        """Unknown id returns None."""
        assert store.get_task("nonexistent") is None


# ---------------------------------------------------------------
# VAL-PERSIST-005: Worker metrics aggregated daily
# ---------------------------------------------------------------


class TestWorkerMetrics:
    """Tests for daily worker metrics aggregation (VAL-PERSIST-005)."""

    def test_record_worker_metrics(self, store: TaskStore) -> None:
        """Recording a completed worker task updates daily metrics."""
        store.record_worker_metric(
            worker="worker-a",
            tasks_completed=1,
            tasks_failed=0,
            latency=2.5,
            tokens=150,
            cost=0.003,
        )
        metrics = store.get_worker_metrics("worker-a")
        assert len(metrics) == 1
        assert metrics[0]["tasks_completed"] == 1
        assert metrics[0]["tasks_failed"] == 0
        assert metrics[0]["total_tokens"] == 150

    def test_metrics_accumulate_same_day(self, store: TaskStore) -> None:
        """Multiple tasks on the same day accumulate counts."""
        store.record_worker_metric(
            worker="worker-a",
            tasks_completed=1,
            tasks_failed=0,
            latency=2.0,
            tokens=100,
            cost=0.002,
        )
        store.record_worker_metric(
            worker="worker-a",
            tasks_completed=1,
            tasks_failed=0,
            latency=3.0,
            tokens=200,
            cost=0.004,
        )
        metrics = store.get_worker_metrics("worker-a")
        assert len(metrics) == 1  # Same day, one row
        assert metrics[0]["tasks_completed"] == 2
        assert metrics[0]["total_tokens"] == 300
        assert metrics[0]["total_cost"] == pytest.approx(0.006)
        # avg_latency should be weighted average
        assert metrics[0]["avg_latency"] == pytest.approx(2.5)

    def test_metrics_separate_workers(self, store: TaskStore) -> None:
        """Metrics for different workers are tracked independently."""
        store.record_worker_metric(worker="worker-a", tasks_completed=1)
        store.record_worker_metric(worker="worker-b", tasks_completed=1)
        a_metrics = store.get_worker_metrics("worker-a")
        b_metrics = store.get_worker_metrics("worker-b")
        assert len(a_metrics) == 1
        assert len(b_metrics) == 1

    def test_get_all_worker_metrics(self, store: TaskStore) -> None:
        """get_all_worker_metrics returns metrics for all workers."""
        store.record_worker_metric(worker="worker-a", tasks_completed=1)
        store.record_worker_metric(worker="worker-b", tasks_completed=2)
        all_metrics = store.get_all_worker_metrics()
        assert len(all_metrics) == 2


# ---------------------------------------------------------------
# VAL-PERSIST-006: Task history survives restart
# ---------------------------------------------------------------


class TestPersistenceAcrossRestart:
    """Tests for persistence across restart (VAL-PERSIST-006)."""

    def test_tasks_survive_restart(self, tmp_path: Path, sample_task: SwarmTask) -> None:
        """Tasks persisted before restart are still queryable after restart."""
        db_path = str(tmp_path / "restart_test.db")

        # First "session" — persist a task.
        store1 = TaskStore(db_path=db_path)
        store1.persist_task(sample_task)
        store1.close()

        # Second "session" — new TaskStore, same db path.
        store2 = TaskStore(db_path=db_path)
        retrieved = store2.get_task("task-abc123")
        assert retrieved is not None
        assert retrieved.prompt == "Fix the auth bug"
        assert retrieved.result is not None
        assert retrieved.result.status == "success"
        store2.close()


# ---------------------------------------------------------------
# VAL-PERSIST-007: Consult results stored with opinions and synthesis
# ---------------------------------------------------------------


class TestConsultPersistence:
    """Tests for consult result persistence (VAL-PERSIST-007)."""

    def test_consult_result_persists_opinions_and_synthesis(
        self, store: TaskStore, sample_consult_task: SwarmTask
    ) -> None:
        """Persisted consult TaskResult includes both individual opinions and synthesized_output."""
        store.persist_task(sample_consult_task)

        # Raw SQLite query to verify both opinions and synthesis are in the result JSON.
        import sqlite3

        conn = sqlite3.connect(store._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT result FROM swarm_tasks WHERE id = ?", ("task-consult-1",)
        ).fetchone()
        conn.close()

        assert row is not None
        result_data = json.loads(row["result"])
        assert len(result_data["individual_opinions"]) == 2
        assert result_data["synthesized_output"] is not None
        assert "Worker A" in result_data["synthesized_output"] or "microservices" in result_data["synthesized_output"]

    def test_consult_result_roundtrip(
        self, store: TaskStore, sample_consult_task: SwarmTask
    ) -> None:
        """Consult result survives roundtrip through TaskStore."""
        store.persist_task(sample_consult_task)
        retrieved = store.get_task("task-consult-1")
        assert retrieved is not None
        assert retrieved.result is not None
        assert len(retrieved.result.individual_opinions) == 2
        assert retrieved.result.synthesized_output is not None
        assert "monolith" in retrieved.result.synthesized_output.lower()


# ---------------------------------------------------------------
# HITL Checkpoint Persistence (VAL-HITL-007)
# ---------------------------------------------------------------


class TestHITLPersistence:
    """Tests for HITL checkpoint state persistence (VAL-HITL-007)."""

    def test_store_paused_task(self, store: TaskStore) -> None:
        """Paused pipeline state is persisted with status='paused'."""
        task = SwarmTask(
            prompt="Pipeline with checkpoint",
            id="task-hitl-1",
            type=TaskType.PIPELINE,
            status=TaskStatus.PAUSED,
            workers=["worker-a", "worker-b", "worker-c"],
            result=TaskResult(
                task_id="task-hitl-1",
                status="paused",
                worker_results=[
                    WorkerResult(
                        worker="worker-a",
                        task_id="task-hitl-1",
                        status="success",
                        output="Step 1 done.",
                        duration_seconds=1.0,
                    ),
                ],
            ),
            metadata={
                "hitl_checkpoint": {
                    "step": 1,
                    "worker": "worker-a",
                    "output_preview": "Step 1 done.",
                    "remaining_workers": ["worker-b", "worker-c"],
                },
                "paused_blackboard": {"key1": "value1"},
            },
        )
        store.persist_task(task)

        retrieved = store.get_task("task-hitl-1")
        assert retrieved is not None
        assert retrieved.status == TaskStatus.PAUSED

    def test_load_paused_tasks(self, store: TaskStore) -> None:
        """get_paused_tasks returns all tasks with status='paused'."""
        # Add a paused task.
        paused = SwarmTask(
            prompt="Paused pipeline",
            id="task-paused-1",
            type=TaskType.PIPELINE,
            status=TaskStatus.PAUSED,
            workers=["worker-a"],
            result=TaskResult(task_id="task-paused-1", status="paused"),
        )
        # Add a completed task.
        completed = SwarmTask(
            prompt="Completed task",
            id="task-done-1",
            type=TaskType.DISPATCH,
            status=TaskStatus.COMPLETED,
            workers=["worker-a"],
            result=TaskResult(task_id="task-done-1", status="success"),
        )
        store.persist_task(paused)
        store.persist_task(completed)

        paused_tasks = store.get_paused_tasks()
        assert len(paused_tasks) == 1
        assert paused_tasks[0].id == "task-paused-1"

    def test_paused_task_preserves_metadata(self, store: TaskStore) -> None:
        """Paused task metadata (blackboard, remaining workers) is preserved."""
        paused = SwarmTask(
            prompt="Paused pipeline",
            id="task-paused-2",
            type=TaskType.PIPELINE,
            status=TaskStatus.PAUSED,
            workers=["worker-a", "worker-b"],
            result=TaskResult(task_id="task-paused-2", status="paused"),
            metadata={
                "hitl_checkpoint": {
                    "step": 1,
                    "worker": "worker-a",
                    "remaining_workers": ["worker-b"],
                },
                "paused_blackboard": {"shared_key": "shared_value"},
            },
        )
        store.persist_task(paused)

        retrieved = store.get_task("task-paused-2")
        assert retrieved is not None
        checkpoint = retrieved.metadata.get("hitl_checkpoint", {})
        assert checkpoint["step"] == 1
        assert checkpoint["remaining_workers"] == ["worker-b"]
        assert retrieved.metadata.get("paused_blackboard", {}).get("shared_key") == "shared_value"

    def test_paused_task_survives_restart(self, tmp_path: Path) -> None:
        """Paused task state survives database close and reopen."""
        db_path = str(tmp_path / "hitl_restart.db")

        store1 = TaskStore(db_path=db_path)
        paused = SwarmTask(
            prompt="Paused pipeline",
            id="task-hitl-restart",
            type=TaskType.PIPELINE,
            status=TaskStatus.PAUSED,
            workers=["worker-a"],
            result=TaskResult(task_id="task-hitl-restart", status="paused"),
            metadata={"hitl_checkpoint": {"step": 1}},
        )
        store1.persist_task(paused)
        store1.close()

        store2 = TaskStore(db_path=db_path)
        paused_tasks = store2.get_paused_tasks()
        assert len(paused_tasks) == 1
        assert paused_tasks[0].id == "task-hitl-restart"
        store2.close()
