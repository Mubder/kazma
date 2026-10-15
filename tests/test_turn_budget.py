"""A turn's wall-clock budget is one rule on every surface (2026-10-15).

Full audit 2026-09-30: the chat apps, ``kazma ask`` and reminders went through
``run_agent_turn``, which honoured ``KAZMA_TURN_TIMEOUT_SECONDS``; the web chat
(the SSE package) never applied it, the standalone agent runner read the same
variable with a 600 s default of its own, and the resume after an approval was
unbounded on every surface. Now ``kazma_core.agent.turn_budget`` answers once
-- the variable, the setting ``agent.turn_timeout_seconds``, ``/long`` -- and
every graph run sits inside ``turn_segment_budget``.

The graph here is a real LangGraph graph on the product's state schema and
node names (as ``tests/_tt_graph.py``), checkpointed by the product's
serializer, driven through the product's own entry points: ``run_agent_turn``
for the chat apps, ``_stream_langgraph_events`` (the web pump and the
approval resume) for the web. Each behaviour has its negative control.
"""

from __future__ import annotations

import ast
import asyncio
import functools
import json
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from tests._tt_graph import run

REPO = Path(__file__).resolve().parents[1]
ENV = "KAZMA_TURN_TIMEOUT_SECONDS"
KEY = "agent.turn_timeout_seconds"


# ── the graph ────────────────────────────────────────────────────────────────


