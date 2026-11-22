"""Spaces S1-15: the swarm follows the space.

Guards act only where Spaces is enforced; an install with one account, and
the older multi-user mode (which keeps its refusal), behave as before.
Enforced:

* a task carries its space and the person who asked for it, and every
  swarm list, task, stream, action and approval keeps to the caller's space
  (another space's answers as an unknown one);
* a task's worker runs in the task's space as that person (a worker's tool
  calls land where it runs), a hand-off target and a sub-agent too; a task
  of no space runs nothing;
* a worker serves every space: its log, its last task and its lessons are
  shown in the space they came from only;
* (the review) whose task it is is Kazma's to record: a request naming
  another space or person is refused, and the admitting scope overwrites
  what a task arrived with; a task of no space never trips the breaker every
  space shares; only the person who asked answers a task or its approval
  (N4); someone else's card and result stay off the owner's chat apps (O1);
  the swarm's writes stay the admin's until feature grants (S2-11).

Each guard is taken out as a control, and the control shows what it stops:
the cases that rest on it fail. The route and tool cases are in
``tests/_swarm_space_cases.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._space_matrix import Matrix, load_cases
from tests._swarm_space_cases import ITEM

CASES = load_cases()

ROUTES = (
    "GET /api/swarm/approvals/{approval_id}", "GET /api/swarm/status", "GET /api/swarm/tasks",
    "GET /api/swarm/tasks/active", "GET /api/swarm/tasks/export", "GET /api/swarm/tasks/{task_id}",
    "GET /api/swarm/tasks/{task_id}/stream", "GET /api/swarm/workers/{name}/logs", "GET /swarm",
    "POST /api/swarm/approvals/{approval_id}/decide", "POST /api/swarm/dispatch",
    "POST /api/swarm/tasks/{task_id}/approve", "POST /api/swarm/tasks/{task_id}/cancel",
    "POST /api/swarm/tasks/{task_id}/reject", "POST /api/swarm/tasks/{task_id}/retry",
)
TOOLS = ("tool:check_swarm_task", "tool:dispatch_swarm", "tool:handoff_to_worker", "tool:spawn_agent",
         "tool:spawn_agents")


@pytest.fixture
def matrix(monkeypatch) -> Matrix:
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


def _fails(target: str, matrix: Matrix) -> None:
    """The case of *target* fails on one of its own assertions: its guard is
    out, and the case saw another space's data or ran outside the asker's."""
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        CASES[target].run(matrix)


def test_the_item_wrote_a_case_for_every_swarm_route_and_tool() -> None:
    mine = sorted(target for target, registered in CASES.items() if registered.item == ITEM)
    assert mine == sorted([*ROUTES, *TOOLS])


# ── each guard taken out ─────────────────────────────────────────────────


@pytest.mark.parametrize("target", (
    "GET /api/swarm/tasks/active", "GET /api/swarm/tasks/{task_id}", "GET /api/swarm/tasks/{task_id}/stream",
    "POST /api/swarm/tasks/{task_id}/approve", "POST /api/swarm/tasks/{task_id}/reject",
    "POST /api/swarm/tasks/{task_id}/cancel", "POST /api/swarm/tasks/{task_id}/retry",
    "tool:check_swarm_task",
))
def test_control_a_task_of_any_space_is_shown_and_acted_on(target, matrix, monkeypatch) -> None:
    """``single_operator.task_of_this_space`` taken out: every task is the
    caller's."""
    import kazma_core.swarm.single_operator as single_operator

    monkeypatch.setattr(single_operator, "task_of_this_space", lambda task: True)
    _fails(target, matrix)


@pytest.mark.parametrize("target", ("GET /api/swarm/tasks", "GET /api/swarm/tasks/export"))
def test_control_the_task_list_with_no_space_filter(target, matrix, monkeypatch) -> None:
    """The stored list as before S1-15: every space's tasks."""
    from kazma_core.swarm.task_store import TaskStore

    every = TaskStore.list_tasks

    def as_before(self, **kwargs: Any) -> Any:
        return every(self, **{**kwargs, "metadata_filter": {"tenant_id": "*"}})

    monkeypatch.setattr(TaskStore, "list_tasks", as_before)
    _fails(target, matrix)


def _worker_scope_as_before(tenant_id: str | None, asker: str | None) -> Any:
    """The worker's context before S1-15: the task's tenant alone, no person."""
    from kazma_core.tenant_context import tenant_scope

    return lambda: tenant_scope(tenant_id or "default")


def _run_with_nothing_bound(matrix: Matrix, worker: str) -> list[tuple[str, Any, str]]:
    """A's task run by the engine with nothing bound -- as a pipeline resumed
    after a restart, or the durable path, runs it: only the task says whose
    it is. What the scripted workers recorded."""
    import contextvars

    from kazma_core.swarm.task import SwarmTask

    from tests._swarm_space_cases import world

    with world(matrix) as w:
        task = SwarmTask(prompt="task from a restart", workers=[worker],
                         metadata={"tenant_id": Matrix.SPACE_A, "asker": matrix.account_a})
        contextvars.Context().run(asyncio.run, w.engine.dispatch(task))
        return list(w.ran)


@pytest.mark.parametrize("worker", ("probe", "first"))  # "first" hands the task to probe
def test_a_task_run_with_nothing_bound_runs_its_worker_as_its_asker(worker, matrix) -> None:
    ran = [(s.space_id, s.asker, tenant) if s else (None, None, tenant) for name, s, tenant in
           _run_with_nothing_bound(matrix, worker) if name == "probe"]
    assert ran == [(Matrix.SPACE_A, matrix.account_a, Matrix.SPACE_A)], ran


@pytest.mark.parametrize("worker", ("probe", "first"))
def test_control_the_unscoped_dispatch_runs_the_worker_as_no_one(worker, matrix, monkeypatch) -> None:
    """The plan's control: a worker run with its task's tenant alone runs as
    no one -- its tool calls cannot be told apart from Kazma's own work."""
    import kazma_core.swarm.worker_dispatch as worker_dispatch

    monkeypatch.setattr(worker_dispatch, "_worker_scope", _worker_scope_as_before)
    ran = [s for name, s, _tenant in _run_with_nothing_bound(matrix, worker) if name == "probe"]
    assert ran == [None], ran


@pytest.mark.parametrize("target", (
    "GET /api/swarm/approvals/{approval_id}", "POST /api/swarm/approvals/{approval_id}/decide",
))
def test_control_an_approval_of_any_space_is_shown_and_decided(target, matrix, monkeypatch) -> None:
    import kazma_ui.swarm_panel.routes_approvals as routes_approvals

    monkeypatch.setattr(routes_approvals, "_not_this_spaces", lambda view: False)
    _fails(target, matrix)


