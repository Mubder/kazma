"""The matrix cases of Spaces S1-15: the swarm (``tests/_space_matrix.py``).

Every case runs with Spaces enforced, as person A in ``matrix-a`` against
person B's ``matrix-b``, through the product's own Swarm router behind the
real tenant middleware, on a real engine with a private task store, or
through the product's own tools.

Tasks are planted straight into the store with the space and person the
engine stamps, or dispatched to a scripted worker (``probe``) that records
the scope it ran in: a worker's tool calls land where it runs, so a worker
running in the asker's space as the asker is the property. Approvals are
opened as a worker's bus request opens one (the gate record, then the row),
in the asking space.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import scope
from kazma_ui import auth, space_binding

from tests._no_models import keep_models_out
from tests._space_matrix import Matrix, case

ITEM = "S1-15"
_UNKNOWN = "task-unknown-000000"


class _PageData:
    """Stands in for the page's templates: the answer is the data the router
    hands ``swarm.html`` (the workers, the only space data the page shows).
    The template itself is drawn by the page tour, through the whole app."""

    def TemplateResponse(self, request: Any, name: str, context: dict[str, Any]) -> Any:  # noqa: N802
        from fastapi.responses import JSONResponse

        return JSONResponse({"template": name, "workers": context.get("workers")})


@dataclass
class Task:
    """A planted task: its id and the marker its prompt holds."""

    id: str
    mark: str


def _ran(ran: list[tuple[str, Any, str]], worker: str) -> Callable[[Any], Any]:
    async def dispatch(self: Any, task: str, context: Any = "") -> dict[str, Any]:
        from kazma_core.tenant_context import get_current_tenant_id

        ran.append((worker, scope.current_scope(), get_current_tenant_id() or ""))
        return {"worker": self.name, "task_id": "", "status": "success", "output": "done", "error": None}

    return dispatch


def _workers(ran: list[tuple[str, Any, str]]) -> dict[str, Any]:
    """``probe`` records the scope it ran in; ``first`` hands its task to it."""
    from kazma_core.swarm.handoff import HandoffRequest
    from kazma_core.swarm.worker import SwarmWorker

    class _Probe(SwarmWorker):
        dispatch = _ran(ran, "probe")

        async def start(self) -> None:
            self._running = True

        async def stop(self) -> None:
            self._running = False

    class _First(_Probe):
        async def dispatch(self, task: str, context: Any = "") -> dict[str, Any]:
            raise HandoffRequest("probe", task, reason="probe does this")

    return {"probe": _Probe(name="probe"), "first": _First(name="first")}


class World:
    """The Swarm router in one app, with two signed-in people."""

    def __init__(self, matrix: Matrix, client: TestClient, engine: Any, ran: list[tuple[str, Any, str]]) -> None:
        self.matrix = matrix
        self.client = client
        self.engine = engine
        self.ran = ran

    def space_of(self, who: str) -> str:
        return {"a": Matrix.SPACE_A, "b": Matrix.SPACE_B}[who]

    def account_of(self, who: str) -> str:
        return {"a": self.matrix.account_a, "b": self.matrix.account_b}[who]

    def call(self, who: str, method: str, path: str, *, space: str = "", **kwargs: Any) -> Any:
        """A request of *who*'s page, in their own space or in *space*."""
        headers = {"x-matrix-person": who, space_binding.SPACE_HEADER: space or self.space_of(who)}
        return self.client.request(method, path, headers=headers, **kwargs)

    @property
    def store(self) -> Any:
        return self.engine._task_store

    def plant(self, who: str, mark: str, *, status: str = "completed", space: str = "") -> Task:
        """A task of *who*'s space (or *space*), asked by them, its prompt
        carrying *mark*."""
        from kazma_core.swarm.task import SwarmTask, TaskStatus

        task = SwarmTask(
            prompt=f"task {mark}", workers=["probe"], status=TaskStatus(status),
            metadata={"tenant_id": space or self.space_of(who), "asker": self.account_of(who)},
            completed_at=datetime.now(UTC).isoformat() if status in ("completed", "failed") else None,
        )
        assert self.store.persist_task(task), task.id
        if status in ("paused", "running"):
            # Held in flight and in the history, as a restored pause is.
            self.engine._active_tasks[task.id] = task
            self.engine._task_history[task.id] = task
        return Task(id=task.id, mark=mark)

    def pair(self, word: str, **kwargs: Any) -> tuple[Task, Task]:
        marks = self.matrix.plant(word)
        return self.plant("a", marks.a, **kwargs), self.plant("b", marks.b, **kwargs)

    def active_pair(self, word: str) -> tuple[Task, Task]:
        """Two running tasks the engine holds in flight, one per space."""
        from kazma_core.swarm.task import SwarmTask, TaskStatus

        marks = self.matrix.plant(word)
        made = []
        for who, mark in (("a", marks.a), ("b", marks.b)):
            task = SwarmTask(prompt=f"task {mark}", workers=["probe"], status=TaskStatus.RUNNING,
                             metadata={"tenant_id": self.space_of(who), "asker": self.account_of(who)})
            self.engine._active_tasks[task.id] = task
            made.append(Task(id=task.id, mark=mark))
        return made[0], made[1]

    def held(self, task: Task) -> dict[str, Any]:
        """A task as the store holds it, whoever asks ({} when gone)."""
        row = self.store.get_task(task.id)
        return {} if row is None else {"status": str(row.status), "prompt": row.prompt,
                                       "space": (row.metadata or {}).get("tenant_id")}

    def approval(self, who: str, mark: str, *, space: str = "") -> str:
        """A bus request a worker opened in *who*'s space (or *space*), running
        as *who*."""
        from kazma_core.safety.hitl_gates import GateRow, register_gate
        from kazma_core.swarm import shared_approvals

        gate_id = "swarm-gate-" + uuid.uuid4().hex[:12]
        args = json.dumps({"path": f"{mark}.txt", "content": mark})
        where = space or self.space_of(who)
        register_gate(GateRow(gate_id=gate_id, thread_id="swarm", tool="file_write", mechanism="swarm_bus",
                              tenant_id=where, args_json=args, message=args), ttl_seconds=60)
        shared_approvals.open_request(gate_id, tool="file_write", args=args, deadline=time.time() + 60,
                                      space=where, asker=self.account_of(who))
        return gate_id

    def tool(self, who: str, name: str, **kwargs: Any) -> str:
        """The product's tool *name*, run as *who* in their space, on the
        app's loop (where the engine's tasks run)."""
        from tests._chat_space_cases import _registry

        tool = _registry().get_tool(name)
        assert tool is not None, name
        space, account = self.space_of(who), self.account_of(who)

        async def as_them() -> str:
            with scope.bind_scope(space, account):
                return str(await tool.func(**kwargs))

        return self.client.portal.call(as_them)

    def settle(self) -> None:
        """Every task the engine started has ended, and every result's hand-over
        (a background task once Spaces is enforced: the request answers first)."""
        async def wait() -> None:
            for _ in range(500):
                handing_over = any(t.get_name().startswith("swarm-result:") for t in asyncio.all_tasks())
                if not handing_over and not self.engine.list_active_tasks():
                    return
                await asyncio.sleep(0.02)

        self.client.portal.call(wait)