class Script:
    """What the scripted supervisor does, and what happened to it.

    A goal ``"tool:target"`` asks for ``tool`` behind an approval and answers
    after it; anything else answers at once. ``plan_s`` / ``answer_s`` are how
    long the model "thinks" before each step.
    """

    def __init__(self, *, plan_s: float = 0.0, answer_s: float = 0.0) -> None:
        self.plan_s = plan_s
        self.answer_s = answer_s
        self.events: list[str] = []

    async def think(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self.events.append("thinking")
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            self.events.append("cancelled")
            raise
        self.events.append("finished")


def build_graph(script: Script, *, checkpointer: bool = True) -> Any:
    from kazma_core.agent.state import NodeName, SupervisorState
    from kazma_core.checkpoint_serde import kazma_checkpoint_serde
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, StateGraph
    from langgraph.types import interrupt

    async def supervisor(state: dict[str, Any]) -> dict[str, Any]:
        goal = str(state.get("active_goal") or "")
        messages = list(state.get("messages") or [])
        tool, sep, target = goal.partition(":")
        if state.get("tool_calls_done") or not sep:
            await script.think(script.answer_s)
            messages.append({"role": "assistant", "content": f"Answer to {goal}"})
            return {"next_node": NodeName.RESPOND, "tool_calls_pending": [], "messages": messages}
        await script.think(script.plan_s)
        call = {"id": f"call-{uuid.uuid4().hex[:8]}", "name": tool, "arguments": {"target": target}}
        messages.append({"role": "assistant", "content": "", "tool_calls": [{
            "id": call["id"], "type": "function",
            "function": {"name": tool, "arguments": json.dumps(call["arguments"])},
        }]})
        return {"next_node": NodeName.TOOL_WORKER, "messages": messages,
                "tool_calls_pending": [call], "tool_calls_done": []}

    def tool_worker(state: dict[str, Any]) -> dict[str, Any]:
        pending = list(state.get("tool_calls_pending") or [])
        decision = interrupt({
            "type": "hitl_approval", "kind": "security",
            "tool": pending[0]["name"] if pending else "",
            "args": pending[0]["arguments"] if pending else {},
            "tools": [p["name"] for p in pending], "message": "approve?",
        })
        ok = isinstance(decision, dict) and bool(decision.get("approved"))
        messages = list(state.get("messages") or [])
        done = []
        for p in pending:
            content = "ran" if ok else "denied"
            messages.append({"role": "tool", "tool_call_id": p["id"], "content": content})
            done.append({"tool_call_id": p["id"], "name": p["name"], "content": content})
        return {"tool_calls_done": done, "tool_calls_pending": [], "messages": messages,
                "next_node": NodeName.SUPERVISOR}

    def respond(state: dict[str, Any]) -> dict[str, Any]:
        return {"next_node": "end", "messages": list(state.get("messages") or [])}

    def route(state: dict[str, Any]) -> str:
        nxt = state.get("next_node")
        if nxt in (NodeName.TOOL_WORKER, NodeName.SUPERVISOR):
            return str(nxt)
        return NodeName.RESPOND

    g = StateGraph(SupervisorState)
    g.add_node(NodeName.SUPERVISOR, supervisor)
    g.add_node(NodeName.TOOL_WORKER, tool_worker)
    g.add_node(NodeName.RESPOND, respond)
    g.set_entry_point(NodeName.SUPERVISOR)
    g.add_conditional_edges(NodeName.SUPERVISOR, route, {
        NodeName.SUPERVISOR: NodeName.SUPERVISOR,
        NodeName.TOOL_WORKER: NodeName.TOOL_WORKER,
        NodeName.RESPOND: NodeName.RESPOND,
    })
    g.add_edge(NodeName.TOOL_WORKER, NodeName.SUPERVISOR)
    g.add_edge(NodeName.RESPOND, END)
    saver = InMemorySaver(serde=kazma_checkpoint_serde()) if checkpointer else None
    return g.compile(checkpointer=saver)


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def _turn_input(thread_id: str, text: str) -> dict[str, Any]:
    from kazma_core.agent.state import initial_supervisor_state

    state = dict(initial_supervisor_state(thread_id=thread_id, max_iterations=15))
    state.pop("scratchpad", None)
    state["messages"] = [{"role": "user", "content": text}]
    state["active_goal"] = text
    return state


async def _stored(graph: Any, thread_id: str) -> Any:
    return await graph.aget_state(_config(thread_id))


def _last_text(snapshot: Any) -> str:
    messages = list((snapshot.values or {}).get("messages") or [])
    return str(messages[-1].get("content") or "") if messages else ""


# ── spies ────────────────────────────────────────────────────────────────────


@pytest.fixture
def alerts(monkeypatch) -> list[str]:
    """Every ops alert raised, by key (the real alert is never sent)."""
    from kazma_core.observability import ops_alerts

    raised: list[str] = []
    monkeypatch.setattr(ops_alerts, "alert", lambda key, *a, **k: raised.append(key) or True)
    return raised


@pytest.fixture
def closes(monkeypatch) -> list[dict[str, Any]]:
    """Every ``close_turn`` call (the real one still runs)."""
    from kazma_ui import turn_runtime

    calls: list[dict[str, Any]] = []
    real = turn_runtime.close_turn

    async def spy(*args: Any, **kwargs: Any) -> bool:
        calls.append(dict(kwargs))
        return await real(*args, **kwargs)

    monkeypatch.setattr(turn_runtime, "close_turn", spy)
    return calls


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


async def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return bool(predicate())


def _settle_tasks(before: set[asyncio.Task]) -> list[asyncio.Task]:
    """Tasks the turn started that still run. The SSE pump's stall watchdog
    is not one: it runs no graph and notices the pump ended at its next
    five-second tick."""
    me = asyncio.current_task()
    return [
        t for t in asyncio.all_tasks()
        if t not in before and t is not me and not t.done()
        and "_pump_watchdog" not in getattr(t.get_coro(), "__qualname__", "")
    ]


# ══ 1. the rule ══════════════════════════════════════════════════════════════


def test_no_limit_by_default(no_env):
    from kazma_core.agent.turn_budget import resolve_turn_budget, turn_timeout_setting

    assert resolve_turn_budget("t-default") is None
    assert turn_timeout_setting() == {"seconds": 0, "source": "default"}


def test_the_setting_sets_it_and_the_environment_wins(no_env, monkeypatch):
    from kazma_core.agent.turn_budget import TurnBudget, resolve_turn_budget
    from kazma_core.config_store import get_config_store

    get_config_store().set(KEY, 90, category="agent")
    assert resolve_turn_budget("t") == TurnBudget(90, "setting")
    monkeypatch.setenv(ENV, "30")
    assert resolve_turn_budget("t") == TurnBudget(30, "env")
    monkeypatch.setenv(ENV, "0")  # the environment says: no limit
    assert resolve_turn_budget("t") is None


def test_zero_in_the_setting_is_no_limit(no_env):
    from kazma_core.agent.turn_budget import resolve_turn_budget
    from kazma_core.config_store import get_config_store

    get_config_store().set(KEY, 0, category="agent")
    assert resolve_turn_budget("t") is None


def test_a_value_that_is_not_whole_seconds_is_ignored_and_said_once(no_env, monkeypatch, caplog):
    from kazma_core.agent import turn_budget
    from kazma_core.config_store import get_config_store

    monkeypatch.setattr(turn_budget, "_warned", set())
    get_config_store().set(KEY, 45, category="agent")
    monkeypatch.setenv(ENV, "ten minutes")
    with caplog.at_level("WARNING", logger="kazma_core.agent.turn_budget"):
        first = turn_budget.resolve_turn_budget("t")
        second = turn_budget.resolve_turn_budget("t")
    assert first == second == turn_budget.TurnBudget(45, "setting")
    assert sum("ten minutes" in r.getMessage() for r in caplog.records) == 1
    for bad in ("-1", "2.5", "86401", "true"):
        assert turn_budget.parse_turn_timeout(bad) is None
    assert turn_budget.parse_turn_timeout(True) is None


@pytest.mark.parametrize("mode", ["budget", "mission"])
def test_a_long_task_lifts_the_limit_for_its_chat_only(no_env, monkeypatch, mode):
    from kazma_core.agent.long_task import enable_long_task
    from kazma_core.agent.turn_budget import TurnBudget, resolve_turn_budget

    monkeypatch.setenv(ENV, "30")
    enable_long_task("t-long", actor="test", mode=mode)
    assert resolve_turn_budget("t-long") is None
    assert resolve_turn_budget("t-other") == TurnBudget(30, "env")


def test_an_unreadable_store_leaves_the_environments_limit(monkeypatch):
    import kazma_core.config_store as config_store
    from kazma_core.agent.turn_budget import TurnBudget, resolve_turn_budget

    def broken() -> Any:
        raise RuntimeError("database is away")

    monkeypatch.setattr(config_store, "get_config_store", broken)
    monkeypatch.setenv(ENV, "30")
    assert resolve_turn_budget("t") == TurnBudget(30, "env")
    monkeypatch.delenv(ENV)
    assert resolve_turn_budget("t") is None


@pytest.fixture
def settings_client(tmp_path, no_env):
    """The real Settings router on a store of its own."""
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_core.config_store import ConfigStore
    from kazma_ui.settings import create_settings_router

    cs = ConfigStore(db_path=str(tmp_path / "settings_api.db"))
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(
        MagicMock(), cs, Jinja2Templates(directory=str(tmp_path / "templates"))))
    client = TestClient(app)
    client.kazma_config_store = cs
    yield client
    cs.close()


