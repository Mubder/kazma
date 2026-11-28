"""A chat that goes away ends what it has running or waiting (2026-11-27).

Live: a private chat's turn waited on an approval (``python_exec``) and the
client deleted the chat (``DELETE /api/chat/sessions/{id}``). The card left the
bell, but the turn's run (``chat_turn:<thread>``) stayed open with nothing
running it, and the run supervisor raised "Chat turn is stuck: nothing is
running it" as a red Ops alert and an incident.

Behaviour, through the real routes, chat store, gate registry, reply sink,
chat-turn adapter and run supervisor (a private incident store; the chat send
and the ops alert recorded): a chat deleted mid-approval leaves no open turn,
no live gate and no incident, and Running work lists the turn cancelled with
why, in English and Arabic; the same for a running turn (cancelled and waited
for, bounded), an archive, ``/reset``, the Dashboard's thread delete, the
store's own limit and a space erased. The old delete -- the chat's work left
as it was -- is the control: it pages the incident.

The class gate reads the product source: every way a chat goes away -- a
chat-store method that deletes or archives a chat, a call that removes a
chat's row (``delete``, ``delete_if``, ``set_archived``) or its thread's whole
state (``adelete_thread``, ``adelete_all_threads``) -- ends the chat's runs
(``kazma_ui.chat_runs_end``), before the state goes, or is declared with why
it need not. Planted removals without an ender, with the ender after the state
is gone, and a store method that deletes without ending are its controls.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from kazma_core.safety import hitl_gates
from kazma_core.safety.hitl_gates import GateRow
from kazma_ui import active_turns, reply_sink, runs_chat_turn, session_manager
from kazma_ui.session_manager import SessionManager

from tests._as_request import as_owner, in_space

REPO = Path(__file__).resolve().parents[1]
STALE = 700.0  # past the chat turn's dead_after_s (600)


# ── the world ────────────────────────────────────────────────────────────


def _clean() -> None:
    from kazma_ui import turn_resume

    active_turns.reset_active_turns()
    reply_sink.reset_reply_turns()
    turn_resume._left.clear()  # cut turns another test left: the supervisor would page them
    runs_chat_turn._abandoned_since.clear()
    with runs_chat_turn._ended_lock:
        runs_chat_turn._ended.clear()


@pytest.fixture
def store(tmp_path):
    before = session_manager.peek_session_manager()
    made = SessionManager(db_path=str(tmp_path / "chats.db"))
    session_manager.set_session_manager(made)
    _clean()
    yield made
    _clean()
    session_manager.set_session_manager(before)
    made.close()


@pytest.fixture
def watched(tmp_path, monkeypatch):
    """The run supervisor over the real chat-turn kind only; a private
    incident store; the chat send and the ops alert recorded."""
    from kazma_core.runs import supervisor
    from kazma_core.runs.incidents import IncidentStore, reset_incident_store
    from kazma_core.runs.kinds import KINDS

    for kind in [k for k in KINDS if k != "chat_turn"]:
        monkeypatch.delitem(KINDS, kind)
    reset_incident_store(IncidentStore(tmp_path / "incidents.db"))
    sent: list[dict[str, Any]] = []
    alerts: list[str] = []

    async def fake_send(target: str, text: str, **kw: Any) -> str:
        sent.append({"target": target, "text": text})
        return f"sent:{target}"

    def fake_alert(key: str, title: str, detail: str = "", **kw: Any) -> bool:
        alerts.append(key)
        if kw.get("on_delivery"):
            receipt = threading.Thread(target=kw["on_delivery"], args=(True,))
            receipt.start()
            receipt.join()
        return True

    monkeypatch.setattr("kazma_core.runs.notify._send", fake_send)
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", fake_alert)
    monkeypatch.setattr(runs_chat_turn, "_graph_getter", None)  # no chat address: the ops channel
    monkeypatch.setattr(supervisor, "_watch_started", time.time() - 3600)
    monkeypatch.setattr(supervisor, "_failing_since", None)
    monkeypatch.setattr(supervisor, "_paged_unavailable", False)
    yield SimpleNamespace(sent=sent, alerts=alerts)
    reset_incident_store(None)


def _tick(at: float) -> dict[str, int]:
    from kazma_core.runs import supervisor

    return asyncio.run(supervisor.tick(now=at))


def _incidents() -> list[dict[str, Any]]:
    from kazma_core.runs.incidents import get_incident_store

    return get_incident_store().recent(limit=50)


def _runs(at: float) -> dict[str, Any]:
    from kazma_core.runs import registry

    listing = asyncio.run(registry.list_runs(kinds=("chat_turn",), now=at))
    assert not listing.errors, listing.errors
    return {run.native_id: run for run in listing.runs}


class Saved:
    """The checkpoint manager: what it was asked to delete."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)

    async def adelete_all_threads(self) -> int:
        self.deleted.append("*")
        return 1


