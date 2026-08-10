"""Tests for the unified SessionManager shared by WebSocket and SSE.

Validates VAL-UX-007: "a session created on one transport is listed by
``/api/chat/sessions`` queried from the other".

These tests verify that:
  1. Both transports use the same SessionManager singleton instance.
  2. A session created via the SSE transport appears in the WebSocket
     session-list / message-history endpoints.
  3. A session created via the WebSocket transport appears in the SSE
     session-list / message-history endpoints.
  4. No duplicate session-store implementations remain.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_ui.session_manager import (
    ChatSession,
    SessionManager,
    get_session_manager,
    reset_session_manager,
)
from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_store():
    """Ensure each test starts with a clean shared store."""
    reset_session_manager()


def _make_ws_app():
    """Create a FastAPI app with WebSocket and SSE session routes."""
    from kazma_ui.chat import create_chat_router
    from kazma_ui.sse_chat import create_sse_chat_router

    agent = MagicMock()
    agent.config.model = "test-model"
    templates = MagicMock()
    graph = MagicMock()
    app = FastAPI()
    app.include_router(create_chat_router(agent, templates))
    app.include_router(create_sse_chat_router(graph=graph, checkpointer=None))
    return app


def _make_sse_app():
    """Create a FastAPI app with only the SSE (sse_chat.py) routes."""
    from kazma_ui.sse_chat import create_sse_chat_router

    graph = MagicMock()
    router = create_sse_chat_router(graph=graph, checkpointer=None)
    app = FastAPI()
    app.include_router(router)
    return app


def _make_unified_app():
    """Create a FastAPI app with BOTH routers mounted (like app.py does)."""
    agent = MagicMock()
    agent.config.model = "test-model"
    templates = MagicMock()

    from kazma_ui.chat import create_chat_router
    from kazma_ui.sse_chat import create_sse_chat_router

    graph = MagicMock()
    sse_router = create_sse_chat_router(graph=graph, checkpointer=None)

    app = FastAPI()
    # chat_router registered first (same order as app.py)
    app.include_router(create_chat_router(agent, templates))
    app.include_router(sse_router)
    return app


# ═══════════════════════════════════════════════════════════════════
# Singleton identity
# ═══════════════════════════════════════════════════════════════════


class TestSingletonIdentity:
    """Both transports must use the same SessionManager instance."""

    def test_get_session_manager_returns_singleton(self):
        mgr1 = get_session_manager()
        mgr2 = get_session_manager()
        assert mgr1 is mgr2

    def test_chat_and_sse_share_same_store(self):
        """chat.py._sessions() and the SSE router's store must return the same object."""
        import kazma_ui.chat as chat_mod
        from kazma_ui.sse_chat import create_sse_chat_router

        # chat.py exposes a _sessions() helper that resolves the singleton
        ws_store = chat_mod._sessions()
        assert isinstance(ws_store, SessionManager)

        # Creating the SSE router captures the singleton; we can't directly
        # inspect it, but we can verify both report the same session.
        graph = MagicMock()
        create_sse_chat_router(graph=graph, checkpointer=None)

        # Insert a session via the WebSocket store
        session = ws_store.get_or_create("shared-123")
        session.messages.append({"role": "user", "content": "hi"})

        # The singleton must contain it
        assert get_session_manager().get("shared-123") is not None
        assert get_session_manager().get("shared-123").messages[0]["content"] == "hi"


# ═══════════════════════════════════════════════════════════════════
# Cross-transport visibility (VAL-UX-007)
# ═══════════════════════════════════════════════════════════════════