@contextmanager
def world(matrix: Matrix) -> Iterator[World]:
    """Spaces enforced; the product's Swarm router behind the real tenant
    middleware, on a real engine with a private store and scripted workers.
    The person a request is signed in as is named by a test header: sign-in
    is S1-03's."""
    from kazma_core.safety import hitl_gates
    from kazma_core.swarm import SwarmConfig, SwarmEngine, TaskStore, get_swarm_engine, set_swarm_engine
    from kazma_ui.services import reset_swarm_service
    from kazma_ui.swarm_panel import create_swarm_router

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    ran: list[tuple[str, Any, str]] = []
    before = get_swarm_engine()
    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(
        ignore_cleanup_errors=True
    ) as tmp:
        patch.setattr(scope, "_enforced", True)
        patch.delenv("KAZMA_GATE_REGISTRY", raising=False)
        keep_models_out(patch)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))
        hitl_gates.set_db_path_for_tests(str(Path(tmp) / "hitl_gates.db"))
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]),
                             task_store=TaskStore(str(Path(tmp) / "swarm_tasks.db")))
        engine._workers.update(_workers(ran))
        set_swarm_engine(engine)
        reset_swarm_service()
        app = FastAPI()
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_swarm_router(_PageData()))
        try:
            with TestClient(app) as client:
                yield World(matrix, client, engine, ran)
        finally:
            hitl_gates.set_db_path_for_tests(None)
            set_swarm_engine(before)
            reset_swarm_service()
            engine._task_store.close()


def _in_world(run: Callable[[World], None]) -> Callable[[Matrix], None]:
    def with_world(matrix: Matrix) -> None:
        with world(matrix) as made:
            run(made)

    with_world.__name__ = run.__name__
    return with_world