@pytest.mark.parametrize("target", ("GET /api/swarm/status", "GET /swarm", "GET /api/swarm/workers/{name}/logs"))
def test_control_a_worker_shows_every_spaces_work(target, matrix, monkeypatch) -> None:
    """A worker as it was shown before S1-15: its last task and every log
    line, whichever space they came from."""
    import kazma_ui.services as services
    from kazma_core.swarm.worker import SwarmWorker

    monkeypatch.setattr(services, "_kept_to_space", lambda worker, shown: shown)
    monkeypatch.setattr(SwarmWorker, "logs_for", lambda self, space: list(self.logs))
    _fails(target, matrix)


@pytest.mark.parametrize("name", ("spawn_agent", "spawn_agents"))
def test_control_a_sub_agent_spawned_with_nothing_bound_is_not_the_askers(name, matrix, enforced) -> None:
    """A sub-agent runs where its spawner runs: spawned by work bound to no
    space (the scope taken out), its turn is no one's -- never A's."""
    import contextvars
    import importlib
    import json

    from tests._chat_space_cases import _registry
    from tests._swarm_space_cases import _ScopeGraph

    sub = importlib.import_module("kazma_core.agent.sub_agent")

    graph = _ScopeGraph()
    before = sub.get_sub_agent_manager()
    sub.set_sub_agent_manager(sub.SubAgentManager(graph_builder=lambda **_kw: graph))
    try:
        tool = _registry().get_tool(name)
        kwargs = {"goal": "sum the column"} if name == "spawn_agent" else {
            "tasks": json.dumps([{"goal": "sum the column"}])}
        contextvars.Context().run(asyncio.run, tool.func(**kwargs))
    finally:
        sub.set_sub_agent_manager(before)
    assert all(bound is None or (bound.space_id, bound.asker) != (Matrix.SPACE_A, matrix.account_a)
               for bound, _tenant in graph.ran_in), graph.ran_in


# ── a task's space and person ────────────────────────────────────────────


def test_a_task_of_no_space_runs_no_worker(matrix, enforced) -> None:
    from kazma_core.swarm.worker_dispatch import _TaskOfNoSpace, _worker_scope

    with pytest.raises(_TaskOfNoSpace):
        _worker_scope("no-such-space", matrix.account_a)


def test_a_task_whose_asker_left_is_refused_never_run_as_anyone_else(matrix, enforced) -> None:
    """The S2-12 review, finding 6, reversed S1-15's rule: a departed
    person's task is refused, never run as the space's acting account
    (tests/test_departed_members_work_refused.py). Work no person asked for
    still runs as the space's own."""
    identity.create_space("Shared", created_by=OWNER_ACCOUNT_ID, space_id="swarm-shared")
    identity.add_member("swarm-shared", matrix.account_a)
    assert identity.work_scope("swarm-shared", matrix.account_a) == ("swarm-shared", matrix.account_a)
    identity.remove_member("swarm-shared", matrix.account_a)
    with pytest.raises(identity.AskerLeft):
        identity.work_scope("swarm-shared", matrix.account_a)
    assert identity.work_scope("swarm-shared", None) == (
        "swarm-shared", identity.acting_account("swarm-shared"))
    assert identity.work_scope("no-such-space", matrix.account_a) is None


def test_not_enforced_a_worker_runs_in_its_tenant_as_before(matrix) -> None:
    from kazma_core.swarm.worker_dispatch import _worker_scope
    from kazma_core.tenant_context import get_current_tenant_id

    with _worker_scope(None, None)():
        assert get_current_tenant_id() == OWNER_SPACE_ID and scope.current_scope() is None


def test_work_bound_to_no_space_is_refused_once_enforced(matrix, enforced) -> None:
    from kazma_core.swarm.single_operator import NO_SPACE_REFUSAL, swarm_refusal

    assert swarm_refusal() == NO_SPACE_REFUSAL
    with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
        assert swarm_refusal() is None


_WRITES = ("/api/swarm/dispatch", "/api/swarm/tasks/{task_id}/approve", "/api/swarm/tasks/{task_id}/reject",
           "/api/swarm/tasks/{task_id}/cancel", "/api/swarm/tasks/{task_id}/retry",
           "/api/swarm/approvals/{approval_id}/decide")


def test_the_swarms_writes_stay_the_admins_once_enforced(enforced) -> None:
    """Until feature grants give the swarm to others (S2-11), a dispatch and
    a task's actions are the admin's in every mode (the S1-15 review)."""
    from kazma_ui.swarm_panel.route_roles import ADMIN, role_for

    assert scope.spaces_enforced()
    assert {path: role_for("POST", path) for path in _WRITES} == dict.fromkeys(_WRITES, ADMIN)


def test_a_bus_request_records_the_space_that_asked(matrix, enforced) -> None:
    from kazma_core.swarm.safety import _asking_space

    assert _asking_space() == ""
    with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
        assert _asking_space() == Matrix.SPACE_A


# ── a worker's lessons stay in their space ───────────────────────────────


@pytest.fixture
def lessons(matrix, enforced, monkeypatch) -> SimpleNamespace:
    from kazma_core.config_store import get_config_store
    from kazma_core.skills import self_improvement as si

    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "1")
    monkeypatch.setattr(si, "_soul_requires_confirm", lambda: False)
    store = get_config_store()
    for key in (si._AGENT_EVO_KEY, si._SPACE_EVO_KEY):
        store.delete(key)
    registry = SimpleNamespace(entry=SimpleNamespace(system_prompt="You are probe."), updates=[])
    registry.get = lambda name: registry.entry
    registry.update = lambda name, **fields: registry.updates.append((name, fields))
    import kazma_core.memory.swarm_bridge as swarm_bridge
    import kazma_core.swarm.registry as registry_mod

    monkeypatch.setattr(registry_mod, "get_worker_registry", lambda: registry)
    monkeypatch.setattr(swarm_bridge, "log_evolution_v2", lambda **_kw: None)
    yield SimpleNamespace(si=si, registry=registry)
    for key in (si._AGENT_EVO_KEY, si._SPACE_EVO_KEY):
        store.delete(key)


def _prompt_in(space: str, account: str, prompt: str = "You are probe.") -> str:
    from kazma_core.swarm.worker import _prompt_in_this_space

    with scope.bind_scope(space, account):
        return _prompt_in_this_space("probe", prompt)


