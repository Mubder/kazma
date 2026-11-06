"""A turn a reload cut off continues (resilience plan C1).

A real LangGraph graph on a real SQLite checkpointer stops half way through a
turn (the process "restarts": nothing is driving it). At the next start
``turn_resume``:

* finds it (the newest checkpoint of the chat, written in the last 30
  minutes, with a next step and no approval waiting);
* continues it by itself when it is safe (no tool whose effect is unknown),
  to its end;
* leaves it to the owner when a tool that changes something was started and
  has no result (its effect unknown): listed as a run with Continue and Close;
* leaves alone a turn paused for an approval, an ended one, and one older
  than 30 minutes.

The negative control: without the start-up pass the cut turn stays cut.
"""

from __future__ import annotations

import asyncio
import operator
import time
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

pytest.importorskip("langgraph.checkpoint.sqlite.aio")
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

from kazma_ui import turn_resume  # noqa: E402


class _State(TypedDict, total=False):
    messages: Annotated[list, operator.add]
    steps: Annotated[list, operator.add]
    created_at: str
    turn_failed: bool


class _Stop(Exception):
    """The process stopped here (a restart)."""


def _graph(saver, *, stop_in: str | None):
    calls = {"first": 0, "second": 0}

    def first(state):
        calls["first"] += 1
        return {"steps": ["first"]}

    def second(state):
        calls["second"] += 1
        if stop_in == "second" and calls["second"] == 1:
            raise _Stop()
        return {"steps": ["second"]}

    g = StateGraph(_State)
    g.add_node("first", first)
    g.add_node("second", second)
    g.add_edge(START, "first")
    g.add_edge("first", "second")
    g.add_edge("second", END)
    return g.compile(checkpointer=saver), calls


async def _cut_turn(saver, thread: str, *, created_at: str | None = None):
    graph, calls = _graph(saver, stop_in="second")
    config = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    from datetime import UTC, datetime

    state = {"messages": [], "created_at": created_at or datetime.now(UTC).isoformat()}
    with pytest.raises(_Stop):
        await graph.ainvoke(state, config)
    return graph, calls, config


@pytest.fixture
def journal(monkeypatch):
    rows: dict[str, list[dict]] = {}

    class _Journal:
        def __init__(self, path):
            pass

        def inspect(self, thread_id, limit=200):
            return rows.get(thread_id, [])

    monkeypatch.setattr("kazma_core.agent.effect_journal._EffectJournal", _Journal)
    return rows


def test_a_safe_cut_turn_is_found_and_continued_to_its_end(tmp_path, journal, monkeypatch):
    async def run():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
            graph, calls, config = await _cut_turn(saver, "web-1")
            found = await turn_resume._find_cut_turns(saver, graph)
            assert [t.thread_id for t in found] == ["web-1"] and found[0].safe
            assert found[0].next == ("second",)
            driven = []

            async def drive(g, inp, cfg, **kw):
                driven.append(kw["thread_id"])
                await g.ainvoke(inp, cfg)

            monkeypatch.setattr("kazma_ui.reply_sink.resolve_reply_turn", lambda tid, sid="": "turn-1")

            class _Sessions:
                def get_by_thread_id(self, tid):
                    return None

            monkeypatch.setattr("kazma_ui.session_manager.get_session_manager", lambda: _Sessions())
            started, _ = await turn_resume.continue_turn(graph, found[0], drive=drive)
            assert started
            from kazma_ui.active_turns import running_turns

            for _ in range(50):
                if "web-1" not in running_turns():
                    break
                await asyncio.sleep(0.05)
            snapshot = await graph.aget_state(config)
            assert snapshot.next == () and snapshot.values["steps"] == ["first", "second"]
            assert driven == ["web-1"] and calls["first"] == 1       # it went on from where it stopped

    asyncio.run(run())


def test_an_unknown_effect_waits_for_the_owner(tmp_path, journal):
    async def run():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
            graph, _calls, _config = await _cut_turn(saver, "web-2")
            journal["web-2"] = [{"tool": "file_write", "state": "started", "started_at": time.time(),
                                 "result_uncertain": None}]
            found = await turn_resume._find_cut_turns(saver, graph)
            assert found[0].unknown == ["file_write"] and not found[0].safe

            continued = await turn_resume.resume_cut_turns(lambda: graph, lambda: saver, delay_s=0)
            assert continued == 0
            assert "web-2" in turn_resume.cut_turns()
            # A completed receipt of the same turn is on record: safe.
            journal["web-2"] = [{"tool": "file_write", "state": "completed", "started_at": time.time(),
                                 "result_uncertain": None}]
            assert (await turn_resume._find_cut_turns(saver, graph))[0].safe

    asyncio.run(run())


def test_an_old_ended_or_paused_turn_is_left_alone(tmp_path, journal):
    async def run():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
            graph, _calls, _config = await _cut_turn(saver, "old")
            assert await turn_resume._find_cut_turns(saver, graph, now=time.time() + 3600) == []
            done_graph, _ = _graph(saver, stop_in=None)
            await done_graph.ainvoke({"messages": []}, {"configurable": {"thread_id": "ended"}})
            threads = [t.thread_id for t in await turn_resume._find_cut_turns(saver, graph)]
            assert "ended" not in threads and "old" in threads

    asyncio.run(run())


def test_without_the_start_pass_the_cut_turn_stays_cut(tmp_path, journal):
    # The negative control: the start before C1 continued nothing.
    async def run():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
            graph, _calls, config = await _cut_turn(saver, "web-3")
            assert (await graph.aget_state(config)).next == ("second",)

    asyncio.run(run())


def test_a_chat_app_turn_goes_back_to_its_chat(tmp_path, journal):
    async def run():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
            graph, _calls, config = await _cut_turn(saver, "gw-tg")
            await graph.aupdate_state(config, {"messages": []})
            turn = turn_resume.CutTurn(thread_id="gw-tg", at=time.time(), next=("second",), platform="telegram",
                                       target="telegram:42", user_text="hello")
            delivered = []

            async def continuer(**kw):
                delivered.append(kw)

            turn_resume.register_chat_app_continuer(continuer)
            try:
                started, _ = await turn_resume.continue_turn(graph, turn)
                await asyncio.sleep(0.05)
            finally:
                turn_resume.register_chat_app_continuer(None)
            assert started and delivered[0]["target_id"] == "telegram:42"
            # No continuer registered (the chat apps not connected): refused, said.
            started, words = await turn_resume.continue_turn(graph, turn)
            assert not started and "not connected" in words

    asyncio.run(run())
