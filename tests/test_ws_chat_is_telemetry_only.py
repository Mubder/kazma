"""/ws/chat is the telemetry bus only: it never runs, resumes or edits a turn.

AUD-026 (2026-09-30). The socket carried a second copy of the chat protocol:
a graph client behind ``KAZMA_WS_GRAPH`` (``send_prompt`` / ``approve_tool``)
and stop / steer / abort twins of the HTTP routes. No shipped client sent any
of them -- the page sends and approves over SSE and stops, steers and aborts
over HTTP; the chat store's WebSocket send methods had no caller -- yet the
copies drifted: the socket's approve path lacked the gate-identity check and
dropped ``approved_ids`` (which fails OPEN), and every delivery fix had to
land twice. The copies are gone; the socket answers each such action with a
refusal naming the route that does it.

Behaviour through the real router with a graph double, and source gates on
the server module and the client store, each with a negative control.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_ui.active_turns import (
    get_orphan_stamp,
    is_turn_running,
    mark_turn_orphaned,
    register_turn,
    reset_active_turns,
)
from kazma_ui.delivery import reset_turn_broker
from kazma_ui.routes.ws_chat import create_ws_chat_router
from kazma_ui.session_manager import get_session_manager, reset_session_manager

REPO_ROOT = Path(__file__).resolve().parents[1]
WS_CHAT = REPO_ROOT / "kazma-ui" / "kazma_ui" / "routes" / "ws_chat.py"
AGENT_STORE = REPO_ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "stores" / "agentStore.js"

#: Calls that run, resume or rewrite a graph turn.
GRAPH_RUNNING_ATTRS = {"astream", "astream_events", "ainvoke", "invoke", "aupdate_state", "update_state"}
GRAPH_RUNNING_NAMES = {"invoke_turn", "run_agent_turn", "build_resume_command", "Command"}


@pytest.fixture(autouse=True)
def _fresh_state():
    reset_turn_broker()
    reset_session_manager()
    reset_active_turns()
    yield
    reset_turn_broker()
    reset_session_manager()
    reset_active_turns()


def _seed(session_id: str, thread_id: str) -> None:
    mgr = get_session_manager()
    sess = mgr.get_or_create(session_id)
    sess.thread_id = thread_id
    mgr.put(sess)


def _client(graph: object) -> TestClient:
    app = FastAPI()
    app.include_router(create_ws_chat_router(graph=graph))
    return TestClient(app)


class _NeverDone:
    def done(self) -> bool:
        return False

    def cancel(self) -> bool:
        return True


# ── behaviour ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("action", "extra", "frame_type", "check"),
    [
        ("send_prompt", {"text": "hi", "client_msg_id": "m1"}, "prompt_ack",
         lambda d: d["accepted"] is False and d["reason"] == "sse_only" and d["client_msg_id"] == "m1"),
        ("approve_tool", {"approved": True, "thread_id": "t-ws"}, "approval_error",
         lambda d: d["code"] == "SSE_ONLY"),
        ("stop", {}, "graph_error", lambda d: d["code"] == "http_only" and "/api/chat/stop" in d["message"]),
        ("steer", {"text": "go left", "mode": "hard"}, "graph_error",
         lambda d: d["code"] == "http_only" and "/api/chat/steer" in d["message"]),
        ("abort", {}, "graph_error", lambda d: d["code"] == "http_only" and "/api/chat/abort" in d["message"]),
    ],
)
def test_turn_control_is_refused_and_touches_no_graph(action, extra, frame_type, check) -> None:
    _seed("s-ws", "t-ws")
    graph = MagicMock()
    with _client(graph).websocket_connect("/ws/chat/s-ws?last_seq=0") as ws:
        assert ws.receive_json()["type"] == "resumed"
        ws.send_json({"action": action, **extra})
        frame = ws.receive_json()
        assert frame["type"] == frame_type, frame
        assert check(frame["data"]), frame
        # The connection lives on.
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    for attr in GRAPH_RUNNING_ATTRS:
        assert not getattr(graph, attr).called, f"{action} called graph.{attr}"


def test_a_frame_that_is_not_an_object_keeps_the_socket() -> None:
    """`123` and `[]` are JSON; `.get` on them used to end the connection."""
    _seed("s-junk", "t-junk")
    with _client(MagicMock()).websocket_connect("/ws/chat/s-junk?last_seq=0") as ws:
        assert ws.receive_json()["type"] == "resumed"
        for junk in ("123", "[]", "not json"):
            ws.send_text(junk)
            frame = ws.receive_json()
            assert frame["type"] == "graph_error", frame
            assert "Invalid JSON" in frame["data"]["message"]
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_a_watching_socket_clears_the_orphan_clock() -> None:
    """A turn whose SSE stream dropped is not reaped while a tab watches it
    here (the connect cleared it through the removed live-socket map)."""
    _seed("s-orphan", "t-orphan")
    register_turn("t-orphan", _NeverDone())
    mark_turn_orphaned("t-orphan")
    assert get_orphan_stamp("t-orphan") is not None
    with _client(MagicMock()).websocket_connect("/ws/chat/s-orphan?last_seq=0") as ws:
        assert ws.receive_json()["type"] == "resumed"
        assert get_orphan_stamp("t-orphan") is None
        assert is_turn_running("t-orphan")


# ── source gates ─────────────────────────────────────────────────────────


def graph_running_calls(source: str) -> list[str]:
    """Calls in *source* that run, resume or rewrite a graph turn."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in GRAPH_RUNNING_ATTRS:
            found.append(f"{node.lineno}: .{f.attr}(")
        elif isinstance(f, ast.Name) and f.id in GRAPH_RUNNING_NAMES:
            found.append(f"{node.lineno}: {f.id}(")
    return found


