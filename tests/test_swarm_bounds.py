"""Bounded swarm inputs and storage (Swarm audit 2026-10-13: SW-S14, SW-E15).

* The Web dispatch refuses an out-of-range request with 422: a timeout of 0,
  negative, NaN or huge; oversize task, context or metadata; too many
  workers or checkpoints. The engine refuses the same from any other entry
  point before admission, and refuses a new pipeline with checkpoints past
  the paused-task cap.
* Task-history search runs in SQL, paged: it read one page of at most 100
  tasks and filtered it in Python, so an older match was never found.
* A stored result holds each large worker output once (the SW-E18 witness
  measured 4.51 stored bytes per output byte); reads see it whole.
* One global cap bounds parallel workers across every fan-out, consult and
  broadcast.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import SwarmTask, TaskType
from kazma_core.swarm.task_store import TaskStore


@pytest.fixture(autouse=True)
def _inert(monkeypatch: pytest.MonkeyPatch) -> None:
    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.worker_dispatch as wd

    async def _no_refiner(task, worker_results, status, total_ms):
        return "refined-stub"

    async def _none(*_a, **_kw):
        return None

    monkeypatch.setattr(patterns, "_synthesize_refined_output", _no_refiner)
    monkeypatch.setattr(patterns, "_run_self_improvement", _none)
    monkeypatch.setattr(wd, "_index_worker_l4_memory", _none)
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.delenv("KAZMA_SWARM_MAX_ACTIVE", raising=False)


def _ok(worker: str, output: str = "ok") -> dict:
    return {"worker": worker, "task_id": "", "status": "success", "output": output, "error": None}


def _client(monkeypatch, engine: SwarmEngine):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.swarm_panel import routes_tasks

    class _Svc:
        def resolve_engine(self, m: Any = None) -> Any:
            return engine

        def has_swarm_core(self) -> bool:
            return True

        def register_task_handle(self, *a: Any) -> None: ...

        def unregister_task_handle(self, *a: Any) -> None: ...

    monkeypatch.setattr(routes_tasks, "get_swarm_service", lambda: _Svc())
    router = APIRouter()
    routes_tasks.register_tasks_routes(router, templates=None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("change,code", [
    ({"timeout": 0}, "timeout_out_of_range"),
    ({"timeout": -5}, "timeout_out_of_range"),
    ({"timeout": "nan"}, "timeout_out_of_range"),
    ({"timeout": 1e9}, "timeout_out_of_range"),
    ({"timeout": "soon"}, "timeout_invalid"),
    ({"task": "x" * (64 * 1024 + 1)}, "prompt_too_large"),
    ({"context": "x" * (256 * 1024 + 1)}, "context_too_large"),
    ({"metadata": {"blob": "x" * (64 * 1024)}}, "metadata_too_large"),
    ({"workers": [f"w{i}" for i in range(33)]}, "too_many_workers"),
    ({"metadata": {"hitl_checkpoints": list(range(1, 34))}}, "too_many_checkpoints"),
])
def test_the_dispatch_route_refuses_out_of_range_input(monkeypatch, change: dict, code: str) -> None:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    engine.add_worker(WorkerConfig(name="w", type="in_process"))
    ran: list[int] = []

    async def w(task, context=None):
        ran.append(1)
        return _ok("w")

    engine.get_worker("w").dispatch = w
    payload = {"workers": ["w"], "task": "p", "pattern": "dispatch", "timeout": 30, **change}
    r = _client(monkeypatch, engine).post("/api/swarm/dispatch", json=payload)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == code
    assert ran == []


def test_an_in_range_request_is_accepted(monkeypatch) -> None:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    engine.add_worker(WorkerConfig(name="w", type="in_process"))

    async def w(task, context=None):
        return _ok("w")

    engine.get_worker("w").dispatch = w
    r = _client(monkeypatch, engine).post("/api/swarm/dispatch", json={
        "workers": ["w"], "task": "p", "pattern": "dispatch", "timeout": 3600})
    assert r.status_code == 200 and r.json()["result_status"] == "success"


async def test_the_engine_refuses_oversize_input_from_any_entry_point() -> None:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    engine.add_worker(WorkerConfig(name="w", type="in_process"))
    ran: list[int] = []

    async def w(task, context=None):
        ran.append(1)
        return _ok("w")

    engine.get_worker("w").dispatch = w
    result = await engine.dispatch(SwarmTask(prompt="x" * 70_000, workers=["w"], timeout=0))
    assert result.status == "failed" and result.metadata["code"] == "prompt_too_large"
    nan = await engine.dispatch(SwarmTask(prompt="p", workers=["w"], timeout=float("nan")))
    assert nan.metadata["code"] == "timeout_out_of_range"
    assert ran == []


async def test_new_checkpointed_pipelines_are_refused_past_the_paused_cap(monkeypatch) -> None:
    from kazma_core.swarm import limits

    monkeypatch.setattr(limits, "MAX_PAUSED_TASKS", 1)
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    for name in ("a", "b"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))
        engine.get_worker(name).dispatch = lambda task, context=None, n=name: asyncio.sleep(0, _ok(n))

    def pipeline() -> SwarmTask:
        return SwarmTask(prompt="p", workers=["a", "b"], type=TaskType.PIPELINE, timeout=0,
                         metadata={"hitl_checkpoints": [1]})

    assert (await engine.dispatch(pipeline())).status == "paused"
    refused = await engine.dispatch(pipeline())
    assert refused.metadata.get("code") == "too_many_paused"
    plain = await engine.dispatch(SwarmTask(prompt="p", workers=["a"], timeout=0))
    assert plain.status == "success", "a task with no checkpoint is not held by the cap"


def test_history_search_finds_an_older_match(monkeypatch, tmp_path: Path) -> None:
    store = TaskStore(db_path=str(tmp_path / "t.db"))
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    old = SwarmTask(prompt="find the needle in here", created_at="2020-01-01T00:00:00+00:00")
    old.completed_at = "2020-01-01T00:00:01+00:00"
    old.status = old.status.COMPLETED
    store.persist_task(old)
    for i in range(150):
        t = SwarmTask(prompt=f"newer task {i}")
        t.status = t.status.COMPLETED
        t.completed_at = f"2026-10-{1 + i % 28:02d}T00:00:00+00:00"
        store.persist_task(t)

    page = store.list_tasks(page=1, page_size=100)
    assert not any("needle" in t.prompt for t in page), "negative control: the old page-then-filter"

    r = _client(monkeypatch, engine).get("/api/swarm/tasks", params={"q": "needle"})
    body = r.json()
    assert [t["prompt"] for t in body["tasks"]] == ["find the needle in here"]
    assert body.get("total", body.get("count")) == 1
    pct = _client(monkeypatch, engine).get("/api/swarm/tasks", params={"q": "100%"}).json()
    assert pct["tasks"] == [], "a % in the query is a literal, not a wildcard"
    store.close()


async def test_each_output_is_stored_once_and_read_whole(tmp_path: Path) -> None:
    """The SW-E18 witness's storage half inverted."""
    db = tmp_path / "t.db"
    store = TaskStore(db_path=str(db))
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    big = "x" * 100_000
    for name in ("a", "b"):
        engine.add_worker(WorkerConfig(name=name, type="in_process"))
        engine.get_worker(name).dispatch = lambda task, context=None, n=name: asyncio.sleep(0, _ok(n, big))
    task = SwarmTask(prompt="p", workers=["a", "b"], type=TaskType.PIPELINE, timeout=0)
    result = await engine.dispatch(task)
    with contextlib.closing(sqlite3.connect(str(db))) as conn:
        stored = conn.execute("SELECT result FROM swarm_tasks WHERE id=?", (task.id,)).fetchone()[0]
    ratio = len(stored) / (2 * len(big))
    assert ratio < 1.2, f"stored {ratio:.2f} bytes per output byte"
    back = store.get_task(task.id).result
    assert back.aggregated_output == result.aggregated_output
    assert back.metadata == result.metadata, "every copy reads back whole"
    store.close()


async def test_one_cap_over_every_parallel_pattern(monkeypatch) -> None:
    from kazma_core.swarm import limits

    monkeypatch.setattr(limits, "MAX_PARALLEL_WORKERS", 2)
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    running = 0
    peak = 0

    async def slow(task, context=None):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return _ok("w")

    for i in range(6):
        engine.add_worker(WorkerConfig(name=f"w{i}", type="in_process"))
        engine.get_worker(f"w{i}").dispatch = slow
    first = SwarmTask(prompt="p", workers=["w0", "w1", "w2"], type=TaskType.FAN_OUT, timeout=0,
                      metadata={"max_concurrent": 3})
    second = SwarmTask(prompt="p", workers=["w3", "w4", "w5"], type=TaskType.FAN_OUT, timeout=0,
                       metadata={"max_concurrent": 5})
    await asyncio.gather(engine.dispatch(first), engine.dispatch(second))
    assert peak <= 2, peak