def test_settings_shows_and_saves_the_limit(settings_client, monkeypatch):
    from kazma_core.agent.turn_budget import turn_timeout_setting

    got = settings_client.get("/api/settings/turn-timeout").json()
    assert got == {"seconds": 0, "source": "default", "max": 86400}
    saved = settings_client.put("/api/settings/single", json={"key": KEY, "value": "120"})
    assert saved.status_code == 200, saved.text
    assert settings_client.kazma_config_store.get(KEY) == 120
    assert turn_timeout_setting(settings_client.kazma_config_store) == {"seconds": 120, "source": "setting"}
    assert settings_client.get("/api/settings/turn-timeout").json()["seconds"] == 120
    monkeypatch.setenv(ENV, "30")
    got = settings_client.get("/api/settings/turn-timeout").json()
    assert (got["seconds"], got["source"]) == (30, "env")


@pytest.mark.parametrize("bad", ["forever", -1, 86401, "2.5", True])
def test_settings_refuses_what_the_limit_could_not_read(settings_client, bad):
    settings_client.put("/api/settings/single", json={"key": KEY, "value": 60})
    resp = settings_client.put("/api/settings/single", json={"key": KEY, "value": bad})
    assert resp.status_code == 400, resp.text
    assert settings_client.kazma_config_store.get(KEY) == 60


def test_a_restored_backup_goes_through_the_same_check():
    from kazma_core.settings_restore import classify
    from kazma_core.settings_validation import SettingRejected, validate_setting

    assert validate_setting(KEY, "90") == (90, "agent")
    with pytest.raises(SettingRejected):
        validate_setting(KEY, "an hour")
    assert classify(KEY) == "setting"


