"""An API token acts with its own role, in every mode (2026-11-15).

``get_request_principal`` called an Account API token an ``operator``, and
``admin_decision`` refused it admin routes. The sign-in gate ran the role
check only ``if _multi_user or _session_cred``: on a single-operator install
(no platform users, no ``KAZMA_MULTI_USER``) a token skipped it, and
``POST /api/mcp/servers`` (a command on the host), ``PUT
/api/settings/single`` and ``POST /api/settings/account/tokens`` answered 200
to ``X-Api-Token: kazma_...`` -- while the docs tell owners to hand tokens to
other agents (A2A). Now a token carries the role it was made with
(``operator`` unless the owner chose another in Settings -> Account), the
gate enforces it in every mode, and ``admin_decision`` gives the same answer.
The shared secret stays the owner's own: admin.

Through ``create_app()``'s whole stack: the tokens are made by the real route.
The rule as it was -- a token skipping the role check like the secret -- is
the negative control.
"""

from __future__ import annotations

import pytest
from kazma_core.security import platform_rbac
from kazma_ui import auth
from starlette.requests import Request

from tests._secured_app import SECRET, call, secured_app

ADMIN_ROUTES = [
    ("POST", "/api/mcp/servers"),
    ("PUT", "/api/settings/single"),
    ("POST", "/api/settings/account/tokens"),
]

JSON = {"content-type": "application/json"}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(platform_rbac, "multi_user_enabled", lambda: False)
    return secured_app(monkeypatch)


def _make_token(app, role: str | None) -> str:
    body = {"name": f"token-{role}"}
    if role is not None:
        body["role"] = role
    import json

    answer = call(app, "POST", "/api/settings/account/tokens", body=json.dumps(body).encode(),
                  headers={**JSON, "x-kazma-secret": SECRET})
    assert answer.status == 200, answer.body
    data = answer.json()
    assert data["role"] == (role or "operator")
    return data["token"]


def _as_token(app, token: str, method: str, path: str):
    return call(app, method, path, body=b"{}", headers={**JSON, "x-api-token": token})


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_an_operator_token_is_refused_admin_routes_outside_multi_user(app, method, path):
    token = _make_token(app, None)  # the default role
    assert _as_token(app, token, method, path).status == 403


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_a_viewer_token_is_refused_too(app, method, path):
    token = _make_token(app, "viewer")
    assert _as_token(app, token, method, path).status == 403


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_an_admin_token_and_the_secret_pass(app, method, path):
    token = _make_token(app, "admin")
    assert _as_token(app, token, method, path).status != 403
    by_secret = call(app, method, path, body=b"{}", headers={**JSON, "x-kazma-secret": SECRET})
    assert by_secret.status not in (401, 403)


def test_an_operator_token_keeps_what_an_operator_may_do(app):
    token = _make_token(app, "operator")
    answer = call(app, "GET", "/api/chat/sessions", headers={"x-api-token": token})
    assert answer.status == 200
    viewer = _make_token(app, "viewer")
    assert call(app, "POST", "/api/bookmarks", body=b"{}", headers={**JSON, "x-api-token": viewer}).status == 403


def test_a_role_no_role_has_is_refused_at_creation(app):
    answer = call(app, "POST", "/api/settings/account/tokens", body=b'{"name": "x", "role": "root"}',
                  headers={**JSON, "x-kazma-secret": SECRET})
    assert answer.status == 400


def _request_with(token: str) -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/api/settings/single", "query_string": b"",
        "headers": [(b"x-api-token", token.encode())], "client": ("127.0.0.1", 1),
    })


def test_the_gate_and_admin_decision_give_one_answer(app):
    for role in ("viewer", "operator", "admin"):
        token = _make_token(app, role)
        request = _request_with(token)
        principal = auth.get_request_principal(request)
        assert principal["role"] == role and principal["source"] == "api_token"
        for method, path in ADMIN_ROUTES:
            gate = auth.principal_allows(principal, path, method)
            assert gate is (auth.admin_decision(request) == "ok") is (role == "admin"), (role, path)


def test_a_token_made_before_roles_is_an_operator_and_a_bad_role_a_viewer():
    assert auth._api_token_role({"name": "old"}) == "operator"
    assert auth._api_token_role({"role": "superuser"}) == "viewer"
    assert auth._api_token_role({"role": "admin"}) == "admin"


def test_negative_control_the_old_rule_let_an_operator_token_in(app, monkeypatch):
    """The gate as it was: outside multi-user only a session was judged by
    its role, so a token went through like the shared secret."""
    token = _make_token(app, "operator")
    real = auth.verify_secret

    def _token_is_the_secret(provided, expected):
        return real(provided, expected) or str(provided).startswith("kazma_")

    monkeypatch.setattr(auth, "verify_secret", _token_is_the_secret)
    assert _as_token(app, token, "PUT", "/api/settings/single").status != 403