class Graph:
    """A graph whose checkpoint holds a turn; records what was written."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.checkpointer = None

    async def aget_state(self, config: dict[str, Any]) -> Any:
        return SimpleNamespace(values={"messages": [{"role": "user", "content": "run it"}]}, next=(), tasks=())

    async def aupdate_state(self, config: dict[str, Any], values: dict[str, Any], as_node: str = "") -> None:
        self.updates.append(dict(values))


@pytest.fixture
def web(store, monkeypatch):
    from fastapi.testclient import TestClient
    from kazma_ui import dashboard
    from kazma_ui.sse_chat import create_sse_chat_router

    saved, graph = Saved(), Graph()
    monkeypatch.setattr(dashboard, "_checkpoint_manager", saved)
    monkeypatch.setattr(dashboard, "require_admin", lambda _request: None)
    app = FastAPI()
    app.include_router(create_sse_chat_router(graph_getter=lambda: graph))
    app.include_router(dashboard.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, saved=saved, graph=graph)


@dataclass
class Waiting:
    session_id: str
    thread_id: str
    turn_id: str
    gate_id: str
    title: str = "Run the numbers"
    extra: dict[str, Any] = field(default_factory=dict)


def _waiting_chat(store: SessionManager, *, thread: str = "", space: str = "", gate: bool = True) -> Waiting:
    """A chat whose turn waits on an approval: the transcript's pending card,
    the open reply turn, the registry's pending gate -- as the stream leaves
    them at the pause."""
    chat = Waiting(session_id=f"s-{uuid4().hex[:8]}", thread_id=thread or f"t-{uuid4().hex[:8]}",
                   turn_id=f"turn-{uuid4().hex[:8]}", gate_id=f"intr-{uuid4().hex[:8]}")
    card = {"type": "hitl", "tool": "python_exec", "state": "pending", "interrupt_id": chat.gate_id,
            "payload": {"tool": "python_exec", "interrupt_id": chat.gate_id}}
    with in_space(space) if space else as_owner():
        held = store.get_or_create(chat.session_id, thread_id=chat.thread_id)
        held.messages = [
            {"role": "user", "content": "work out the totals"},
            {"role": "assistant", "content": "", "turn_id": chat.turn_id, "open": True, "parts": [card]},
        ]
        held.title = chat.title
        store.put(held)
    reply_sink.open_reply_turn(chat.thread_id, chat.turn_id)
    if gate:
        hitl_gates.register_gate(GateRow(gate_id=chat.gate_id, thread_id=chat.thread_id, tool="python_exec",
                                         session_id=chat.session_id, turn_id=chat.turn_id))
    return chat


def _ended_cleanly(chat: Waiting, reason: str) -> None:
    """No open turn, no live gate, the gate settled as not needed, the run
    recorded as ended with *reason*."""
    assert chat.thread_id not in reply_sink.open_turns_snapshot()
    assert hitl_gates.live_gates(chat.thread_id) == []
    gate = hitl_gates.gate_for(chat.gate_id)
    assert gate is not None and gate.state == "settled" and gate.decision == "not_needed", gate
    assert runs_chat_turn._ended_turns()[chat.thread_id].reason == reason


# ── the incident of 2026-11-27 ───────────────────────────────────────────


def test_a_chat_deleted_mid_approval_leaves_no_run_gate_or_incident(store, web, watched) -> None:
    from kazma_core.runs.contract import RunState
    from kazma_core.runs.text import format_listing

    chat = _waiting_chat(store)
    t0 = time.time()
    _tick(t0)
    assert _runs(t0)[chat.thread_id].state == RunState.WAITING_HUMAN

    assert web.client.delete(f"/api/chat/sessions/{chat.session_id}").json() == {"status": "ok"}
    assert web.saved.deleted == [chat.thread_id]
    _ended_cleanly(chat, "chat_deleted")

    for at in (t0 + 60, t0 + 60 + STALE, t0 + 60 + 2 * STALE):
        _tick(at)
    assert _incidents() == [] and watched.alerts == [] and watched.sent == []
    run = _runs(t0 + 60 + STALE)[chat.thread_id]
    assert run.state == RunState.CANCELLED and run.ended_reason == "chat_deleted" and run.title == chat.title
    assert run.verdict is not None and str(run.verdict) == "ok"
    english = format_listing([run], {}, lang="en", include_finished=True, now=t0 + 60 + STALE)
    arabic = format_listing([run], {}, lang="ar", include_finished=True, now=t0 + 60 + STALE)
    assert "The chat was deleted" in english, english
    assert "حُذفت المحادثة" in arabic, arabic


def test_control_the_old_delete_pages_the_turn_stuck(store, web, watched, monkeypatch) -> None:
    """The delete before: the chat's work left as it was."""
    import kazma_ui.chat_runs_end as chat_runs_end

    async def left_as_it_was(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(chat_runs_end, "end_chat_runs", left_as_it_was)
    chat = _waiting_chat(store)
    t0 = time.time()
    assert web.client.delete(f"/api/chat/sessions/{chat.session_id}").json() == {"status": "ok"}
    assert chat.thread_id in reply_sink.open_turns_snapshot()
    assert [g.gate_id for g in hitl_gates.live_gates(chat.thread_id)] == [chat.gate_id]
    _tick(t0 + 60)
    _tick(t0 + 60 + STALE)
    paged = _incidents()
    assert [(row["run_id"], row["condition"]) for row in paged] == [(f"chat_turn:{chat.thread_id}", "dead")]
    assert watched.alerts, "the stuck turn was paged"


def test_a_late_close_does_not_open_a_deleted_chats_turn_again(store, web) -> None:
    """A cancelled turn's own closer with no session left (it names no turn):
    the chat was deleted, so no turn opens that nothing would run."""
    chat = _waiting_chat(store)
    assert web.client.delete(f"/api/chat/sessions/{chat.session_id}").json() == {"status": "ok"}
    assert reply_sink.resolve_reply_turn(chat.thread_id, "")
    assert chat.thread_id not in reply_sink.open_turns_snapshot()
    # Control: a thread whose chat stays opens its turn as before.
    other = f"t-{uuid4().hex[:8]}"
    reply_sink.open_reply_turn(other)
    assert other in reply_sink.open_turns_snapshot()


# ── a running turn ───────────────────────────────────────────────────────


def test_a_running_turn_is_cancelled_and_waited_for(store) -> None:
    from kazma_ui.chat_runs_end import end_chat_runs

    thread = f"t-{uuid4().hex[:8]}"
    seen: list[str] = []

    async def scenario() -> Any:
        async def turn() -> None:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                seen.append("cancelled")
                raise

        task = asyncio.ensure_future(turn())
        active_turns.register_turn(thread, task)
        reply_sink.open_reply_turn(thread, "turn-1")
        await asyncio.sleep(0)
        ended = await end_chat_runs(thread, reason="chat_deleted", title="busy")
        return task, ended

    task, ended = asyncio.run(scenario())
    assert task.cancelled() and seen == ["cancelled"]
    assert ended.cancelled and ended.recorded
    assert not active_turns.is_turn_running(thread)
    assert thread not in reply_sink.open_turns_snapshot()
    assert runs_chat_turn._ended_turns()[thread].reason == "chat_deleted"


def test_a_turn_that_absorbs_its_cancel_is_waited_for_with_a_bound(store, monkeypatch, caplog) -> None:
    """A cancel the turn's own code swallows (section 69): the wait ends at
    the bound, said at WARNING, and the rest still ends."""
    import logging

    import kazma_ui.chat_runs_end as chat_runs_end

    monkeypatch.setattr(chat_runs_end, "CANCEL_WAIT_S", 0.3)
    thread = f"t-{uuid4().hex[:8]}"

    async def scenario() -> Any:
        stop = asyncio.Event()

        async def stubborn() -> None:
            while not stop.is_set():
                try:
                    await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    continue  # absorbed

        task = asyncio.ensure_future(stubborn())
        active_turns.register_turn(thread, task)
        reply_sink.open_reply_turn(thread, "turn-1")
        await asyncio.sleep(0)
        began = time.monotonic()
        ended = await chat_runs_end.end_chat_runs(thread, reason="chat_cleared")
        took = time.monotonic() - began
        stop.set()
        await asyncio.wait({task}, timeout=2)
        return ended, took

    with caplog.at_level(logging.WARNING, logger="kazma_core.bounded_cancel"):
        ended, took = asyncio.run(scenario())
    assert took < 2.0 and ended.recorded
    assert any("did not end" in r.getMessage() for r in caplog.records), caplog.text
    assert thread not in reply_sink.open_turns_snapshot()


def test_from_a_thread_the_cancel_is_handed_to_the_turns_loop(store) -> None:
    """The chat store's own removals run in a thread: the turn's loop
    cancels it and waits for it there."""
    from kazma_ui.chat_runs_end import chat_went_away

    loop = asyncio.new_event_loop()
    runner = threading.Thread(target=loop.run_forever, daemon=True)
    runner.start()
    thread = f"t-{uuid4().hex[:8]}"
    try:
        async def turn() -> None:
            await asyncio.sleep(3600)

        task = asyncio.run_coroutine_threadsafe(_make_task(turn()), loop).result(5)
        active_turns.register_turn(thread, task)
        ended = chat_went_away(thread, reason="space_erased", title="")
        assert ended.cancelled and ended.recorded
        deadline = time.monotonic() + 5
        while not task.done() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert task.cancelled()
        assert not active_turns.is_turn_running(thread)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        runner.join(5)
        loop.close()


async def _make_task(coro: Any) -> asyncio.Task[Any]:
    return asyncio.ensure_future(coro)


# ── every other way a chat goes away ─────────────────────────────────────


def test_an_archived_chat_ends_its_turn_and_keeps_its_words(store, web) -> None:
    chat = _waiting_chat(store)
    answer = web.client.post(f"/api/chat/sessions/{chat.session_id}/archive").json()
    assert answer == {"status": "ok", "archived": True}
    _ended_cleanly(chat, "chat_archived")
    kept = store.get(chat.session_id)
    assert kept is not None and kept.archived and kept.messages[0]["content"] == "work out the totals"
    card = [p for p in kept.messages[-1]["parts"] if p.get("type") == "hitl"]
    assert card and card[-1]["state"] == "settled", kept.messages[-1]
    # Its checkpoint says the turn was abandoned, as Stop writes it.
    assert web.graph.updates and web.graph.updates[-1]["task_status"] == "abandoned"


def test_archiving_a_twin_leaves_the_thread_running_in_the_chat_that_shows(store, web) -> None:
    """A chat app's auto-titled twin, archived: the thread goes on in the
    named chat, and so does its turn."""
    chat = _waiting_chat(store)
    twin = f"s-{uuid4().hex[:8]}"
    with as_owner():
        held = store.get_or_create(twin, thread_id=chat.thread_id)
        held.messages = [{"role": "user", "content": "same thread"}]
        store.put(held)
    assert web.client.post(f"/api/chat/sessions/{twin}/archive").json()["archived"] is True
    assert chat.thread_id in reply_sink.open_turns_snapshot()
    assert [g.gate_id for g in hitl_gates.live_gates(chat.thread_id)] == [chat.gate_id]
    assert chat.thread_id not in runs_chat_turn._ended_turns()


def test_reset_ends_the_waiting_turn_before_its_state_goes(store, web, monkeypatch) -> None:
    order: list[str] = []
    chat = _waiting_chat(store)

    class Checkpointer:
        async def adelete_thread(self, thread_id: str) -> None:
            order.append("state" if thread_id not in runs_chat_turn._ended_turns() else "state after the end")

    web.graph.checkpointer = Checkpointer()
    reply = web.client.post("/api/chat/stream", json={"session_id": chat.session_id, "message": "/reset"}).text
    assert "event: done" in reply, reply
    _ended_cleanly(chat, "chat_cleared")
    assert order == ["state after the end"]


def test_the_dashboards_thread_delete_ends_the_turn(store, web) -> None:
    chat = _waiting_chat(store)
    assert web.client.delete(f"/api/sessions/{chat.thread_id}").json()["deleted"] is True
    _ended_cleanly(chat, "chat_deleted")


def test_the_dashboards_clear_all_ends_every_turn(store, web) -> None:
    first, second = _waiting_chat(store), _waiting_chat(store)
    assert web.client.post("/api/sessions/clear-all").json()["deleted"] is True
    for chat in (first, second):
        _ended_cleanly(chat, "chat_cleared")


def test_the_stores_limit_ends_the_turn_of_the_chat_it_removes(tmp_path) -> None:
    before = session_manager.peek_session_manager()
    small = SessionManager(max_sessions=1, db_path=str(tmp_path / "small.db"))
    session_manager.set_session_manager(small)
    _clean()
    try:
        chat = _waiting_chat(small)
        with as_owner():
            small.update_from_dict(f"s-{uuid4().hex[:8]}", {"messages": [{"role": "user", "content": "x"}],
                                                            "thread_id": f"t-{uuid4().hex[:8]}"})
        assert small.held(chat.session_id) is None
        _ended_cleanly(chat, "chat_store_full")
    finally:
        _clean()
        session_manager.set_session_manager(before)
        small.close()


def test_a_space_erased_ends_its_runs_before_its_rows_go(store, monkeypatch, tmp_path) -> None:
    """Through the real erase: the run is ended while the space's rows and
    thread records still say whose it is, and recorded in that space -- ended
    after, it landed in the owner's Running work with no space and no title."""
    from kazma_core.spaces import space_data, threads
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bind_scope

    from tests._space_world import A, plant_world

    world = plant_world(monkeypatch, tmp_path / "world")
    next(world)
    try:
        thread = f"t-{uuid4().hex[:8]}"
        with bind_scope(A, OWNER_ACCOUNT_ID):
            threads.record_thread(thread, A, OWNER_ACCOUNT_ID)
            hitl_gates.register_gate(GateRow(gate_id=f"intr-{uuid4().hex[:8]}", thread_id=thread,
                                             tool="python_exec"))
        reply_sink.open_reply_turn(thread, "turn-a")
        space_data.erase_space(A, by=OWNER_ACCOUNT_ID)
        assert thread not in reply_sink.open_turns_snapshot()
        assert hitl_gates.live_gates(thread) == []
        assert runs_chat_turn._ended_turns()[thread].space == A
        assert runs_chat_turn._ended_turns()[thread].reason == "space_erased"
    finally:
        world.close()


def test_a_space_erased_leaves_other_spaces_turns(store) -> None:
    from kazma_core.spaces import threads
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bind_scope
    from kazma_ui.chat_runs_end import end_space_runs

    with bind_scope("se0a5e00000x0", OWNER_ACCOUNT_ID):
        chat = _waiting_chat(store, space="se0a5e00000x0")
        threads.record_thread(chat.thread_id, "se0a5e00000x0", OWNER_ACCOUNT_ID)
    other = _waiting_chat(store)  # the owner's: it goes on
    assert end_space_runs("se0a5e00000x0") == 1
    _ended_cleanly(chat, "space_erased")
    assert runs_chat_turn._ended_turns()[chat.thread_id].space == "se0a5e00000x0"
    assert other.thread_id in reply_sink.open_turns_snapshot()
    assert [g.gate_id for g in hitl_gates.live_gates(other.thread_id)] == [other.gate_id]


# ── the review of f9a455918 ──────────────────────────────────────────────


@pytest.mark.parametrize(("resuming", "drove"), [(False, False), (True, False), (True, True)],
                         ids=["claimed", "resuming", "resuming-driven"])
def test_a_delete_racing_an_approve_keeps_the_persons_decision(store, web, resuming, drove) -> None:
    """F1: a gate a person answered keeps their decision -- never "no longer
    needed" (D2). One whose drive had started is an error with it (its call
    may have run); one whose drive never started is settled, its call
    recorded as not run (the review of ec538d779, L1:
    tests/test_chat_gone_race_lows.py)."""
    from kazma_ui.chat_runs_end import begin_drive, drive_clock

    chat = _waiting_chat(store)
    hitl_gates.claim_gate(chat.gate_id, "approve", "owner")
    if resuming:
        hitl_gates.mark_resuming(chat.gate_id)
    if drove:
        assert begin_drive(chat.thread_id, chat.gate_id, since=drive_clock())
    assert web.client.delete(f"/api/chat/sessions/{chat.session_id}").json() == {"status": "ok"}
    gate = hitl_gates.gate_for(chat.gate_id)
    assert gate is not None and gate.decision == "approve" and gate.actor == "owner", gate
    assert gate.state == ("error" if drove else "settled"), gate
    assert gate.ended_reason == "chat_deleted"
    assert hitl_gates.live_gates(chat.thread_id) == []


def test_a_gate_answered_between_the_read_and_the_end_keeps_its_answer(store) -> None:
    """F1, the race itself: a pending gate claimed just before its end is
    not taken from the person."""
    chat = _waiting_chat(store)
    hitl_gates.claim_gate(chat.gate_id, "approve", "owner")
    assert hitl_gates.end_gate(chat.gate_id, "pending", outcome="not_needed") is None
    gate = hitl_gates.gate_for(chat.gate_id)
    assert gate.state == "claimed" and gate.decision == "approve"


def test_clear_all_leaves_gates_that_are_not_a_chats(store, web) -> None:
    """F2: a ``kazma mcp`` bridge question and a swarm bus request are their
    own work's: clear-all neither answers them nor lists them as chat runs."""
    bridge = GateRow(gate_id=f"g-{uuid4().hex[:8]}", thread_id="mcp:watcher-1", tool="shell_exec",
                     mechanism="bus_bridge")
    swarm = GateRow(gate_id=f"g-{uuid4().hex[:8]}", thread_id="task-1", tool="file_write", mechanism="swarm_bus")
    for row in (bridge, swarm):
        hitl_gates.register_gate(row)
    chat = _waiting_chat(store)
    assert web.client.post("/api/sessions/clear-all").json()["deleted"] is True
    _ended_cleanly(chat, "chat_cleared")
    for row in (bridge, swarm):
        assert hitl_gates.gate_for(row.gate_id).state == "pending", row
        assert row.thread_id not in runs_chat_turn._ended_turns()


def test_a_delete_ends_only_the_chats_own_gates(store, web) -> None:
    """F2: a swarm request carried on the chat's thread is the swarm's."""
    chat = _waiting_chat(store)
    swarm = GateRow(gate_id=f"g-{uuid4().hex[:8]}", thread_id=chat.thread_id, tool="file_write",
                    mechanism="swarm_bus")
    hitl_gates.register_gate(swarm)
    assert web.client.delete(f"/api/chat/sessions/{chat.session_id}").json() == {"status": "ok"}
    assert hitl_gates.gate_for(chat.gate_id).decision == "not_needed"
    assert hitl_gates.gate_for(swarm.gate_id).state == "pending"


def test_archive_leaves_a_running_turn_to_finish(store, web) -> None:
    """F3: an archived chat's running answer finishes and is saved, as before
    the change; only what waits is ended."""
    chat = _waiting_chat(store, gate=False)
    loop = asyncio.new_event_loop()
    runner = threading.Thread(target=loop.run_forever, daemon=True)
    runner.start()
    try:
        async def turn() -> None:
            await asyncio.sleep(3600)

        task = asyncio.run_coroutine_threadsafe(_make_task(turn()), loop).result(5)
        active_turns.register_turn(chat.thread_id, task)
        answer = web.client.post(f"/api/chat/sessions/{chat.session_id}/archive").json()
        assert answer == {"status": "ok", "archived": True}
        for _ in range(3):  # whatever was handed to the turn's loop has run by now
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(5)
        assert not task.done()
        assert active_turns.is_turn_running(chat.thread_id)
        assert chat.thread_id in reply_sink.open_turns_snapshot()
        assert chat.thread_id not in runs_chat_turn._ended_turns()
        assert web.graph.updates == []
    finally:
        loop.call_soon_threadsafe(task.cancel)
        loop.call_soon_threadsafe(loop.stop)
        runner.join(5)
        loop.close()


def _paused_graph() -> tuple[Any, Any]:
    """A real LangGraph graph whose first node asks for an approval
    (``interrupt``), on an in-memory checkpointer."""
    from typing import TypedDict

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    class State(TypedDict, total=False):
        messages: list
        task_status: str
        auto_continue: bool

    def ask(state: State) -> dict[str, Any]:
        interrupt({"tool": "python_exec"})
        return {}

    def tools(state: State) -> dict[str, Any]:
        return {}

    builder = StateGraph(State)
    builder.add_node("ask", ask)
    builder.add_node("tools", tools)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", "tools")
    builder.add_edge("tools", END)
    saver = InMemorySaver()
    return builder.compile(checkpointer=saver), saver


def test_an_archived_paused_turn_is_never_continued_at_a_start(store) -> None:
    """F4: Stop's marker over a paused checkpoint leaves LangGraph a next
    node and no interrupt -- what the start pass reads as a cut turn, and
    continued, asking the approval again in the archived chat."""
    from kazma_ui.turn_abort import mark_turn_abandoned
    from kazma_ui.turn_resume import _find_cut_turns

    graph, saver = _paused_graph()
    thread = f"t-{uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}

    async def scenario() -> tuple[list[Any], list[Any], Any]:
        await graph.ainvoke({"messages": [{"role": "user", "content": "run it"}]}, config)
        paused = await _find_cut_turns(saver, graph)
        assert await mark_turn_abandoned(thread, graph)
        after = await _find_cut_turns(saver, graph)
        return paused, after, await graph.aget_state(config)

    paused, after, snapshot = asyncio.run(scenario())
    assert paused == []                       # paused: its card is the action
    assert snapshot.values.get("task_status") == "abandoned" and snapshot.next  # the shape the scan met
    assert [t.thread_id for t in after] == []


def test_an_archived_chats_cut_turn_is_never_continued(store) -> None:
    """F4: a turn a restart cut in a chat since archived is left alone; the
    same turn in a chat that shows is continued (the control)."""
    from typing import TypedDict

    from kazma_ui.turn_resume import _find_cut_turns
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    class State(TypedDict, total=False):
        messages: list

    builder = StateGraph(State)
    builder.add_node("think", lambda state: {})
    builder.add_node("tools", lambda state: {})
    builder.add_edge(START, "think")
    builder.add_edge("think", "tools")
    builder.add_edge("tools", END)
    saver = InMemorySaver()
    graph = builder.compile(checkpointer=saver, interrupt_before=["tools"])
    showing, archived = _waiting_chat(store, gate=False), _waiting_chat(store, gate=False)
    with as_owner():
        store.set_archived(archived.session_id, True)

    async def scenario() -> list[str]:
        for chat in (showing, archived):
            await graph.ainvoke({"messages": [{"role": "user", "content": "go"}]},
                                {"configurable": {"thread_id": chat.thread_id, "checkpoint_ns": ""}})
        return sorted(t.thread_id for t in await _find_cut_turns(saver, graph))

    with as_owner():
        assert asyncio.run(scenario()) == [showing.thread_id]


def _went_away_lines(caplog: Any, thread_id: str) -> list[str]:
    """The end's "went away" lines for THIS thread: a line of another test's
    leftover work in the same process is not this test's (2026-11-28)."""
    return [r.getMessage() for r in caplog.records if r.name == "kazma_ui.chat_runs_end"
            and "went away (" in r.getMessage() and f"thread={thread_id[:12]} " in r.getMessage()]


def test_two_ends_at_once_report_and_log_once(store, monkeypatch, caplog) -> None:
    """F6: two ends of one waiting chat at once; the first to take what is
    left reads the gate only once the other end is under way too. One takes
    it all and reports and logs; the other finds nothing. Every wait is a
    bounded condition (it was a 3 s barrier, and on a loaded Linux run the
    end whose gate write lost took the reply turn first: both reported --
    a real race, closed by ``chat_runs_end._one_end_at_a_time``)."""
    import contextlib
    import logging

    from kazma_ui import chat_runs_end
    from kazma_ui.chat_runs_end import end_chat_runs

    chat = _waiting_chat(store)
    real_live, real_going = hitl_gates.live_gates, chat_runs_end._going_away
    under_way = threading.Condition()
    ends = [0]
    first: list[bool] = []

    @contextlib.contextmanager
    def going(thread_id: str, reason: str = "") -> Any:
        with real_going(thread_id, reason):
            with under_way:
                ends[0] += 1
                under_way.notify_all()
            yield

    def read_once_both_are_under_way(thread_id: str) -> list[Any]:
        if not first:
            first.append(True)
            with under_way:
                assert under_way.wait_for(lambda: ends[0] >= 2, timeout=30), "the second end never started"
        return real_live(thread_id)

    monkeypatch.setattr(chat_runs_end, "_going_away", going)
    monkeypatch.setattr(hitl_gates, "live_gates", read_once_both_are_under_way)

    async def scenario() -> list[Any]:
        return list(await asyncio.gather(end_chat_runs(chat.thread_id, reason="chat_deleted"),
                                         end_chat_runs(chat.thread_id, reason="chat_deleted")))

    with caplog.at_level(logging.INFO, logger="kazma_ui.chat_runs_end"):
        one, other = asyncio.run(scenario())
    assert one.gates + other.gates == 1, (one, other)
    assert [one.turn, other.turn].count(True) == 1
    assert [one.recorded, other.recorded].count(True) == 1
    assert len(_went_away_lines(caplog, chat.thread_id)) == 1, caplog.text
    _ended_cleanly(chat, "chat_deleted")


def test_control_two_ends_without_the_end_lock_both_report(store, monkeypatch, caplog) -> None:
    """Control: without ``_one_end_at_a_time``, both ends read the gate,
    the one whose write wins waits (bounded) until the loser took the reply
    turn -- the Linux run's order, forced -- and both report and log."""
    import contextlib
    import logging

    from kazma_ui import chat_runs_end
    from kazma_ui.chat_runs_end import end_chat_runs

    chat = _waiting_chat(store)
    real_live, real_end, real_take = hitl_gates.live_gates, hitl_gates.end_gate, reply_sink.take_reply_turn
    readers = threading.Condition()
    read = [0]
    took = threading.Event()

    def read_together(thread_id: str) -> list[Any]:
        rows = real_live(thread_id)
        with readers:
            read[0] += 1
            readers.notify_all()
            assert readers.wait_for(lambda: read[0] >= 2, timeout=30), "the other end never read"
        return rows

    def end_then_let_the_loser_take_the_turn(*a: Any, **k: Any) -> Any:
        row = real_end(*a, **k)
        if row is not None:
            assert took.wait(timeout=30), "the loser never took the reply turn"
        return row

    def take(thread_id: str) -> str:
        got = real_take(thread_id)
        if got:
            took.set()
        return got

    monkeypatch.setattr(chat_runs_end, "_one_end_at_a_time", lambda thread_id: contextlib.nullcontext())
    monkeypatch.setattr(hitl_gates, "live_gates", read_together)
    monkeypatch.setattr(hitl_gates, "end_gate", end_then_let_the_loser_take_the_turn)
    monkeypatch.setattr(reply_sink, "take_reply_turn", take)

    async def scenario() -> list[Any]:
        return list(await asyncio.gather(end_chat_runs(chat.thread_id, reason="chat_deleted"),
                                         end_chat_runs(chat.thread_id, reason="chat_deleted")))

    with caplog.at_level(logging.INFO, logger="kazma_ui.chat_runs_end"):
        one, other = asyncio.run(scenario())
    assert one.gates + other.gates == 1 and [one.turn, other.turn].count(True) == 1
    assert len(_went_away_lines(caplog, chat.thread_id)) == 2, caplog.text


# ── the class gate ───────────────────────────────────────────────────────

#: What ends a chat's runs (kazma_ui/chat_runs_end.py, and the chat store's
#: own way to it).
ENDERS = frozenset({"end_chat_runs", "chat_went_away", "end_space_runs", "end_every_chat_run",
                    "_chat_went_away"})
#: Chat-store methods whose caller names the chat that goes (checked at the
#: caller, below).
ROW_REMOVERS = frozenset({"delete", "delete_if", "set_archived"})
#: What removes a thread's whole state: the checkpointer's thread deletes,
#: and a space's rows in every store (its chats, checkpoints, gates, thread
#: records: ``space_data._erase_rows``).
STATE_REMOVERS = frozenset({"adelete_thread", "adelete_all_threads", "_erase_rows"})
#: Where a function gets the chat store from.
STORE_GETTERS = frozenset({"get_session_manager", "peek_session_manager", "_get_store"})
STORE_MODULE = "kazma-ui/kazma_ui/session_manager.py"
_REMOVING_SQL = re.compile(r"\bDELETE\s+FROM\s+(sessions|kazma_chat_sessions)\b", re.I)

#: Ways a chat goes away that end no run, and why.
DECLARED: dict[tuple[str, str], str] = {
    ("kazma-ui/kazma_ui/chat_move.py", "_put_back"):
        "takes away a move's copy only while it is exactly the copy (_still_the_copy): nothing was "
        "written in it, so no turn ever ran on its new thread; the chat itself stays",
    ("kazma-core/kazma_core/spaces/space_move.py", "_take_back"):
        "takes back what a space import wrote before the import stood: nothing of that space was here "
        "before it began, its threads are released (never ended), and none of its chats ran a turn",
    ("kazma-core/kazma_core/sessions/directory.py", "prune_twin_sessions"):
        "archives a chat app's auto-titled twin; the thread goes on in the named chat it keeps, and "
        "so does its turn",
}
#: Chat-store methods that remove a chat and end no run, and why.
STORE_DECLARED: dict[str, str] = {
    "clear": "every chat of the space, for the tests' reset; no product code calls it",
    "prune_empty_web_sessions": "an empty web shell (no message, no custom title, not pinned), not "
                                "marked deleted: a turn writes its question first, so none ran in it",
    "hand_over": "takes back only the copy it just wrote, which nobody has seen (the move did not commit)",
    "space_erased": "the last step of erase_space, which ended the space's runs (end_space_runs) before any "
                    "of its rows went; the gate checks that order there (_erase_rows)",
    "reset_session_manager": "the tests' reset of the store",
}


@dataclass
class Func:
    module: str
    qualname: str
    name: str
    is_route: bool
    removals: list[tuple[str, int]] = field(default_factory=list)  # ("row" | "state", line)
    enders: list[int] = field(default_factory=list)
    names: list[tuple[str, int]] = field(default_factory=list)      # Name loads
    attrs: list[tuple[str, int]] = field(default_factory=list)      # attribute uses
    imports: set[str] = field(default_factory=set)


def _product_sources() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py"],
                            cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    return {rel: (REPO / rel).read_text(encoding="utf-8", errors="ignore") for rel in listed
            if "_tests/" not in rel and "/tests/" not in rel and (REPO / rel).exists()}


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _as_called(node: ast.Call) -> ast.Call:
    """The call *node* makes: ``asyncio.to_thread(f, a, b)`` is ``f(a, b)``."""
    if _called_name(node) == "to_thread" and node.args:
        return ast.Call(func=node.args[0], args=list(node.args[1:]), keywords=list(node.keywords))
    return node


def _archives(node: ast.Call) -> bool:
    """``set_archived(x, True)`` or with a value that is not the constant False."""
    value = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "archived"),
                                                          None)
    return not (isinstance(value, ast.Constant) and value.value is False)