def test_a_workers_lesson_is_kept_in_the_space_it_was_learned_in(lessons, matrix) -> None:
    skill = lessons.si.SelfImprovementSkill()
    with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
        applied = asyncio.run(skill.apply_mutation("probe", "Check the totals twice.", space=Matrix.SPACE_A))
    assert applied
    assert lessons.registry.updates == [], "never in the prompt every space's tasks share"
    assert lessons.si.worker_lessons("probe", space=Matrix.SPACE_A) == ["Check the totals twice."]
    assert "Check the totals twice." in _prompt_in(Matrix.SPACE_A, matrix.account_a)
    assert "Check the totals twice." not in _prompt_in(Matrix.SPACE_B, matrix.account_b)


def test_control_learned_as_before_the_lesson_is_in_the_shared_prompt(lessons, matrix, monkeypatch) -> None:
    """The lesson written as before S1-15 (Spaces not enforced at the write):
    into the worker's registry prompt, which every space's tasks read."""
    skill = lessons.si.SelfImprovementSkill()
    monkeypatch.setattr(scope, "_enforced", False)
    assert asyncio.run(skill.apply_mutation("probe", "Check the totals twice."))
    [(name, fields)] = lessons.registry.updates
    assert name == "probe" and "Check the totals twice." in fields["system_prompt"]


def test_lessons_from_before_spaces_are_shown_in_the_owners_space_only(lessons, matrix) -> None:
    """A registry prompt's own lessons were learned before Spaces was
    enforced: the owner's work. Shown in the owner's space, never another's."""
    learned = "You are probe.\n\n[SelfImprovement] Cite the source of every figure."
    assert "Cite the source" in _prompt_in(OWNER_SPACE_ID, OWNER_ACCOUNT_ID, learned)
    assert "Cite the source" not in _prompt_in(Matrix.SPACE_A, matrix.account_a, learned)
    assert _prompt_in(Matrix.SPACE_A, matrix.account_a, learned).startswith("You are probe.")


_LEARNED = "You are probe.\n\n[SelfImprovement] Cite the source of every figure."


def test_a_real_worker_shows_the_owners_lessons_in_the_owners_space_only(lessons, matrix, monkeypatch) -> None:
    """Through a real in-process worker: what its model is sent."""
    assert "Cite the source" in _worker_was_told(monkeypatch, OWNER_SPACE_ID, OWNER_ACCOUNT_ID, _LEARNED)
    told = _worker_was_told(monkeypatch, Matrix.SPACE_A, matrix.account_a, _LEARNED)
    assert "You are probe." in told and "Cite the source" not in told


def test_control_the_whole_prompt_shows_the_owners_lessons_everywhere(lessons, matrix, monkeypatch) -> None:
    """The reader as before S1-15: the worker's prompt whole, in any space --
    through the same real worker, A's model is sent the owner's lesson."""
    import kazma_core.swarm.worker as worker_mod

    monkeypatch.setattr(worker_mod, "_prompt_in_this_space", lambda name, prompt: prompt)
    assert "Cite the source" in _worker_was_told(monkeypatch, Matrix.SPACE_A, matrix.account_a, _LEARNED)


def test_with_no_space_bound_a_worker_gets_no_lessons(lessons) -> None:
    from kazma_core.swarm.worker import _prompt_in_this_space

    learned = "You are probe.\n\n[SelfImprovement] Cite the source of every figure."
    assert _prompt_in_this_space("probe", learned) == "You are probe."


def _scripted_model(monkeypatch, replies: list[Any]) -> list[list[dict[str, Any]]]:
    """Every in-process worker's model, scripted: answers *replies* in turn
    and records the messages of each call."""
    from unittest.mock import AsyncMock, MagicMock

    import kazma_core.model_registry as model_registry
    import kazma_core.models.selection as selection

    sent: list[list[dict[str, Any]]] = []
    answers = iter(replies)

    async def chat(*_args: Any, **kwargs: Any) -> Any:
        sent.append([dict(m) for m in kwargs.get("messages") or _args[0]])
        return next(answers)

    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=chat)
    registry = MagicMock()
    registry.get_client = MagicMock(return_value=provider)
    monkeypatch.setattr(model_registry, "get_model_registry", lambda: registry)
    monkeypatch.setattr(selection, "select_provider_for_task", lambda registry, prompt: None)
    return sent


def _reply(content: str = "", calls: list[Any] | None = None) -> Any:
    from kazma_core.llm_provider import LLMResponse

    return LLMResponse(content=content, tool_calls=calls or [], finish_reason="stop",
                       usage={"prompt_tokens": 10, "completion_tokens": 5}, cost_usd=0.0)


def _worker_was_told(monkeypatch, space: str, account: str, prompt: str) -> str:
    """The system messages a real in-process worker sends its model when it
    runs in *space* as *account* with *prompt* as its own."""
    from kazma_core.swarm.worker import InProcessWorker

    sent = _scripted_model(monkeypatch, [_reply("done")])
    worker = InProcessWorker(name="probe", role="tester", system_prompt=prompt)
    with scope.bind_scope(space, account):
        asyncio.run(worker.dispatch("sum the column"))
    assert sent, "the worker never called its model"
    return "\n".join(str(m.get("content") or "") for m in sent[0] if m.get("role") == "system")


# ── the review: whose task it is is Kazma's to record ────────────────────


def _dispatch_forged(matrix: Matrix) -> tuple[Any, list[tuple[Any, Any, str]]]:
    """A's dispatch naming B's space and person, as the review's probe sent
    it; where the scripted worker ran."""
    from tests._swarm_space_cases import world

    with world(matrix) as w:
        done = w.call("a", "POST", "/api/swarm/dispatch", json={
            "task": "forged", "workers": ["probe"],
            "metadata": {"tenant_id": Matrix.SPACE_B, "asker": matrix.account_b}})
        w.settle()
        return done, [(s.space_id if s else None, s.asker if s else None, tenant)
                      for name, s, tenant in w.ran if name == "probe"]


def test_a_forged_dispatch_is_refused_and_runs_nothing(matrix) -> None:
    done, runs = _dispatch_forged(matrix)
    assert done.status_code == 400 and done.json()["code"] == "metadata_set_by_kazma", done.text
    assert runs == []


def test_without_the_routes_refusal_the_engine_still_runs_the_worker_as_the_asker(matrix, monkeypatch) -> None:
    """The second layer: the admitting scope overwrites what a task arrived
    with (``SwarmEngine._stamp_whose``)."""
    import kazma_core.swarm.task_metadata as task_metadata

    monkeypatch.setattr(task_metadata, "kazma_keys_in", lambda metadata: [])
    done, runs = _dispatch_forged(matrix)
    assert done.status_code == 200, done.text
    assert runs == [(Matrix.SPACE_A, matrix.account_a, Matrix.SPACE_A)], runs