def test_the_reply_says_where_to_raise_the_limit():
    from kazma_core.agent.turn_budget import TurnBudget, _timed_out_text

    from_env = _timed_out_text(TurnBudget(30, "env"))
    from_setting = _timed_out_text(TurnBudget(30, "setting"))
    assert from_env.startswith("⚠️ Turn timed out after 30s") and ENV in from_env
    assert "Settings > System" in from_setting and ENV not in from_setting
    assert "/long" in from_setting


# ══ 2. the chat apps, the CLI, reminders: run_agent_turn ═════════════════════


def test_a_chat_app_turn_past_its_limit_is_cut_and_says_so(no_env, monkeypatch, alerts, closes):
    from kazma_core.agent.turn import run_agent_turn
    from kazma_core.agent.turn_budget import TurnBudget, _timed_out_text

    monkeypatch.setenv(ENV, "1")
    script = Script(answer_s=30)
    graph = build_graph(script)
    thread = "gw-telegram-" + uuid.uuid4().hex[:8]

    async def go():
        before = set(asyncio.all_tasks())
        started = time.monotonic()
        result = await run_agent_turn(graph=graph, thread_id=thread, state=_turn_input(thread, "q"))
        elapsed = time.monotonic() - started
        await asyncio.sleep(0.2)
        return result, elapsed, _settle_tasks(before), await _stored(graph, thread)

    result, elapsed, left, snap = run(go())
    text = _timed_out_text(TurnBudget(1, "env"))
    assert elapsed < 5, elapsed
    assert result.turn_failed and result.text == text and result.error == text
    # The gateway delivers the last assistant message of the state it gets.
    assert result.state["messages"][-1] == {"role": "assistant", "content": text}
    assert script.events == ["thinking", "cancelled"], "the run was not cancelled"
    assert left == [], left
    assert _last_text(snap) == text and tuple(snap.next) == ()
    assert snap.values.get("turn_failed") is True
    assert alerts == ["turn.timed_out"]
    assert len(closes) == 1 and not closes[0].get("interrupted")


def test_without_a_limit_the_same_turn_runs_past_it(no_env, alerts):
    """Negative control: the default is no limit, and this graph then runs
    past the second the turn above was given."""
    from kazma_core.agent.turn import run_agent_turn

    script = Script(answer_s=1.5)
    graph = build_graph(script)
    thread = "gw-telegram-" + uuid.uuid4().hex[:8]

    async def go():
        started = time.monotonic()
        result = await run_agent_turn(graph=graph, thread_id=thread, state=_turn_input(thread, "q"),
                                      persist=False)
        return result, time.monotonic() - started

    result, elapsed = run(go())
    assert elapsed >= 1.5 and result.text == "Answer to q" and not result.turn_failed
    assert script.events == ["thinking", "finished"] and alerts == []


def test_a_graph_without_a_checkpointer_still_gets_the_reply(no_env, monkeypatch, alerts):
    """A scheduled task's one-shot graph: nothing to write, the reply comes back."""
    from kazma_core.agent.turn import run_agent_turn

    monkeypatch.setenv(ENV, "1")
    script = Script(answer_s=30)
    graph = build_graph(script, checkpointer=False)

    result = run(run_agent_turn(graph=graph, thread_id="cron-x", state=_turn_input("cron-x", "q"),
                                persist=False))
    assert result.turn_failed and result.text.startswith("⚠️ Turn timed out after 1s")
    assert script.events == ["thinking", "cancelled"] and alerts == ["turn.timed_out"]


