"""A restore is a coordinated, refused-when-unsafe operation (Time Travel TT-02, TT-04, TT-G1).

Audit 2026-10-13: a restore was one raw ``aupdate_state``. On a thread paused
at an approval it wrote a mid-turn step with its tool calls still pending
(``next == ('tool_worker',)``, no interrupt); the approve route counted
"next without an interrupt" as paused and resumed it with a gate-less
security command, so gate A's still-live card ran the restored step's call
-- ``rm -rf build``, which the human had DENIED in turn 1 (witness
``executed == ['rm -rf build']``). And a restore during a running turn
answered ok and was then overwritten by the turn.

Here, on the real graph and saver of ``tests/_tt_graph.py`` (SQLite always;
Postgres when a throwaway database is configured):

- a restore under a pending approval answers ``approval_pending`` naming the
  card; under a running turn ``turn_running``; both through the route as 409;
- a restore writes a turn-boundary state: nothing pending, nothing runnable;
- the approve route resumes only a LIVE interrupt, the card's own, with a
  resume keyed by its id; ``next`` without an interrupt is a 409;
- the negative controls run the old restore and the old approve and show the
  denied call run, and the restore lost;
- gates: every product function that rewrites a checkpoint holds the
  thread's mutation lock; only ``kazma_ui.thread_rewind`` calls
  ``apply_snapshot_to_thread``; every approval resume is keyed by its
  interrupt id. Each with a planted violation as its control.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests import _tt_graph as tt

REPO = Path(__file__).resolve().parents[1]


def _params():
    return [pytest.param("sqlite", id="sqlite"),
            pytest.param("postgres", id="postgres", marks=pytest.mark.postgres)]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A private data dir, gate registry and audit trail per test."""
    from kazma_core import time_travel, time_travel_audit
    from kazma_core.safety import hitl_gates

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    hitl_gates.set_db_path_for_tests(str(tmp_path / "hitl_gates.db"))
    time_travel_audit.set_db_path_for_tests(str(tmp_path / "tt_audit.db"))
    time_travel.set_process_recorder(None)
    yield
    time_travel.set_process_recorder(None)
    hitl_gates.set_db_path_for_tests(None)
    time_travel_audit.set_db_path_for_tests(None)


def _recorder(tmp_path):
    from kazma_core.time_travel import SnapshotRecorder

    return SnapshotRecorder(db_path=str(tmp_path / "snapshots.db"))


def _thread() -> str:
    import uuid

    return f"tt-{uuid.uuid4().hex[:10]}"


def _first_step_of_turn(rec, thread_id: str, turn_index: int):
    steps = [s for s in rec.list_snapshots(thread_id) if s.turn_index == turn_index]
    return steps[0]


async def _denied_then_pending(graph, rec, thread_id):
    """Turn 1 asks for `rm -rf build` and the human denies it; turn 2 asks
    for `notes.md` and waits at gate A. Returns turn 1's first step."""
    await tt.run_turn(graph, thread_id, "shell_exec:rm -rf build")
    await tt.decide(graph, thread_id, approved=False)
    step1 = _first_step_of_turn(rec, thread_id, 1)
    await tt.run_turn(graph, thread_id, "file_write:notes.md")
    return step1


# ── TT-02: a restore under a pending approval is refused ─────────────────


