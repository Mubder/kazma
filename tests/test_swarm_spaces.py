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
* the swarm's writes are a member's own work in their space (``SPACE`` in
  ``route_roles``), not the admin's.

Each guard is taken out as a control, and the control shows what it stops:
the cases that rest on it fail. The route and tool cases are in
``tests/_swarm_space_cases.py``.
"""

from __future__ import annotations

import asyncio
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

    return tenant_scope(tenant_id or "default")


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


def test_a_task_whose_asker_left_runs_as_the_spaces_own_work(matrix, enforced) -> None:
    identity.create_space("Shared", created_by=OWNER_ACCOUNT_ID, space_id="swarm-shared")
    identity.add_member("swarm-shared", matrix.account_a)
    assert identity.work_scope("swarm-shared", matrix.account_a) == ("swarm-shared", matrix.account_a)
    identity.remove_member("swarm-shared", matrix.account_a)
    assert identity.work_scope("swarm-shared", matrix.account_a) == (
        "swarm-shared", identity.acting_account("swarm-shared"))
    assert identity.work_scope("no-such-space", matrix.account_a) is None


def test_not_enforced_a_worker_runs_in_its_tenant_as_before(matrix) -> None:
    from kazma_core.swarm.worker_dispatch import _worker_scope
    from kazma_core.tenant_context import get_current_tenant_id

    with _worker_scope(None, None):
        assert get_current_tenant_id() == OWNER_SPACE_ID and scope.current_scope() is None


def test_work_bound_to_no_space_is_refused_once_enforced(matrix, enforced) -> None:
    from kazma_core.swarm.single_operator import NO_SPACE_REFUSAL, swarm_refusal

    assert swarm_refusal() == NO_SPACE_REFUSAL
    with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
        assert swarm_refusal() is None


def test_a_members_swarm_write_needs_no_admin_once_enforced(monkeypatch) -> None:
    from kazma_ui.swarm_panel.route_roles import ADMIN, SPACE, role_for

    assert role_for("POST", "/api/swarm/dispatch") in (SPACE, ADMIN)
    monkeypatch.setattr(scope, "_enforced", False)
    assert role_for("POST", "/api/swarm/dispatch") == ADMIN, "not enforced: the admin's, as before"


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


def test_control_the_whole_prompt_shows_the_owners_lessons_everywhere(lessons, matrix, monkeypatch) -> None:
    """The reader as before S1-15: the worker's prompt whole, in any space."""
    import kazma_core.swarm.worker as worker_mod

    monkeypatch.setattr(worker_mod, "_prompt_in_this_space", lambda name, prompt: prompt)
    learned = "You are probe.\n\n[SelfImprovement] Cite the source of every figure."
    assert "Cite the source" in _prompt_in(Matrix.SPACE_A, matrix.account_a, learned)


def test_with_no_space_bound_a_worker_gets_no_lessons(lessons) -> None:
    from kazma_core.swarm.worker import _prompt_in_this_space

    learned = "You are probe.\n\n[SelfImprovement] Cite the source of every figure."
    assert _prompt_in_this_space("probe", learned) == "You are probe."
