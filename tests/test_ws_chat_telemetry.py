"""Unit and Integration tests for WebSocket Chat Telemetry Gateway and EventBridge."""

import json
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from kazma_core.tracing.events import TelemetryEvent, EventBridge
from kazma_ui.routes.ws_chat import create_ws_chat_router


def test_telemetry_event_serialization():
    event = TelemetryEvent(
        type="status_update",
        data={"status": "thinking", "active_node": "Supervisor"},
        thread_id="test-thread-123",
    )
    d = event.to_dict()
    assert d["type"] == "status_update"
    assert d["data"]["status"] == "thinking"
    assert d["thread_id"] == "test-thread-123"

    json_str = event.to_json()
    parsed = json.loads(json_str)
    assert parsed["type"] == "status_update"


@pytest.mark.asyncio
async def test_event_bridge_process_stream():
    async def mock_raw_stream():
        yield {"event": "on_chain_start", "name": "Supervisor", "data": {}}
        yield {"event": "on_chat_model_stream", "name": "chat_model", "data": {"chunk": {"content": "Hello "}}}
        yield {"event": "on_chat_model_stream", "name": "chat_model", "data": {"chunk": {"content": "world!"}}}
        yield {"event": "on_tool_start", "name": "mcp__test__tool", "data": {"input": {"arg": 1}}}
        yield {"event": "on_tool_end", "name": "mcp__test__tool", "data": {"output": "ok"}}

    events = []
    async for ev in EventBridge.process_stream(mock_raw_stream(), thread_id="t-123"):
        events.append(ev)

    types = [e.type for e in events]
    assert "status_update" in types
    assert "llm_delta" in types
    assert "tool_lifecycle" in types

    deltas = [e.data["content"] for e in events if e.type == "llm_delta"]
    assert "".join(deltas) == "Hello world!"


def test_ws_chat_endpoint_ping_pong():
    app = FastAPI()
    router = create_ws_chat_router()
    app.include_router(router)

    client = TestClient(app)
    with client.websocket_connect("/ws/chat/session-test-456") as websocket:
        websocket.send_json({"action": "ping"})
        data = websocket.receive_json()
        assert data == {"type": "pong"}


def test_ws_connect_does_not_create_empty_listed_session():
    """Bare WS connect must not leave a 'Web Session · 0 msgs' sidebar row."""
    from kazma_ui.session_manager import get_session_manager, reset_session_manager

    reset_session_manager()
    app = FastAPI()
    router = create_ws_chat_router()
    app.include_router(router)

    client = TestClient(app)
    sid = "ws-empty-shell-test"
    with client.websocket_connect(f"/ws/chat/{sid}") as websocket:
        websocket.send_json({"action": "ping"})
        assert websocket.receive_json() == {"type": "pong"}

    mgr = get_session_manager()
    # Hidden from the default sidebar list even if a memory shell exists.
    assert all(s.session_id != sid for s in mgr.list_all())
    shell = mgr.get(sid)
    if shell is not None:
        assert shell.messages == []