def _stamp_as_before(task: Any) -> None:
    """The stamp before the review: whatever the task arrived with stood."""
    from kazma_core.spaces.scope import bound_asker
    from kazma_core.tenant_isolation import require_tenant_id

    task.metadata = dict(task.metadata or {})
    task.metadata.setdefault("tenant_id", require_tenant_id())
    if bound_asker():
        task.metadata.setdefault("asker", bound_asker())


def test_control_with_both_layers_out_the_worker_runs_as_the_forged_person(matrix, monkeypatch) -> None:
    """The review's witness: the request's metadata taken whole and the
    stamp a ``setdefault`` -- A's request ran the worker in B's space, as B."""
    import kazma_core.swarm.task_metadata as task_metadata
    from kazma_core.swarm.engine import SwarmEngine

    monkeypatch.setattr(task_metadata, "kazma_keys_in", lambda metadata: [])
    monkeypatch.setattr(SwarmEngine, "_stamp_whose", staticmethod(_stamp_as_before))
    _done, runs = _dispatch_forged(matrix)
    assert runs == [(Matrix.SPACE_B, matrix.account_b, Matrix.SPACE_B)], runs


# ── the review: no task of no space trips the shared breaker ─────────────


def _run_alone(matrix: Matrix, tenant: str, asker: str) -> tuple[list[Any], int, Any]:
    """A task run by the engine with nothing bound, on a worker of its own
    (breakers are shared by worker name): what ran, the worker's breaker
    failures, the result."""
    import contextvars

    from kazma_core.swarm.task import SwarmTask

    from tests._swarm_space_cases import world

    name = "probe-" + uuid.uuid4().hex[:8]
    with world(matrix) as w:
        w.engine._workers[name] = type(w.engine._workers["probe"])(name=name)
        task = SwarmTask(prompt="a task", workers=[name], metadata={"tenant_id": tenant, "asker": asker})
        result = contextvars.Context().run(asyncio.run, w.engine.dispatch(task))
        reliability = getattr(w.engine, "_reliability", None)
        breaker = (reliability.breaker_for_dispatch(name)
                   if reliability is not None and hasattr(reliability, "breaker_for_dispatch")
                   else w.engine.get_circuit_breaker(name))
        return list(w.ran), int(breaker.consecutive_failures), result


def test_a_task_of_no_space_never_trips_the_shared_breaker(matrix) -> None:
    ran, failures, result = _run_alone(matrix, "no-such-space", matrix.account_a)
    assert ran == [] and failures == 0, (ran, failures)
    assert result.status != "success"


def test_control_refused_inside_the_dispatch_the_task_trips_the_breaker(matrix, monkeypatch) -> None:
    """Refused where it was before the review -- inside the worker's
    dispatch, past the breaker -- a task naming no space counts against the
    worker for every space."""
    import kazma_core.swarm.worker_dispatch as worker_dispatch

    async def refused_inside(engine: Any, worker: Any, context: Any, span: Any) -> Any:
        def make() -> Any:
            raise worker_dispatch._TaskOfNoSpace("this task belongs to no space")
        return make

    monkeypatch.setattr(worker_dispatch, "_scope_or_refusal", refused_inside)
    ran, failures, _result = _run_alone(matrix, "no-such-space", matrix.account_a)
    assert ran == [] and failures >= 1, (ran, failures)


def test_an_identity_outage_never_trips_the_shared_breaker(matrix, monkeypatch) -> None:
    """Whose task it is cannot be read (the database away): the worker does
    not run, the outage is reported to the database watch, and the breaker
    counts nothing."""
    import kazma_core.swarm.worker_dispatch as worker_dispatch
    from kazma_core.db import availability
    from kazma_core.db.postgres_pool import PostgresUnreachable

    def away(tenant_id: Any, asker: Any) -> Any:
        raise identity.IdentityUnavailable("the identity store could not be read") \
            from PostgresUnreachable(3, "connection refused")

    availability.reset()
    monkeypatch.setattr(availability, "_spawn_prober", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_dispatch, "_worker_scope", away)
    try:
        ran, failures, _result = _run_alone(matrix, Matrix.SPACE_A, matrix.account_a)
        assert ran == [] and failures == 0, (ran, failures)
        assert availability.is_away()
    finally:
        availability.reset()


# ── the review: only the person who asked answers (N4) ───────────────────

SHARED = "matrix-swarm-shared"


@pytest.fixture
def shared(matrix) -> str:
    """A space A made and B joined."""
    identity.create_space("Shared", created_by=matrix.account_a, space_id=SHARED)
    identity.add_member(SHARED, matrix.account_b)
    return SHARED


def _b_answers_as(verb: str, status: str, matrix: Matrix, space: str) -> tuple[Any, Any, Any, bool]:
    """B's answer to A's task in *space*: what B saw, what B's action got,
    what A's own got, and whether B's action left the task as it was."""
    from tests._swarm_space_cases import world

    with world(matrix) as w:
        task = w.plant("a", w.matrix.plant(verb).a, status=status, space=space)
        before = w.held(task)
        seen = w.call("b", "GET", f"/api/swarm/tasks/{task.id}", space=space)
        theirs = w.call("b", "POST", f"/api/swarm/tasks/{task.id}/{verb}", space=space)
        untouched = w.held(task) == before
        own = w.call("a", "POST", f"/api/swarm/tasks/{task.id}/{verb}", space=space)
        return seen, theirs, own, untouched


_ANSWERS = (("approve", "paused"), ("reject", "paused"), ("cancel", "paused"), ("retry", "failed"))


@pytest.mark.parametrize(("verb", "status"), _ANSWERS)
def test_in_a_shared_space_only_the_asker_answers_a_task(verb, status, matrix, shared) -> None:
    seen, theirs, own, untouched = _b_answers_as(verb, status, matrix, shared)
    assert seen.status_code == 200, seen.text  # B sees the space's task
    assert theirs.status_code == 403 and theirs.json()["code"] == "not_yours", theirs.text
    assert untouched
    assert own.status_code != 403 and own.json().get("code") != "not_yours", own.text


@pytest.mark.parametrize(("verb", "status"), _ANSWERS)
def test_control_any_member_answers_a_task(verb, status, matrix, shared, monkeypatch) -> None:
    import kazma_core.swarm.single_operator as single_operator

    monkeypatch.setattr(single_operator, "answered_by_caller", lambda space, asker, tools=(): True)
    _seen, theirs, _own, _untouched = _b_answers_as(verb, status, matrix, shared)
    assert theirs.json().get("code") != "not_yours", theirs.text


