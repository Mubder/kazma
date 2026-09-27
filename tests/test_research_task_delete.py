"""The research panel's Delete removes the task through the task store.

It ran its own SQL against the store's tables -- the Postgres table through
the pool, the SQLite one through ``_get_conn`` -- so the one route that deletes
a task was the one place outside the store that knew both schemas, and no
test ran it on either. ``TaskStore.delete_task`` holds the SQL now, tested on
both backends in ``tests/test_task_store_backends.py``.
"""

from __future__ import annotations

import ast
import inspect

from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.swarm.task import SwarmTask, TaskResult, TaskStatus, TaskType
from kazma_core.swarm.task_store import TaskStore


def _client(monkeypatch, store) -> TestClient:
    from kazma_ui.research_panel import routes

    monkeypatch.setattr(routes, "_get_store", lambda: store)
    app = FastAPI()
    app.include_router(routes.create_research_router())
    return TestClient(app)


def _task(store: TaskStore, task_id: str) -> None:
    store.persist_task(SwarmTask(
        prompt="research: x", id=task_id, type=TaskType.RESEARCH if hasattr(TaskType, "RESEARCH") else TaskType.DISPATCH,
        status=TaskStatus.COMPLETED, workers=["researcher"],
        result=TaskResult(task_id=task_id, status="success"),
    ))


def test_delete_removes_the_task_and_only_it(tmp_path, monkeypatch):
    store = TaskStore(db_path=str(tmp_path / "swarm_tasks.db"))
    try:
        _task(store, "task-doomed")
        _task(store, "task-kept")
        client = _client(monkeypatch, store)

        resp = client.delete("/api/research/tasks/task-doomed")

        assert resp.status_code == 200 and resp.json() == {"ok": True, "deleted": "task-doomed"}
        assert store.get_task("task-doomed") is None
        assert store.get_task("task-kept") is not None
        again = client.delete("/api/research/tasks/task-doomed")
        assert again.status_code == 200, "an id already gone is what the caller wanted"
    finally:
        store.close()


def test_no_store_is_a_503(monkeypatch):
    assert _client(monkeypatch, None).delete("/api/research/tasks/x").status_code == 503


def test_the_route_leaves_the_sql_to_the_store():
    """Negative control on the shape: the route names no table and opens no
    connection of its own."""
    from kazma_ui.research_panel import routes

    src = inspect.getsource(routes.create_research_router)
    tree = ast.parse(src.replace("\r\n", "\n").lstrip() if src.startswith(" ") else src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "delete_research")
    body = ast.unparse(fn)
    assert "delete_task" in body
    for leaked in ("swarm_tasks", "get_pool", "_get_conn"):
        assert leaked not in body, leaked