@pytest.mark.parametrize("saver", _params())
def test_a_restore_under_a_pending_approval_is_refused_and_runs_nothing(saver, tmp_path):
    from kazma_ui.thread_rewind import RewindRefused, rewind_thread

    rec = _recorder(tmp_path)
    executed: list[str] = []
    thread_id = _thread()

    async def run():
        async with tt.open_saver(saver, tmp_path) as cp:
            graph = tt.build_graph(rec, executed, cp)
            step1 = await _denied_then_pending(graph, rec, thread_id)
            assert step1.iteration == 1 and executed == []
            with pytest.raises(RewindRefused) as refused:
                await rewind_thread(graph, thread_id, step1.id, actor="test", surface="web", recorder=rec)
            assert refused.value.code == "approval_pending" and refused.value.status == 409
            assert [g["tool"] for g in refused.value.gates] == ["file_write"]
            # The card is still gate A: Approve decides it, and only it.
            iid, payload = tt.live_interrupt(await graph.aget_state(tt.config(thread_id)))
            assert payload["tool"] == "file_write" and iid in {g["gate_id"] for g in refused.value.gates}
            await tt.decide(graph, thread_id, approved=True)
            assert executed == ["notes.md"]

    tt.run(run())


@pytest.mark.parametrize("saver", _params())
def test_the_old_restore_and_approve_ran_the_denied_call(saver, tmp_path):
    """Negative control: the restore and the approve route as they were."""
    from langgraph.types import Command

    from kazma_core.time_travel import checkpoint_payload_from_snapshot

    rec = _recorder(tmp_path)
    executed: list[str] = []
    thread_id = _thread()

    async def run():
        async with tt.open_saver(saver, tmp_path) as cp:
            graph = tt.build_graph(rec, executed, cp)
            step1 = await _denied_then_pending(graph, rec, thread_id)
            # The old apply_snapshot_to_thread: one raw aupdate_state.
            old = checkpoint_payload_from_snapshot(rec.get(step1.id).get_state(), thread_id)
            await graph.aupdate_state(tt.config(thread_id), old)
            pre = await graph.aget_state(tt.config(thread_id))
            assert pre.next == ("tool_worker",) and tt.live_interrupt(pre)[1] is None
            # The old route: "next without an interrupt — still try resume",
            # with the gate-less security command.
            await graph.ainvoke(Command(resume={"approved": True, "reason": "", "scope": "once"}),
                                tt.config(thread_id))
            assert executed == ["rm -rf build"]

    tt.run(run())


@pytest.mark.parametrize("saver", _params())
def test_a_restore_writes_a_turn_boundary_and_a_late_approve_runs_nothing(saver, tmp_path):
    from langgraph.types import Command

    from kazma_ui.thread_rewind import rewind_thread

    rec = _recorder(tmp_path)
    executed: list[str] = []
    thread_id = _thread()

    async def run():
        async with tt.open_saver(saver, tmp_path) as cp:
            graph = tt.build_graph(rec, executed, cp)
            step1 = await _denied_then_pending(graph, rec, thread_id)
            gate_a, _ = tt.live_interrupt(await graph.aget_state(tt.config(thread_id)))
            await tt.decide(graph, thread_id, approved=False)  # the human answers gate A
            out = await rewind_thread(graph, thread_id, step1.id, actor="test", surface="web", recorder=rec)
            assert out["ok"] and out["turn_index"] == 1
            snap = await graph.aget_state(tt.config(thread_id))
            assert snap.next == () and snap.values.get("tool_calls_pending") == []
            # No tool call shell without its result survives (strict providers).
            assert not any(m.get("tool_calls") for m in snap.values["messages"] if isinstance(m, dict))
            # A late Approve of gate A, keyed by its id, resumes nothing.
            await graph.ainvoke(Command(resume={gate_a: {"approved": True}}), tt.config(thread_id))
            assert executed == []

    tt.run(run())


# ── TT-04: a restore during a running turn is refused ───────────────────