def swarm_case(target: str) -> Callable[[Callable[[World], None]], Callable[[Matrix], None]]:
    def register(run: Callable[[World], None]) -> Callable[[Matrix], None]:
        return case(target, ITEM)(_in_world(run))

    return register


def _never_shown(response: Any, theirs: Task) -> None:
    assert theirs.mark not in response.text and theirs.id not in response.text, response.text


def _same_as_unknown(w: World, method: str, path: str, theirs: Task, **kwargs: Any) -> None:
    """A request for B's task answers exactly what a request for no task does."""
    refused = w.call("a", method, path.format(theirs.id), **kwargs)
    nobodys = w.call("a", method, path.format(_UNKNOWN), **kwargs)
    assert refused.status_code == nobodys.status_code == 404, refused.text
    assert refused.text.replace(theirs.id, _UNKNOWN) == nobodys.text
    assert theirs.mark not in refused.text


def _asked_as_a_in_a(w: World, worker: str = "probe") -> None:
    """The scripted worker ran in A's space, as A, and only there."""
    runs = [(s.space_id if s else None, s.asker if s else None, tenant) for name, s, tenant in w.ran
            if name == worker]
    assert runs and set(runs) == {(Matrix.SPACE_A, w.matrix.account_a, Matrix.SPACE_A)}, runs


# ── the lists and one task ───────────────────────────────────────────────


@swarm_case("GET /api/swarm/tasks")
def swarm_list(w: World) -> None:
    mine, theirs = w.pair("list")
    listed = w.call("a", "GET", "/api/swarm/tasks")
    assert listed.status_code == 200, listed.text
    assert mine.id in listed.text
    _never_shown(listed, theirs)


@swarm_case("GET /api/swarm/tasks/active")
def swarm_active(w: World) -> None:
    mine, theirs = w.active_pair("active")
    listed = w.call("a", "GET", "/api/swarm/tasks/active")
    assert listed.status_code == 200, listed.text
    assert mine.id in listed.text
    _never_shown(listed, theirs)


@swarm_case("GET /api/swarm/tasks/export")
def swarm_export(w: World) -> None:
    mine, theirs = w.pair("export")
    for fmt in ("json", "csv"):
        listed = w.call("a", "GET", f"/api/swarm/tasks/export?format={fmt}")
        assert listed.status_code == 200, listed.text
        assert mine.id in listed.text
        _never_shown(listed, theirs)


@swarm_case("GET /api/swarm/tasks/{task_id}")
def swarm_detail(w: World) -> None:
    mine, theirs = w.pair("detail")
    _same_as_unknown(w, "GET", "/api/swarm/tasks/{}", theirs)
    own = w.call("a", "GET", f"/api/swarm/tasks/{mine.id}")
    assert own.status_code == 200 and mine.mark in own.text, own.text


@swarm_case("GET /api/swarm/tasks/{task_id}/stream")
def swarm_stream(w: World) -> None:
    mine, theirs = w.pair("stream")
    _same_as_unknown(w, "GET", "/api/swarm/tasks/{}/stream", theirs)
    own = w.call("a", "GET", f"/api/swarm/tasks/{mine.id}/stream")
    assert own.status_code == 200 and "task_completed" in own.text, own.text


# ── actions on a task ────────────────────────────────────────────────────


def _action(w: World, verb: str, **kwargs: Any) -> None:
    mine, theirs = w.pair(verb, **kwargs)
    before = w.held(theirs)
    _same_as_unknown(w, "POST", "/api/swarm/tasks/{}/" + verb, theirs)
    assert w.held(theirs) == before
    own = w.call("a", "POST", f"/api/swarm/tasks/{mine.id}/{verb}")
    nobodys = w.call("a", "POST", f"/api/swarm/tasks/{_UNKNOWN}/{verb}")
    # Reached: A's own task is A's to act on (not answered as an unknown one).
    assert own.text.replace(mine.id, _UNKNOWN) != nobodys.text, own.text
    assert w.held(theirs) == before


@swarm_case("POST /api/swarm/tasks/{task_id}/approve")
def swarm_approve(w: World) -> None:
    _action(w, "approve", status="paused")


@swarm_case("POST /api/swarm/tasks/{task_id}/reject")
def swarm_reject(w: World) -> None:
    _action(w, "reject", status="paused")


@swarm_case("POST /api/swarm/tasks/{task_id}/cancel")
def swarm_cancel(w: World) -> None:
    _action(w, "cancel", status="paused")


