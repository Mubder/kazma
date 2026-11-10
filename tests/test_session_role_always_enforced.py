"""A signed-in session's role is enforced whatever mode the install is in.

Security audit 2026-11-10, SEC-05. The middleware asked ``role_allows`` only
when multi-user mode was on (``KAZMA_MULTI_USER`` or local platform users).
An OIDC sign-in mints ``viewer`` / ``operator`` sessions with neither, and a
removed last user leaves live sessions behind: such a session reached every
admin route -- ``POST /api/mcp/servers`` (a command on the host), settings,
API tokens. The install's mail and calendar connections had no admin rule at
all (operators could reconnect or disconnect the install's mailbox).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_core.security import platform_rbac
from kazma_core.security.platform_rbac import role_allows
from kazma_core.security.web_sessions import SESSION_COOKIE, create_session
from kazma_ui import auth
from kazma_ui.auth import SECRET_HEADER, create_auth_middleware

SECRET = "a-long-enough-test-secret-0123456789"

ADMIN_ROUTES = [
    ("post", "/api/mcp/servers"),
    ("put", "/api/settings/single"),
    ("post", "/api/settings/account/tokens"),
    ("get", "/api/providers"),
    ("get", "/api/email/status"),
    ("get", "/api/email/oauth/gmail/start"),
    ("get", "/api/calendar/status"),
]


def _app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(create_auth_middleware())

    async def _ok() -> dict:
        return {"ok": True}

    for method, path in ADMIN_ROUTES:
        app.add_api_route(path, _ok, methods=[method.upper()])
    app.add_api_route("/api/chat/sessions", _ok, methods=["GET"])
    app.add_api_route("/api/chat/stream", _ok, methods=["POST"])
    return app


@pytest.fixture()
def single_operator(monkeypatch):
    """A secret is set; no local users, no KAZMA_MULTI_USER: OIDC's shape."""
    monkeypatch.setenv("KAZMA_SECRET", SECRET)
    monkeypatch.delenv("KAZMA_MULTI_USER", raising=False)
    monkeypatch.delenv("KAZMA_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("KAZMA_DEMO_MODE", raising=False)
    monkeypatch.setattr(platform_rbac, "multi_user_enabled", lambda: False)
    return TestClient(_app())


def _as(client: TestClient, role: str, method: str, path: str):
    sid = create_session(actor="oidc", role=role, username=f"{role}@example.com")
    return client.request(method.upper(), path, cookies={SESSION_COOKIE: sid},
                          headers={"origin": "http://testserver"})


@pytest.mark.parametrize("role", ["viewer", "operator"])
@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_a_non_admin_session_is_refused_admin_routes(single_operator, role, method, path):
    assert _as(single_operator, role, method, path).status_code == 403


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_an_admin_session_and_the_secret_still_pass(single_operator, method, path):
    assert _as(single_operator, "admin", method, path).status_code == 200
    by_secret = single_operator.request(method.upper(), path, headers={SECRET_HEADER: SECRET})
    assert by_secret.status_code == 200


def test_roles_keep_what_they_may_do(single_operator):
    assert _as(single_operator, "viewer", "get", "/api/chat/sessions").status_code == 200
    assert _as(single_operator, "viewer", "post", "/api/chat/stream").status_code == 403
    assert _as(single_operator, "operator", "post", "/api/chat/stream").status_code == 200


def test_an_unreadable_user_list_is_not_single_operator(single_operator, monkeypatch):
    """The user store failing must not open the admin API to a viewer."""
    def _boom() -> bool:
        raise platform_rbac.PlatformUsersUnavailable("store away")

    monkeypatch.setattr(platform_rbac, "multi_user_enabled", _boom)
    assert _as(single_operator, "viewer", "post", "/api/mcp/servers").status_code == 403
    assert _as(single_operator, "admin", "post", "/api/mcp/servers").status_code == 200


def test_control_the_old_rule_let_the_viewer_in(single_operator, monkeypatch):
    """With the role rule answering as it did when it was never asked."""
    monkeypatch.setattr(platform_rbac, "role_allows", lambda role, path, method="GET": True)
    assert _as(single_operator, "viewer", "post", "/api/mcp/servers").status_code == 200


def test_mail_and_calendar_connections_are_the_admins():
    for path in ("/api/email/status", "/api/email/accounts", "/api/email/oauth/gmail/start",
                 "/api/calendar/status", "/api/calendar/oauth/google/start.json"):
        for method in ("GET", "POST", "DELETE"):
            assert role_allows("operator", path, method) is False, (method, path)
            assert role_allows("admin", path, method) is True
    # a look-alike prefix is not swept in
    assert role_allows("operator", "/api/emailer", "GET") is True


def test_the_middleware_reads_the_session_credential():
    """The source names the rule, so a refactor cannot drop it silently."""
    import inspect

    source = inspect.getsource(auth.create_auth_middleware)
    assert "_multi_user or _session_cred" in source