@pytest.mark.parametrize("saver", _params())
def test_a_restore_during_a_running_turn_is_refused_not_lost(saver, tmp_path):
    from kazma_ui.active_turns import register_turn, unregister_turn
    from kazma_ui.thread_rewind import RewindRefused, rewind_thread

    rec = _recorder(tmp_path)
    thread_id = _thread()

    async def run():
        async with tt.open_saver(saver, tmp_path) as cp:
            graph = tt.build_graph(rec, [], cp)
            await tt.run_turn(graph, thread_id, "hello")
            step1 = rec.list_snapshots(thread_id)[0]
            entered, release = asyncio.Event(), asyncio.Event()
            graph_slow = _slow_graph(rec, cp, entered, release)
            task = asyncio.create_task(tt.run_turn(graph_slow, thread_id, "second question"))
            register_turn(thread_id, task)
            try:
                await asyncio.wait_for(entered.wait(), 10)
                with pytest.raises(RewindRefused) as refused:
                    await rewind_thread(graph, thread_id, step1.id, actor="test", surface="web", recorder=rec)
                assert refused.value.code == "turn_running"
            finally:
                release.set()
                await asyncio.wait_for(task, 10)
                unregister_turn(thread_id, task)
            final = [m["content"] for m in (await graph.aget_state(tt.config(thread_id))).values["messages"]]
            assert final[-1] == "late answer"
            # Afterwards it is allowed, and it sticks.
            out = await rewind_thread(graph, thread_id, step1.id, actor="test", surface="web", recorder=rec)
            assert out["ok"]
            msgs = (await graph.aget_state(tt.config(thread_id))).values["messages"]
            assert "late answer" not in [m.get("content") for m in msgs]

    tt.run(run())


def _slow_graph(rec, cp, entered, release):
    """The fixture graph's shape with a supervisor that waits mid-turn."""
    from langgraph.graph import END, StateGraph

    from kazma_core.agent.state import NodeName, SupervisorState

    async def supervisor(state):
        entered.set()
        await release.wait()
        return {"messages": [*state.get("messages", []), {"role": "assistant", "content": "late answer"}],
                "next_node": "end"}

    g = StateGraph(SupervisorState)
    g.add_node(NodeName.SUPERVISOR, supervisor)
    g.set_entry_point(NodeName.SUPERVISOR)
    g.add_edge(NodeName.SUPERVISOR, END)
    return g.compile(checkpointer=cp)


def test_the_old_restore_during_a_running_turn_was_silently_lost(tmp_path):
    """Negative control: a raw write while the turn runs answers ok and the
    turn checkpoints over it."""
    from langgraph.checkpoint.memory import InMemorySaver

    from kazma_core.time_travel import checkpoint_payload_from_snapshot

    async def run():
        cp = InMemorySaver()
        entered, release = asyncio.Event(), asyncio.Event()
        graph = _slow_graph(None, cp, entered, release)
        cfg = tt.config("t-old")
        release.set()
        await graph.ainvoke({"thread_id": "t-old", "messages": [{"role": "user", "content": "u1"}]}, cfg)
        release.clear()
        entered.clear()
        task = asyncio.create_task(graph.ainvoke(
            {"thread_id": "t-old", "messages": [{"role": "user", "content": "u1"},
                                                 {"role": "user", "content": "u2"}]}, cfg))
        await asyncio.wait_for(entered.wait(), 5)
        await graph.aupdate_state(cfg, checkpoint_payload_from_snapshot(
            {"messages": [{"role": "user", "content": "u1"}]}, "t-old"))
        release.set()
        await asyncio.wait_for(task, 5)
        final = [m["content"] for m in (await graph.aget_state(cfg)).values["messages"]]
        assert final[-1] == "late answer" and final != ["u1"]

    tt.run(run())


