"""Unit tests for dual-backend SessionManager / TaskStore (SQLite path + backend flags).

Postgres integration is exercised when KAZMA_DATABASE_URL is set; otherwise
SQLite paths and backend selection are fully covered without a live DB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kazma_core.db.backend import DatabaseBackend, get_backend
from kazma_core.swarm.task import SwarmTask, TaskStatus, TaskType
from kazma_core.swarm.task_store import TaskStore


def test_backend_default_sqlite(monkeypatch):
    monkeypatch.delenv("KAZMA_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KAZMA_DB_BACKEND", raising=False)
    assert get_backend() == DatabaseBackend.SQLITE


def test_task_store_sqlite_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("KAZMA_DATABASE_URL", raising=False)
    monkeypatch.delenv("KAZMA_DB_BACKEND", raising=False)
    db = tmp_path / "swarm.db"
    store = TaskStore(db_path=str(db))
    assert store._pg is False
    task = SwarmTask(
        id="t-smoke-1",
        type=TaskType.DISPATCH,
        prompt="hello",
        status=TaskStatus.COMPLETED,
        workers=["w1"],
    )
    store.persist_task(task)
    got = store.get_task("t-smoke-1")
    assert got is not None
    assert got.prompt == "hello"
    assert got.id == "t-smoke-1"
    listed = store.list_tasks(page=1, page_size=10)
    assert any(t.id == "t-smoke-1" for t in listed)
    store.record_worker_metric(worker="w1", tasks_completed=1, latency=0.1)
    metrics = store.get_worker_metrics("w1")
    assert metrics
    store.clear()
    assert store.get_task("t-smoke-1") is None
    store.close()


def test_session_manager_sqlite_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("KAZMA_DATABASE_URL", raising=False)
    monkeypatch.delenv("KAZMA_DB_BACKEND", raising=False)
    from kazma_ui.session_manager import SessionManager

    db = tmp_path / "chat.db"
    sm = SessionManager(max_sessions=100, db_path=str(db))
    assert sm._pg is False
    s = sm.get_or_create("sess-1")
    s.messages.append({"role": "user", "content": "hi"})
    sm.put(s)
    loaded = sm.get("sess-1")
    assert loaded is not None
    assert loaded.messages[-1]["content"] == "hi"
    sm.delete("sess-1")
    assert sm.get("sess-1") is None
    if sm._conn:
        sm._conn.close()


def test_task_store_pg_flag(monkeypatch):
    monkeypatch.setenv("KAZMA_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.delenv("KAZMA_DB_BACKEND", raising=False)
    # Don't open real pool in unit test — only verify backend detection path
    from kazma_core.db.pg_helpers import use_postgres

    assert use_postgres() is True


@pytest.mark.skipif(
    not __import__("os").environ.get("KAZMA_DATABASE_URL", "").startswith("postgres"),
    reason="Live Postgres not configured (set KAZMA_DATABASE_URL to run)",
)
def test_task_store_postgres_live():
    store = TaskStore()
    assert store._pg is True
    tid = "t-pg-live-1"
    task = SwarmTask(
        id=tid,
        type=TaskType.DISPATCH,
        prompt="pg-live",
        status=TaskStatus.COMPLETED,
        workers=["pgw"],
    )
    store.persist_task(task)
    assert store.get_task(tid) is not None
    store.clear()