def test_the_non_stop_envelope_runs_inside_the_budget(no_env, monkeypatch, alerts):
    """With ``agent.nonstop`` on, a timed-out run is not resumed past the
    budget, and its invoke task is cancelled with it."""
    from kazma_core.agent.nonstop import NonStopConfig
    from kazma_core.agent.turn import run_agent_turn

    monkeypatch.setenv(ENV, "1")
    ns = NonStopConfig(enabled=True)
    ns.healing.backoff_base_seconds = 0.0
    script = Script(answer_s=30)
    graph = build_graph(script)
    thread = "gw-slack-" + uuid.uuid4().hex[:8]

    async def go():
        before = set(asyncio.all_tasks())
        started = time.monotonic()
        result = await run_agent_turn(graph=graph, thread_id=thread, state=_turn_input(thread, "q"),
                                      nonstop_config=ns, persist=False)
        elapsed = time.monotonic() - started
        await asyncio.sleep(0.2)
        return result, elapsed, _settle_tasks(before)

    result, elapsed, left = run(go())
    assert result.turn_failed and result.text.startswith("⚠️ Turn timed out after 1s")
    assert elapsed < 8, elapsed
    assert script.events == ["thinking", "cancelled"], script.events
    assert left == [] and alerts == ["turn.timed_out"]


def _old_supervised_invoke():
    """``supervised_invoke`` as it was: an outer cancellation re-raised and
    left the invoke task running."""
    import inspect

    from kazma_core.agent import supervisor_watchdog

    src = inspect.getsource(supervisor_watchdog)
    start = src.index("        except asyncio.CancelledError:\n            # Outer cancellation")
    end = src.index("            raise\n", start) + len("            raise\n")
    old = src[:start] + "        except asyncio.CancelledError:\n            raise\n" + src[end:]
    namespace: dict[str, Any] = {"__name__": "old_supervisor_watchdog"}
    exec(compile(old, "old_supervisor_watchdog", "exec"), namespace)  # noqa: S102 - the old code, as the control
    return namespace["supervised_invoke"]


@pytest.mark.parametrize("version", ["current", "old"])
def test_an_outer_cancellation_reaches_the_supervised_run(version):
    """The leak under the non-stop envelope: Stop, /abort or the budget
    cancelled the turn and the graph run went on in its own task."""
    from kazma_core.agent import supervisor_watchdog
    from kazma_core.agent.nonstop import NonStopConfig

    invoke = supervisor_watchdog.supervised_invoke if version == "current" else _old_supervised_invoke()
    script = Script()

    async def slow(graph, state, config):
        await script.think(30)
        return {}

    async def go():
        task = asyncio.create_task(invoke(
            None, {}, {"configurable": {"thread_id": "t"}},
            nonstop_config=NonStopConfig(enabled=True), turn_timeout=0, invoke_fn=slow,
        ))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.2)
        events = list(script.events)
        for leftover in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
            leftover.cancel()
        return events

    events = run(go())
    if version == "current":
        assert events == ["thinking", "cancelled"]
    else:
        assert events == ["thinking"], "the control must show the old leak"


# ══ 3. the web chat: the SSE pump and the approval resume ════════════════════


def _frames(raw: list[str]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for frame in raw:
        event, data = "", None
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                except ValueError:
                    data = None
        if event and isinstance(data, dict):
            out.append((event, data))
    return out


@pytest.fixture
def web_session(tmp_path, monkeypatch):
    """A web chat with one question and its open reply row."""
    from kazma_ui.active_turns import reset_active_turns
    from kazma_ui.session_manager import get_session_manager, reset_session_manager

    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: tmp_path)
    reset_session_manager()
    reset_active_turns()
    session = get_session_manager().get_or_create("sess-" + uuid.uuid4().hex[:8])
    session.messages = [{"role": "user", "content": "q"},
                        {"role": "assistant", "turn_id": "turn-tb", "content": ""}]
    get_session_manager().put(session)
    yield session.session_id
    reset_active_turns()


def _reply_row(session_id: str) -> dict[str, Any]:
    from kazma_ui.session_manager import get_session_manager

    rows = get_session_manager().get(session_id).messages
    return next(m for m in rows if m.get("role") == "assistant" and m.get("turn_id") == "turn-tb")


async def _web_segment(graph: Any, thread: str, session_id: str, input_state: Any) -> list:
    from kazma_ui.sse_chat import _stream_langgraph_events

    return _frames([f async for f in _stream_langgraph_events(
        graph, input_state, _config(thread),
        thread_id=thread, session_id=session_id, reply_turn_id="turn-tb",
    )])


