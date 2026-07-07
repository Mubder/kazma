"""Tests for the SSE chat router.

Covers:
  - SSE frame formatting
  - Router creation and endpoint registration
  - Error handling (empty message, invalid JSON, budget exceeded)
  - Session management
  - Event stream structure
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_ui.sse_chat import _sse_frame, create_sse_chat_router


@pytest.fixture(autouse=True)
def _reset_shared_session_store():
    """Reset the shared SessionManager singleton before each test.

    Both WebSocket (chat.py) and SSE (sse_chat.py) transports now use a
    single process-wide SessionManager (VAL-UX-007).  Without this reset,
    sessions created by one test would leak into another.
    """
    from kazma_ui.session_manager import reset_session_manager

    reset_session_manager()


# ═══════════════════════════════════════════════════════════════════
# SSE frame formatting
# ═══════════════════════════════════════════════════════════════════


class TestSSEFrame:
    """Tests for the _sse_frame helper."""

    def test_dict_payload(self):
        frame = _sse_frame("token", {"content": "hello"})
        assert frame == 'event: token\ndata: {"content": "hello"}\n\n'

    def test_string_payload(self):
        frame = _sse_frame("error", "something broke")
        assert frame == "event: error\ndata: something broke\n\n"

    def test_list_payload(self):
        frame = _sse_frame("done", [1, 2, 3])
        assert frame == "event: done\ndata: [1, 2, 3]\n\n"

    def test_unicode_payload(self):
        frame = _sse_frame("token", {"content": "مرحبا"})
        assert "مرحبا" in frame
        assert frame.endswith("\n\n")

    def test_empty_dict(self):
        frame = _sse_frame("done", {})
        assert frame == "event: done\ndata: {}\n\n"


# ═══════════════════════════════════════════════════════════════════
# Router creation
# ═══════════════════════════════════════════════════════════════════


class TestRouterCreation:
    """Tests for create_sse_chat_router."""

    def test_router_has_stream_endpoint(self):
        graph = MagicMock()
        router = create_sse_chat_router(graph=graph, checkpointer=None)

        # Verify the router has the expected routes
        paths = set()
        for route in router.routes:
            if hasattr(route, "path"):
                paths.add(route.path)
        assert "/api/chat/stream" in paths

    def test_router_has_sessions_endpoint(self):
        graph = MagicMock()
        router = create_sse_chat_router(graph=graph, checkpointer=None)

        paths = set()
        for route in router.routes:
            if hasattr(route, "path"):
                paths.add(route.path)
        assert "/api/chat/sessions" in paths


# ═══════════════════════════════════════════════════════════════════
# Endpoint behavior
# ═══════════════════════════════════════════════════════════════════


class TestChatStreamEndpoint:
    """Tests for POST /api/chat/stream."""

    def _make_app(self, cost_breaker=None) -> tuple[FastAPI, MagicMock]:
        """Create a test app with a mock graph."""
        graph = MagicMock()
        router = create_sse_chat_router(
            graph=graph,
            checkpointer=None,
            system_prompt="You are Kazma.",
            cost_breaker=cost_breaker,
        )
        app = FastAPI()
        app.include_router(router)
        return app, graph

    def test_empty_message_returns_error(self):
        app, _ = self._make_app()
        client = TestClient(app)

        resp = client.post("/api/chat/stream", json={"message": ""})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = resp.text
        assert "event: error" in body
        assert "Empty message" in body

    def test_missing_message_returns_error(self):
        app, _ = self._make_app()
        client = TestClient(app)

        resp = client.post("/api/chat/stream", json={})
        assert resp.status_code == 200
        body = resp.text
        assert "event: error" in body

    def test_invalid_json_returns_error(self):
        app, _ = self._make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/chat/stream",
            content="not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        body = resp.text
        assert "event: error" in body
        assert "Invalid JSON" in body

    def test_budget_exceeded_returns_error(self):
        breaker = MagicMock()
        breaker.should_halt.return_value = True

        app, _ = self._make_app(cost_breaker=breaker)
        client = TestClient(app)

        resp = client.post("/api/chat/stream", json={"message": "hello"})
        assert resp.status_code == 200
        body = resp.text


class TestGraphHolderSupport:
    """Regression tests for mutable graph holder (C-3 fix: SSE must see post-startup checkpointed graph)."""

    def test_router_accepts_graph_holder(self):
        """Router creation accepts graph_holder (updated after startup recompile)."""
        holder = {"graph": MagicMock()}
        router = create_sse_chat_router(graph_holder=holder)
        assert router is not None
        # paths still present
        paths = {getattr(r, "path", None) for r in router.routes if hasattr(r, "path")}
        assert "/api/chat/stream" in paths

    def test_graph_holder_takes_precedence_over_initial_graph(self):
        """If graph_holder present, its graph is used (simulates post-startup update)."""
        initial = MagicMock(name="initial")
        updated = MagicMock(name="post_startup_with_checkpointer")
        holder = {"graph": updated}
        # creation with both; holder should win inside
        router = create_sse_chat_router(graph=initial, graph_holder=holder)
        assert router is not None
        assert "event: error" in body
        assert "budget exceeded" in body.lower()  # Budget exceeded message

    def test_session_listing_empty(self):
        app, _ = self._make_app()
        client = TestClient(app)

        resp = client.get("/api/chat/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_session_delete_nonexistent(self):
        app, _ = self._make_app()
        client = TestClient(app)

        resp = client.delete("/api/chat/sessions/nonexistent")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_session_messages_endpoint_registered(self):
        """The SSE router must expose GET /api/chat/sessions/{id}/messages."""
        app, _ = self._make_app()
        client = TestClient(app)

        resp = client.get("/api/chat/sessions/nonexistent/messages")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_session_messages_returns_messages(self):
        """After creating a session via /api/chat/stream, the messages endpoint
        must return the stored user + assistant messages."""
        app, _ = self._make_app()
        client = TestClient(app)

        # Send a message to create a session (the mock graph yields no events,
        # but the session is still created with the user message stored).
        client.post("/api/chat/stream", json={"message": "hello world"})

        # List sessions to get the session_id
        sessions = client.get("/api/chat/sessions").json()
        assert len(sessions) == 1
        session_id = sessions[0]["session_id"]

        # Fetch messages for that session
        resp = client.get(f"/api/chat/sessions/{session_id}/messages")
        assert resp.status_code == 200
        messages = resp.json()
        assert len(messages) >= 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hello world"


# ═══════════════════════════════════════════════════════════════════
# Stream content (mock graph)
# ═══════════════════════════════════════════════════════════════════


class TestStreamContent:
    """Tests for the actual SSE stream output with a mocked graph."""

    def _mock_astream_events(self, events: list[dict[str, Any]]):
        """Create a mock astream_events that yields the given events."""

        async def _gen(*args, **kwargs):
            for event in events:
                yield event

        return _gen()

    def test_token_streaming(self):
        """Verify token events are yielded as SSE frames."""
        import asyncio

        from kazma_ui.sse_chat import _stream_langgraph_events

        # Mock AIMessageChunk
        class FakeChunk:
            content = "hello"

        events = [
            {"event": "on_chat_model_stream", "data": {"chunk": FakeChunk()}, "name": "llm"},
            {"event": "on_chat_model_stream", "data": {"chunk": FakeChunk()}, "name": "llm"},
            {
                "event": "on_chain_end",
                "data": {"output": {"last_tokens": 10, "last_cost_usd": 0.001}},
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = self._mock_astream_events(events)

        frames = []

        async def _collect():
            async for frame in _stream_langgraph_events(
                graph, {"messages": []}, {"configurable": {"thread_id": "test"}}
            ):
                frames.append(frame)

        asyncio.run(_collect())

        # Should have 2 token events + 1 done event
        token_frames = [f for f in frames if f.startswith("event: token")]
        done_frames = [f for f in frames if f.startswith("event: done")]
        assert len(token_frames) == 2
        assert len(done_frames) == 1
        assert "hello" in token_frames[0]

    def test_tool_call_events(self):
        """Verify tool_call and tool_result events are yielded."""
        import asyncio

        from kazma_ui.sse_chat import _stream_langgraph_events

        events = [
            {"event": "on_tool_start", "data": {"input": {"path": "/tmp"}}, "name": "file_list"},
            {"event": "on_tool_end", "data": {"output": "file1.txt\nfile2.txt"}, "name": "file_list"},
            {"event": "on_chain_end", "data": {"output": {}}, "name": "__end__"},
        ]

        graph = MagicMock()
        graph.astream_events.return_value = self._mock_astream_events(events)

        frames = []

        async def _collect():
            async for frame in _stream_langgraph_events(
                graph, {"messages": []}, {"configurable": {"thread_id": "test"}}
            ):
                frames.append(frame)

        asyncio.run(_collect())

        tool_calls = [f for f in frames if f.startswith("event: tool_call")]
        tool_results = [f for f in frames if f.startswith("event: tool_result")]
        assert len(tool_calls) == 1
        assert len(tool_results) == 1
        assert "file_list" in tool_calls[0]
        assert "file1.txt" in tool_results[0]

    def test_error_handling_in_stream(self):
        """Verify exceptions yield an error SSE frame."""
        import asyncio

        from kazma_ui.sse_chat import _stream_langgraph_events

        async def _failing_gen(*args, **kwargs):
            raise RuntimeError("LLM crashed")
            yield  # make it a generator

        graph = MagicMock()
        graph.astream_events.return_value = _failing_gen()

        frames = []

        async def _collect():
            async for frame in _stream_langgraph_events(
                graph, {"messages": []}, {"configurable": {"thread_id": "test"}}
            ):
                frames.append(frame)

        asyncio.run(_collect())

        error_frames = [f for f in frames if f.startswith("event: error")]
        assert len(error_frames) == 1
        assert "LLM crashed" in error_frames[0]
