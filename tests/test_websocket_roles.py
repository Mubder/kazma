"""The voice and dashboard sockets ask what their HTTP twins ask.

Security audit 2026-11-10. ``/ws/voice`` asked only "is anyone signed in?"
(``websocket_is_authenticated``): a viewer could run chat turns by voice, in
no tenant at all, on whatever chat id it named. ``/ws/dashboard`` streamed
every user's tool and model traces to a viewer, while its HTTP twin
(``GET /api/agents/tools``) is not a viewer's. Both now resolve the
principal: voice needs the role that may post a turn
(``POST /api/chat/stream``), runs in the caller's tenant and only on the
caller's own chat; the dashboard needs the role its twin needs. The single
operator (the secret, an admin session, this machine) is admitted as before.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from kazma_core.security.web_sessions import SESSION_COOKIE, create_session
from kazma_core.tenant_context import get_current_tenant_id
from kazma_ui import auth, routes_voice_ws
from kazma_ui.auth import SECRET_HEADER
from starlette.websockets import WebSocketDisconnect

SECRET = "a-long-enough-test-secret-for-sockets-01"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("KAZMA_AUTH_DISABLED", "KAZMA_DEV_WS_BYPASS", "KAZMA_DEMO_MODE",
                "KAZMA_TRUSTED_PROXIES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_SECRET", SECRET)


# ── /ws/voice ───────────────────────────────────────────────────────────


@pytest.fixture()
def voice(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, list[str | None]]:
    seen: list[str | None] = []

    async def _loop(websocket: WebSocket, graph_getter=None, agent_getter=None) -> None:
        await websocket.accept()
        seen.append(get_current_tenant_id())
        await websocket.send_json({"type": "ready"})
        await websocket.close()

    monkeypatch.setattr(routes_voice_ws, "handle_voice_websocket", _loop)
    app = FastAPI()

    @app.websocket("/ws/voice")
    async def _ws(websocket: WebSocket) -> None:
        await routes_voice_ws.serve_voice_websocket(websocket)

    return TestClient(app), seen


def _connect(client: TestClient, *, cookie: str | None = None, headers: dict | None = None):
    if cookie:
        client.cookies.set(SESSION_COOKIE, cookie)
    return client.websocket_connect("/ws/voice", headers=headers or {})


def test_a_viewer_cannot_talk(voice) -> None:
    client, seen = voice
    sid = create_session(actor="oidc", role="viewer", username="v@example.com")
    with pytest.raises(WebSocketDisconnect) as closed:
        with _connect(client, cookie=sid):
            pass
    assert closed.value.code == 4403
    assert seen == []


def test_an_operator_talks_in_their_own_tenant(voice) -> None:
    client, seen = voice
    sid = create_session(actor="login", role="operator", username="op", tenant_id="acme")
    with _connect(client, cookie=sid) as ws:
        assert ws.receive_json() == {"type": "ready"}
    assert seen == ["acme"]


def test_the_single_operator_is_admitted_as_before(voice) -> None:
    client, seen = voice
    with _connect(client, headers={SECRET_HEADER: SECRET}) as ws:
        assert ws.receive_json()["type"] == "ready"
    assert seen == ["default"]


def test_no_credential_is_unauthorized(voice) -> None:
    client, seen = voice
    with pytest.raises(WebSocketDisconnect) as closed:
        with _connect(client):
            pass
    assert closed.value.code == 4003 and seen == []


def test_control_without_the_role_rule_the_viewer_talked(voice, monkeypatch) -> None:
    """Negative control: the socket as it was (signed in is enough)."""
    client, seen = voice
    monkeypatch.setattr(auth, "principal_allows", lambda principal, path, method: True)
    sid = create_session(actor="oidc", role="viewer", username="v2@example.com")
    with _connect(client, cookie=sid) as ws:
        assert ws.receive_json()["type"] == "ready"
    assert len(seen) == 1


def test_a_voice_chat_must_be_the_callers_alone(monkeypatch) -> None:
    from kazma_ui import voice_turn
    from kazma_ui.session_manager import ChatSession

    session = ChatSession(session_id="vs-1")
    session.thread_id = "thread-shared"

    class _Store:
        exclusive = False

        def get(self, sid):
            return session if sid == "vs-1" else None

        def put(self, _s):
            pass

        def thread_is_exclusive(self, thread_id):
            return self.exclusive

    store = _Store()
    monkeypatch.setattr("kazma_ui.session_manager.get_session_manager", lambda: store)
    assert voice_turn.resolve_voice_session("vs-1") is None
    store.exclusive = True
    assert voice_turn.resolve_voice_session("vs-1") == (session, "thread-shared")


# ── /ws/dashboard ───────────────────────────────────────────────────────


@pytest.fixture()
def dashboard() -> TestClient:
    from kazma_ui.routes_direct import misc

    builder = SimpleNamespace(app=FastAPI(), _hitl_state={}, _graph_holder={},
                              session_store=None, templates=None)
    misc.register_misc_routes(builder)
    return TestClient(builder.app)


def _dash(client: TestClient, role: str):
    client.cookies.set(SESSION_COOKIE, create_session(actor="login", role=role, username=role))
    return client.websocket_connect("/ws/dashboard")


def test_a_viewer_gets_no_traces(dashboard: TestClient) -> None:
    with _dash(dashboard, "viewer") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4403


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_an_operator_and_an_admin_get_the_feed(dashboard: TestClient, role: str) -> None:
    with _dash(dashboard, role) as ws:
        assert ws.receive_json()["type"] == "connected"


def test_control_without_the_role_rule_the_viewer_got_the_feed(dashboard, monkeypatch) -> None:
    monkeypatch.setattr(auth, "principal_allows", lambda principal, path, method: True)
    with _dash(dashboard, "viewer") as ws:
        assert ws.receive_json()["type"] == "connected"


def test_the_twins_rules() -> None:
    from kazma_core.security.platform_rbac import role_allows

    assert not role_allows("viewer", "/api/agents/tools", "GET")
    assert role_allows("operator", "/api/agents/tools", "GET")
    assert not role_allows("viewer", "/api/chat/stream", "POST")
    assert role_allows("operator", "/api/chat/stream", "POST")