async def _approve(graph: Any, thread: str) -> Any:
    from kazma_core.safety.commitment.resume import build_resume_command

    from tests._tt_graph import live_interrupt

    iid, payload = live_interrupt(await _stored(graph, thread))
    assert iid, "the turn is not paused at an approval"
    return build_resume_command(payload, approved=True, interrupt_id=iid)


def test_a_web_turn_past_its_limit_is_cut_and_closed(no_env, monkeypatch, alerts, closes, web_session):
    from kazma_core.agent.turn_budget import TurnBudget, _timed_out_text
    from kazma_ui.active_turns import is_turn_running

    monkeypatch.setenv(ENV, "1")
    script = Script(answer_s=30)
    graph = build_graph(script)
    thread = "web-" + uuid.uuid4().hex[:8]

    async def go():
        before = set(asyncio.all_tasks())
        started = time.monotonic()
        frames = await _web_segment(graph, thread, web_session, _turn_input(thread, "q"))
        elapsed = time.monotonic() - started
        await _wait_for(lambda: closes)  # the pump's done-callback closes the turn
        await asyncio.sleep(0.2)
        return frames, elapsed, _settle_tasks(before), await _stored(graph, thread)

    frames, elapsed, left, snap = run(go())
    text = _timed_out_text(TurnBudget(1, "env"))
    done = [d for e, d in frames if e == "done"]
    assert elapsed < 5, elapsed
    assert len(done) == 1 and done[0]["content"] == text and done[0]["interrupted"] is False
    assert not [e for e, _ in frames if e in ("error", "approval_required")]
    assert script.events == ["thinking", "cancelled"], "the graph run was left behind"
    assert left == [], left
    assert not is_turn_running(thread)
    assert _last_text(snap) == text and tuple(snap.next) == ()
    assert alerts == ["turn.timed_out"]
    assert closes and closes[0]["turn_id"] == "turn-tb"
    row = _reply_row(web_session)
    assert row["content"] == text and not row.get("pending")


def test_without_the_budget_the_web_turn_runs_past_the_limit(no_env, monkeypatch, alerts, web_session):
    """Negative control: the web pump as it was -- no budget around the graph
    -- with the limit set to one second. The turn runs to its own end."""
    from kazma_core.agent import turn_budget

    @asynccontextmanager
    async def no_budget(*args: Any, **kwargs: Any):
        yield turn_budget.TurnSegment()

    monkeypatch.setattr(turn_budget, "turn_segment_budget", no_budget)
    monkeypatch.setenv(ENV, "1")
    script = Script(answer_s=1.5)
    graph = build_graph(script)
    thread = "web-" + uuid.uuid4().hex[:8]

    async def go():
        started = time.monotonic()
        frames = await _web_segment(graph, thread, web_session, _turn_input(thread, "q"))
        return frames, time.monotonic() - started

    frames, elapsed = run(go())
    done = [d for e, d in frames if e == "done"]
    assert elapsed >= 1.5 and done[-1]["content"] == "Answer to q"
    assert script.events == ["thinking", "finished"] and alerts == []


def test_waiting_for_an_approval_is_not_counted(no_env, monkeypatch, alerts, web_session):
    """A human who takes longer than the limit to decide never times the
    turn out: the pause ends the segment, the resume starts a fresh one."""
    monkeypatch.setenv(ENV, "1")
    script = Script(plan_s=0.3, answer_s=0.3)
    graph = build_graph(script)
    thread = "web-" + uuid.uuid4().hex[:8]

    async def go():
        paused = await _web_segment(graph, thread, web_session, _turn_input(thread, "file_write:notes.md"))
        await asyncio.sleep(2.5)  # the human reads the card: longer than the limit
        resumed = await _web_segment(graph, thread, web_session, await _approve(graph, thread))
        return paused, resumed

    paused, resumed = run(go())
    assert [e for e, _ in paused if e == "approval_required"], paused
    assert not [e for e, _ in paused if e == "done"]
    done = [d for e, d in resumed if e == "done"]
    assert done and done[-1]["content"] == "Answer to file_write:notes.md"
    assert alerts == [] and "cancelled" not in script.events


