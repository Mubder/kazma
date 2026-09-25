"""Swarm task history is pruned by a setting, on a cadence that actually runs.

``TaskStore.prune_tasks`` existed for months with nothing calling it, so
history was never pruned (2026-09-25). The owner chose a setting with a
30-day default: ``swarm.task_retention_days``, ``0`` keeps every task, applied
by the 15-minute maintenance sweep in ``memory.worker_bootstrap``.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from kazma_core.config_store import ConfigStore
from kazma_core.swarm.task import SwarmTask, TaskResult, TaskStatus, TaskType
from kazma_core.swarm.task_store import (
    TASK_RETENTION_KEY,
    TaskStore,
    parse_task_retention_days,
    prune_finished_tasks,
    task_retention_days,
)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """An isolated settings store, installed as the process-wide one."""
    import kazma_core.config_store as cs_mod

    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    monkeypatch.setattr(cs_mod, "get_config_store", lambda: store)
    return store


@pytest.fixture
def tasks(tmp_path):
    store = TaskStore(db_path=str(tmp_path / "swarm_tasks.db"))
    yield store
    store.close()


def _task(store: TaskStore, status: TaskStatus, days_ago: float) -> str:
    task_id = f"task-{uuid.uuid4().hex[:10]}"
    store.persist_task(SwarmTask(
        prompt="p", id=task_id, type=TaskType.DISPATCH, status=status, workers=["w"],
        completed_at=(datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),
        result=TaskResult(task_id=task_id, status=str(status)),
    ))
    return task_id


# ── the setting ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("raw", "days"), [
    (30, 30), ("30", 30), (" 7 ", 7), (0, 0), ("0", 0), (3650, 3650),
    (3651, None), (-1, None), ("a week", None), ("2.5", None), (True, None), (None, None),
])
def test_what_counts_as_a_retention(raw, days):
    assert parse_task_retention_days(raw) == days


def test_unset_means_thirty_days(settings):
    assert task_retention_days() == 30


def test_the_stored_value_is_read_live(settings):
    settings.set(TASK_RETENTION_KEY, 7, category="swarm")
    assert task_retention_days() == 7
    settings.set(TASK_RETENTION_KEY, 0, category="swarm")
    assert task_retention_days() == 0


def test_an_unreadable_value_falls_back_with_one_warning(settings, caplog):
    settings.set(TASK_RETENTION_KEY, "forever", category="swarm")
    with caplog.at_level(logging.WARNING, logger="kazma_core.swarm.task_store"):
        assert task_retention_days() == 30
        assert task_retention_days() == 30
    # Only the retention warning counts: the settings store logs its own INFO
    # "Setting updated: ... = 'forever'" for the write above, which reaches
    # caplog whenever an earlier test has lowered that logger's level.
    hits = [
        r.getMessage() for r in caplog.records
        if r.name == "kazma_core.swarm.task_store" and r.levelno >= logging.WARNING
    ]
    assert len(hits) == 1 and "forever" in hits[0], hits


# ── what it deletes ───────────────────────────────────────────────────


def test_finished_tasks_older_than_the_setting_are_deleted(settings, tasks):
    settings.set(TASK_RETENTION_KEY, 30, category="swarm")
    old = {s: _task(tasks, s, 45) for s in (
        TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.TIMEOUT)}
    recent = _task(tasks, TaskStatus.COMPLETED, 5)
    unfinished = {s: _task(tasks, s, 45) for s in (
        TaskStatus.PAUSED, TaskStatus.RUNNING, TaskStatus.PENDING)}

    assert prune_finished_tasks(tasks) == 4

    assert all(tasks.get_task(t) is None for t in old.values()), "timed-out tasks are finished too"
    assert tasks.get_task(recent) is not None
    assert all(tasks.get_task(t) is not None for t in unfinished.values())


def test_zero_keeps_every_task(settings, tasks):
    settings.set(TASK_RETENTION_KEY, 0, category="swarm")
    ancient = _task(tasks, TaskStatus.COMPLETED, 3000)

    assert prune_finished_tasks(tasks) == 0
    assert tasks.get_task(ancient) is not None


def test_the_running_engines_store_is_the_one_pruned(settings, tasks):
    from kazma_core.swarm import SwarmConfig
    from kazma_core.swarm.engine import SwarmEngine, set_swarm_engine

    settings.set(TASK_RETENTION_KEY, 30, category="swarm")
    old = _task(tasks, TaskStatus.COMPLETED, 45)
    set_swarm_engine(SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=tasks))
    try:
        assert prune_finished_tasks() == 1
    finally:
        set_swarm_engine(None)
    assert tasks.get_task(old) is None


# ── on a cadence that runs ────────────────────────────────────────────


def test_the_maintenance_cadence_carries_the_retention_sweep():
    from kazma_core.memory import worker_bootstrap as wb

    labels = [label for label, _ in wb._MAINTENANCE_SWEEPS]
    assert labels == [
        "commitment GC cycle", "artifact GC", "gate TTL sweep",
        "task queue purge", "swarm task retention",
    ], "a sweep left this list stops running; one added must be added here too"


def test_the_scheduler_runs_the_sweeps():
    """The list only matters if the loop the server starts runs it."""
    from kazma_core.memory import worker_bootstrap as wb

    tree = ast.parse(inspect.getsource(wb._start_commitment_gc_scheduler))
    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_run_maintenance_sweeps" in called
    assert "_start_commitment_gc_scheduler" in inspect.getsource(wb.start_memory_worker)


def test_a_failing_sweep_does_not_stop_the_retention_sweep(settings, tasks, monkeypatch):
    from kazma_core.memory import worker_bootstrap as wb
    from kazma_core.swarm.engine import set_swarm_engine
    from kazma_core.swarm import SwarmConfig
    from kazma_core.swarm.engine import SwarmEngine

    def _broken() -> None:
        raise RuntimeError("an earlier sweep broke")

    monkeypatch.setattr(wb, "_MAINTENANCE_SWEEPS", (
        ("broken", _broken), ("swarm task retention", wb._prune_swarm_tasks),
    ))
    settings.set(TASK_RETENTION_KEY, 30, category="swarm")
    old = _task(tasks, TaskStatus.FAILED, 45)
    set_swarm_engine(SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=tasks))
    try:
        asyncio.run(wb._run_maintenance_sweeps())
    finally:
        set_swarm_engine(None)
    assert tasks.get_task(old) is None


# ── the Settings API ──────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_ui.settings import create_settings_router

    cs = ConfigStore(db_path=str(tmp_path / "api.db"))
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(
        MagicMock(), cs, Jinja2Templates(directory=str(tmp_path / "templates"))))
    test_client = TestClient(app)
    test_client.kazma_config_store = cs
    return test_client


def test_the_api_shows_the_default(client):
    assert client.get("/api/settings/swarm/task-retention").json() == {
        "days": 30, "default": 30, "max": 3650,
    }


def test_the_api_saves_a_whole_number_of_days(client):
    ok = client.put("/api/settings/single", json={"key": TASK_RETENTION_KEY, "value": "45"})
    assert ok.status_code == 200
    assert client.kazma_config_store.get(TASK_RETENTION_KEY) == 45
    assert client.get("/api/settings/swarm/task-retention").json()["days"] == 45


@pytest.mark.parametrize("bad", ["forever", -1, 3651, "2.5"])
def test_the_api_refuses_what_the_sweep_could_not_read(client, bad):
    client.put("/api/settings/single", json={"key": TASK_RETENTION_KEY, "value": 45})

    resp = client.put("/api/settings/single", json={"key": TASK_RETENTION_KEY, "value": bad})

    assert resp.status_code == 400
    assert "0 (keep every task)" in resp.json()["detail"]
    assert client.kazma_config_store.get(TASK_RETENTION_KEY) == 45, "a refused save changed nothing"
