"""A swarm task's row is written as its own space's (S4-01 step 0a, piece 4:
rows 24-27, 36, 47, 48, and the list's own faults).

A task ends by whoever ends it: the watchdog, a shutdown, an auto-reject armed
at a start, another person's dispatch that reaps it. Each wrote its row with
nothing bound, or bound to another space. And once Spaces is enforced the boot
recovery's list was refused, the owner's list left out the tasks of before
Spaces, and a run list inside a space was the newest forty of everyone's.

On SQLite here, and on a real Postgres in the CI Postgres job, where the table
is shared: every id is unique and every assertion is about the test's own.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.scope import NoSpaceBound, Scope, bind_scope
from kazma_core.swarm import SwarmConfig, SwarmTask, TaskStatus, TaskType
from kazma_core.swarm import engine as engine_module
from kazma_core.swarm.config import WorkerConfig
from kazma_core.swarm.engine import STORE_UNAVAILABLE, SwarmEngine
from kazma_core.swarm.task import TaskResult
from kazma_core.swarm.task_store import TaskStore

from tests._space_door import Calls, watched

pytestmark = pytest.mark.postgres

SPACE_REFUSED = "space_refused"


def _space() -> str:
    return f"s{uuid.uuid4().hex[:12]}"


@pytest.fixture
def a() -> str:
    return _space()


@pytest.fixture
def b() -> str:
    return _space()


@pytest.fixture
def enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture
def store(tmp_path: Any) -> Any:
    made = TaskStore(str(tmp_path / "tasks.db"))
    made.mine = []
    yield made
    for task_id in made.mine:
        made.delete_task(task_id)
    made.close()


def _task(store: Any, space: str | None, status: TaskStatus = TaskStatus.RUNNING, **more: Any) -> SwarmTask:
    metadata = dict(more.pop("metadata", {}))
    if space is not None:
        metadata["tenant_id"] = space
    task = SwarmTask(prompt="p", id=f"task-{uuid.uuid4().hex[:12]}", status=status, workers=["w"],
                     metadata=metadata, **more)
    store.mine.append(task.id)
    return task


def _engine(store: Any) -> SwarmEngine:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    engine.add_worker(WorkerConfig(name="w", type="in_process"))
    return engine


_WRITES = ((TaskStore, "persist_task"), (TaskStore, "admit"), (TaskStore, "record_orphan"), (TaskStore, "get_task"))


def _said(calls: Calls, *names: str) -> set[str]:
    return {c.space for c in calls if c.name in names}


# ── endings, whoever ends it (rows 24-26) ────────────────────────────────


def _stale(store: Any, space: str) -> SwarmTask:
    task = _task(store, space, timeout=1)
    task.started_at = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    assert store.persist_task(task)
    return task


def _reaped(store: Any, space: str, bound: Any) -> Calls:
    engine = _engine(store)
    task = _stale(store, space)
    engine._active_tasks[task.id] = task

    async def run() -> None:
        with bound:
            assert engine.reap_stale_tasks() == 1
        await engine._flush_writes(task.id)

    with watched(*_WRITES) as calls:
        asyncio.run(run())
    assert store.get_task(task.id).status == TaskStatus.TIMEOUT
    return calls


@pytest.mark.usefixtures("enforced")
@pytest.mark.parametrize("who", ["the watchdog", "another person's dispatch"])
def test_a_reaped_tasks_ending_is_its_own_spaces(store: Any, a: str, b: str, who: str) -> None:
    calls = _reaped(store, a, nullcontext() if who == "the watchdog" else bind_scope(b, "omar"))
    assert _said(calls, "persist_task") == {a}
    assert {c.scope for c in calls} <= {None, Scope(b, "omar")}, "nobody acts as anyone else"


def test_off_an_ending_binds_nothing(store: Any, a: str) -> None:
    assert {c.scope for c in _reaped(store, a, nullcontext())} == {None}


@pytest.mark.usefixtures("enforced")
def test_an_ending_of_a_run_that_did_not_stop_is_its_own_spaces(store: Any, a: str) -> None:
    engine = _engine(store)
    task = _stale(store, a)
    engine._active_tasks[task.id] = task
    engine._interrupts[task.id] = ("cancelled", "Stopped: Kazma shut down.", "shutdown")
    with watched(*_WRITES) as calls:
        engine._record_unstoppable(task.id)
    assert _said(calls, "persist_task") == {a}


@pytest.mark.usefixtures("enforced")
def test_an_auto_reject_armed_at_a_start_is_its_own_spaces(store: Any, a: str) -> None:
    task = _task(store, a, TaskStatus.PAUSED, type=TaskType.PIPELINE, metadata={
        "hitl_checkpoint": {"step": 1, "worker": "w", "output_preview": "draft"}, "checkpoint_timeout": 0.05})
    task.result = TaskResult(task_id=task.id, status="paused")
    assert store.persist_task(task)
    store.get_paused_tasks = lambda: [store.get_task(task.id)]  # this test's, in a shared table
    engine = _engine(store)
    engine.restore_paused_tasks()

    async def run() -> None:
        assert await engine.arm_pending_checkpoint_timeouts() >= 1
        for _ in range(100):
            await asyncio.sleep(0.05)
            await engine._flush_writes(task.id)
            if store.get_task(task.id).status == TaskStatus.FAILED:
                return
        raise AssertionError("never rejected")

    with watched(*_WRITES[:3]) as calls:
        asyncio.run(run())
    assert _said(calls, "persist_task", "record_orphan") == {a}


@pytest.mark.usefixtures("enforced")
def test_a_late_outcome_kept_as_evidence_is_its_own_spaces(store: Any, a: str, b: str) -> None:
    engine = _engine(store)
    task = _stale(store, a)
    task.status = TaskStatus.COMPLETED
    task.result = TaskResult(task_id=task.id, status="completed")
    assert store.persist_task(task)
    task.status = TaskStatus.FAILED  # the transition table refuses it
    with watched(*_WRITES) as calls, bind_scope(b, "omar"):
        engine._persist(task, "failed", "late")
    assert _said(calls, "persist_task", "record_orphan", "get_task") == {a}
    assert store.get_task(task.id).metadata["orphan_results"]


@pytest.mark.usefixtures("enforced")
def test_an_admission_and_a_journaled_step_are_the_tasks(store: Any, a: str) -> None:
    engine = _engine(store)
    task = _task(store, a, generation=1)

    async def run() -> None:
        assert await engine._admit_to_store(task) is None
        assert await engine._admit_to_store(task) is not None, "held already"
        await engine._journal_step(task, 1, "begun")

    with watched(*_WRITES) as calls:
        asyncio.run(run())
    assert _said(calls, "admit", "get_task", "persist_task") == {a}
    assert {c.name for c in calls} >= {"admit", "get_task", "persist_task"}


# ── a start's recovery (row 27, the recovery list) ───────────────────────


@pytest.mark.usefixtures("enforced")
def test_an_orphaned_task_is_recovered_as_its_own_spaces(store: Any, a: str, b: str) -> None:
    tasks = [_stale(store, space) for space in (a, b)]
    with watched((TaskStore, "persist_task")) as calls:
        recovered = store.requeue_orphaned_running()
    assert {t.id for t in tasks} <= set(recovered["failed"])
    ids = {t.id: t.metadata["tenant_id"] for t in tasks}
    mine = [(c.args[1].id, c.space) for c in calls if c.args[1].id in ids]
    assert dict(mine) == ids


@pytest.mark.usefixtures("enforced")
def test_the_start_holds_interrupted_rows_once_enforced(store: Any, a: str, b: str) -> None:
    """Its list was refused (no space bound): the start held nothing."""
    tasks = [_task(store, space, TaskStatus.PENDING, metadata={"recovery_count": 1}) for space in (a, b)]
    for task in tasks:
        assert store.persist_task(task)
    with watched((TaskStore, "list_tasks"), (TaskStore, "persist_task")) as calls:
        _engine(store).redispatch_recovered_tasks(max_tasks=100)
    for task in tasks:
        assert store.get_task(task.id).status == TaskStatus.FAILED, "held, with its outcome unknown"
    assert calls.of("list_tasks") == ["<install>"]
    ids = {t.id: t.metadata["tenant_id"] for t in tasks}
    assert {c.args[1].id: c.space for c in calls if c.name == "persist_task" and c.args[1].id in ids} == ids


def test_off_the_start_holds_them_as_before(store: Any, a: str) -> None:
    task = _task(store, a, TaskStatus.PENDING, metadata={"recovery_count": 1})
    assert store.persist_task(task)
    with watched((TaskStore, "list_tasks")) as calls:
        _engine(store).redispatch_recovered_tasks(max_tasks=100)
    assert store.get_task(task.id).status == TaskStatus.FAILED
    assert [c.kwargs.get("metadata_filter") for c in calls] == [None] and calls.scopes("list_tasks") == [None]


# ── the list ─────────────────────────────────────────────────────────────


def _listed(store: Any, kind: str, **more: Any) -> set[str]:
    tasks, total = store.list_tasks(page_size=100, include_count=True, metadata_filter={"kind": kind, **more})
    assert total == len(tasks)
    return {t.id for t in tasks}


def _before_and_after(store: Any, b: str) -> tuple[str, dict[str, SwarmTask]]:
    """Tasks of before Spaces (no tenant, an empty one), the owner's and B's."""
    kind = f"k{uuid.uuid4().hex[:8]}"
    made = {name: _task(store, space, TaskStatus.COMPLETED, metadata={"kind": kind})
            for name, space in (("none", None), ("empty", ""), ("owner", "default"), ("b", b))}
    for task in made.values():
        assert store.persist_task(task)
    return kind, made


@pytest.mark.usefixtures("enforced")
def test_the_owners_list_has_the_tasks_of_before_spaces(store: Any, b: str) -> None:
    from kazma_core.swarm.single_operator import task_of_this_space

    kind, made = _before_and_after(store, b)
    with bind_scope("default", "owner"):
        assert _listed(store, kind) == {made[n].id for n in ("none", "empty", "owner")}
        assert [n for n, t in made.items() if task_of_this_space(t)] == ["none", "empty", "owner"]
    with bind_scope(b, "omar"):
        assert _listed(store, kind) == {made["b"].id}
        assert [n for n, t in made.items() if task_of_this_space(t)] == ["b"]
    with pytest.raises(NoSpaceBound):
        _listed(store, kind)


def test_off_the_list_is_every_tasks_as_before(store: Any, b: str) -> None:
    kind, made = _before_and_after(store, b)
    assert _listed(store, kind) == {t.id for t in made.values()}
    assert _listed(store, kind, tenant_id=b) == {made["b"].id}, "a filter asked for is kept"


@pytest.mark.parametrize("on", [True, False], ids=["enforced", "Spaces off"])
@pytest.mark.parametrize("switch", ["1", "0"])
def test_every_spaces_list_is_every_spaces_whatever_the_old_switch_says(
        store: Any, b: str, monkeypatch: pytest.MonkeyPatch, on: bool, switch: str) -> None:
    monkeypatch.setattr(scope, "_enforced", on)
    monkeypatch.setenv("KAZMA_TENANT_FILTER", switch)
    kind, made = _before_and_after(store, b)
    assert _listed(store, kind, tenant_id="*") == {t.id for t in made.values()}


# ── the runs list (row 36) ───────────────────────────────────────────────


@pytest.mark.usefixtures("enforced")
def test_another_spaces_tasks_do_not_push_out_a_spaces_own(store: Any, a: str, b: str) -> None:
    from kazma_core.runs.adapters.swarm import _SwarmAdapter

    mine = _task(store, b, TaskStatus.PENDING)
    mine.created_at = "2020-01-01T00:00:00+00:00"
    assert store.persist_task(mine)
    for _ in range(45):
        assert store.persist_task(_task(store, a, TaskStatus.PENDING))
    with watched((TaskStore, "list_tasks")) as calls, bind_scope(b, "omar"):
        assert [t.id for t in _SwarmAdapter._stored(store, 0.0)] == [mine.id]
    assert {c.space for c in calls} == {b}, "a list inside a space stays that space's (H6)"
    with watched((TaskStore, "list_tasks")) as calls:
        assert len(_SwarmAdapter._stored(store, 0.0)) >= 40
    assert {c.space for c in calls} == {"<install>"}, "the supervisor's is every space's"


def test_off_the_runs_list_is_as_before(store: Any, a: str) -> None:
    from kazma_core.runs.adapters.swarm import _SwarmAdapter

    task = _task(store, a, TaskStatus.PENDING)
    assert store.persist_task(task)
    with watched((TaskStore, "list_tasks")) as calls:
        assert task.id in {t.id for t in _SwarmAdapter._stored(store, 0.0)}
    assert {c.scope for c in calls} == {None} and {c.kwargs["metadata_filter"] for c in calls} == {None}


# ── a refusal is not "try again shortly" (row 47) ────────────────────────


def _refused_admission(store: Any, a: str, monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> TaskResult:
    monkeypatch.setattr(engine_module, "_WRITE_RETRY_WAITS", ())

    def refuse(*_a: Any, **_k: Any) -> bool:
        raise exc

    monkeypatch.setattr(store, "admit", refuse)
    result = asyncio.run(_engine(store)._admit_to_store(_task(store, a)))
    assert result is not None and result.status == "failed"
    return result


@pytest.mark.usefixtures("enforced")
def test_a_refused_task_is_said_to_be_refused(store: Any, a: str, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _refused_admission(store, a, monkeypatch, NoSpaceBound("no space is bound"))
    assert (result.metadata["code"], result.metadata["retryable"]) == (SPACE_REFUSED, False)
    assert "try again shortly" not in result.error
    outage = _refused_admission(store, a, monkeypatch, RuntimeError("Postgres pool unavailable"))
    assert (outage.metadata["code"], outage.metadata["retryable"]) == (STORE_UNAVAILABLE, True)


def test_off_every_unrecorded_task_is_said_as_before(store: Any, a: str, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _refused_admission(store, a, monkeypatch, NoSpaceBound("no space is bound"))
    assert (result.metadata["code"], result.metadata["retryable"]) == (STORE_UNAVAILABLE, True)


# ── the tables (row 48) ──────────────────────────────────────────────────


class _Pool:
    """Kazma's pool, whose schema statements end as *exc* says."""

    def __init__(self, exc: BaseException | None) -> None:
        self.exc = exc

    def connection(self) -> Any:
        if self.exc is not None:
            raise self.exc
        from types import SimpleNamespace

        return nullcontext(SimpleNamespace(cursor=lambda: nullcontext(None), commit=lambda: None))