def test_a_resume_past_its_limit_is_cut_too(no_env, monkeypatch, alerts, closes, web_session):
    """The resume after an approval is a running segment: the limit applies
    (it was unbounded on every surface)."""
    from kazma_core.agent.turn_budget import TurnBudget, _timed_out_text

    monkeypatch.setenv(ENV, "1")
    script = Script(answer_s=30)
    graph = build_graph(script)
    thread = "web-" + uuid.uuid4().hex[:8]

    async def go():
        await _web_segment(graph, thread, web_session, _turn_input(thread, "file_write:notes.md"))
        before = set(asyncio.all_tasks())
        started = time.monotonic()
        resumed = await _web_segment(graph, thread, web_session, await _approve(graph, thread))
        elapsed = time.monotonic() - started
        await asyncio.sleep(0.2)
        return resumed, elapsed, _settle_tasks(before), await _stored(graph, thread)

    resumed, elapsed, left, snap = run(go())
    text = _timed_out_text(TurnBudget(1, "env"))
    done = [d for e, d in resumed if e == "done"]
    assert elapsed < 5, elapsed
    assert done and done[-1]["content"] == text
    assert script.events == ["thinking", "cancelled"] and left == []
    assert _last_text(snap) == text and tuple(snap.next) == ()
    assert alerts == ["turn.timed_out"]
    assert any(not c.get("interrupted") for c in closes), "the resume was not closed"
    assert _reply_row(web_session)["content"] == text


# ══ 4. the gates ═════════════════════════════════════════════════════════════


@functools.cache
def _product_sources() -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "kazma-*/*.py", "kazma-*/**/*.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    return {
        f: (REPO / f).read_text(encoding="utf-8", errors="replace")
        for f in files
        if "_tests" not in f and "/tests/" not in f and (REPO / f).is_file()
    }


_BUDGET_MODULE = "kazma-core/kazma_core/agent/turn_budget.py"

#: Calls that name the setting without reading it, and why: the settings
#: backup's rule that classes the key (importing the budget module there
#: would load the whole agent package with every settings read).
NAMES_WITHOUT_READING = {("kazma-core/kazma_core/settings_restore.py", "KeyRule")}


def limit_readers(sources: dict[str, str]) -> list[str]:
    """``path:line`` of every string that names the limit's variable or setting
    outside the budget module: a read needs the name. The one exception is
    the name as the first argument of a declared call."""
    out = []
    for rel, text in sources.items():
        if rel == _BUDGET_MODULE:
            continue
        tree = ast.parse(text)
        declared: set[int] = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and node.args
                    and (rel, getattr(node.func, "id", "")) in NAMES_WITHOUT_READING):
                declared.add(id(node.args[0]))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and node.value in (ENV, KEY)
                    and id(node) not in declared):
                out.append(f"{rel}:{node.lineno}")
    return sorted(out)


def test_only_the_budget_module_reads_the_limit():
    sources = _product_sources()
    assert limit_readers(sources) == []
    for rel, _call in NAMES_WITHOUT_READING:  # each declaration still names it
        assert KEY in sources[rel], rel


def test_a_second_reader_of_the_limit_is_caught():
    """Negative control: the agent runner's own copy, and the turn helper's."""
    old = {
        "kazma-core/kazma_core/agent_runner.py": (
            "import os\n"
            "def run():\n"
            "    raw_to = (os.environ.get('KAZMA_TURN_TIMEOUT_SECONDS') or '600').strip()\n"
        ),
        "kazma-ui/kazma_ui/settings.py": "def get(cs):\n    return cs.get('agent.turn_timeout_seconds')\n",
        # A read beside the declared rule is still a read.
        "kazma-core/kazma_core/settings_restore.py": (
            "RULES = (KeyRule('agent.turn_timeout_seconds', 'setting', 'why'),)\n"
            "def limit(cs):\n"
            "    return cs.get('agent.turn_timeout_seconds')\n"
        ),
    }
    assert limit_readers(old) == ["kazma-core/kazma_core/agent_runner.py:3",
                                  "kazma-core/kazma_core/settings_restore.py:3",
                                  "kazma-ui/kazma_ui/settings.py:2"]


#: Functions that run a graph for their callers without a budget of their own;
#: every use of them must be inside one.
PASS_THROUGH = {
    "kazma-core/kazma_core/agent/turn.py": "_ainvoke",
    "kazma-ui/kazma_ui/turn_runtime.py": "astream_events",
}


