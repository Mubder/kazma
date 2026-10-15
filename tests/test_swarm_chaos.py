"""Swarm chaos runs end honestly (Swarm tracker SQ04).

``scripts/swarm_chaos.py`` injects one fault at a time into a real
``SwarmEngine`` -- the task store's SQLite file locked at admission (for good,
and for a moment), at a resumed step's journal write, at the ending's write
(briefly and for good), at an approval; the shared breakers' settings store
down; Temporal configured with nothing there (and required), and Temporal
going away mid-run (on its time-skipping test server); a shutdown in the
middle of a pipeline, a fan-out and a consult; the Postgres task store down
at admission and at the ending (on a disposable server,
``@pytest.mark.postgres``) -- and records the ending. These run the same
cases with short waits (a 300 ms busy timeout, short write retries) and hold
each ending, with the counted ``kazma_swarm_failures_total`` sites.

Negative controls, each the code before 2026-10-15 put back:

* a new task whose RUNNING row the store would not take ran anyway, with no
  crash evidence (now refused, ``task_store_unavailable``);
* the admission's write without its retries refuses a task a lock held for
  a moment;
* the ending's write without its retries loses a lock that passes;
* a run whose ending was not stored, reported as the run said: the caller
  is told a success the store does not hold;
* an approval the store did not take, resumed anyway: the steps run on an
  approval a crash would offer again;
* required Temporal unreachable, answered without finalizing: the row stays
  RUNNING and the task holds its admission slot;
* a lost write left uncounted (the engine's counter call taken out);
* the honesty check itself, on a made-up ending of each dishonest shape.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
from pathlib import Path

import pytest

from tests._no_models import finish_turn_memory, keep_models_out


@pytest.fixture(autouse=True)
def _no_models(monkeypatch):
    """No real embedder or model behind this file's memory and swarm paths
    (tests/_no_models.py; tests/_egress_guard.py refuses the real ones)."""
    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()  # a turn's memory write ends inside its test


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import swarm_chaos  # noqa: E402

pytestmark = pytest.mark.timeout(240)

FAST = swarm_chaos.Timing(busy_ms=300, write_waits=(0.1, 0.2, 0.4), pg_pool_timeout=1.0)

#: The kazma_swarm_failures_total sites each case must count.
_COUNTED = {
    "sqlite_locked_admission": {"running_row"},
    "sqlite_locked_admission_briefly": set(),
    "sqlite_locked_step_journal": {"step_journal"},
    "sqlite_locked_finalize_briefly": {"worker_metric"},
    "sqlite_locked_finalize": {"task_write", "worker_metric"},
    "sqlite_locked_approval": {"running_row"},
    "breaker_store_down": {"breaker_read", "breaker_persist", "breaker_lease"},
    "temporal_unreachable": set(),
    "temporal_unreachable_required": set(),
    "temporal_lost_mid_run": set(),
    "shutdown_mid_patterns": set(),
    "postgres_down_at_admission": set(),
    "postgres_down_at_finalize": {"task_write"},
}
_LOCAL = sorted(set(swarm_chaos.CASES) - swarm_chaos.POSTGRES_CASES)


def test_every_case_declares_what_it_counts():
    assert set(_COUNTED) == set(swarm_chaos.CASES)


@pytest.mark.parametrize("name", _LOCAL)
async def test_each_fault_ends_honestly(name: str, tmp_path: Path) -> None:
    out = await swarm_chaos.run_case(name, tmp_path, timing=FAST)
    if out.skipped:
        pytest.skip(out.skipped)
    assert out.honest, (out.problems, out.returned, out.stored, out.recovered, out.notes)
    if out.counted is not None:  # None without prometheus-client
        assert _COUNTED[name] <= set(out.counted), (name, out.counted)


def _postgres_dsn() -> str | None:
    from kazma_core.db.backend import get_database_url, is_postgres

    return get_database_url() if is_postgres() else None


@pytest.mark.postgres
@pytest.mark.parametrize("name", sorted(swarm_chaos.POSTGRES_CASES))
async def test_the_postgres_store_going_down_ends_honestly(name: str, tmp_path: Path) -> None:
    """On a disposable server (the Postgres job's, or ``docker run --rm -p
    55433:5432 postgres:16-alpine``): a database of the case's own is created,
    taken down by refusing its connections and ending its sessions, and
    dropped."""
    dsn = _postgres_dsn()
    if not dsn:
        pytest.skip("needs KAZMA_DB_BACKEND=postgres and KAZMA_DATABASE_URL (a disposable server)")
    out = await swarm_chaos.run_case(name, tmp_path, timing=FAST, dsn=dsn)
    assert out.honest, (out.problems, out.returned, out.stored, out.recovered)
    if out.counted is not None:
        assert _COUNTED[name] <= set(out.counted), (name, out.counted)


# ── negative controls: the code before 2026-10-15 ───────────────────────────


async def test_without_retries_a_lock_that_passes_loses_the_ending(tmp_path: Path) -> None:
    timing = swarm_chaos.Timing(busy_ms=300, write_waits=())
    out = await swarm_chaos.run_case("sqlite_locked_finalize_briefly", tmp_path, timing=timing)
    assert not out.honest
    assert out.returned["status"] == "failed" and out.returned.get("unsaved") is True, out.returned


async def test_an_unsaved_ending_reported_as_run_is_caught(tmp_path: Path, monkeypatch) -> None:
    from kazma_core.swarm.engine import SwarmEngine

    monkeypatch.setattr(SwarmEngine, "_unsaved_ending", lambda self, task, result: result)
    out = await swarm_chaos.run_case("sqlite_locked_finalize", tmp_path, timing=FAST)
    assert "the caller was told success, the store holds running" in out.problems, out.problems


async def _run_anyway(self, task, *, must_record=False):
    """``_admit_to_store`` before 2026-10-15 for a new task: one attempt, and
    a write the store refused let the task run without its RUNNING row."""
    import asyncio

    try:
        await asyncio.to_thread(self._task_store.admit, task)
    except Exception:
        return None
    return None


async def test_a_task_run_without_its_running_row_is_caught(tmp_path: Path, monkeypatch) -> None:
    """Negative control for the refusal: the old run-anyway admission runs
    the task with no crash evidence, and the case says so."""
    from kazma_core.swarm.engine import SwarmEngine

    monkeypatch.setattr(SwarmEngine, "_admit_to_store", _run_anyway)
    out = await swarm_chaos.run_case("sqlite_locked_admission", tmp_path, timing=FAST)
    assert "the task ran without its RUNNING row" in out.problems, out.problems


async def test_without_retries_a_lock_that_passes_refuses_the_admission(tmp_path: Path) -> None:
    """Negative control for the admission's retries: one attempt refuses a
    task a lock held for a moment, which the retries run."""
    timing = swarm_chaos.Timing(busy_ms=300, write_waits=())
    out = await swarm_chaos.run_case("sqlite_locked_admission_briefly", tmp_path, timing=timing)
    assert not out.honest
    assert out.returned.get("code") == "task_store_unavailable", out.returned


async def test_an_unrecorded_approval_that_resumes_is_caught(tmp_path: Path, monkeypatch) -> None:
    from kazma_core.swarm.engine import SwarmEngine

    # The approval's write failing let it resume (it was admitted as a new
    # task's was, run anyway).
    monkeypatch.setattr(SwarmEngine, "_admit_to_store", _run_anyway)
    out = await swarm_chaos.run_case("sqlite_locked_approval", tmp_path, timing=FAST)
    assert any("nothing ran on the unrecorded approval" in p for p in out.problems), out.problems


async def test_required_temporal_answered_bare_is_caught(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("temporalio")
    import kazma_core.swarm.durable as durable
    from kazma_core.swarm.task import TaskResult

    def bare(engine, task, started, task_span, error):
        return TaskResult(task_id=task.id, status="failed", error=error)

    monkeypatch.setattr(durable, "_not_run", bare)
    out = await swarm_chaos.run_case("temporal_unreachable_required", tmp_path, timing=FAST)
    assert any("expected a stored failure" in p for p in out.problems), out.problems
    assert any("holds a slot" in p for p in out.problems), out.problems


async def test_a_lost_write_left_uncounted_is_caught(tmp_path: Path, monkeypatch) -> None:
    """Negative control for the counted sites: the engine's counter call
    taken out, the admission case no longer counts ``running_row``."""
    import kazma_core.swarm.engine as engine_module

    monkeypatch.setattr(engine_module, "record_swarm_failure", lambda site: None)
    out = await swarm_chaos.run_case("sqlite_locked_admission", tmp_path, timing=FAST)
    if out.counted is None:
        pytest.skip("prometheus-client is not installed: nothing is counted")
    assert not _COUNTED["sqlite_locked_admission"] <= set(out.counted), out.counted


def test_the_honesty_check_sees_each_dishonest_ending() -> None:
    def ending(told, held, held_result=None, recovered=None):
        out = swarm_chaos.Outcome("made-up", "", "")
        out.returned = {"status": told}
        out.stored = {"status": held, "result_status": held_result}
        out.recovered = recovered or {}
        return swarm_chaos._ending_problems(out)

    assert ending("success", "running")  # told success, never stored
    assert ending("success", "completed", "failed")  # stored another result
    assert ending("failed", "running", recovered={"status": "running"})  # left RUNNING
    assert ending("success", "completed", "success", {"status": "completed"}) == []
    assert ending("failed", "running", recovered={"status": "failed"}) == []


@pytest.mark.parametrize("unrecorded, status, code", [
    (True, 503, "approval_not_recorded"),
    ("temporal", 503, "temporal_unavailable"),
    (False, 409, "not_pending"),  # control: an ended task (a stale card) as before
])
def test_the_page_hears_an_unrecorded_approval(monkeypatch, unrecorded, status, code) -> None:
    """The approve route answers an approval the store did not take -- or one
    deferred because Temporal is required and down (SW-E16) -- with 503 (the
    checkpoint still pending, try again), never the stale card's 409."""
    from typing import Any

    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.swarm.task import TaskResult
    from kazma_ui.swarm_panel import routes_tasks

    class _Engine:
        def get_checkpoint_info(self, task_id: str) -> object:
            return object()

        async def approve_checkpoint(self, task_id: str, *, wait: bool = True) -> TaskResult:
            meta = ({"checkpoint_open": True, "code": "temporal_unavailable"} if unrecorded == "temporal"
                    else {"admission_unrecorded": True, "checkpoint_open": True} if unrecorded else {})
            return TaskResult(task_id=task_id, status="failed", error="not recorded", metadata=meta)

    class _Svc:
        def resolve_engine(self, m: Any = None) -> Any:
            return _Engine()

        def has_swarm_core(self) -> bool:
            return True

    monkeypatch.setattr(routes_tasks, "get_swarm_service", lambda: _Svc())
    router = APIRouter()
    routes_tasks.register_tasks_routes(router, templates=None)
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post("/api/swarm/tasks/t1/approve")
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    if unrecorded:
        assert response.json()["task_status"] == "paused" and response.headers.get("Retry-After")


