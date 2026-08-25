"""Turn Delivery V2 — WebSocket transport integration tests (plan P1).

Covers the ws_chat.py wiring of docs/plans/TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md:
broker-backed sender (multi-tab fan-out, journal-while-orphaned), the
structured ``resumed`` handshake replacing prose string-matching, cursor
replay over both the ``?last_seq=`` connect form and the mid-connection
``{"action":"resume"}`` form, and legacy-client compatibility.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kazma_ui.routes.ws_chat as ws_chat_mod
from kazma_ui.active_turns import register_turn, unregister_turn
from kazma_ui.delivery import (
    TurnBroker,
    TurnJournal,
    get_turn_broker,
    reset_turn_broker,
)
from kazma_ui.routes.ws_chat import (
    _make_ws_sender,
    _ws_resume_handshake,
    create_ws_chat_router,
)
from kazma_ui.session_manager import get_session_manager, reset_session_manager


class _FakeSocket:
    """Duck-typed Starlette WebSocket stand-in collecting sent frames."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.frames.append(payload)


@pytest.fixture(autouse=True)
def _fresh_broker_and_sessions():
    reset_turn_broker()
    reset_session_manager()
    yield
    reset_turn_broker()
    reset_session_manager()


# ── Sender routing ────────────────────────────────────────────────────────


class TestBrokerBackedSender:
    @pytest.mark.asyncio
    async def test_send_fans_out_to_all_bound_tabs(self) -> None:
        broker = get_turn_broker()
        tab_a, tab_b = _FakeSocket(), _FakeSocket()
        ca = broker.register_socket("tX", tab_a)
        cb = broker.register_socket("tX", tab_b)
        try:
            send, is_lost = _make_ws_sender(tab_a, "tX")
            assert is_lost() is False
            assert await send({"type": "token", "data": {"content": "hi"}}) is True
            # Both tabs received the SAME seq-stamped frame.
            assert tab_a.frames[0]["seq"] == 1
            assert tab_b.frames[0]["seq"] == 1
            assert tab_b.frames[0]["data"]["content"] == "hi"
        finally:
            broker.unregister_socket("tX", ca)
            broker.unregister_socket("tX", cb)

    @pytest.mark.asyncio
    async def test_send_with_no_audience_journals_for_later_resume(self) -> None:
        # THE plan scenario at delivery layer: turn keeps streaming after the
        # tab died; frames are journaled even though nobody is listening.
        send, is_lost = _make_ws_sender(object(), "tY")
        assert is_lost() is True
        assert await send({"type": "llm_delta", "data": {"content": "chunk"}}) is False
        frames, gap, head = get_turn_broker().resume("tY", 0)
        assert gap is False
        assert head == 1
        assert frames[0]["data"]["content"] == "chunk"

    @pytest.mark.asyncio
    async def test_is_lost_recovers_when_new_tab_registers(self) -> None:
        broker = get_turn_broker()
        send, is_lost = _make_ws_sender(object(), "tY2")
        assert is_lost() is True
        sock = _FakeSocket()
        conn_id = broker.register_socket("tY2", sock)
        try:
            assert is_lost() is False
            assert await send({"type": "turn_complete", "data": {}}) is True
            assert sock.frames[0]["type"] == "turn_complete"
        finally:
            broker.unregister_socket("tY2", conn_id)


# ── Resume handshake ─────────────────────────────────────────────────────