def test_the_route_answers_409_while_a_turn_runs(tmp_path, monkeypatch):
    """The restore route with a registered running turn: 409 turn_running,
    nothing written (the witness: 200 and a write)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui import active_turns, thread_ownership
    from kazma_ui.replay_routes import create_replay_router

    rec = _recorder(tmp_path)
    rec.capture({"thread_id": "t-run", "iteration": 1, "created_at": "x", "messages": []})
    step = rec.list_snapshots("t-run")[0]
    writes: list[Any] = []
    graph = SimpleNamespace(aupdate_state=AsyncMock(side_effect=lambda *a, **k: writes.append(a)),
                            aget_state=AsyncMock(return_value=None), nodes={})
    monkeypatch.setattr(thread_ownership, "require_thread_owned", AsyncMock(return_value=None))
    running = SimpleNamespace(done=lambda: False)
    active_turns.register_turn("t-run", running)
    try:
        app = FastAPI()
        app.include_router(create_replay_router(recorder=rec, graph=graph))
        got = TestClient(app).post("/api/replay/restore", json={"thread_id": "t-run", "snapshot_id": step.id})
    finally:
        active_turns.unregister_turn("t-run", running)
    assert got.status_code == 409, got.text
    assert got.json()["error"] == "turn_running" and writes == []


# ── TT-02: the approve route resumes only the live interrupt it names ────


def _approve_app(graph, monkeypatch):
    """The REAL approve route, with a driver that runs the resume on the graph."""
    from fastapi import FastAPI

    from kazma_ui import auth, hitl_decision, hitl_status, reply_sink, session_manager, turn_runtime
    from kazma_ui.routes_direct import misc
    import kazma_ui.sse_chat._streaming as streaming

    builder = SimpleNamespace(app=FastAPI(), _hitl_state={"graph": graph, "checkpointer": object()},
                              _graph_holder={"graph": graph}, session_store=None)
    owner = SimpleNamespace(session_id="owner", messages=[])
    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "")
    monkeypatch.setattr(session_manager, "get_session_manager", lambda: SimpleNamespace(
        get_by_thread_id=lambda _: owner, thread_is_exclusive=lambda _: True))
    monkeypatch.setattr(hitl_status, "persisted_hitl_for_thread", lambda _: None)
    monkeypatch.setattr(turn_runtime, "resolve_session_id", lambda _: "owner")
    monkeypatch.setattr(reply_sink, "resolve_reply_turn", lambda *a: "turn")
    monkeypatch.setattr(hitl_decision, "record_gate_decision", AsyncMock(return_value=None))
    monkeypatch.setattr(misc, "_apply_scope_grants", lambda *a, **k: "once")
    drove: list[Any] = []

    async def _drive(graph_ref, cmd, config, **_kw):
        drove.append(cmd)
        return await graph_ref.ainvoke(cmd, config)

    monkeypatch.setattr(streaming, "_drive_graph_to_journal", _drive)
    misc.register_misc_routes(builder)
    return builder.app, drove


def _post_approve(app, thread_id: str, body: dict[str, Any]):
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.post(f"/api/approve/{thread_id}", json=body)
        # Let the spawned drive finish on the client's loop.
        client.portal.call(asyncio.sleep, 0.3)
    return response


def test_the_route_refuses_next_without_an_interrupt(tmp_path, monkeypatch):
    """The witness's state (the old restore under gate A), through the real
    route: 409, and the denied call does not run."""
    from langgraph.checkpoint.memory import InMemorySaver

    from kazma_core.time_travel import checkpoint_payload_from_snapshot

    rec = _recorder(tmp_path)
    executed: list[str] = []
    thread_id = _thread()
    graph = tt.build_graph(rec, executed, InMemorySaver())

    async def setup():
        step1 = await _denied_then_pending(graph, rec, thread_id)
        old = checkpoint_payload_from_snapshot(rec.get(step1.id).get_state(), thread_id)
        await graph.aupdate_state(tt.config(thread_id), old)

    tt.run(setup())
    app, drove = _approve_app(graph, monkeypatch)
    response = _post_approve(app, thread_id, {"action": "approve"})
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == "not_pending"
    assert drove == [] and executed == []


def test_the_route_decides_the_live_card_with_a_keyed_resume(tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    rec = _recorder(tmp_path)
    executed: list[str] = []
    thread_id = _thread()
    graph = tt.build_graph(rec, executed, InMemorySaver())
    tt.run(tt.run_turn(graph, thread_id, "file_write:notes.md"))
    iid, _ = tt.live_interrupt(tt.run(graph.aget_state(tt.config(thread_id))))
    app, drove = _approve_app(graph, monkeypatch)
    response = _post_approve(app, thread_id, {"action": "approve", "interrupt_id": iid})
    assert response.status_code == 200, response.text
    assert len(drove) == 1 and set(drove[0].resume) == {iid}, drove
    assert executed == ["notes.md"]


def test_a_card_the_registry_knows_as_another_gate_is_refused(tmp_path, monkeypatch):
    """A registered card that is not the live interrupt: 409, nothing runs."""
    from langgraph.checkpoint.memory import InMemorySaver

    from kazma_core.safety.hitl_gates import GateRow, register_gate

    rec = _recorder(tmp_path)
    executed: list[str] = []
    thread_id = _thread()
    graph = tt.build_graph(rec, executed, InMemorySaver())
    tt.run(tt.run_turn(graph, thread_id, "file_write:notes.md"))
    register_gate(GateRow(gate_id="0" * 32, thread_id=thread_id, tool="shell_exec", mechanism="graph"))
    app, drove = _approve_app(graph, monkeypatch)
    response = _post_approve(app, thread_id, {"action": "approve", "interrupt_id": "0" * 32})
    assert response.status_code == 409, response.text
    assert drove == [] and executed == []


# ── TT-G1: the gates ──────────────────────────────────────────────────────

#: Product functions that write a checkpoint without the thread's mutation
#: lock, and why that is safe.
NOT_UNDER_THE_LOCK = {
    "kazma-core/kazma_core/agent/supervisor_watchdog.py:supervised_invoke":
        "inside the running turn it supervises (the turn is registered; restores are refused)",
    "kazma-core/kazma_core/time_travel.py:apply_snapshot_to_thread":
        "called only by kazma_ui.thread_rewind, under the lock (the next gate)",
    "kazma-gateway/kazma_gateway/stores/checkpoint.py:adelete_thread":
        "the checkpoint store's own delete; its callers hold the lock",
}

_WRITERS = ("aupdate_state", "adelete_thread")


def _lock_names(fn: ast.AST) -> set[str]:
    """Names bound to ``thread_mutation(...)`` in *fn*."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            callee = node.value.func
            if getattr(callee, "id", getattr(callee, "attr", "")) == "thread_mutation":
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _under_lock(fn: ast.AST, call: ast.Call, parents: dict[int, ast.AST]) -> bool:
    locks = _lock_names(fn)
    node: Any = call
    while id(node) in parents:
        node = parents[id(node)]
        if node is fn:
            return False
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                expr = item.context_expr
                if isinstance(expr, ast.Call) and getattr(expr.func, "id", getattr(expr.func, "attr", "")) == "thread_mutation":
                    return True
                if isinstance(expr, ast.Name) and expr.id in locks:
                    return True
    return False