def _is_route(node: ast.AST) -> bool:
    for deco in getattr(node, "decorator_list", []):
        if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute) and deco.func.attr in (
                "get", "post", "put", "patch", "delete", "websocket", "api_route"):
            return True
    return False


def functions(module: str, source: str) -> list[Func]:
    """Every function of *module*, with what it removes, ends and names; a
    call belongs to its innermost function."""
    tree = ast.parse(source)
    imports = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
               for alias in node.names}
    found: list[Func] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func = Func(module, f"{prefix}{child.name}", child.name, _is_route(child), imports=imports)
                _read(child, func)
                found.append(func)
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    def _read(fn: ast.AST, func: Func) -> None:
        stores: set[str] = set()
        body = list(_own_nodes(fn))
        for node in body:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and isinstance(
                    getattr(node, "value", None), ast.Call) and _called_name(node.value) in STORE_GETTERS:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                stores |= {t.id for t in targets if isinstance(t, ast.Name)}
        for node in body:
            if isinstance(node, ast.Call):
                call = _as_called(node)
                name = _called_name(call)
                if name in ENDERS:
                    func.enders.append(node.lineno)
                if name in STATE_REMOVERS and func.name not in STATE_REMOVERS:
                    func.removals.append(("state", node.lineno))
                elif name in ("delete_if",) or (name == "set_archived" and _archives(call)):
                    func.removals.append(("row", node.lineno))
                elif name == "delete" and isinstance(call.func, ast.Attribute) and (
                        (isinstance(call.func.value, ast.Name) and call.func.value.id in stores)
                        or (isinstance(call.func.value, ast.Call) and _called_name(call.func.value) in STORE_GETTERS)):
                    func.removals.append(("row", node.lineno))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                func.names.append((node.id, node.lineno))
            elif isinstance(node, ast.Attribute):
                func.attrs.append((node.attr, node.lineno))

    visit(tree, "")
    return found