class TestResumeHandshake:
    @pytest.mark.asyncio
    async def test_handshake_replays_frames_after_cursor(self) -> None:
        broker = get_turn_broker()
        for i in range(3):
            await broker.emit("tZ", {"type": "llm_delta", "data": {"content": f"c{i}"}})
        sock = _FakeSocket()
        await _ws_resume_handshake(sock, "tZ", 2)
        assert len(sock.frames) == 2
        resumed = sock.frames[0]
        assert resumed["type"] == "resumed"
        data = resumed["data"]
        assert data["from"] == 2
        assert data["to"] == 3
        assert data["count"] == 1
        assert data["gap"] is False
        assert isinstance(data["running"], bool)
        replayed = sock.frames[1]
        assert replayed["seq"] == 3
        assert replayed["data"]["content"] == "c2"

    @pytest.mark.asyncio
    async def test_handshake_running_flag_reflects_active_turn(self) -> None:
        broker = get_turn_broker()
        await broker.emit("tRun", {"type": "status_update"})
        task = asyncio.create_task(asyncio.sleep(3600))
        register_turn("tRun", task)
        try:
            sock = _FakeSocket()
            await _ws_resume_handshake(sock, "tRun", 0)
            assert sock.frames[0]["data"]["running"] is True
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            unregister_turn("tRun", task)

    @pytest.mark.asyncio
    async def test_handshake_gap_signals_snapshot_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        small = TurnBroker(journal=TurnJournal(max_events_per_thread=2))
        for _ in range(8):
            await small.emit("tGap", {"type": "llm_delta"})
        monkeypatch.setattr(ws_chat_mod, "get_turn_broker", lambda: small)
        sock = _FakeSocket()
        await _ws_resume_handshake(sock, "tGap", 1)
        assert len(sock.frames) == 1  # resumed only — zero partial history
        assert sock.frames[0]["data"]["gap"] is True
        assert sock.frames[0]["data"]["count"] == 0

    @pytest.mark.asyncio
    async def test_handshake_survives_dead_socket(self) -> None:
        broker = get_turn_broker()
        await broker.emit("tDead", {"type": "llm_delta"})

        class _Dying(_FakeSocket):
            async def send_json(self, payload: dict) -> None:
                raise RuntimeError("socket gone")

        # Must not raise even though the very first frame fails.
        await _ws_resume_handshake(_Dying(), "tDead", 0)


# ── Endpoint integration (real WS through FastAPI TestClient) ────────────


def _mk_client() -> TestClient:
    app = FastAPI()
    app.include_router(create_ws_chat_router())
    return TestClient(app)


def _seed_session(session_id: str, thread_id: str) -> None:
    mgr = get_session_manager()
    sess = mgr.get_or_create(session_id)
    sess.thread_id = thread_id
    mgr.put(sess)


def test_endpoint_serves_cursor_resume_on_connect():
    _seed_session("v2-cursor-session", "v2-cursor-thread")
    broker = get_turn_broker()
    for i in range(3):
        asyncio.run(broker.emit("v2-cursor-thread", {"type": "llm_delta", "data": {"content": f"p{i}"}}))

    client = _mk_client()
    with client.websocket_connect("/ws/chat/v2-cursor-session?last_seq=1") as ws:
        resumed = ws.receive_json()
        assert resumed["type"] == "resumed"
        assert resumed["data"]["from"] == 1
        assert resumed["data"]["to"] == 3
        f2 = ws.receive_json()
        f3 = ws.receive_json()
        assert [f["seq"] for f in (f2, f3)] == [2, 3]
        assert [f["data"]["content"] for f in (f2, f3)] == ["p1", "p2"]
        # Connection fully functional after resume.
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_endpoint_legacy_clients_get_no_resumed_frame():
    """No cursor ⇒ byte-identical legacy behavior: no proactive frame on an
    empty session; ping/pong works; resume action still available."""
    _seed_session("v2-legacy-session", "v2-legacy-thread")
    broker = get_turn_broker()
    asyncio.run(broker.emit("v2-legacy-thread", {"type": "llm_delta", "data": {"content": "x"}}))

    client = _mk_client()
    with client.websocket_connect("/ws/chat/v2-legacy-session") as ws:
        ws.send_json({"action": "ping"})
        # FIRST frame back must be the pong — nothing proactive preceded it.
        assert ws.receive_json() == {"type": "pong"}
        # Late opt-in via action form.
        ws.send_json({"action": "resume", "last_seq": 0})
        resumed = ws.receive_json()
        assert resumed["type"] == "resumed"
        assert resumed["data"]["count"] == 1
        assert ws.receive_json()["seq"] == 1