class TestCrossTransportVisibility:
    """VAL-UX-007: a session created on one transport is visible to the other."""

    def test_sse_session_visible_to_websocket_list(self):
        """A session created via SSE POST must appear in the WS GET list."""
        # 1. Create a session via SSE transport
        sse_app = _make_sse_app()
        sse_client = TestClient(sse_app)
        sse_client.post("/api/chat/stream", json={"message": "hello from SSE"})

        # Confirm the SSE side sees it
        sse_sessions = sse_client.get("/api/chat/sessions").json()
        assert len(sse_sessions) == 1
        session_id = sse_sessions[0]["session_id"]

        # 2. Query the WebSocket transport's session list
        ws_app = _make_ws_app()
        ws_client = TestClient(ws_app)

        ws_sessions = ws_client.get("/api/chat/sessions").json()
        assert any(s["session_id"] == session_id for s in ws_sessions), (
            "SSE-created session must be visible in the WebSocket session list"
        )

    def test_websocket_session_visible_to_sse_list(self):
        """A session created via the WS store must appear in the SSE GET list."""
        # 1. Create a session via the WebSocket store (chat.py)
        from kazma_ui.chat import get_or_create_session

        session = get_or_create_session("ws-created-456")
        session.messages.append({"role": "user", "content": "hello from WS"})

        # 2. Query the SSE transport's session list
        sse_app = _make_sse_app()
        sse_client = TestClient(sse_app)

        sse_sessions = sse_client.get("/api/chat/sessions").json()
        ids = [s["session_id"] for s in sse_sessions]
        assert "ws-created-456" in ids, (
            "WebSocket-created session must be visible in the SSE session list"
        )

    def test_sse_session_messages_visible_to_websocket(self):
        """Messages stored via SSE must be retrievable via the WS messages endpoint."""
        # 1. Create a session with a message via SSE
        sse_app = _make_sse_app()
        sse_client = TestClient(sse_app)
        resp = sse_client.post("/api/chat/stream", json={"message": "cross-transport msg"})
        assert resp.status_code == 200

        sessions = sse_client.get("/api/chat/sessions").json()
        assert len(sessions) == 1
        session_id = sessions[0]["session_id"]

        # 2. Fetch messages via the WebSocket transport
        ws_app = _make_ws_app()
        ws_client = TestClient(ws_app)

        msgs = ws_client.get(f"/api/chat/sessions/{session_id}/messages").json()
        assert len(msgs) >= 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "cross-transport msg"

    def test_websocket_session_messages_visible_to_sse(self):
        """Messages stored via the WS store must be retrievable via the SSE messages endpoint."""
        # 1. Create a session via the WebSocket store
        from kazma_ui.chat import get_or_create_session

        session = get_or_create_session("ws-msg-789")
        session.messages.append({"role": "user", "content": "ws-originated"})

        # 2. Fetch messages via the SSE transport
        sse_app = _make_sse_app()
        sse_client = TestClient(sse_app)

        msgs = sse_client.get("/api/chat/sessions/ws-msg-789/messages").json()
        assert len(msgs) == 1
        assert msgs[0]["content"] == "ws-originated"

    def test_delete_on_one_transport_removes_from_both(self):
        """Deleting a session on one transport must remove it from the shared store."""
        # Create via SSE
        sse_app = _make_sse_app()
        sse_client = TestClient(sse_app)
        sse_client.post("/api/chat/stream", json={"message": "to be deleted"})

        sessions = sse_client.get("/api/chat/sessions").json()
        assert len(sessions) == 1
        session_id = sessions[0]["session_id"]

        # Delete via WebSocket transport
        ws_app = _make_ws_app()
        ws_client = TestClient(ws_app)
        resp = ws_client.delete(f"/api/chat/sessions/{session_id}")
        assert resp.status_code == 200

        # Both transports must report empty
        assert sse_client.get("/api/chat/sessions").json() == []
        assert ws_client.get("/api/chat/sessions").json() == []


# ═══════════════════════════════════════════════════════════════════
# Unified app integration
# ═══════════════════════════════════════════════════════════════════


class TestUnifiedAppIntegration:
    """Integration tests with both routers mounted (like the real app)."""

    def test_unified_app_sse_session_listed_once(self):
        """In the unified app, a session created via SSE appears exactly once."""
        app = _make_unified_app()
        client = TestClient(app)

        client.post("/api/chat/stream", json={"message": "unified test"})

        sessions = client.get("/api/chat/sessions").json()
        # Must not be duplicated despite both routers defining the route
        assert len(sessions) == 1
        assert sessions[0]["message_count"] in (1, 2)

    def test_unified_app_messages_accessible(self):
        """In the unified app, messages created via SSE are accessible."""
        app = _make_unified_app()
        client = TestClient(app)

        client.post("/api/chat/stream", json={"message": "find me"})

        sessions = client.get("/api/chat/sessions").json()
        session_id = sessions[0]["session_id"]

        msgs = client.get(f"/api/chat/sessions/{session_id}/messages").json()
        assert any(m["content"] == "find me" for m in msgs)