@swarm_case("POST /api/swarm/tasks/{task_id}/retry")
def swarm_retry(w: World) -> None:
    _action(w, "retry", status="failed")


# ── dispatch: the worker runs in the asker's space ──────────────────────


@swarm_case("POST /api/swarm/dispatch")
def swarm_dispatch(w: World) -> None:
    mark = w.matrix.plant("dispatch").a
    # Naming B's space and person in the request's metadata (the S1-15
    # review's witness) is refused: nothing recorded, no worker run.
    forged = w.call("a", "POST", "/api/swarm/dispatch", json={
        "task": f"task {mark}forged", "workers": ["probe"],
        "metadata": {"tenant_id": Matrix.SPACE_B, "asker": w.matrix.account_b}})
    assert forged.status_code == 400 and forged.json()["code"] == "metadata_set_by_kazma", forged.text
    w.settle()
    assert w.ran == [] and _stored_with(w, f"{mark}forged") == []
    done = w.call("a", "POST", "/api/swarm/dispatch", json={"task": f"task {mark}", "workers": ["probe"]})
    assert done.status_code == 200, done.text
    w.settle()
    _asked_as_a_in_a(w)
    assert [(t.metadata or {}).get("tenant_id") for t in _stored_with(w, mark)] == [Matrix.SPACE_A]
    assert mark not in w.call("b", "GET", "/api/swarm/tasks").text


def _stored_with(w: World, mark: str) -> list[Any]:
    """Every stored task whose prompt holds *mark*, of any space."""
    return [t for t in w.store.list_tasks(page=1, page_size=100, metadata_filter={"tenant_id": "*"})
            if mark in t.prompt]


# ── approvals ────────────────────────────────────────────────────────────


@swarm_case("GET /api/swarm/approvals/{approval_id}")
def swarm_approval_view(w: World) -> None:
    marks = w.matrix.plant("approvalview")
    mine, theirs = w.approval("a", marks.a), w.approval("b", marks.b)
    refused = w.call("a", "GET", f"/api/swarm/approvals/{theirs}")
    nobodys = w.call("a", "GET", "/api/swarm/approvals/swarm-gate-000000000000")
    assert refused.status_code == nobodys.status_code == 404, refused.text
    assert marks.b not in refused.text
    own = w.call("a", "GET", f"/api/swarm/approvals/{mine}")
    assert own.status_code == 200 and marks.a in own.text, own.text


@swarm_case("POST /api/swarm/approvals/{approval_id}/decide")
def swarm_approval_decide(w: World) -> None:
    from kazma_core.swarm import shared_approvals

    marks = w.matrix.plant("approvaldecide")
    mine, theirs = w.approval("a", marks.a), w.approval("b", marks.b)
    refused = w.call("a", "POST", f"/api/swarm/approvals/{theirs}/decide", json={"decision": "approve"})
    assert refused.status_code == 404 and marks.b not in refused.text, refused.text
    assert shared_approvals.describe(theirs)["status"] == "pending"
    own = w.call("a", "POST", f"/api/swarm/approvals/{mine}/decide", json={"decision": "deny"})
    assert own.status_code == 200, own.text
    assert shared_approvals.describe(mine)["status"] == "denied"


# ── a worker serves every space: its log and last task ───────────────────


def _probe_worked_for_both(w: World, word: str) -> Any:
    marks = w.matrix.plant(word)
    probe = w.engine._workers["probe"]
    probe.mark_dispatched(f"task {marks.a}", space=Matrix.SPACE_A)
    probe.mark_completed("success", space=Matrix.SPACE_A)
    probe.mark_dispatched(f"task {marks.b}", space=Matrix.SPACE_B)
    probe.mark_completed("success", space=Matrix.SPACE_B)
    return marks


@swarm_case("GET /api/swarm/workers/{name}/logs")
def swarm_worker_logs(w: World) -> None:
    marks = _probe_worked_for_both(w, "logs")
    mine = w.call("a", "GET", "/api/swarm/workers/probe/logs")
    assert mine.status_code == 200, mine.text
    assert marks.a in mine.text and marks.b not in mine.text, mine.text


@swarm_case("GET /api/swarm/status")
def swarm_status(w: World) -> None:
    marks = _probe_worked_for_both(w, "status")  # its last task is B's
    seen = w.call("a", "GET", "/api/swarm/status")
    assert seen.status_code == 200 and "probe" in seen.text, seen.text
    assert marks.a in seen.text and marks.b not in seen.text  # A's log line, never B's task
    theirs = w.call("b", "GET", "/api/swarm/status")
    assert marks.b in theirs.text and marks.a not in theirs.text