def test_endpoint_multi_tab_both_receive_live_frames():
    """Two tabs on one session: a prompt-path broadcast reaches both."""
    _seed_session("v2-multitab-session", "v2-multitab-thread")
    broker = get_turn_broker()

    client = _mk_client()
    with client.websocket_connect("/ws/chat/v2-multitab-session") as ws1:
        with client.websocket_connect("/ws/chat/v2-multitab-session") as ws2:
            # Both connections registered themselves with the broker on
            # accept — simulate a turn event emission exactly as the
            # broker-backed sender would.
            asyncio.run(broker.emit("v2-multitab-thread", {"type": "status_update",
                                                           "data": {"status": "thinking"}}))
            got1 = ws1.receive_json()
            got2 = ws2.receive_json()
            assert got1["type"] == "status_update" and got1["seq"] == 1
            assert got2["type"] == "status_update" and got2["seq"] == 1


def test_v2_cursor_connection_survives_send_prompt_ack_path(monkeypatch):
    """Default: WS is telemetry. send_prompt is rejected with prompt_ack
    (accepted=False, reason=sse_only) and the cursor connection stays alive
    (ping still works). Pre-2026-08-24 a function-local import made this
    path UnboundLocalError and killed the socket."""
    from unittest.mock import MagicMock

    monkeypatch.delenv("KAZMA_WS_GRAPH", raising=False)

    from fastapi import FastAPI as _FastAPI

    _seed_session("v2-send-session", "v2-send-thread")

    app = _FastAPI()
    app.include_router(create_ws_chat_router(graph=MagicMock()))
    client = TestClient(app)
    with client.websocket_connect("/ws/chat/v2-send-session?last_seq=0") as ws:
        resumed = ws.receive_json()
        assert resumed["type"] == "resumed"

        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}

        ws.send_json({
            "action": "send_prompt",
            "text": "hello regression",
            "client_msg_id": "regression-ack-1",
        })
        ack = ws.receive_json()
        assert ack["type"] == "prompt_ack", ack
        assert ack["data"]["accepted"] is False
        assert ack["data"]["reason"] == "sse_only"

        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_ws_graph_escape_hatch_accepts_send_prompt(monkeypatch):
    """KAZMA_WS_GRAPH=1 restores the second graph client (debug only)."""
    from unittest.mock import MagicMock

    from fastapi import FastAPI as _FastAPI

    monkeypatch.setenv("KAZMA_WS_GRAPH", "1")
    _seed_session("v2-graph-session", "v2-graph-thread")
    app = _FastAPI()
    app.include_router(create_ws_chat_router(graph=MagicMock()))
    client = TestClient(app)
    with client.websocket_connect("/ws/chat/v2-graph-session?last_seq=0") as ws:
        assert ws.receive_json()["type"] == "resumed"
        ws.send_json({
            "action": "send_prompt",
            "text": "hello graph hatch",
            "client_msg_id": "graph-ack-1",
        })
        ack = ws.receive_json()
        assert ack["type"] == "prompt_ack", ack
        assert ack["data"]["accepted"] is True


def test_ws_approve_tool_rejected_when_graph_off(monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.delenv("KAZMA_WS_GRAPH", raising=False)

    from fastapi import FastAPI as _FastAPI

    _seed_session("v2-hitl-session", "v2-hitl-thread")
    app = _FastAPI()
    app.include_router(create_ws_chat_router(graph=MagicMock()))
    client = TestClient(app)
    with client.websocket_connect("/ws/chat/v2-hitl-session") as ws:
        ws.send_json({
            "action": "approve_tool",
            "approved": True,
            "thread_id": "v2-hitl-thread",
        })
        err = ws.receive_json()
        assert err["type"] == "approval_error", err
        assert err["data"]["code"] == "SSE_ONLY"


def test_chat_js_does_not_prefer_ws_graph_client():
    """Browser chat must not route turns over WS when the telemetry bus is up."""
    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
    ).read_text(encoding="utf-8")
    assert "agentStore.sendPrompt(" not in src
    assert "agentStore.submitApproval(" not in src
    assert "_capStore.sendPrompt(" not in src


def test_no_function_local_active_turns_imports():
    """Source contract: active_turns names are imported ONCE at module level.
    A function-local re-import inside chat_websocket shadows them for the
    whole scope (the outage above)."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "routes" / "ws_chat.py"
    ).read_text(encoding="utf-8")
    body_after_imports = src.split("from kazma_ui.active_turns import", 2)[-1]
    assert "from kazma_ui.active_turns import" not in body_after_imports