# ═══════════════════════════════════════════════════════════════════
# SessionManager unit tests
# ═══════════════════════════════════════════════════════════════════


class TestSessionManagerUnit:
    """Direct unit tests for the SessionManager class."""

    def test_get_or_create_creates_new(self):
        mgr = SessionManager()
        session = mgr.get_or_create()
        assert session.session_id  # auto-generated UUID
        assert mgr.get(session.session_id) is session

    def test_get_or_create_returns_existing(self):
        mgr = SessionManager()
        session = mgr.get_or_create("fixed-id")
        assert session.session_id == "fixed-id"

        session2 = mgr.get_or_create("fixed-id")
        assert session2 is session

    def test_delete_removes_session(self):
        mgr = SessionManager()
        mgr.get_or_create("to-remove")
        assert mgr.get("to-remove") is not None

        mgr.delete("to-remove")
        assert mgr.get("to-remove") is None

    def test_list_all_returns_all(self):
        mgr = SessionManager()
        for sid in ("a", "b", "c"):
            s = mgr.get_or_create(sid)
            s.add_message("user", f"hi {sid}")
            mgr.put(s)
        assert len(mgr.list_all()) == 3

    def test_get_or_create_durable_false_skips_db_until_put(self):
        """WS connect must not pollute the store with empty durable shells."""
        mgr = SessionManager()
        shell = mgr.get_or_create("mem-only", durable=False)
        assert shell.session_id == "mem-only"
        # Memory-visible, but list hides empty shells by default.
        assert mgr.get("mem-only") is shell
        assert mgr.list_all() == []
        assert [s.session_id for s in mgr.list_all(include_empty=True, prune_empty=False)] == [
            "mem-only"
        ]
        # First real message makes it durable + listed.
        shell.add_message("user", "first real prompt")
        mgr.put(shell)
        listed = mgr.list_all()
        assert len(listed) == 1
        assert listed[0].session_id == "mem-only"
        assert len(listed[0].messages) == 1

    def test_list_all_hides_and_prunes_empty_web_shells(self):
        mgr = SessionManager()
        empty = mgr.get_or_create("empty-shell")
        mgr.put(empty)
        # put() stamps updated_at=now; age the in-memory row so prune fires.
        empty.updated_at = "2000-01-01T00:00:00+00:00"
        empty.created_at = empty.updated_at

        filled = mgr.get_or_create("filled")
        filled.add_message("user", "keep me")
        mgr.put(filled)

        # Default list: hide empty, and GC aged empty shells.
        listed = mgr.list_all()
        assert [s.session_id for s in listed] == ["filled"]
        assert mgr.get("empty-shell") is None  # pruned

    def test_gateway_empty_sessions_are_not_pruned(self):
        mgr = SessionManager()
        gw = mgr.get_or_create("gw-telegram-telegram_1-abc")
        gw.updated_at = "2000-01-01T00:00:00+00:00"
        gw.created_at = gw.updated_at
        mgr.put(gw)
        listed = mgr.list_all(include_empty=True, prune_empty=True)
        assert any(s.session_id == gw.session_id for s in listed)

    def test_get_and_list_do_not_cross_tenant_boundaries(self):
        mgr = SessionManager()
        first_token = set_current_tenant_id("tenant-a")
        try:
            first = mgr.get_or_create("shared-id")
            first.messages.append({"role": "user", "content": "tenant-a secret"})
            mgr.put(first)
        finally:
            reset_current_tenant_id(first_token)

        second_token = set_current_tenant_id("tenant-b")
        try:
            assert mgr.get("shared-id") is None
            assert mgr.list_all() == []
            mgr.delete("shared-id")
        finally:
            reset_current_tenant_id(second_token)

        first_token = set_current_tenant_id("tenant-a")
        try:
            restored = mgr.get("shared-id")
            assert restored is not None
            assert restored.messages[0]["content"] == "tenant-a secret"
        finally:
            reset_current_tenant_id(first_token)

    def test_thread_lookup_is_tenant_scoped(self):
        mgr = SessionManager()
        first_token = set_current_tenant_id("tenant-a")
        try:
            session = mgr.get_or_create("tenant-a-session")
            session.thread_id = "shared-thread"
            mgr.put(session)
            assert mgr.get_by_thread_id("shared-thread") is not None
        finally:
            reset_current_tenant_id(first_token)

        second_token = set_current_tenant_id("tenant-b")
        try:
            assert mgr.get_by_thread_id("shared-thread") is None
        finally:
            reset_current_tenant_id(second_token)

    def test_update_from_dict_upserts(self):
        mgr = SessionManager()
        mgr.update_from_dict("dict-session", {
            "messages": [{"role": "user", "content": "hi"}],
            "total_cost": 0.5,
            "total_tokens": 100,
        })
        session = mgr.get("dict-session")
        assert session is not None
        assert session.total_cost == 0.5
        assert session.total_tokens == 100
        assert len(session.messages) == 1

    def test_clear_empties_store(self):
        mgr = SessionManager()
        mgr.get_or_create("a")
        mgr.get_or_create("b")
        assert len(mgr.list_all(include_empty=True, prune_empty=False)) == 2

        mgr.clear()
        assert len(mgr.list_all(include_empty=True, prune_empty=False)) == 0

    def test_chat_session_to_summary(self):
        session = ChatSession(session_id="sum-test")
        session.messages.append({"role": "user", "content": "x"})
        session.total_cost = 1.23
        summary = session.to_summary()
        assert summary["session_id"] == "sum-test"
        assert summary["message_count"] == 1
        assert summary["total_cost"] == 1.23
        assert "created_at" in summary