def _b_decides(matrix: Matrix, space: str) -> tuple[Any, str, Any, str]:
    from kazma_core.swarm import shared_approvals

    from tests._swarm_space_cases import world

    with world(matrix) as w:
        request = w.approval("a", w.matrix.plant("sharedrequest").a, space=space)
        seen = w.call("b", "GET", f"/api/swarm/approvals/{request}", space=space)
        theirs = w.call("b", "POST", f"/api/swarm/approvals/{request}/decide", space=space,
                        json={"decision": "approve"})
        after_theirs = shared_approvals.describe(request)["status"]
        own = w.call("a", "POST", f"/api/swarm/approvals/{request}/decide", space=space,
                     json={"decision": "deny"})
        return seen, after_theirs, theirs, (own.status_code, shared_approvals.describe(request)["status"])


def test_in_a_shared_space_only_the_asker_decides_an_approval(matrix, shared) -> None:
    seen, after_theirs, theirs, own = _b_decides(matrix, shared)
    assert seen.status_code == 200, seen.text
    assert theirs.status_code == 403 and theirs.json()["error"] == "not_yours", theirs.text
    assert after_theirs == "pending"
    assert own == (200, "denied"), own


def test_control_any_member_decides_an_approval(matrix, shared, monkeypatch) -> None:
    import kazma_core.swarm.single_operator as single_operator

    monkeypatch.setattr(single_operator, "answered_by_caller", lambda space, asker, tools=(): True)
    _seen, after_theirs, theirs, _own = _b_decides(matrix, shared)
    assert theirs.status_code == 200 and after_theirs == "approved", theirs.text


# ── the review: someone else's card and result stay off the owner's chat apps (O1) ──


