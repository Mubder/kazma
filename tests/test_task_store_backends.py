"""TaskStore behaves the same on SQLite and Postgres where their SQL differs.

The two backends share an API and nothing else: the worker filter is
``json_each`` on SQLite and JSONB containment on Postgres, the metadata and
tenant filters are ``json_extract`` against ``@>``, and counts come back from
different drivers. ``tests/test_swarm_task_store.py`` pins the SQLite schema
itself; this file pins the behaviour, and the CI Postgres job runs it on a real
``kazma_swarm_tasks`` table. It found that ``prune_tasks`` reported 0 deletions
on Postgres whatever it deleted (2026-09-25).

The Postgres table is shared by the whole job, so every task and worker name is
unique and every assertion is about the test's own rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from kazma_core.swarm.task import SwarmTask, TaskResult, TaskStatus, TaskType
from kazma_core.swarm.task_store import TaskStore

# Verified against a real Postgres (throwaway postgres:16, twice); the CI
# Postgres job runs every test carrying this marker (scripts/postgres_suite.py).
pytestmark = pytest.mark.postgres


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


@pytest.fixture
def store(tmp_path):
    """A store over a fresh SQLite file locally, the shared table on Postgres.
    Whatever a test leaves paused or running is cancelled at teardown."""
    made: list[str] = []
    s = TaskStore(db_path=str(tmp_path / "swarm_tasks.db"))
    s.made = made  # type: ignore[attr-defined]
    yield s
    for task_id in made:
        task = s.get_task(task_id)
        if task is not None and task.status in (TaskStatus.PAUSED, TaskStatus.RUNNING, TaskStatus.PENDING):
            task.status = TaskStatus.CANCELLED
            s.persist_task(task)
    s.close()


def _put(store: TaskStore, *, worker: str, status: TaskStatus = TaskStatus.COMPLETED,
         task_type: TaskType = TaskType.DISPATCH, completed_at: str | None = None,
         metadata: dict | None = None, workers: list[str] | None = None) -> SwarmTask:
    task_id = f"task-{_uid()}"
    task = SwarmTask(
        prompt="p",
        id=task_id,
        type=task_type,
        status=status,
        workers=workers or [worker],
        completed_at=completed_at or _ago(0),
        metadata=dict(metadata or {}),
        result=TaskResult(task_id=task_id, status=str(status)),
    )
    store.persist_task(task)
    store.made.append(task_id)  # type: ignore[attr-defined]
    return task


def _ids(tasks) -> set[str]:
    return {t.id for t in tasks}


# ── filters ───────────────────────────────────────────────────────────


def test_the_worker_filter_matches_whole_names(store):
    """A name is an element of the list, not a substring of the JSON: a
    ``LIKE`` filter would also return the worker whose name starts the same."""
    w = f"alpha-{_uid()}"
    mine = _put(store, worker=w)
    longer = _put(store, worker=w + "x")
    shared = _put(store, worker="other", workers=["other", w])

    found = _ids(store.list_tasks(worker=w, page_size=100))

    assert found == {mine.id, shared.id}
    assert longer.id not in found


def test_status_type_and_worker_filters_combine(store):
    w = f"combo-{_uid()}"
    done = _put(store, worker=w, status=TaskStatus.COMPLETED, task_type=TaskType.PIPELINE)
    failed = _put(store, worker=w, status=TaskStatus.FAILED, task_type=TaskType.PIPELINE)
    consult = _put(store, worker=w, status=TaskStatus.COMPLETED, task_type=TaskType.CONSULT)

    assert _ids(store.list_tasks(worker=w, status="completed")) == {done.id, consult.id}
    assert _ids(store.list_tasks(worker=w, task_type="pipeline")) == {done.id, failed.id}
    assert _ids(store.list_tasks(worker=w, status="completed", task_type="pipeline")) == {done.id}
    assert store.list_tasks(worker=w, status="timeout") == []


def test_pages_and_the_total_count(store):
    w = f"pages-{_uid()}"
    made = [_put(store, worker=w, completed_at=_ago(5 - i)) for i in range(5)]
    newest_first = [t.id for t in reversed(made)]

    page1, total = store.list_tasks(worker=w, page=1, page_size=2, include_count=True)
    page3, total3 = store.list_tasks(worker=w, page=3, page_size=2, include_count=True)

    assert total == total3 == 5
    assert [t.id for t in page1] == newest_first[:2]
    assert [t.id for t in page3] == newest_first[4:]


def test_the_metadata_filter(store):
    w = f"meta-{_uid()}"
    kind = f"research-{_uid()}"
    wanted = _put(store, worker=w, metadata={"kind": kind, "extra": "x"})
    _put(store, worker=w, metadata={"kind": "other"})

    assert _ids(store.list_tasks(worker=w, metadata_filter={"kind": kind})) == {wanted.id}


def test_production_lists_only_the_callers_tenant(store, monkeypatch):
    """In production the store adds the caller's tenant to every listing;
    ``{"tenant_id": "*"}`` is the explicit opt-out."""
    from kazma_core.tenant_context import tenant_scope

    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.delenv("KAZMA_TENANT_FILTER", raising=False)
    w = f"tenant-{_uid()}"
    a, b = f"a-{_uid()}", f"b-{_uid()}"
    task_a = _put(store, worker=w, metadata={"tenant_id": a})
    task_b = _put(store, worker=w, metadata={"tenant_id": b})

    with tenant_scope(a):
        assert _ids(store.list_tasks(worker=w)) == {task_a.id}
        assert _ids(store.list_tasks(worker=w, metadata_filter={"tenant_id": "*"})) == {
            task_a.id, task_b.id,
        }
    with tenant_scope(b):
        assert _ids(store.list_tasks(worker=w)) == {task_b.id}


# ── metrics ───────────────────────────────────────────────────────────


def test_worker_metrics_accumulate_with_a_weighted_latency(store):
    w = f"metrics-{_uid()}"
    store.record_worker_metric(worker=w, tasks_completed=1, latency=2.0, tokens=10, cost=0.5,
                               date="2026-09-24")
    store.record_worker_metric(worker=w, tasks_completed=3, latency=6.0, tokens=30, cost=1.5,
                               date="2026-09-24")
    store.record_worker_metric(worker=w, tasks_failed=1, latency=1.0, date="2026-09-25")

    rows = store.get_worker_metrics(w)

    assert [str(r["date"]) for r in rows] == ["2026-09-25", "2026-09-24"]
    day = rows[1]
    assert (day["tasks_completed"], day["tasks_failed"], day["total_tokens"]) == (4, 0, 40)
    assert float(day["avg_latency"]) == pytest.approx(5.0)  # (2*1 + 6*3) / 4
    assert float(day["total_cost"]) == pytest.approx(2.0)


# ── maintenance ───────────────────────────────────────────────────────


def test_prune_reports_what_it_deleted(store):
    """On Postgres this returned 0 whatever it deleted: the pool returns rows
    only for a statement that produces them, and the DELETE had no RETURNING."""
    w = f"prune-{_uid()}"
    old_done = _put(store, worker=w, status=TaskStatus.COMPLETED, completed_at=_ago(100))
    recent_done = _put(store, worker=w, status=TaskStatus.COMPLETED, completed_at=_ago(1))
    old_paused = _put(store, worker=w, status=TaskStatus.PAUSED, completed_at=_ago(100))

    deleted = store.prune_tasks(retention_days=30)

    assert deleted >= 1, "prune deleted a row and reported none"
    assert store.get_task(old_done.id) is None
    assert store.get_task(recent_done.id) is not None
    assert store.get_task(old_paused.id) is not None, "only finished tasks are pruned"


def test_orphaned_running_tasks_are_requeued_then_failed(store):
    """A task left 'running' by a crash is requeued a bounded number of times."""
    orphan = _put(store, worker=f"orphan-{_uid()}", status=TaskStatus.RUNNING)

    for attempt in (1, 2):
        report = store.requeue_orphaned_running(max_recovery=2)
        assert orphan.id in report["requeued"]
        task = store.get_task(orphan.id)
        assert task.status == TaskStatus.PENDING
        assert task.metadata["recovery_count"] == attempt
        task.status = TaskStatus.RUNNING  # it crashed again
        store.persist_task(task)

    report = store.requeue_orphaned_running(max_recovery=2)
    assert orphan.id in report["failed"]
    assert store.get_task(orphan.id).status == TaskStatus.FAILED