def test_the_panel_answers_a_store_refusal_with_503(monkeypatch, tmp_path: Path) -> None:
    """A dispatch whose RUNNING row the store will not take: the route answers
    503 ``task_store_unavailable`` with Retry-After, nothing ran, no slot is
    held; a background dispatch ends its task with the same refusal (the page
    shows it from the stream); once the store is back the same request runs."""
    import sqlite3
    import time as _time
    from typing import Any

    import kazma_core.swarm.engine as engine_module
    import kazma_gateway.agent_handler as ah
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.swarm.config import SwarmConfig, WorkerConfig
    from kazma_core.swarm.engine import STORE_UNAVAILABLE, SwarmEngine
    from kazma_core.swarm.task_store import TaskStore
    from kazma_ui.swarm_panel import routes_tasks

    class _Locked(TaskStore):
        locked = True

        def admit(self, task):
            if self.locked:
                raise sqlite3.OperationalError("database is locked")
            return super().admit(task)

    monkeypatch.setattr(engine_module, "_WRITE_RETRY_WAITS", (0.01, 0.01))
    for name, value in (("KAZMA_SHARED_BREAKERS", "0"), ("KAZMA_TEMPORAL", "0"), ("KAZMA_GATE_REGISTRY", "0"),
                        ("KAZMA_SELF_IMPROVEMENT", "0")):
        monkeypatch.setenv(name, value)
    store = _Locked(db_path=str(tmp_path / "swarm_tasks.db"))
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    engine.add_worker(WorkerConfig(name="probe", type="in_process"))
    ran: list[str] = []

    async def probe(task: str, context: Any = None) -> dict:
        ran.append(task)
        return {"worker": "probe", "task_id": "", "status": "success", "output": "ok", "error": None}

    engine.get_worker("probe").dispatch = probe

    class _Svc:
        def resolve_engine(self, m: Any = None) -> Any:
            return engine

        def has_swarm_core(self) -> bool:
            return True

        def register_task_handle(self, *a: Any) -> None: ...

        def unregister_task_handle(self, *a: Any) -> None: ...

    async def _no_send(*a: Any, **k: Any) -> bool:
        return False

    monkeypatch.setattr(routes_tasks, "get_swarm_service", lambda: _Svc())
    monkeypatch.setattr(ah, "_maybe_send_to_output_target", _no_send)
    router = APIRouter()
    routes_tasks.register_tasks_routes(router, templates=None)
    app = FastAPI()
    app.include_router(router)
    try:
        with TestClient(app) as client:
            r = client.post("/api/swarm/dispatch", json={"workers": ["probe"], "task": "fore"})
            assert r.status_code == 503, r.text
            assert r.json()["code"] == STORE_UNAVAILABLE and r.headers.get("Retry-After")
            assert ran == [] and engine.list_active_tasks() == []
            assert store.get_task(r.json()["task_id"]) is None  # nothing stored, nothing to recover

            r = client.post("/api/swarm/dispatch", json={"workers": ["probe"], "task": "back", "background": True})
            assert r.status_code == 200 and r.json()["result_status"] == "running", r.text
            tid = r.json()["task_id"]
            until = _time.monotonic() + 10
            while (engine.get_task(tid) is None or engine.get_task(tid).result is None) and _time.monotonic() < until:
                _time.sleep(0.05)
            refused = engine.get_task(tid).result
            assert refused.metadata["code"] == STORE_UNAVAILABLE and ran == [], refused
            assert engine.list_active_tasks() == []

            store.locked = False
            r = client.post("/api/swarm/dispatch", json={"workers": ["probe"], "task": "fore"})
            assert r.status_code == 200 and r.json()["result_status"] == "success", r.text
            assert ran == ["fore"]
    finally:
        store.close()


