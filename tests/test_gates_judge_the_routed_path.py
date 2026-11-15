"""The sign-in gate, CSRF and the role check judge the path the router
matches (2026-11-15).

They read ``request.url.path``, which Starlette builds by parsing
``root_path + scope["path"]`` as a URL. ``scope["path"]`` is percent-decoded,
so ``%3F`` or ``%23`` in a request cut that reading short at the decoded
``?`` or ``#`` -- ``/api/auth/login%3Fx`` read as ``/api/auth/login``,
always open -- while the router matched the whole path; and under a
``root_path`` every gate read ``/<root>/api/...``, which no gate rule names.
No route of today was reachable that way; a route such as
``/api/mcp/{x:path}`` would have been. Now every gate asks
``kazma_ui.request_path.judged_path`` (the router's own reading) and a path
holding a decoded ``?`` or ``#`` is refused outright.

Synthetic routes stand for the future route; the gates are the product's.
Reading ``request.url.path`` again is the negative control.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from kazma_core.security import platform_rbac
from kazma_core.security.web_sessions import SESSION_COOKIE, create_session
from kazma_ui import auth, csrf, request_path

from tests._secured_app import call, secured_app

SECRET = "routed-path-test-secret-0123456789abcdef"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _signed_in_install(monkeypatch):
    monkeypatch.setenv("KAZMA_SECRET", SECRET)
    for name in ("KAZMA_AUTH_DISABLED", "KAZMA_DEMO_MODE", "KAZMA_MULTI_USER", "KAZMA_TRUSTED_PROXIES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(platform_rbac, "multi_user_enabled", lambda: False)


def _gated_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/auth/login{rest:path}")
    def future_login_child(rest: str) -> dict:
        return {"served": "a route under an always-open path"}

    @app.post("/api/chat{rest:path}")
    def future_chat_child(rest: str) -> dict:
        return {"served": "a route under a viewer path"}

    @app.post("/api/settings/x")
    def settings_write() -> dict:
        return {"served": "an admin write"}

    # The product's order: CSRF outside the tenant scope, outside sign-in.
    app.middleware("http")(auth.create_auth_middleware())
    app.middleware("http")(auth.create_tenant_middleware())
    app.middleware("http")(csrf.create_csrf_middleware())
    return app


def _raw(app, method: str, path: str, *, headers: dict[str, str] | None = None, root_path: str = ""):
    sent = [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
    sent.append((b"host", b"127.0.0.1:59999"))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.replace("?", "%3F").replace("#", "%23").encode(),
        "query_string": b"", "headers": sent, "client": ("203.0.113.9", 40000),
        "server": ("127.0.0.1", 59999), "root_path": root_path,
    }
    status: list[int] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status.append(message["status"])

    asyncio.run(app(scope, receive, send))
    return status[0]


def _url_path(conn):
    return conn.url.path


def _old_reading(monkeypatch):
    """The gates as they were: ``request.url.path``, nothing refused."""
    monkeypatch.setattr(auth, "judged_path", _url_path)
    monkeypatch.setattr(auth, "path_is_ambiguous", lambda conn: False)
    monkeypatch.setattr(csrf, "judged_path", _url_path)


def test_an_encoded_question_mark_is_refused_by_the_sign_in_gate():
    assert _raw(_gated_app(), "GET", "/api/auth/login?x") == 400


def test_negative_control_the_url_reading_served_it_without_sign_in(monkeypatch):
    _old_reading(monkeypatch)
    assert _raw(_gated_app(), "GET", "/api/auth/login?x") == 200


def test_the_role_check_reads_the_routed_path(monkeypatch):
    """A viewer session writing to an admin route served under a root path:
    the role check judges ``/api/settings/x``, the URL reading judged
    ``/kazma/api/settings/x`` -- no rule names it, so it passed."""
    sid = create_session(actor="test", role="viewer", username="viewer@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={sid}"}
    assert _raw(_gated_app(), "POST", "/kazma/api/settings/x", headers=cookie, root_path="/kazma") == 403
    assert _raw(_gated_app(), "POST", "/api/chat#/x", headers=cookie) == 400
    _old_reading(monkeypatch)
    assert _raw(_gated_app(), "POST", "/kazma/api/settings/x", headers=cookie, root_path="/kazma") == 200
    assert _raw(_gated_app(), "GET", "/api/auth/login#x") == 200


def test_under_a_root_path_every_gate_reads_the_app_path(monkeypatch):
    """Served under ``--root-path /kazma``: a cross-origin write to an admin
    route is refused by CSRF, and without a credential by the sign-in gate."""
    app = _gated_app()
    cross = {"origin": "https://evil.example"}
    assert _raw(app, "POST", "/kazma/api/settings/x", headers=cross, root_path="/kazma") == 403
    assert _raw(app, "POST", "/kazma/api/settings/x", root_path="/kazma") == 401
    _old_reading(monkeypatch)
    # Old: "/kazma/api/settings/x" is no /api path -- no CSRF, no sign-in.
    assert _raw(_gated_app(), "POST", "/kazma/api/settings/x", headers=cross, root_path="/kazma") == 200


def test_the_app_refuses_a_decoded_question_mark_or_hash_before_any_gate(monkeypatch):
    app = secured_app(monkeypatch)
    for path in ("/api/mcp?x/servers", "/api/settings#/single", "/chat?x"):
        answer = call(app, "GET", path, raw_path=path.replace("?", "%3F").replace("#", "%23").encode())
        assert answer.status == 400, path
        assert answer.json()["code"] == "ambiguous_path"


def test_a_websocket_with_one_is_closed():
    sent: list[dict] = []

    async def app(scope, receive, send):  # pragma: no cover - never reached
        raise AssertionError("the guard let it through")

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    guard = request_path.AmbiguousPathGuard(app)
    asyncio.run(guard({"type": "websocket", "path": "/ws/chat/a?b", "headers": []}, receive, send))
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_judged_path_is_the_routers_reading():
    scope = {"type": "http", "path": "/kazma/api/x?y", "root_path": "/kazma"}
    assert request_path.judged_path(scope) == "/api/x?y"
    assert request_path.path_is_ambiguous(scope)
    assert not request_path.path_is_ambiguous({"path": "/api/x/y"})


#: Modules whose gates judge a request's path.
GATE_MODULES = (
    "kazma-ui/kazma_ui/auth.py",
    "kazma-ui/kazma_ui/csrf.py",
    "kazma-ui/kazma_ui/ide_effects.py",
    "kazma-core/kazma_core/security/platform_rbac.py",
)


def test_no_gate_reads_the_url_path():
    pattern = re.compile(r"\.url\.path\b")
    found = [
        f"{rel}:{n}" for rel in GATE_MODULES
        for n, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line) and not line.lstrip().startswith("#")
    ]
    assert not found, f"a gate reads request.url.path again: {found}"


def test_the_scan_finds_a_planted_url_path_read(tmp_path):
    planted = tmp_path / "gate.py"
    planted.write_text("def gate(request):\n    return request.url.path\n", encoding="utf-8")
    assert re.search(r"\.url\.path\b", planted.read_text(encoding="utf-8"))
