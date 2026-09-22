"""One admin decision, and it fails closed.

Regression for the 2026-09-22 audit. The admin check was copied into six route
modules and the copies had drifted: documents and skills denied when the check
raised; backup, both swarm panels and (implicitly) the memory clear ALLOWED.
Backup download hands out every database and secret-bearing file. Separately,
``get_request_principal`` read a session with no role as ``admin`` while
``create_session`` had long refused to mint one without a role.

``tests/test_static_gates.py`` keeps anyone from re-deriving the decision
outside ``kazma_ui.auth``.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

import kazma_ui.auth as auth


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/x", "headers": []})


@pytest.fixture
def auth_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "configured")
    monkeypatch.setattr(auth, "is_authenticated", lambda request, secret: True)

    def set_principal(principal):
        if isinstance(principal, Exception):
            def boom(request):
                raise principal

            monkeypatch.setattr(auth, "get_request_principal", boom)
        else:
            monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)

    return set_principal


def test_no_secret_is_open_mode(monkeypatch):
    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "")
    assert auth.admin_decision(_request()) == "ok"


def test_unauthenticated_is_unauthorized(monkeypatch):
    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "configured")
    monkeypatch.setattr(auth, "is_authenticated", lambda request, secret: False)
    assert auth.admin_decision(_request()) == "unauthorized"


@pytest.mark.parametrize(
    ("principal", "expected"),
    [
        ({"source": "secret", "role": "admin"}, "ok"),
        ({"source": "session", "role": "admin"}, "ok"),
        ({"source": "session", "role": "operator"}, "forbidden"),
        ({"source": "api_token", "role": "operator"}, "forbidden"),
        ({}, "forbidden"),
    ],
)
def test_roles(auth_on, principal, expected):
    auth_on(principal)
    assert auth.admin_decision(_request()) == expected


def test_a_check_that_raises_denies(auth_on):
    auth_on(RuntimeError("config store closed"))
    assert auth.admin_decision(_request()) == "forbidden"
    resp = auth.require_admin(_request())
    assert resp is not None and resp.status_code == 403


def test_backup_admin_gate_now_denies_when_the_check_raises(auth_on):
    """The worst drifted copy: it returned None (allow) on any exception."""
    from kazma_ui.routes_direct.backup import _require_admin

    auth_on(RuntimeError("config store closed"))
    resp = _require_admin(_request())
    assert resp is not None and resp.status_code == 403


def test_a_session_without_a_role_is_not_an_admin(monkeypatch):
    import kazma_core.security.web_sessions as web_sessions

    monkeypatch.setattr(auth, "extract_provided_credential", lambda request: "session:abc")
    monkeypatch.setattr(
        web_sessions, "get_session_payload", lambda sid: {"username": "legacy"}
    )
    principal = auth.get_request_principal(_request())
    assert principal is not None and principal["role"] == "viewer"