async def test_a_refused_task_dispatched_again_runs(tmp_path: Path, monkeypatch) -> None:
    """The refusal is not a recorded ending: the same task dispatched again
    once the store is back runs (one run, stored completed)."""
    import sqlite3

    import kazma_core.swarm.engine as engine_module
    from kazma_core.swarm.config import SwarmConfig, WorkerConfig
    from kazma_core.swarm.engine import STORE_UNAVAILABLE, SwarmEngine
    from kazma_core.swarm.task import SwarmTask, TaskStatus
    from kazma_core.swarm.task_store import TaskStore

    class _Locked(TaskStore):
        locked = True

        def admit(self, task):
            if self.locked:
                raise sqlite3.OperationalError("database is locked")
            return super().admit(task)

    monkeypatch.setattr(engine_module, "_WRITE_RETRY_WAITS", (0.01,))
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    store = _Locked(db_path=str(tmp_path / "t.db"))
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    engine.add_worker(WorkerConfig(name="w", type="in_process"))
    runs: list[str] = []

    async def w(task: str, context=None) -> dict:
        runs.append(task)
        return {"worker": "w", "task_id": "", "status": "success", "output": "ok", "error": None}

    engine.get_worker("w").dispatch = w
    task = SwarmTask(prompt="p", workers=["w"], timeout=30)
    try:
        first = await engine.dispatch(task)
        assert first.metadata["code"] == STORE_UNAVAILABLE and runs == []
        store.locked = False
        second = await engine.dispatch(task)
        assert second.status == "success" and runs == ["p"]
        assert store.get_task(task.id).status == TaskStatus.COMPLETED
    finally:
        store.close()