def unlocked_writers(sources: dict[str, str]) -> list[str]:
    """``path:function`` of every product function that calls a checkpoint
    writer outside an ``async with thread_mutation(...)``."""
    out: list[str] = []
    for rel, text in sources.items():
        tree = ast.parse(text)
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(fn):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr in _WRITERS):
                    continue
                # The innermost function holding the call.
                owner: Any = parents.get(id(call))
                while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owner = parents.get(id(owner))
                if owner is not fn:
                    continue
                key = f"{rel}:{fn.name}"
                if key not in NOT_UNDER_THE_LOCK and not _under_lock(fn, call, parents):
                    out.append(key)
    return sorted(set(out))


def _product_sources() -> dict[str, str]:
    sources = {}
    for pkg in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                "kazma-cli/kazma_cli", "kazma-tui/kazma_tui"):
        for p in (REPO / pkg).rglob("*.py"):
            text = p.read_text(encoding="utf-8", errors="replace")
            if any(f".{w}(" in text for w in _WRITERS):
                sources[p.relative_to(REPO).as_posix()] = text
    return sources


def test_every_checkpoint_rewriter_holds_the_threads_lock():
    assert unlocked_writers(_product_sources()) == []
    # The declarations name functions that still exist and still write.
    found = {f"{rel}:{fn.name}" for rel, text in _product_sources().items()
             for fn in ast.walk(ast.parse(text)) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert set(NOT_UNDER_THE_LOCK) <= found


def test_a_rewriter_without_the_lock_is_caught():
    """Negative control: the restore route and /undo as they were."""
    old = (
        "async def restore_snapshot(graph, config, payload):\n"
        "    await graph.aupdate_state(config, payload)\n"
        "async def _handle_undo(thread_id, config):\n"
        "    cmd_lock = thread_mutation(thread_id)\n"
        "    async with cmd_lock:\n"
        "        await graph.aupdate_state(config, {})\n"
        "async def _reset(graph, thread_id):\n"
        "    async with other_lock:\n"
        "        await graph.checkpointer.adelete_thread(thread_id)\n"
    )
    assert unlocked_writers({"x.py": old}) == ["x.py:_reset", "x.py:restore_snapshot"]


def callers_of_apply(sources: dict[str, str]) -> list[str]:
    out = []
    for rel, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "apply_snapshot_to_thread":
                out.append(rel)
    return sorted(set(out))


def test_only_the_rewind_service_writes_a_snapshot_onto_a_thread():
    sources = {}
    for pkg in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                "kazma-cli/kazma_cli", "kazma-tui/kazma_tui"):
        for p in (REPO / pkg).rglob("*.py"):
            sources[p.relative_to(REPO).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    assert callers_of_apply(sources) == ["kazma-ui/kazma_ui/thread_rewind.py"]


def test_a_route_writing_a_restore_itself_is_caught():
    """Negative control: the old restore route."""
    old = "async def restore(g, s, t):\n    return await apply_snapshot_to_thread(g, s, t)\n"
    assert callers_of_apply({"kazma-ui/kazma_ui/replay_routes.py": old}) == ["kazma-ui/kazma_ui/replay_routes.py"]


def unkeyed_resumes(sources: dict[str, str]) -> list[str]:
    """Calls of ``build_resume_command`` that name no interrupt (a steer's
    ``action=`` resume names none by design)."""
    out = []
    for rel, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "build_resume_command":
                kws = {k.arg for k in node.keywords}
                if not ({"interrupt_id", "action"} & kws):
                    out.append(f"{rel}:{node.lineno}")
    return out


def test_every_approval_resume_is_keyed_by_its_interrupt():
    sources = {}
    for pkg in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway"):
        for p in (REPO / pkg).rglob("*.py"):
            text = p.read_text(encoding="utf-8", errors="replace")
            if "build_resume_command(" in text and not p.name == "resume.py":
                sources[p.relative_to(REPO).as_posix()] = text
    assert sources and unkeyed_resumes(sources) == []


def test_an_unkeyed_resume_is_caught():
    """Negative control: the approve route's gate-less fallback."""
    old = ("cmd = build_resume_command({'type': 'hitl_approval', 'kind': 'security'},\n"
           "                           approved=True, scope='once')\n")
    assert unkeyed_resumes({"misc.py": old}) == ["misc.py:1"]


def test_a_keyed_resume_names_the_interrupt_and_nothing_else():
    from kazma_core.safety.commitment.resume import build_resume_command

    iid = "a" * 32
    cmd = build_resume_command({"type": "hitl_approval", "kind": "security"}, approved=True, interrupt_id=iid)
    assert set(cmd.resume) == {iid} and cmd.resume[iid]["approved"] is True
    # A hash alias is not LangGraph's id: the resume stays unkeyed (a dict
    # keyed by it would be read as the resume VALUE).
    alias = build_resume_command({"type": "hitl_approval"}, approved=True, interrupt_id="abc123")
    assert alias.resume["approved"] is True