def _scoped(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """Inside ``async with turn_segment_budget(...)`` in the same function."""
    while id(node) in parents:
        node = parents[id(node)]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return False
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, ast.Call) and getattr(
                    call.func, "id", getattr(call.func, "attr", "")
                ) == "turn_segment_budget":
                    return True
    return False


def unbudgeted_runs(sources: dict[str, str]) -> list[str]:
    """``path:line`` of every graph run outside the turn's budget: a
    ``.ainvoke(`` / ``.astream_events(`` call, or a use of a pass-through,
    except inside the pass-through itself."""
    out = []
    for rel, text in sources.items():
        tree = ast.parse(text)
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
        # Names a pass-through is used by here: its own name in its module,
        # or what an import of it is bound to.
        wrappers = {PASS_THROUGH[rel]} if rel in PASS_THROUGH else set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "kazma_ui.turn_runtime":
                wrappers |= {a.asname or a.name for a in node.names if a.name == "astream_events"}
        own = {
            id(fn) for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and PASS_THROUGH.get(rel) == fn.name
        }
        for node in ast.walk(tree):
            hit = (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("ainvoke", "astream_events")
            ) or (isinstance(node, ast.Name) and node.id in wrappers and isinstance(node.ctx, ast.Load))
            if not hit:
                continue
            inside_own = False
            up: ast.AST = node
            while id(up) in parents:
                up = parents[id(up)]
                if id(up) in own:
                    inside_own = True
                    break
            if not inside_own and not _scoped(node, parents):
                out.append(f"{rel}:{node.lineno}")
    return sorted(set(out))


def test_every_graph_run_is_inside_the_turn_budget():
    assert unbudgeted_runs(_product_sources()) == []


def test_a_graph_run_outside_the_budget_is_caught():
    """Negative control: the turn helper and the web pump as they were."""
    old = {
        "kazma-core/kazma_core/agent/turn.py": (
            "async def _ainvoke(graph, state, cfg):\n"
            "    return await graph.ainvoke(state, cfg)\n"
            "async def run_agent_turn(graph, state, cfg, timeout):\n"
            "    return await asyncio.wait_for(_ainvoke(graph, state, cfg), timeout=timeout)\n"
        ),
        "kazma-ui/kazma_ui/sse_chat/_streaming.py": (
            "async def _pump_events(graph, input_state, config):\n"
            "    from kazma_ui.turn_runtime import astream_events as _astream_turn\n"
            "    async for ev in _astream_turn(graph, input_state, config):\n"
            "        pass\n"
            "    async with other_scope():\n"
            "        await graph.ainvoke(input_state, config)\n"
        ),
    }
    assert unbudgeted_runs(old) == [
        "kazma-core/kazma_core/agent/turn.py:4",
        "kazma-ui/kazma_ui/sse_chat/_streaming.py:3",
        "kazma-ui/kazma_ui/sse_chat/_streaming.py:6",
    ]


#: Callers allowed to run the graph without the budget, and why.
BUDGET_OFF = {
    "kazma-ui/kazma_ui/sse_chat/__init__.py": "/compact: maintenance the operator ran, not a turn",
    "kazma-gateway/kazma_gateway/agent_handler/graph.py": "/compact: maintenance the operator ran, not a turn",
}


def budget_switched_off(sources: dict[str, str]) -> list[str]:
    """``path:line`` of every call passing ``budget=False`` or
    ``enforce=False``, outside the declared callers."""
    out = []
    for rel, text in sources.items():
        if rel in BUDGET_OFF:
            continue
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg in ("budget", "enforce") and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                    out.append(f"{rel}:{node.lineno}")
    return sorted(out)


def test_only_compaction_runs_without_the_budget():
    sources = _product_sources()
    assert budget_switched_off(sources) == []
    for rel in BUDGET_OFF:  # the declarations still name a caller that does it
        assert "budget=False" in sources[rel], rel


def test_a_caller_switching_the_budget_off_is_caught():
    planted = {"kazma-ui/kazma_ui/x.py": "async def f(g, s, c):\n    await invoke_turn(g, s, c, budget=False)\n"}
    assert budget_switched_off(planted) == ["kazma-ui/kazma_ui/x.py:2"]