# ── the class: an ending after admission is finalized ──────────────────────


def _bare_endings(source: str) -> list[int]:
    """Lines of ``run_via_durable`` returning a built ``TaskResult`` outside
    the submitted-then-lost branch (``if submission_attempted:``): once the
    task is admitted, every other ending goes through ``_not_run``."""
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_via_durable")
    allowed: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and ast.unparse(node.test) == "submission_attempted":
            allowed |= {n.lineno for n in ast.walk(node) if hasattr(n, "lineno")}
    return [
        node.lineno for node in ast.walk(fn)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call)
        and ast.unparse(node.value.func) == "TaskResult" and node.lineno not in allowed
    ]


def test_every_durable_ending_after_admission_is_finalized() -> None:
    import kazma_core.swarm.durable as durable

    assert _bare_endings(inspect.getsource(durable)) == []


def test_the_durable_gate_sees_a_bare_ending() -> None:
    """Negative control: the required-and-unreachable answer as it was."""
    planted = (
        "async def run_via_durable(engine, task, started, task_span):\n"
        "    if submission_attempted:\n"
        "        return TaskResult(task_id=task.id, status='failed', error='unknown')\n"
        "    if durable_required():\n"
        "        return TaskResult(task_id=task.id, status='failed', error='down')\n"
    )
    assert _bare_endings(planted) == [5]


def test_the_chaos_harness_runs_as_an_operator_starts_it(tmp_path: Path) -> None:
    """One case, in its own process, as the tracker's table was recorded."""
    import json
    import subprocess

    report = tmp_path / "chaos.json"
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "swarm_chaos.py"), "--case", "temporal_unreachable_required",
         "--data-dir", str(tmp_path / "data"), "--report", str(report)],
        capture_output=True, text=True, timeout=180, cwd=str(REPO),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    (outcome,) = data["outcomes"]
    if outcome["skipped"]:
        pytest.skip(outcome["skipped"])
    assert proc.returncode == 0 and data["status"] == "passed", (proc.stdout[-2000:], proc.stderr[-2000:])
    assert outcome["honest"] and outcome["stored"]["status"] == "failed"
    assert "| `temporal_unreachable_required` |" in proc.stdout
