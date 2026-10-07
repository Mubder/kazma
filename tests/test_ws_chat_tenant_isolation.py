"""Real socket boundary tests using synthetic tenants and disposable stores."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kazma_core.security.web_sessions import create_session, revoke_session
from kazma_core.tenant_context import get_current_tenant_id, tenant_scope
from kazma_ui import auth
from kazma_ui.routes import ws_chat
from kazma_ui.session_manager import ChatSession, SessionManager


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "synthetic-secret")
    monkeypatch.delenv("KAZMA_DEV_WS_BYPASS", raising=False)
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    # Exercise credential precedence even when a peer would be trusted.
    monkeypatch.setattr(auth, "_peer_trust_allowed", lambda ws: True)
    monkeypatch.setattr(auth, "_is_loopback_client", lambda ws: True)
    monkeypatch.setattr(auth, "_host_is_local_name", lambda ws: True)
    store = SessionManager(db_path=str(tmp_path / "sessions.db"))
    monkeypatch.setattr(ws_chat, "get_session_manager", lambda: store)
    broker = MagicMock()
    broker.resume.side_effect = lambda tid, seq: ([], False, seq)
    broker.register_socket.return_value = "registration"
    monkeypatch.setattr(ws_chat, "get_turn_broker", lambda: broker)
    clear = MagicMock()
    monkeypatch.setattr(ws_chat, "clear_orphan_stamp", clear)
    graph = MagicMock()
    app = FastAPI()
    app.include_router(ws_chat.create_ws_chat_router(graph_getter=graph))
    yield store, broker, clear, graph, TestClient(app)
    store.close()


def seed(store, tenant, sid, tid, *, content=""):
    with tenant_scope(tenant):
        session = ChatSession(session_id=sid, tenant_id=tenant, thread_id=tid)
        if content:
            session.add_message("assistant", content)
        store.put(session)


def headers(tenant):
    cookie = create_session(actor=tenant, username=tenant, role="viewer", tenant_id=tenant)
    return {"Origin": "http://testserver", "Cookie": f"kazma-session={cookie}"}


@pytest.mark.parametrize("sid", ["victim", "gw-telegram-victim", "unknown"])
@pytest.mark.parametrize("cursor", ["", "?last_seq=0"])
def test_foreign_or_unknown_never_touches_graph_journal_or_orphan(boundary, sid, cursor):
    store, broker, clear, graph, client = boundary
    seed(store, "default", sid if sid != "unknown" else "other", "foreign-thread",
         content="SYNTHETIC_OTHER_TENANT_CONTENT")
    with client.websocket_connect(f"/ws/chat/{sid}{cursor}", headers=headers("tenant-b")) as ws:
        with pytest.raises(WebSocketDisconnect) as denied:
            ws.receive_json()
        assert denied.value.code == 4004
    assert not graph.called
    assert not broker.mock_calls
    clear.assert_not_called()
    with tenant_scope("tenant-b"):
        assert store.get(sid) is None


def test_two_tenants_keep_context_for_replay_resume_and_cleanup(boundary):
    store, broker, clear, graph, client = boundary
    for tenant in ("a", "b"):
        seed(store, tenant, "same-browser-id", f"thread-{tenant}")
    observed = []

    def resume(tid, seq):
        observed.append((get_current_tenant_id(), tid))
        return [], False, seq

    broker.resume.side_effect = resume
    with client.websocket_connect("/ws/chat/same-browser-id?last_seq=0", headers=headers("a")) as a:
        assert a.receive_json()["thread_id"] == "thread-a"
        with client.websocket_connect("/ws/chat/same-browser-id?last_seq=0", headers=headers("b")) as b:
            assert b.receive_json()["thread_id"] == "thread-b"
            for ws, tid in ((a, "thread-a"), (b, "thread-b")):
                ws.send_json({"action": "resume", "last_seq": 3, "thread_id": "foreign"})
                assert ws.receive_json()["thread_id"] == tid
    assert observed == [("a", "thread-a"), ("b", "thread-b"),
                        ("a", "thread-a"), ("b", "thread-b")]
    assert broker.unregister_socket.call_count == 2
    assert get_current_tenant_id() is None


def test_valid_owned_gateway_replays_only_its_answer_ignoring_forged_header(boundary):
    store, broker, clear, graph, client = boundary
    seed(store, "a", "gw-owned", "gw-owned", content="OWN ANSWER")
    credential = {**headers("a"), "X-Tenant-ID": "default"}
    with client.websocket_connect("/ws/chat/gw-owned", headers=credential) as ws:
        assert ws.receive_json()["data"]["content"] == "OWN ANSWER"
        ws.send_json({"action": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_empty_owned_memory_shell_can_watch_without_becoming_durable(boundary):
    store, broker, clear, graph, client = boundary
    with tenant_scope("a"):
        shell = store.get_or_create("shell", durable=False)
        shell.thread_id = "fresh-thread"
    with client.websocket_connect("/ws/chat/shell?last_seq=0", headers=headers("a")) as ws:
        assert ws.receive_json()["thread_id"] == "fresh-thread"
    with tenant_scope("a"):
        assert store.list_all() == []


def test_legacy_duplicate_graph_binding_cannot_claim_foreign_history(boundary):
    store, broker, clear, graph, client = boundary
    seed(store, "default", "victim", "shared-old-thread", content="FOREIGN")
    seed(store, "a", "old-copy", "shared-old-thread")
    store._sessions.clear()  # Detect durable rows outside the memory cache too.
    with client.websocket_connect("/ws/chat/old-copy?last_seq=0", headers=headers("a")) as ws:
        with pytest.raises(WebSocketDisconnect) as denied:
            ws.receive_json()
        assert denied.value.code == 4004
    assert not broker.mock_calls
    clear.assert_not_called()


def test_expired_cookie_cannot_fall_back_to_loopback_operator(boundary):
    store, broker, clear, graph, client = boundary
    seed(store, "default", "victim", "foreign-thread")
    credential = headers("a")
    revoke_session(credential["Cookie"].split("=", 1)[1])
    with client.websocket_connect("/ws/chat/victim", headers=credential) as ws:
        with pytest.raises(WebSocketDisconnect) as denied:
            ws.receive_json()
        assert denied.value.code == 4003
    assert not broker.mock_calls
    clear.assert_not_called()


def test_store_outage_denies_before_any_observation(boundary, monkeypatch):
    store, broker, clear, graph, client = boundary

    def broken(sid):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        raise OSError("synthetic ownership outage")

    monkeypatch.setattr(store, "get", broken)
    with client.websocket_connect("/ws/chat/victim", headers=headers("a")) as ws:
        with pytest.raises(WebSocketDisconnect) as denied:
            ws.receive_json()
        assert denied.value.code == 4004
    assert not broker.mock_calls
    clear.assert_not_called()


def test_cross_origin_cookie_is_rejected_even_on_loopback(boundary):
    *_, client = boundary
    credential = {**headers("a"), "Origin": "https://attacker.example"}
    with client.websocket_connect("/ws/chat/victim", headers=credential) as ws:
        with pytest.raises(WebSocketDisconnect) as denied:
            ws.receive_json()
        assert denied.value.code == 4003


def test_http_shell_registration_keeps_graphs_separate_and_enables_socket(boundary, monkeypatch):
    from kazma_ui import session_manager
    from kazma_ui.sse_chat import create_sse_chat_router

    store, broker, clear, graph, client = boundary
    monkeypatch.setattr(session_manager, "get_session_manager", lambda: store)
    client.app.middleware("http")(auth.create_tenant_middleware())
    client.app.include_router(create_sse_chat_router())
    threads = []
    for tenant in ("a", "b"):
        credential = headers(tenant)
        response = client.post("/api/chat/sessions", json={"session_id": "same-new-id"}, headers=credential)
        assert response.status_code == 200, response.text
        tid = response.json()["thread_id"]
        threads.append(tid)
        with client.websocket_connect("/ws/chat/same-new-id?last_seq=0", headers=credential) as ws:
            assert ws.receive_json()["thread_id"] == tid
        with tenant_scope(tenant):
            assert store.list_all() == []
    assert len(set(threads)) == 2
    assert "same-new-id" not in threads
    response = client.post("/api/chat/sessions", json={"session_id": "gw-foreign"}, headers=headers("a"))
    assert response.status_code == 404
    with tenant_scope("a"):
        assert store.get("gw-foreign") is None