def _own_nodes(fn: ast.AST) -> list[ast.AST]:
    """The nodes of *fn* outside the functions defined inside it."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def unended(sources: dict[str, str], declared: dict[tuple[str, str], str] | None = None) -> list[str]:
    """Every removal of a chat outside the chat store that ends no run:
    ``module:function:line (row|state)``. A function is covered when it
    calls an ender -- before the state goes, for a state removal -- or when
    every product function that names it is (a helper), or it is declared."""
    declared = DECLARED if declared is None else declared
    funcs = [f for module, text in sources.items() if module != STORE_MODULE for f in functions(module, text)]
    by_name: dict[str, list[tuple[Func, int]]] = {}
    for func in funcs:
        for name, line in func.names:
            by_name.setdefault(name, []).append((func, line))
        for attr, line in func.attrs:
            by_name.setdefault("." + attr, []).append((func, line))

    def callers(func: Func) -> list[tuple[Func, int]]:
        named = [(g, line) for g, line in by_name.get(func.name, [])
                 if g is not func and (g.module == func.module or func.name in g.imports)]
        return named + [(g, line) for g, line in by_name.get("." + func.name, []) if g is not func]

    def covered(func: Func, kind: str, line: int, seen: frozenset[int]) -> bool:
        if (func.module, func.qualname) in declared:
            return True
        if any(kind == "row" or at < line for at in func.enders):
            return True
        if func.is_route or id(func) in seen:
            return False
        uses = callers(func)
        return bool(uses) and all(covered(g, kind, at, seen | {id(func)}) for g, at in uses)

    out = []
    for func in funcs:
        for kind, line in func.removals:
            if not covered(func, kind, line, frozenset()):
                out.append(f"{func.module}:{func.qualname}:{line} ({kind})")
    return sorted(out)


def store_methods_that_end_nothing(source: str) -> list[str]:
    """The chat store's own removals (a ``DELETE`` of a chat row or an
    archive) that neither end the chat's runs nor leave it to a caller who
    names the chat, nor are declared."""
    tree = ast.parse(source)
    manager = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SessionManager")
    methods = {n.name: n for n in manager.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    module_funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def removes(node: ast.AST) -> bool:
        for sub in _own_nodes(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and _REMOVING_SQL.search(sub.value):
                return True
            if isinstance(sub, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "archived"
                                                   for t in sub.targets):
                return True
        return False

    def calls(node: ast.AST) -> set[str]:
        return {sub.func.attr for sub in _own_nodes(node) if isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute) and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == "self"}

    # The store's own ways to an ender, closed over its methods: what the
    # eviction queues is ended by _end_evicted, which _locked runs once the
    # store's lock is released (the review of 974f2ae85, N1).
    store_enders = {name for name, node in methods.items()
                    if any(isinstance(sub, ast.Call) and _called_name(sub) in ENDERS for sub in _own_nodes(node))}
    while True:
        grown = {name for name, node in methods.items() if name not in store_enders
                 and calls(node) & store_enders}
        if not grown:
            break
        store_enders |= grown

    def ends(node: ast.AST) -> bool:
        direct = any(isinstance(sub, ast.Call) and _called_name(sub) in ENDERS for sub in _own_nodes(node))
        return direct or bool(calls(node) & store_enders)

    removing = {name for name, node in methods.items() if removes(node)}
    while True:  # a method that calls a removal that does not end runs itself removes too
        more = {name for name, node in methods.items() if name not in removing
                and any(m in removing and not ends(methods[m]) and m not in STORE_DECLARED
                        for m in calls(node))}
        if not more:
            break
        removing |= more
    out = []
    for name in sorted(removing):
        if ends(methods[name]) or name in ROW_REMOVERS or name in STORE_DECLARED:
            continue
        if name.startswith("_") and name in {m for n in removing for m in calls(methods[n])}:
            continue  # private: every method that calls it is checked here
        out.append(f"SessionManager.{name}")
    out += [name for name, node in module_funcs.items() if removes(node) and not ends(node)
            and name not in STORE_DECLARED]
    return out


def test_every_way_a_chat_goes_away_ends_its_runs() -> None:
    sources = _product_sources()
    missing = unended(sources)
    assert not missing, (
        "A chat goes away here and what it had running or waiting stays: a turn waiting on an approval "
        "is paged \"stuck: nothing is running it\". End it with kazma_ui.chat_runs_end.end_chat_runs "
        "before the state goes, or declare why in DECLARED: " + ", ".join(missing))
    assert not store_methods_that_end_nothing(sources[STORE_MODULE])


def test_the_gate_sees_every_way_in_the_source() -> None:
    """What it enumerates is what is there: each route and store method this
    fix changed is found removing a chat, and each is covered."""
    sources = _product_sources()
    removing = {(f.module, f.qualname) for module, text in sources.items() if module != STORE_MODULE
                for f in functions(module, text) if f.removals}
    for expected in (
        ("kazma-ui/kazma_ui/chat_delete.py", "_remove_row"),
        ("kazma-ui/kazma_ui/chat_delete.py", "remove_chat_remains"),
        ("kazma-ui/kazma_ui/sse_chat/__init__.py", "create_sse_chat_router.archive_session"),
        ("kazma-ui/kazma_ui/dashboard.py", "delete_session"),
        ("kazma-ui/kazma_ui/dashboard.py", "clear_all_sessions"),
        ("kazma-ui/kazma_ui/dashboard.py", "_delete_chats_of_thread"),
        ("kazma-ui/kazma_ui/chat_move.py", "_remove_copy_row"),
        ("kazma-core/kazma_core/sessions/directory.py", "prune_twin_sessions"),
    ):
        assert expected in removing, expected
    assert any(m == "kazma-gateway/kazma_gateway/agent_handler/graph.py" for m, _q in removing)
    assert any(m == "kazma-ui/kazma_ui/routes_direct/misc.py" for m, _q in removing)
    assert ("kazma-core/kazma_core/spaces/space_data.py", "erase_space") in removing
    tree = ast.parse(sources[STORE_MODULE])
    manager = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SessionManager")
    ends_itself = {n.name for n in manager.body if isinstance(n, ast.FunctionDef)
                   and any(isinstance(s, ast.Call) and _called_name(s) in ENDERS for s in _own_nodes(n))}
    assert "_end_evicted" in ends_itself


def test_every_declaration_is_still_needed() -> None:
    """Each declaration covers a way a chat goes away that the source still
    has: without it, the gate fails."""
    sources = _product_sources()
    for key in DECLARED:
        without = {k: v for k, v in DECLARED.items() if k != key}
        assert unended(sources, without), f"{key} is declared, but nothing needs it any more"
    store_names = {n.name for n in ast.walk(ast.parse(sources[STORE_MODULE]))
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert set(STORE_DECLARED) <= store_names


_PLANTED_ROW = '''
from kazma_ui.session_manager import get_session_manager

def drop(session_id):
    chats = get_session_manager()
    chats.delete(session_id)
'''

_PLANTED_LATE = '''
from kazma_ui.chat_runs_end import end_chat_runs

@router.post("/x/{thread_id}/clear")
async def clear(thread_id):
    await checkpoints.adelete_thread(thread_id)
    await end_chat_runs(thread_id, reason="chat_cleared")
'''

_PLANTED_HELPER = '''
def _wipe(store, sid):
    store.delete_if(sid, lambda row: True)

@router.delete("/y/{sid}")
def route(sid):
    _wipe(get_session_manager(), sid)
'''

_COVERED = '''
from kazma_ui.chat_runs_end import end_chat_runs
from kazma_ui.session_manager import get_session_manager

def _drop(session_id):
    get_session_manager().delete(session_id)

@router.delete("/z/{thread_id}")
async def delete(thread_id, session_id):
    await end_chat_runs(thread_id, reason="chat_deleted")
    await asyncio.to_thread(_drop, session_id)
    await checkpoints.adelete_thread(thread_id)
'''


def test_control_a_removal_that_ends_no_run_is_caught() -> None:
    assert unended({"kazma-ui/kazma_ui/planted.py": _PLANTED_ROW}) == [
        "kazma-ui/kazma_ui/planted.py:drop:6 (row)"]
    assert unended({"kazma-ui/kazma_ui/planted.py": _PLANTED_HELPER}) == [
        "kazma-ui/kazma_ui/planted.py:_wipe:3 (row)"]
    # The covered shape passes: the gate is not a refusal of every delete.
    assert unended({"kazma-ui/kazma_ui/planted.py": _COVERED}) == []


def test_control_an_ender_after_the_state_is_gone_is_caught() -> None:
    assert unended({"kazma-ui/kazma_ui/planted.py": _PLANTED_LATE}) == [
        "kazma-ui/kazma_ui/planted.py:clear:6 (state)"]


def test_control_a_store_method_that_deletes_and_ends_nothing_is_caught() -> None:
    source = (REPO / STORE_MODULE).read_text(encoding="utf-8")
    planted = source.replace(
        "    def clear(self) -> None:",
        "    def forget_old(self) -> None:\n"
        "        self._conn.execute(\"DELETE FROM sessions WHERE updated_at < '2020'\")\n\n"
        "    def clear(self) -> None:", 1)
    assert planted != source
    assert store_methods_that_end_nothing(planted) == ["SessionManager.forget_old"]
    # And the eviction without its ender, as it was: every save that can
    # evict a chat removes one (a private method's removal is its callers').
    old = source.replace('self._chat_went_away(held, session_id, "chat_store_full")', "pass", 1)
    assert old != source
    assert store_methods_that_end_nothing(old) == [
        "SessionManager.add_usage", "SessionManager.get_or_create", "SessionManager.put",
        "SessionManager.transact", "SessionManager.update_from_dict"]


# ── the words ────────────────────────────────────────────────────────────


def test_every_reason_is_worded_in_english_and_arabic() -> None:
    from kazma_core.runs.text import ended_word
    from kazma_ui.chat_runs_end import REASONS
    from kazma_ui.i18n import TRANSLATIONS

    arabic = re.compile(r"[؀-ۿ]")
    for reason in REASONS:
        entry = TRANSLATIONS[f"runs.ended.{reason}"]
        assert entry["en"] and not arabic.search(entry["en"]), reason
        assert arabic.search(entry["ar"]), reason
        assert ended_word(reason, "en") == entry["en"] and ended_word(reason, "ar") == entry["ar"]
    worded = {k.removeprefix("runs.ended.") for k in TRANSLATIONS if k.startswith("runs.ended.")}
    assert worded == set(REASONS)


def test_every_reason_the_source_passes_is_one_the_catalog_words() -> None:
    from kazma_ui.chat_runs_end import REASONS

    passed = set()
    for text in _product_sources().values():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and _called_name(node) in ENDERS:
                passed |= {k.value.value for k in node.keywords if k.arg == "reason"
                           and isinstance(k.value, ast.Constant)}
                passed |= {a.value for a in node.args[2:3] if isinstance(a, ast.Constant)}
    assert passed and passed <= set(REASONS), sorted(passed - set(REASONS))
    with pytest.raises(ValueError):
        asyncio.run(_ender_refuses())


async def _ender_refuses() -> None:
    from kazma_ui.chat_runs_end import end_chat_runs

    await end_chat_runs("t-x", reason="gone fishing")


def test_the_reasons_are_json_safe_in_the_run() -> None:
    from kazma_core.runs.contract import Run, RunState

    run = Run(run_id="chat_turn:t", kind="chat_turn", native_id="t", title="x", state=RunState.CANCELLED,
              native_state="ended", ended_reason="chat_deleted")
    assert json.loads(json.dumps(run.to_dict()))["ended_reason"] == "chat_deleted"