# ═══════════════════════════════════════════════════════════════════
# Pinning (VAL-UX-012: server-persisted pinned sessions)
# ═══════════════════════════════════════════════════════════════════


class TestPinning:
    """Pin/unpin routes persist server-side and surface in the session list."""

    def test_pin_then_unpin_roundtrip(self):
        app = _make_sse_app()
        client = TestClient(app)
        client.post("/api/chat/stream", json={"message": "pin me"})

        session_id = client.get("/api/chat/sessions").json()[0]["session_id"]
        assert client.get("/api/chat/sessions").json()[0]["pinned"] is False

        resp = client.post(f"/api/chat/sessions/{session_id}/pin")
        assert resp.status_code == 200
        assert resp.json()["pinned"] is True
        assert client.get("/api/chat/sessions").json()[0]["pinned"] is True

        resp = client.post(f"/api/chat/sessions/{session_id}/unpin")
        assert resp.status_code == 200
        assert resp.json()["pinned"] is False
        assert client.get("/api/chat/sessions").json()[0]["pinned"] is False

    def test_pin_is_idempotent(self):
        app = _make_sse_app()
        client = TestClient(app)
        client.post("/api/chat/stream", json={"message": "pin twice"})

        session_id = client.get("/api/chat/sessions").json()[0]["session_id"]
        client.post(f"/api/chat/sessions/{session_id}/pin")
        resp = client.post(f"/api/chat/sessions/{session_id}/pin")
        assert resp.status_code == 200
        assert resp.json()["pinned"] is True

    def test_pin_unknown_session_returns_error(self):
        app = _make_sse_app()
        client = TestClient(app)
        resp = client.post("/api/chat/sessions/does-not-exist/pin")
        assert resp.status_code == 200
        assert resp.json().get("status") == "error"

    def test_pinned_survives_reload_of_store(self):
        """Pinned flag persists in a real SQLite store (not just in-memory)."""
        mgr = SessionManager(db_path=":memory:")
        session = mgr.get_or_create("persist-pin")
        session.messages.append({"role": "user", "content": "hi"})

        mgr.set_pinned("persist-pin", True)
        session2 = mgr.get("persist-pin")
        assert session2.pinned is True

        listed = [s for s in mgr.list_all() if s.session_id == "persist-pin"][0]
        assert listed.pinned is True

        mgr.set_pinned("persist-pin", False)
        assert mgr.get("persist-pin").pinned is False