@swarm_case("GET /swarm")
def swarm_page(w: World) -> None:
    marks = _probe_worked_for_both(w, "page")
    page = w.call("a", "GET", "/swarm")
    assert page.status_code == 200, page.text[:300]
    assert page.json()["template"] == "swarm.html"
    assert marks.a in page.text and marks.b not in page.text
    refused = w.client.request("GET", "/swarm", headers={"x-matrix-person": "a",
                                                          space_binding.SPACE_HEADER: Matrix.SPACE_B})
    assert refused.status_code == 403 and refused.json()["code"] == "not_a_member"


# ── tools ────────────────────────────────────────────────────────────────


@swarm_case("tool:dispatch_swarm")
def tool_dispatch(w: World) -> None:
    mark = w.matrix.plant("tooldispatch").a
    w.tool("a", "dispatch_swarm", prompt=f"task {mark}", worker="probe")
    w.settle()
    _asked_as_a_in_a(w)
    [task] = _stored_with(w, mark)
    assert (task.metadata or {}).get("tenant_id") == Matrix.SPACE_A
    assert mark not in w.call("b", "GET", "/api/swarm/tasks").text
    assert "not found" in w.tool("b", "check_swarm_task", task_id=task.id, wait_seconds=0)


@swarm_case("tool:check_swarm_task")
def tool_check(w: World) -> None:
    mine, theirs = w.pair("toolcheck")
    refused = w.tool("a", "check_swarm_task", task_id=theirs.id, wait_seconds=0)
    nobodys = w.tool("a", "check_swarm_task", task_id=_UNKNOWN, wait_seconds=0)
    assert refused.replace(theirs.id, _UNKNOWN) == nobodys
    assert theirs.mark not in refused
    own = w.tool("a", "check_swarm_task", task_id=mine.id, wait_seconds=0)
    assert mine.id in own and "not found" not in own.lower(), own


@swarm_case("tool:handoff_to_worker")
def tool_handoff(w: World) -> None:
    """``first`` hands the task to ``probe``: the target runs in the asker's
    space too (the hand-off keeps the task's space and person)."""
    mark = w.matrix.plant("handoff").a
    done = w.call("a", "POST", "/api/swarm/dispatch", json={"task": f"task {mark}", "workers": ["first"]})
    assert done.status_code == 200, done.text
    w.settle()
    _asked_as_a_in_a(w, "probe")


class _ScopeGraph:
    """A sub-agent's turn that records the scope it ran in."""

    def __init__(self) -> None:
        self.ran_in: list[tuple[Any, str]] = []

    async def ainvoke(self, state: Any, config: Any = None) -> dict[str, Any]:
        from kazma_core.tenant_context import get_current_tenant_id

        self.ran_in.append((scope.current_scope(), get_current_tenant_id() or ""))
        messages = list(state.get("messages", []))
        messages.append({"role": "assistant", "content": "Done."})
        return {"messages": messages}


def _sub_agent(w: World, name: str, **kwargs: Any) -> list[tuple[Any, str]]:
    """The product's sub-agent tool *name* as A, on a manager whose child
    graph records where it ran."""
    sub = importlib.import_module("kazma_core.agent.sub_agent")
    graph = _ScopeGraph()
    before = sub.get_sub_agent_manager()
    sub.set_sub_agent_manager(sub.SubAgentManager(graph_builder=lambda **_kw: graph))
    try:
        answer = w.tool("a", name, **kwargs)
    finally:
        sub.set_sub_agent_manager(before)
    assert '"success"' in answer, answer
    return graph.ran_in


def _ran_as_a_in_a(w: World, ran_in: list[tuple[Any, str]], count: int) -> None:
    assert len(ran_in) == count, ran_in
    for bound, tenant in ran_in:
        assert bound is not None and (bound.space_id, bound.asker) == (Matrix.SPACE_A, w.matrix.account_a)
        assert tenant == Matrix.SPACE_A


@swarm_case("tool:spawn_agent")
def tool_spawn_agent(w: World) -> None:
    _ran_as_a_in_a(w, _sub_agent(w, "spawn_agent", goal="sum the column"), 1)


@swarm_case("tool:spawn_agents")
def tool_spawn_agents(w: World) -> None:
    tasks = json.dumps([{"goal": "sum the column"}, {"goal": "count the rows"}])
    _ran_as_a_in_a(w, _sub_agent(w, "spawn_agents", tasks=tasks), 2)
