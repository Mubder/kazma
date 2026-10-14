"""Time Travel through the chat apps' real handler and the TUI (TT-Q1).

The audit exercised the routes and the engine; nothing drove ``/replay`` and
``/fork`` through the gateway's message handler (the closures
``_handle_replay``/``_handle_fork`` of ``create_graph_handler``) or the TUI's
commands. Here both run on the real graph and saver of ``tests/_tt_graph.py``:
a sender's two-turn chat is listed, restored to its first turn and forked
from the chat apps, and the TUI lists, restores and forks over the Time
Travel API. The TUI test talks to the real routes in-process (an ASGI
transport on the test's own loop, where the saver lives).

Negative control: the old step reference. An iteration number (``/replay 1``)
named a different step on each surface (TT-01); it now names no step, and
the handler says so instead of restoring.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests import _tt_graph as tt


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from kazma_core import time_travel, time_travel_audit
    from kazma_core.safety import hitl_gates

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    hitl_gates.set_db_path_for_tests(str(tmp_path / "hitl_gates.db"))
    time_travel_audit.set_db_path_for_tests(str(tmp_path / "tt_audit.db"))
    time_travel.set_process_recorder(None)
    yield
    time_travel.set_process_recorder(None)
    hitl_gates.set_db_path_for_tests(None)
    time_travel_audit.set_db_path_for_tests(None)


def _contents(snapshot: Any) -> list[str]:
    return [str(m.get("content") or "") for m in (snapshot.values or {}).get("messages") or []]


def _turn_one_last_step(recorder: Any, thread_id: str) -> str:
    return [s for s in recorder.list_snapshots(thread_id) if s.turn_index == 1][-1].id


def test_the_chat_apps_list_restore_and_fork_through_the_real_handler(tmp_path, monkeypatch):
    from kazma_core.sessions import directory
    from kazma_core.time_travel import SnapshotRecorder, set_process_recorder
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore
    from kazma_gateway.gateway import IncomingMessage

    rec = SnapshotRecorder(db_path=str(tmp_path / "snapshots.db"))
    set_process_recorder(rec)
    thread_id = f"gw-discord-{uuid.uuid4().hex[:8]}"
    sender = "discord:4242"
    # The sender is on this chat, as after /session (the directory's answer).
    monkeypatch.setattr(directory, "find_mouth_thread", lambda *a, **k: thread_id)

    async def main() -> dict[str, Any]:
        async with tt.open_saver("sqlite", tmp_path) as cp:
            graph = tt.build_graph(rec, [], cp)
            await tt.run_turn(graph, thread_id, "tea please")
            await tt.run_turn(graph, thread_id, "coffee please")
            manager = SimpleNamespace(send=AsyncMock(), adapters=[])
            handler = create_graph_handler(graph=graph, manager=manager, store=_InMemoryStore())

            async def say(text: str) -> str:
                manager.send.reset_mock()
                await handler(IncomingMessage("discord", sender, text,
                                              context_metadata={"channel_id": "c1", "user_id": "4242"}))
                return "\n".join(call.args[0].text for call in manager.send.await_args_list)

            out: dict[str, Any] = {}
            out["list"] = await say("/replay list")
            target = _turn_one_last_step(rec, thread_id)
            out["target"] = target
            out["by_iteration"] = await say("/replay 1")
            out["after_iteration"] = _contents(await graph.aget_state(tt.config(thread_id)))
            out["restore"] = await say(f"/replay {target[:8]}")
            out["after_restore"] = _contents(await graph.aget_state(tt.config(thread_id)))
            out["steps_after"] = [s.turn_index for s in rec.list_snapshots(thread_id)]
            out["fork"] = await say(f"/fork {target[:8]}")
            new_id = next(w.strip("`") for w in out["fork"].split() if w.strip("`").startswith("gw-discord-discord_"))
            out["fork_state"] = _contents(await graph.aget_state(tt.config(new_id)))
            out["fork_owner"] = directory.thread_owner(new_id)
            out["source_after_fork"] = _contents(await graph.aget_state(tt.config(thread_id)))
            return out

    out = tt.run(main())
    # Listed by turn, each step by its id.
    assert "turn 1" in out["list"] and "turn 2" in out["list"], out["list"]
    assert out["target"][:8] in out["list"]
    # The old reference (an iteration number) names no step: nothing restored.
    assert "No such step" in out["by_iteration"], out["by_iteration"]
    assert any("coffee" in c for c in out["after_iteration"])
    # Restored to turn 1: turn 2 is gone from the chat and from the steps.
    assert "Restored this chat to step" in out["restore"], out["restore"]
    assert any("tea" in c for c in out["after_restore"])
    assert not any("coffee" in c for c in out["after_restore"]), out["after_restore"]
    assert set(out["steps_after"]) == {1}
    # Forked: a new chat with turn 1, owned by the sender; the source unchanged.
    assert "Forked step" in out["fork"], out["fork"]
    assert any("tea" in c for c in out["fork_state"]) and not any("coffee" in c for c in out["fork_state"])
    assert out["fork_owner"] == sender
    assert out["source_after_fork"] == out["after_restore"]


def test_a_chat_app_restore_under_a_pending_approval_is_refused_by_the_handler(tmp_path, monkeypatch):
    """TT-02 on the chat apps: the turn paused at an approval keeps its card;
    ``/replay <step>`` names the waiting tool and changes nothing."""
    from kazma_core.sessions import directory
    from kazma_core.time_travel import SnapshotRecorder, set_process_recorder
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore
    from kazma_gateway.gateway import IncomingMessage

    rec = SnapshotRecorder(db_path=str(tmp_path / "snapshots.db"))
    set_process_recorder(rec)
    thread_id = f"gw-discord-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(directory, "find_mouth_thread", lambda *a, **k: thread_id)
    executed: list[str] = []

    async def main() -> dict[str, Any]:
        async with tt.open_saver("sqlite", tmp_path) as cp:
            graph = tt.build_graph(rec, executed, cp)
            await tt.run_turn(graph, thread_id, "tea please")
            await tt.run_turn(graph, thread_id, "rm:build")  # paused at its approval
            before = await graph.aget_state(tt.config(thread_id))
            manager = SimpleNamespace(send=AsyncMock(), adapters=[])
            handler = create_graph_handler(graph=graph, manager=manager, store=_InMemoryStore())
            target = _turn_one_last_step(rec, thread_id)
            await handler(IncomingMessage("discord", "discord:4242", f"/replay {target[:8]}",
                                          context_metadata={"channel_id": "c1", "user_id": "4242"}))
            after = await graph.aget_state(tt.config(thread_id))
            return {"said": "\n".join(c.args[0].text for c in manager.send.await_args_list),
                    "before": (before.next, tt.live_interrupt(before)[0]),
                    "after": (after.next, tt.live_interrupt(after)[0])}

    out = tt.run(main())
    assert "An approval is waiting in this chat (rm)" in out["said"], out["said"]
    assert out["after"] == out["before"] and out["after"][1], "the card was not left as it was"
    assert executed == []


class _Tui:
    """The TUI chat panel's Time Travel commands, without a terminal: the
    real methods, ``_api`` over the real routes, ``write`` collected."""

    def __init__(self, client: Any, thread_id: str) -> None:
        self.client = client
        self._thread_id = thread_id
        self._session_id = thread_id
        self._messages: list[dict[str, Any]] = []
        self.lines: list[tuple[str, str]] = []

    async def _api(self, method: str, path: str, payload: Any | None = None) -> Any:
        resp = await self.client.request(method, path, json=payload)
        return resp.json()

    def write(self, role: str, text: str, *, ts: str | None = None) -> None:
        self.lines.append((role, text))

    async def _load_season_messages(self, session_id: str, *, thread_id: str = "") -> int:
        return 0

    def _replay_season_log(self) -> None:
        return None


def test_the_tui_lists_restores_and_forks_over_the_api(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI

    from kazma_core.time_travel import SnapshotRecorder, set_process_recorder
    from kazma_tui.chat import ChatPanel
    from kazma_ui import thread_ownership
    from kazma_ui.replay_routes import create_replay_router

    for name in ("_replay_steps", "_replay_step_id", "_cmd_replay", "_cmd_fork", "_replay_thread_id"):
        setattr(_Tui, name, getattr(ChatPanel, name))
    monkeypatch.setattr(thread_ownership, "require_thread_owned", AsyncMock(return_value=None))
    rec = SnapshotRecorder(db_path=str(tmp_path / "snapshots.db"))
    set_process_recorder(rec)
    thread_id = f"tui-{uuid.uuid4().hex[:8]}"

    async def main() -> dict[str, Any]:
        async with tt.open_saver("sqlite", tmp_path) as cp:
            graph = tt.build_graph(rec, [], cp)
            await tt.run_turn(graph, thread_id, "tea please")
            await tt.run_turn(graph, thread_id, "coffee please")
            app = FastAPI()
            app.include_router(create_replay_router(recorder=rec, graph=graph))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://tui.test") as client:
                tui = _Tui(client, thread_id)
                await tui._cmd_replay("/replay list")
                listing = tui.lines[-1][1]
                target = _turn_one_last_step(rec, thread_id)
                await tui._cmd_replay(f"/replay {target[:8]}")
                restored_line = tui.lines[-1]
                after = _contents(await graph.aget_state(tt.config(thread_id)))
                await tui._cmd_fork(f"/fork {target[:8]}")
                fork_line = tui.lines[-1]
                fork_state = _contents(await graph.aget_state(tt.config(tui._thread_id)))
        return {"listing": listing, "target": target, "restored": restored_line, "after": after,
                "fork": fork_line, "fork_state": fork_state, "fork_id": tui._thread_id}

    out = tt.run(main())
    assert "turn 1" in out["listing"] and "turn 2" in out["listing"], out["listing"]
    assert out["target"][:8] in out["listing"]
    assert out["restored"][0] == "system" and "Restored season to step" in out["restored"][1], out["restored"]
    assert not any("coffee" in c for c in out["after"]) and any("tea" in c for c in out["after"])
    assert out["fork"][0] == "system" and "Forked step" in out["fork"][1], out["fork"]
    assert out["fork_id"] != thread_id
    assert any("tea" in c for c in out["fork_state"]) and not any("coffee" in c for c in out["fork_state"])