def test_the_socket_module_runs_no_turn() -> None:
    assert graph_running_calls(WS_CHAT.read_text(encoding="utf-8")) == []


def test_negative_control_a_graph_run_is_seen() -> None:
    old_shape = (
        "async def pump(graph, cmd, cfg):\n"
        "    async for ev in graph.astream_events(cmd, cfg, version='v2'):\n"
        "        pass\n"
        "    await graph.aupdate_state(cfg, {})\n"
        "    return await invoke_turn(graph, build_resume_command(action='apply'), cfg)\n"
    )
    assert graph_running_calls(old_shape) == [
        "2: .astream_events(", "4: .aupdate_state(", "5: invoke_turn(", "5: build_resume_command(",
    ]


def switch_reads(source: str) -> list[int]:
    """Lines where code READS ``KAZMA_WS_GRAPH`` (docs may still name it)."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and any(isinstance(a, ast.Constant) and a.value == "KAZMA_WS_GRAPH" for a in node.args)
    ]


def test_nothing_reads_the_old_switch() -> None:
    hits = []
    for pkg in ("kazma-ui", "kazma-core", "kazma-gateway", "kazma-cli", "kazma-tui", "kazma-skills"):
        for path in (REPO_ROOT / pkg).rglob("*.py"):
            if "_tests" in path.parts[-2] or "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "KAZMA_WS_GRAPH" in text:
                hits += [f"{path.relative_to(REPO_ROOT)}:{n}" for n in switch_reads(text)]
    assert hits == [], hits


def test_negative_control_a_switch_read_is_seen() -> None:
    src = 'import os\nON = os.environ.get("KAZMA_WS_GRAPH") == "1"\n'
    assert switch_reads(src) == [2]


#: The page's socket sends nothing; these were its turn-control frames.
_CLIENT_SEND = re.compile(r"_socket\.send\(|action:\s*'(send_prompt|approve_tool|stop|steer|abort)'")


def test_the_chat_store_sends_nothing_on_the_socket() -> None:
    src = AGENT_STORE.read_text(encoding="utf-8")
    assert _CLIENT_SEND.findall(src) == []
    assert "prompt_ack" not in src


def test_negative_control_the_old_store_send_is_seen() -> None:
    old = "const payload = {\n  action: 'send_prompt',\n};\nthis._socket.send(JSON.stringify(payload));\n"
    assert len(_CLIENT_SEND.findall(old)) == 2