def _made_on_postgres(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, exc: BaseException | None) -> Calls:
    from kazma_core.db import pg_helpers, pg_schema

    pool = _Pool(exc)
    monkeypatch.setattr(pg_helpers, "use_postgres", lambda: True)
    monkeypatch.setattr(pg_helpers, "get_pool", lambda: pool)
    monkeypatch.setattr(pg_schema, "ensure_schema", lambda _cur, _ddl: None)
    with watched((pool, "connection")) as calls:
        TaskStore(str(tmp_path / "none.db"))
    return calls


@pytest.mark.usefixtures("enforced")
def test_the_swarms_tables_are_the_installs_whoever_makes_them(monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
                                                               b: str) -> None:
    with bind_scope(b, "omar"):  # a request's own store (chat_tasks)
        assert _made_on_postgres(monkeypatch, tmp_path, None).of("connection") == ["<install>"]


@pytest.mark.usefixtures("enforced")
def test_a_refused_table_is_raised(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    with pytest.raises(NoSpaceBound):
        _made_on_postgres(monkeypatch, tmp_path, NoSpaceBound("no space is bound"))


@pytest.mark.parametrize("on", [True, False], ids=["an outage, enforced", "Spaces off"])
def test_every_other_failure_warns_as_before(monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
                                             caplog: pytest.LogCaptureFixture, on: bool) -> None:
    monkeypatch.setattr(scope, "_enforced", on)
    caplog.set_level("WARNING", logger="kazma_core.swarm.task_store")
    _made_on_postgres(monkeypatch, tmp_path, ConnectionError("away") if on else NoSpaceBound("refused"))
    assert "Postgres schema update failed" in caplog.text


def test_the_panel_answers_a_refusal_with_its_own_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """403 and ``space_refused``, never 503 "try again" with Retry-After."""
    import kazma_gateway.agent_handler as ah
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.db import pg_helpers
    from kazma_ui.swarm_panel import routes_tasks

    class _Refuses(TaskStore):
        def admit(self, task: SwarmTask) -> bool:
            raise RuntimeError("the database refused this")

    for name in ("KAZMA_SHARED_BREAKERS", "KAZMA_TEMPORAL", "KAZMA_GATE_REGISTRY", "KAZMA_SELF_IMPROVEMENT"):
        monkeypatch.setenv(name, "0")
    store = _Refuses(db_path=str(tmp_path / "swarm_tasks.db"))
    engine = _engine(store)

    class _Svc:
        def resolve_engine(self, m: Any = None) -> Any:
            return engine

        def has_swarm_core(self) -> bool:
            return True

        def register_task_handle(self, *a: Any) -> None: ...

        def unregister_task_handle(self, *a: Any) -> None: ...

    async def _no_send(*_a: Any, **_k: Any) -> bool:
        return False

    monkeypatch.setattr(routes_tasks, "get_swarm_service", lambda: _Svc())
    monkeypatch.setattr(ah, "_maybe_send_to_output_target", _no_send)
    monkeypatch.setattr(pg_helpers, "is_space_refusal", lambda exc: "refused" in str(exc))
    router = APIRouter()
    routes_tasks.register_tasks_routes(router, templates=None)
    app = FastAPI()
    app.include_router(router)
    try:
        with TestClient(app) as client:
            answer = client.post("/api/swarm/dispatch", json={"workers": ["w"], "task": "fore"})
        assert answer.status_code == 403 and answer.json()["code"] == SPACE_REFUSED, answer.text
        assert "Retry-After" not in answer.headers and engine.list_active_tasks() == []
    finally:
        store.close()
