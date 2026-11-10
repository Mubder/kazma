"""A demo visitor chats; the admin routes need an admin credential.

Security audit 2026-11-10. ``KAZMA_DEMO_MODE`` returned from the auth
middleware before any check, and these routes have no check of their own:
``POST /api/mcp/servers`` and ``/api/mcp/servers/{name}/start`` (a stdio
command is code on the host), ``POST /api/settings/account/tokens`` (a token
that then passes ``/api/approve``) and every ``PUT /api/settings/*``. A demo
visitor is now an operator (``auth.DEMO_VISITOR_ROLE``): the demo's chat
keeps working, the admin routes answer 403 unless the request carries the
shared secret or an admin session.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.security.web_sessions import SESSION_COOKIE, create_session
from kazma_ui import auth
from kazma_ui.auth import SECRET_HEADER, create_auth_middleware

SECRET = "a-long-enough-demo-secret-0123456789"
FLY = ("172.17.0.5", 50000)  # the demo's proxy: not loopback

ADMIN_ROUTES = [
    ("post", "/api/mcp/servers"),
    ("post", "/api/mcp/servers/fs/start"),
    ("post", "/api/settings/account/tokens"),
    ("put", "/api/settings/single"),
    ("put", "/api/settings/agent"),
    ("get", "/api/settings"),
    ("post", "/api/x/credentials"),
]
VISITOR_ROUTES = [
    ("post", "/api/chat/stream"),
    ("get", "/api/chat/sessions"),
    ("post", "/api/approve/t1"),
    ("get", "/api/auth/status"),
]


def _app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(create_auth_middleware())

    async def _ok() -> dict:
        return {"ok": True}

    for method, path in ADMIN_ROUTES + VISITOR_ROUTES:
        app.add_api_route(path, _ok, methods=[method.upper()])
    return app


@pytest.fixture()
def demo(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for var in ("KAZMA_AUTH_DISABLED", "KAZMA_PRODUCTION", "KAZMA_MULTI_USER", "KAZMA_TRUSTED_PROXIES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DEMO_MODE", "true")
    monkeypatch.setenv("KAZMA_SECRET", SECRET)
    return TestClient(_app(), client=FLY, base_url="http://kazma-demo.fly.dev")


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_a_visitor_is_refused_the_admin_routes(demo: TestClient, method: str, path: str) -> None:
    resp = demo.request(method.upper(), path)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Not available in the demo"


@pytest.mark.parametrize("method,path", VISITOR_ROUTES)
def test_a_visitor_still_chats(demo: TestClient, method: str, path: str) -> None:
    assert demo.request(method.upper(), path).status_code == 200


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_the_operator_of_the_demo_still_reaches_them(demo: TestClient, method: str, path: str) -> None:
    assert demo.request(method.upper(), path, headers={SECRET_HEADER: SECRET}).status_code == 200
    sid = create_session(actor="login", role="admin", username="owner")
    assert demo.request(method.upper(), path, cookies={SESSION_COOKIE: sid}).status_code == 200


def test_an_operator_session_or_a_wrong_secret_is_not_enough(demo: TestClient) -> None:
    sid = create_session(actor="login", role="operator", username="op")
    assert demo.post("/api/mcp/servers", cookies={SESSION_COOKIE: sid}).status_code == 403
    assert demo.post("/api/mcp/servers", headers={SECRET_HEADER: "wrong"}).status_code == 403


def test_with_no_secret_nobody_reaches_them(demo: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAZMA_SECRET")
    assert demo.post("/api/mcp/servers").status_code == 403
    assert demo.post("/api/chat/stream").status_code == 200


def test_control_the_old_demo_bypass_opened_them(demo: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: the demo branch as it was, passing every request."""
    async def _always(request, path, expected) -> bool:
        return True

    monkeypatch.setattr(auth, "_demo_visitor_may", _always)
    assert demo.post("/api/mcp/servers").status_code == 200
    assert demo.post("/api/settings/account/tokens").status_code == 200


def test_auth_me_says_the_visitor_is_not_an_admin() -> None:
    import inspect

    from kazma_ui.routes_direct import auth as auth_routes

    source = inspect.getsource(auth_routes.register_auth_routes)
    assert '"role": DEMO_VISITOR_ROLE' in source
    assert auth.DEMO_VISITOR_ROLE == "operator"