class _OwnersChatApps:
    """The install's chat apps (the owner's), recording every card posted."""

    def __init__(self) -> None:
        self.posted: list[str] = []

    async def send(self, message: Any) -> bool:
        return True

    async def send_report(self, report: Any) -> None:
        return None

    async def send_alert(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def request_approval(self, approval: Any, timeout: float = 60.0) -> bool:
        from kazma_core.swarm import shared_approvals

        self.posted.append(approval.task_id)

        async def edit(_row: Any) -> None:
            return None

        return await shared_approvals.await_card(approval.task_id, edit)


def _asked_on_the_bus(monkeypatch, tmp_path, space: str, account: str) -> tuple[bool, list[str], bool]:
    """A worker's danger call in *space* as *account*, answered on the web:
    (approved, the cards the owner's chat apps got, whether a chat button
    could have decided it)."""
    import kazma_core.swarm.bus as bus_mod
    from kazma_core.safety import hitl_gates
    from kazma_core.swarm import shared_approvals
    from kazma_core.swarm.bus import SwarmMessageBus
    from kazma_core.swarm.safety import SafetyMiddleware

    owners = _OwnersChatApps()
    bus = SwarmMessageBus(owners)
    monkeypatch.setattr(bus_mod, "get_message_bus", lambda: bus)

    class _Middleware(SafetyMiddleware):
        approval_timeout = 10.0

    middleware = _Middleware(enabled=True)
    hitl_gates.set_db_path_for_tests(str(tmp_path / "hitl_gates.db"))
    button = [True]

    async def go() -> bool:
        with scope.bind_scope(space, account):
            asking = asyncio.create_task(middleware.check("file_write", '{"path": "a.txt"}',
                                                          task_id="t1", worker_name="probe"))
            for _ in range(250):
                rows = await asyncio.to_thread(lambda: _pending_requests(account))
                if rows:
                    break
                await asyncio.sleep(0.02)
            [request] = rows
            button[0] = await asyncio.to_thread(shared_approvals._a_chat_button_may_decide, request)
            await asyncio.to_thread(shared_approvals.decide_from_web, request, True, actor="web user")
            return await asking

    try:
        approved = asyncio.run(go())
    finally:
        hitl_gates.set_db_path_for_tests(None)
    return approved, owners.posted, button[0]


def _pending_requests(account: str) -> list[str]:
    from kazma_core.config_store import get_config_store

    rows = get_config_store().get("swarm.approval") or {}
    return [rid for rid, row in rows.items()
            if isinstance(row, dict) and row.get("status") == "pending" and row.get("asker") == account]


def test_a_members_request_never_reaches_the_owners_chat_apps(matrix, enforced, monkeypatch, tmp_path) -> None:
    approved, posted, button = _asked_on_the_bus(monkeypatch, tmp_path, Matrix.SPACE_A, matrix.account_a)
    assert approved and posted == [], posted  # answered in A's space, on the web
    assert not button, "a chat app's button never decides someone else's request"


def test_the_owners_own_request_still_reaches_the_owners_chat_apps(matrix, enforced, monkeypatch,
                                                                   tmp_path) -> None:
    approved, posted, button = _asked_on_the_bus(monkeypatch, tmp_path, OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
    assert approved and len(posted) == 1 and button


def test_control_a_members_request_posted_to_the_bus(matrix, enforced, monkeypatch, tmp_path) -> None:
    """The bus as before the review: A's card went to the owner's chat apps."""
    import kazma_core.swarm.safety as safety_mod

    monkeypatch.setattr(safety_mod, "_kept_in_its_space", lambda: False)
    _approved, posted, _button = _asked_on_the_bus(monkeypatch, tmp_path, Matrix.SPACE_A, matrix.account_a)
    assert len(posted) == 1


def _dispatched_and_routed(matrix: Matrix, monkeypatch) -> list[str]:
    """What the install's output target got after A's dispatch."""
    import kazma_gateway.agent_handler as agent_handler

    from tests._swarm_space_cases import world

    routed: list[str] = []

    async def output_target(*args: Any, **_kwargs: Any) -> bool:
        routed.append(str(args[1] if len(args) > 1 else ""))
        return True

    monkeypatch.setattr(agent_handler, "_maybe_send_to_output_target", output_target)
    with world(matrix) as w:
        done = w.call("a", "POST", "/api/swarm/dispatch", json={"task": "task routed", "workers": ["probe"]})
        assert done.status_code == 200, done.text
        w.settle()
    return routed


def test_a_members_result_never_goes_to_the_owners_output_target(matrix, monkeypatch) -> None:
    assert _dispatched_and_routed(matrix, monkeypatch) == []


def test_control_a_members_result_routed_to_the_output_target(matrix, monkeypatch) -> None:
    import kazma_core.swarm.single_operator as single_operator

    monkeypatch.setattr(single_operator, "_task_for_the_owner", lambda task: True)
    assert len(_dispatched_and_routed(matrix, monkeypatch)) == 1


async def test_a_members_task_result_is_not_sent_to_a_chat(matrix, enforced, monkeypatch) -> None:
    import kazma_core.tools.send_message as send_message
    from kazma_core.swarm import chat_delivery

    sent: list[str] = []

    async def send(target: str, text: str, **_kwargs: Any) -> str:
        sent.append(target)
        return f"sent:{target}"

    monkeypatch.setattr(send_message, "send_message", send)
    result = SimpleNamespace(status="success", aggregated_output="done", synthesized_output="", error="",
                             duration_seconds=1.0, worker_results=[])
    theirs = SimpleNamespace(id="t-member-" + uuid.uuid4().hex[:6],
                             metadata={"tenant_id": Matrix.SPACE_A, "asker": matrix.account_a})
    await chat_delivery._deliver(theirs, result, "telegram", "telegram:777")
    assert sent == []
    owners = SimpleNamespace(id="t-owner-" + uuid.uuid4().hex[:6],
                             metadata={"tenant_id": OWNER_SPACE_ID, "asker": OWNER_ACCOUNT_ID})
    await chat_delivery._deliver(owners, result, "telegram", "telegram:777")
    assert sent == ["telegram:777"]


# ── the review: a real tool call lands in the asker's space ──────────────


def _scribe_stores(matrix: Matrix, monkeypatch, mark: str, *, tool: str = "memory_link_entities") -> list[str]:
    """A real in-process worker, run by the engine with nothing bound (as a
    task resumed after a restart is), calls the product's ``memory_link_entities``
    (a fact) or ``memory_store`` (a note): the spaces it was stored in.
    ``memory_store`` writes only when the user's own words ask to remember (the
    memory plan's finding 1), and a worker has no user message: its note is
    refused (``test_a_workers_memory_store_is_refused``)."""
    import contextvars
    import sqlite3

    import kazma_core.agent.tool_registry as tool_registry
    from kazma_core.llm_provider import ToolCall
    from kazma_core.paths import primary_memory_db
    from kazma_core.swarm.task import SwarmTask, WorkerCapabilities
    from kazma_core.swarm.worker import InProcessWorker

    from tests._chat_space_cases import _registry
    from tests._swarm_space_cases import world

    arguments = ({"text": f"scribe note {mark}"} if tool == "memory_store"
                 else {"subject": "user", "object": f"scribe note {mark}", "predicate": "noted"})
    _scripted_model(monkeypatch, [
        _reply(calls=[ToolCall(id="call-1", name=tool, arguments=arguments)]),
        _reply("stored"),
    ])
    monkeypatch.setattr(tool_registry, "get_tool_registry", _registry)
    with world(matrix) as w:
        w.engine._workers["scribe"] = InProcessWorker(
            name="scribe", role="scribe", capabilities=WorkerCapabilities(role="scribe", tools=[tool]))
        task = SwarmTask(prompt="store the note", workers=["scribe"],
                         metadata={"tenant_id": Matrix.SPACE_A, "asker": matrix.account_a})
        contextvars.Context().run(asyncio.run, w.engine.dispatch(task))
    conn = sqlite3.connect(primary_memory_db())
    try:
        # A run that refused before any memory was written (a worker bound to
        # no space once Spaces is enforced, S1-02) leaves no memory table.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'episodes'").fetchone() is None:
            return []
        rows = conn.execute("SELECT tenant_id FROM episodes WHERE user_text LIKE ?", (f"%{mark}%",)).fetchall()
        rows += conn.execute("SELECT tenant_id FROM beliefs WHERE object LIKE ?", (f"%{mark}%",)).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def test_a_workers_real_tool_call_lands_in_the_askers_space(matrix, monkeypatch) -> None:
    mark = matrix.plant("scribe").a
    assert _scribe_stores(matrix, monkeypatch, mark) == [Matrix.SPACE_A]


def test_a_workers_memory_store_is_refused(matrix, monkeypatch) -> None:
    """A swarm worker has no user message, so no user asked it to remember:
    its note is refused before anything is written (finding 1)."""
    mark = matrix.plant("scribe").a
    assert _scribe_stores(matrix, monkeypatch, mark, tool="memory_store") == []


def test_control_a_worker_run_bound_to_nothing_stores_nothing_in_the_askers_space(matrix, monkeypatch) -> None:
    """The worker's scope taken out: its tool call is bound to no space."""
    import contextlib

    import kazma_core.swarm.worker_dispatch as worker_dispatch

    monkeypatch.setattr(worker_dispatch, "_worker_scope", lambda tenant_id, asker: contextlib.nullcontext)
    mark = matrix.plant("scribe").a
    assert Matrix.SPACE_A not in _scribe_stores(matrix, monkeypatch, mark)


async def test_a_chat_button_pressed_on_the_loop_reads_whose_request_off_it(enforced, monkeypatch) -> None:
    """A chat app's button handler runs on the loop: whose request it is (the
    identity store) is read in a thread (``resolve_from_callback``, declared
    in the loop-stall gate)."""
    import threading

    from kazma_core.swarm import shared_approvals

    loop_thread = threading.get_ident()
    read_in: list[int] = []

    def whose(approval_id: str) -> tuple[str, str]:
        read_in.append(threading.get_ident())
        return "", ""

    monkeypatch.setattr(shared_approvals, "whose", whose)
    assert shared_approvals.decide_from_button("swarm_approve_req-on-the-loop", platform="telegram") \
        == "req-on-the-loop"
    for _ in range(200):
        if read_in:
            break
        await asyncio.sleep(0.01)
    assert read_in and loop_thread not in read_in, read_in


# ── the S1-16 follow-up: one check inside the shared task actions ────────


def _owner_retries_from_the_supervisor(matrix: Matrix, monkeypatch, *, bound: bool = True
                                       ) -> tuple[dict[str, Any], Any]:
    """The run supervisor's Retry (``runs.adapters.swarm``) of person A's
    failed task, pressed by the owner in the owner's space (or with nothing
    bound): its outcome, and the retried task as the store holds it."""
    import kazma_gateway.agent_handler as agent_handler
    from kazma_core.runs.adapters.swarm import ADAPTER

    from tests._swarm_space_cases import world

    async def no_output_target(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(agent_handler, "_maybe_send_to_output_target", no_output_target)
    with world(matrix) as w:
        task = w.plant("a", w.matrix.plant("supervised").a, status="failed")

        async def press() -> Any:
            if not bound:
                return await ADAPTER.act(task.id, "retry", actor="owner")
            with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
                return await ADAPTER.act(task.id, "retry", actor="owner")

        outcome = w.client.portal.call(press)
        w.settle()
        retried = None
        if outcome.ok:
            new_id = outcome.message.rsplit("'", 2)[-2]
            retried = w.store.get_task(new_id)
        return {"ok": outcome.ok, "code": outcome.code}, retried


def test_the_supervisors_retry_never_takes_a_members_task_into_the_owners_space(matrix, monkeypatch) -> None:
    outcome, retried = _owner_retries_from_the_supervisor(matrix, monkeypatch)
    assert outcome == {"ok": False, "code": "not_found"}, outcome  # another space's: as an unknown one
    assert retried is None


def test_control_without_the_shared_check_the_retry_moves_the_task(matrix, monkeypatch) -> None:
    """The hole the review found: the supervisor's Retry had no check, and
    the admission stamp put the retried task in the owner's space."""
    from kazma_ui.swarm_panel import task_actions

    monkeypatch.setattr(task_actions, "task_refusal", lambda engine, task_id: None)
    outcome, retried = _owner_retries_from_the_supervisor(matrix, monkeypatch)
    assert outcome["ok"], outcome
    assert (retried.metadata or {}).get("tenant_id") == OWNER_SPACE_ID  # moved


def test_the_supervisors_retry_with_nothing_bound_is_refused_once_enforced(matrix, monkeypatch) -> None:
    outcome, retried = _owner_retries_from_the_supervisor(matrix, monkeypatch, bound=False)
    assert outcome == {"ok": False, "code": "no_space"} and retried is None


def test_not_enforced_the_shared_check_asks_nothing(matrix) -> None:
    from kazma_ui.swarm_panel.task_actions import task_refusal

    assert not scope.spaces_enforced()
    assert task_refusal(SimpleNamespace(), "any-task") is None  # nothing read


@pytest.mark.parametrize("verb", ("cancel", "retry"))
def test_in_a_shared_space_the_shared_action_refuses_a_member_who_did_not_ask(verb, matrix, shared) -> None:
    """The check is inside the shared action: the supervisor's button, not
    only the page's route, refuses B for A's task."""
    from kazma_core.runs.adapters.swarm import ADAPTER

    from tests._swarm_space_cases import world

    with world(matrix) as w:
        task = w.plant("a", w.matrix.plant(verb).a, status="paused" if verb == "cancel" else "failed",
                       space=shared)
        before = w.held(task)

        async def as_b() -> Any:
            with scope.bind_scope(shared, matrix.account_b):
                return await ADAPTER.act(task.id, verb, actor="b")

        outcome = w.client.portal.call(as_b)
        assert (outcome.ok, outcome.code) == (False, "not_yours"), outcome
        assert w.held(task) == before


# ── the S1-16 follow-up: dispatch_swarm is the admin's, as the routes ────


def _dispatched_by_tool(matrix: Matrix, who: str) -> tuple[str, list[Any]]:
    from kazma_core.agent.tool_refusal import RefusedBeforeEffect

    from tests._swarm_space_cases import world

    with world(matrix) as w:
        mark = w.matrix.plant("admintool").a
        try:
            answer = w.tool(who, "dispatch_swarm", prompt=f"task {mark}", worker="probe")
        except RefusedBeforeEffect as exc:
            answer = f"refused: {exc}"
        w.settle()
        stored = [t for t in w.store.list_tasks(metadata_filter={"tenant_id": "*"}, page_size=100)
                  if mark in t.prompt]
        return answer, stored


def test_with_sign_in_on_dispatch_swarm_is_the_admins_once_enforced(matrix, monkeypatch) -> None:
    from kazma_core.swarm.single_operator import ADMIN_ONLY_REFUSAL

    monkeypatch.setenv("KAZMA_SECRET", "s" * 40)
    answer, stored = _dispatched_by_tool(matrix, "a")
    assert ADMIN_ONLY_REFUSAL in answer and stored == [], answer


def test_control_dispatch_swarm_with_the_read_rule_only(matrix, monkeypatch) -> None:
    import kazma_core.swarm.single_operator as single_operator

    monkeypatch.setenv("KAZMA_SECRET", "s" * 40)
    monkeypatch.setattr(single_operator, "swarm_write_refusal", single_operator.swarm_refusal)
    answer, stored = _dispatched_by_tool(matrix, "a")
    assert "dispatched" in answer and len(stored) == 1, answer


def test_the_admin_rule_follows_the_routes(matrix, enforced, monkeypatch) -> None:
    """Sign-in off: no admin role to require (the routes are open too);
    sign-in on: the owner only. Not enforced: the read rule, as before (the
    matrix's platform users make it the older multi-user refusal)."""
    from kazma_core.swarm.single_operator import ADMIN_ONLY_REFUSAL, swarm_refusal, swarm_write_refusal

    with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
        assert swarm_write_refusal() is None
        monkeypatch.setenv("KAZMA_SECRET", "s" * 40)
        assert swarm_write_refusal() == ADMIN_ONLY_REFUSAL
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert swarm_write_refusal() is None
    monkeypatch.setattr(scope, "_enforced", False)
    with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
        assert swarm_write_refusal() == swarm_refusal()


# ── the S1-16 follow-up: an approval whose person cannot be read ─────────


def test_an_approval_whose_person_cannot_be_read_answers_503_and_waits(matrix, shared, monkeypatch) -> None:
    import kazma_core.swarm.single_operator as single_operator
    from kazma_core.swarm import shared_approvals

    from tests._swarm_space_cases import world

    def unreadable(space: str, asker: str | None) -> str:
        raise identity.IdentityUnavailable("the identity store could not be read: PoolTimeout")

    with world(matrix) as w:
        request = w.approval("a", w.matrix.plant("unreadable").a, space=shared)
        monkeypatch.setattr(single_operator, "_acts_for", unreadable)
        answer = w.call("a", "POST", f"/api/swarm/approvals/{request}/decide", space=shared,
                        json={"decision": "approve"})
        assert answer.status_code == 503 and answer.json()["error"] == "identity_unavailable", answer.text
        assert shared_approvals.describe(request)["status"] == "pending"


# ── the S1-16 follow-up: the owner's result waits through an outage ──────


def _owners_result_with_identity_failing(monkeypatch, failures: int) -> list[str]:
    """The owner's chat-app task finishes while whose it is cannot be read
    *failures* times: the chats the result reached."""
    import kazma_core.shutdown as shutdown
    import kazma_core.swarm.single_operator as single_operator
    import kazma_core.tools.send_message as send_message
    from kazma_core.swarm import chat_delivery

    sent: list[str] = []
    left = [failures]

    async def send(target: str, text: str, **_kwargs: Any) -> str:
        sent.append(target)
        return f"sent:{target}"

    def flaky(task: Any) -> bool:
        if left[0]:
            left[0] -= 1
            raise identity.IdentityUnavailable("the identity store could not be read: PoolTimeout")
        return True

    async def no_wait(_seconds: float) -> bool:
        return False

    monkeypatch.setattr(send_message, "send_message", send)
    monkeypatch.setattr(single_operator, "_task_for_the_owner", flaky)
    monkeypatch.setattr(shutdown, "sleep_or_shutdown", no_wait)
    result = SimpleNamespace(status="success", aggregated_output="done", synthesized_output="", error="",
                             duration_seconds=1.0, worker_results=[])
    owners = SimpleNamespace(id="t-owner-" + uuid.uuid4().hex[:6],
                             metadata={"tenant_id": OWNER_SPACE_ID, "asker": OWNER_ACCOUNT_ID})
    asyncio.run(chat_delivery._deliver(owners, result, "telegram", "telegram:777"))
    return sent


def test_the_owners_result_is_held_while_whose_it_is_cannot_be_read(enforced, monkeypatch) -> None:
    assert _owners_result_with_identity_failing(monkeypatch, failures=3) == ["telegram:777"]


def test_control_dropped_at_the_first_failure_the_owners_result_is_lost(enforced, monkeypatch) -> None:
    """The delivery as before the review: one unreadable answer kept the
    owner's result in the Swarm panel for good."""
    import kazma_core.swarm.single_operator as single_operator

    async def drop_at_once(task: Any, *, label: str) -> bool:
        try:
            return await asyncio.to_thread(single_operator._task_for_the_owner, task)
        except identity.IdentityUnavailable:
            return False

    monkeypatch.setattr(single_operator, "wait_for_owner_answer", drop_at_once)
    assert _owners_result_with_identity_failing(monkeypatch, failures=1) == []


def test_a_result_held_past_the_limit_stays_in_the_panel(enforced, monkeypatch) -> None:
    import kazma_core.swarm.single_operator as single_operator

    monkeypatch.setattr(single_operator, "OWNER_ANSWER_WAIT_S", 0.0)
    assert _owners_result_with_identity_failing(monkeypatch, failures=1) == []


# ── the S1-17 follow-up: a foreground dispatch answers with its task done ─


def _foreground_dispatch_while_held(matrix: Matrix, monkeypatch) -> list[str]:
    """A dispatches in the foreground while whose the task is cannot be read
    yet (the hand-over held, up to 3 s here, hours in an outage): the order
    in which the page got its answer and the hand-over was decided."""
    import threading

    import kazma_core.swarm.single_operator as single_operator

    from tests._swarm_space_cases import world

    order: list[str] = []
    released = threading.Event()

    async def held(task: Any, *, label: str) -> bool:
        await asyncio.to_thread(released.wait, 3.0)
        order.append("hand-over decided")
        return False

    async def decided() -> None:
        for _ in range(250):
            if "hand-over decided" in order:
                return
            await asyncio.sleep(0.02)

    monkeypatch.setattr(single_operator, "wait_for_owner_answer", held)
    with world(matrix) as w:
        answer = w.call("a", "POST", "/api/swarm/dispatch", json={"task": "a task", "workers": ["probe"]})
        order.append("answered")
        assert answer.status_code == 200 and answer.json()["result_status"] == "success", answer.text
        released.set()
        w.client.portal.call(decided)
    return order


def test_a_foreground_dispatch_answers_while_its_result_is_held(matrix, monkeypatch) -> None:
    assert _foreground_dispatch_while_held(matrix, monkeypatch) == ["answered", "hand-over decided"]


def _routing_breaks(monkeypatch) -> tuple[int, list[str]]:
    """One account, a foreground dispatch whose result cannot be formatted:
    the page's status and what the output target was told."""
    from unittest.mock import AsyncMock, MagicMock

    import kazma_gateway.agent_handler as agent_handler
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.swarm import TaskResult, WorkerResult
    from kazma_ui.services import SwarmService
    from kazma_ui.swarm_panel import create_swarm_router, routes_tasks

    told: list[str] = []

    async def output_target(manager: Any, text: str, *, is_html: bool = False) -> bool:
        told.append(text)
        return True

    def broken(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("the result could not be formatted")

    engine = MagicMock()
    engine.get_worker.return_value = MagicMock()
    engine.dispatch = AsyncMock(return_value=TaskResult(
        task_id="t1", status="success", aggregated_output="done",
        worker_results=[WorkerResult(worker="w1", task_id="t1", status="success", output="done")]))
    monkeypatch.setattr(agent_handler, "_maybe_send_to_output_target", output_target)
    monkeypatch.setattr(routes_tasks, "tg_quote", broken)
    monkeypatch.setattr(SwarmService, "resolve_engine", lambda self, swarm_manager=None: engine)
    app = FastAPI()
    app.include_router(create_swarm_router(templates=None))
    with TestClient(app, raise_server_exceptions=False) as client:
        answer = client.post("/api/swarm/dispatch", json={"workers": ["w1"], "task": "t", "background": False})
    return answer.status_code, told


def test_a_result_that_cannot_be_routed_sends_the_failure_message(monkeypatch) -> None:
    status, told = _routing_breaks(monkeypatch)
    assert status == 500 and len(told) == 1 and "failed" in told[0], (status, told)


def test_control_routing_outside_the_handler_tells_no_one(monkeypatch) -> None:
    """The routing as the follow-up first moved it (74ddd48b7): out of the
    try, an exception there sent no message."""
    from kazma_ui.swarm_panel import routes_tasks

    async def untold(swarm_task: Any, step: Any, *, tell: Any) -> Any:
        return await step()

    monkeypatch.setattr(routes_tasks, "_reported", untold)
    status, told = _routing_breaks(monkeypatch)
    assert status == 500 and told == []


def test_control_a_hand_over_awaited_in_the_request_holds_the_page(matrix, monkeypatch) -> None:
    """The route as before: the page's answer waited for the hand-over."""
    from kazma_ui.swarm_panel import routes_tasks

    async def inline(deliver: Any, *, label: str, later: bool) -> None:
        await deliver()

    monkeypatch.setattr(routes_tasks, "_hand_over_result", inline)
    assert _foreground_dispatch_while_held(matrix, monkeypatch) == ["hand-over decided", "answered"]
