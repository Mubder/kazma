"""What a fired scheduled task may do (Scheduled audit SCH-S05, decision D12).

A fired task runs with nobody watching. It ran with the whole agent minus the
danger tools -- every write-tier tool (sub-agents, the swarm, research, memory
writes) -- and, with approvals switched off, everything. Now:

* a job declares its capability set at booking (``kind``: a reminder runs no
  model and no tool; ``tools``: read-only by default, read-and-write opt-in),
  and the approval card says what it will do when it fires;
* the fire's graph is OFFERED exactly the declared tools, and its executor
  REFUSES any other name the model produces;
* an agent task does not fire while approvals are off, unless the operator
  opts in (``safety.cron_fire_without_hitl``, which the agent cannot set);
* the stored prompt reaches the model labelled as a stored instruction.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from kazma_core.cron.capabilities import cron_fire_graph, _declared_tool_names
from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob, SQLiteCronStore

ROOT = Path(__file__).resolve().parents[1]


def _registry():
    from kazma_core.agent.tool_registry import LocalToolRegistry

    return LocalToolRegistry(include_builtins=True)


def _names(defs):
    return sorted(str(d.get("function", d).get("name", d.get("name", ""))) for d in defs)


class _Agent:
    """Enough of KazmaAgent for build_child_graph."""

    def __init__(self, tools):
        self.tools = tools
        self.llm = self.cost_breaker = self.authority = self.tracer = object()
        self.system_prompt = "sys"
        self._snapshot_recorder = None
        self.config = type("C", (), {"raw": {}})()


@pytest.mark.parametrize("capabilities", ["read", "read_write"])
def test_the_fire_is_offered_exactly_its_declared_tools(capabilities):
    from kazma_core.agent_runner import DeclaredToolExecutor, KazmaAgent
    from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS, get_tool_tier

    registry = _registry()
    agent = _Agent(registry)
    agent.build_child_graph = lambda **kw: KazmaAgent.build_child_graph(agent, **kw)
    with patch("kazma_core.agent.graph_builder.build_supervisor_graph", return_value=object()) as build, \
            patch("kazma_core.safety.hitl.get_hitl_config", return_value={"enabled": True}):
        cron_fire_graph(agent, capabilities=capabilities)
    kwargs = build.call_args.kwargs
    offered = _names(kwargs["tool_definitions"])
    declared = _declared_tool_names(list(registry.get_tool_definitions()), capabilities)
    assert offered == declared and offered
    tiers = {"read"} if capabilities == "read" else {"read", "write"}
    assert all(get_tool_tier(n) in tiers for n in offered)
    assert not set(offered) & set(ALWAYS_HITL_TOOLS)
    assert not {"schedule_task", "send_message", "shell_exec", "file_write"} & set(offered)
    if capabilities == "read":
        assert not {"dispatch_swarm", "spawn_agent", "memory_store"} & set(offered)
    else:
        assert {"dispatch_swarm", "memory_store"} <= set(offered)
    assert isinstance(kwargs["tool_executor"], DeclaredToolExecutor)
    assert kwargs["hitl_config"]["auto_deny"] is True  # no checkpointer: approvals cannot be answered


def test_negative_control_the_old_builder_offered_the_whole_registry():
    from kazma_core.agent_runner import KazmaAgent

    registry = _registry()
    agent = _Agent(registry)
    with patch("kazma_core.agent.graph_builder.build_supervisor_graph", return_value=object()) as build, \
            patch("kazma_core.safety.hitl.get_hitl_config", return_value={"enabled": True}):
        KazmaAgent.build_child_graph(agent)  # what the app's cron builder called
    offered = set(_names(build.call_args.kwargs["tool_definitions"]))
    assert {"dispatch_swarm", "memory_store", "file_write"} <= offered


async def test_the_executor_refuses_a_tool_it_was_not_offered():
    from kazma_core.agent_runner import DeclaredToolExecutor

    ran: list[str] = []

    class _Inner:
        async def execute(self, name, arguments):
            ran.append(name)
            return {"content": "ok"}

    executor = DeclaredToolExecutor(_Inner(), allowed=["web_search"])
    assert (await executor.execute("web_search", {}))["content"] == "ok"
    refused = await executor.execute("dispatch_swarm", {"task": "x"})
    assert refused["is_error"] and "not available to this scheduled task" in refused["content"]
    assert ran == ["web_search"]


def test_the_app_builds_fires_through_the_declared_set():
    src = (ROOT / "kazma-ui/kazma_ui/app.py").read_text(encoding="utf-8")
    block = src.split("def _cron_graph_builder", 1)[1].split("self.cron_store = SQLiteCronStore()", 1)[0]
    assert "cron_fire_graph(agent, capabilities=" in block
    assert "build_child_graph()" not in block


class _Recording:
    def __init__(self):
        self.built: list[dict] = []
        self.states: list[dict] = []

    def builder(self, **kw):
        self.built.append(kw)
        outer = self

        class _G:
            async def ainvoke(self, state, config=None):
                outer.states.append(state)
                return {"messages": [*state["messages"], {"role": "assistant", "content": "done"}]}

        return _G()


async def _job(tmp_path, *, kind="task", capabilities="read"):
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    await store.insert(ScheduledJob(
        job_id="cron-cap", timing="2026-10-14T09:00", prompt="summarize the overnight alerts",
        platform="telegram", thread_id="t", next_run=(datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
        kind=kind, capabilities=capabilities, delivery_target=""))
    return store


async def test_a_fire_passes_its_capabilities_and_labels_the_stored_instruction(tmp_path):
    store = await _job(tmp_path, capabilities="read_write")
    rec = _Recording()
    try:
        sched = CronScheduler(store=store, graph_builder=rec.builder)
        await sched._execute(await store.get("cron-cap"))
        assert rec.built == [{"capabilities": "read_write"}]
        system = rec.states[0]["messages"][0]["content"]
        assert "STORED instruction" in system and "cron-cap" in system and "read and write tools" in system
        assert rec.states[0]["messages"][1] == {"role": "user", "content": "summarize the overnight alerts"}
    finally:
        await store.close()


async def test_a_task_does_not_fire_while_approvals_are_off(tmp_path, monkeypatch):
    import kazma_core.cron.settings as cron_settings

    monkeypatch.setattr(cron_settings, "hitl_enabled", lambda: False)
    store = await _job(tmp_path)
    rec = _Recording()
    try:
        sched = CronScheduler(store=store, graph_builder=rec.builder)
        await sched._execute(await store.get("cron-cap"))
        job = await store.get("cron-cap")
        assert rec.built == []  # never built, never run
        assert job.status == JobStatus.FAILED and job.last_outcome == "hitl_off"
        assert "Approvals are switched off" in job.last_result
    finally:
        await store.close()


async def test_the_operator_may_opt_in(tmp_path, monkeypatch):
    import kazma_core.cron.settings as cron_settings
    from kazma_core.config_store import get_config_store

    monkeypatch.setattr(cron_settings, "hitl_enabled", lambda: False)
    get_config_store().set(cron_settings.FIRE_WITHOUT_HITL_KEY, True)
    store = await _job(tmp_path)
    rec = _Recording()
    try:
        sched = CronScheduler(store=store, graph_builder=rec.builder)
        await sched._execute(await store.get("cron-cap"))
        assert rec.built  # it fired
    finally:
        get_config_store().delete(cron_settings.FIRE_WITHOUT_HITL_KEY)
        await store.close()


def test_the_agent_cannot_opt_itself_in():
    from kazma_core.cron.settings import FIRE_WITHOUT_HITL_KEY
    from kazma_core.safety.protected_config import is_protected_config_key

    assert is_protected_config_key(FIRE_WITHOUT_HITL_KEY)


async def test_a_reminder_runs_no_model_even_with_approvals_off(tmp_path, monkeypatch):
    import kazma_core.cron.settings as cron_settings

    monkeypatch.setattr(cron_settings, "hitl_enabled", lambda: False)
    store = await _job(tmp_path, kind="reminder")
    rec = _Recording()
    try:
        sched = CronScheduler(store=store, graph_builder=rec.builder)
        await sched._execute(await store.get("cron-cap"))
        assert rec.built == []
        # (No address here: the reminder is undeliverable, but it did not fail as a task.)
        assert (await store.get("cron-cap")).last_outcome in ("undeliverable", "delivering", "delivered")
    finally:
        await store.close()


# ── the approval card ────────────────────────────────────────────────────


async def test_the_booking_card_says_what_the_job_will_do():
    from kazma_core.cron.card import scheduling_card_notes

    notes = await scheduling_card_notes([
        {"name": "schedule_task", "arguments": {"timing": "daily at 9am", "prompt": "x", "kind": "task"}},
        {"name": "schedule_task", "arguments": {"timing": "2h", "prompt": "x"}},
        {"name": "schedule_task", "arguments": {"timing": "2h", "prompt": "x", "kind": "task",
                                                "tools": "read_write"}},
    ])
    assert "read-only tools" in notes[0] and "repeats (daily at 9am)" in notes[0]
    assert "runs no model and no tool" in notes[1] and "runs once" in notes[1]
    assert "read and write tools" in notes[2]


async def test_the_edit_card_shows_the_prompt_it_replaces(tmp_path, monkeypatch):
    import kazma_core.cron.scheduler as sched_mod
    from kazma_core.cron.card import scheduling_card_notes

    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    try:
        sched = CronScheduler(store=store)
        monkeypatch.setattr(sched_mod, "get_cron_scheduler", lambda: sched)
        booked = await sched.schedule("2h", "the original instruction", delivery_target="telegram:1")
        notes = await scheduling_card_notes([{"name": "edit_scheduled", "arguments": {
            "job_id": booked["job_id"], "prompt": "the new instruction"}}])
        assert "Prompt now: the original instruction" in notes[0]
        assert "Prompt after: the new instruction" in notes[0]
    finally:
        await store.close()


def test_the_tool_worker_adds_the_card_notes():
    src = (ROOT / "kazma-core/kazma_core/agent/graph_tool_worker.py").read_text(encoding="utf-8")
    assert "_notes += await scheduling_card_notes(danger_tools)" in src


def test_the_edit_resolver_checks_the_callers_jobs(tmp_path, monkeypatch):
    """edit_scheduled had no resolver (act None): any id passed. Now an id
    that is not a live job of the caller's tenant is refused before the card."""
    import kazma_core.safety.commitment.constraints as constraints
    from kazma_core.safety.commitment import authorize_effect

    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setattr(constraints, "cron_pending_jobs",
                        lambda thread_id=None, tenant_id="default", statuses=None: [
                            {"job_id": "cron-mine", "prompt": "mine", "status": "pending"}])
    kw = dict(user_text="move it", request_at=datetime.now(UTC), memory_beliefs=[], thread_id="t1")
    assert authorize_effect("edit_scheduled", {"job_id": "cron-mine", "timing": "2h"}, **kw).decision == "allow"
    d = authorize_effect("edit_scheduled", {"job_id": "cron-theirs", "prompt": "x"}, **kw)
    assert d.decision == "deny" and "not a live scheduled job" in d.reason


async def test_negative_control_without_the_thread_the_resolvers_never_ran(tmp_path, monkeypatch):
    """The tool worker passed no thread id, so cancel_job / edit_job ran
    audit-only on the graph path; the gate passes it now."""
    src = (ROOT / "kazma-core/kazma_core/agent/graph_tool_worker.py").read_text(encoding="utf-8")
    call = src.split("_dec = _authz(", 1)[1].split("if _dec.decision", 1)[0]
    assert 'thread_id=str(state.get("thread_id")' in call
    from kazma_core.safety.commitment import authorize_effect

    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    d = authorize_effect("edit_scheduled", {"job_id": "cron-theirs"}, user_text="x",
                         request_at=datetime.now(UTC), memory_beliefs=[])  # no thread: the old call
    assert d.decision == "allow"
    await asyncio.sleep(0)
