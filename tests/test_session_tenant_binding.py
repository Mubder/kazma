"""A session carries its user's tenant, and the request runs under it.

Regression for the 2026-09-22 audit. ``create_session`` took a ``tenant_id``
that no login ever passed, and ``get_request_principal`` dropped it anyway, so
the tenant middleware's "principal may carry tenant" branch never fired: every
local or OIDC user ran as ``default``, and the tenant-binding branch of
``POST /api/memory/graph/clear`` was unreachable.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

import kazma_ui.auth as auth


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/x", "headers": []})


def _principal_for_payload(monkeypatch, payload):
    import kazma_core.security.web_sessions as web_sessions

    monkeypatch.setattr(auth, "extract_provided_credential", lambda request: "session:abc")
    monkeypatch.setattr(web_sessions, "get_session_payload", lambda sid: payload)
    return auth.get_request_principal(_request())


def test_a_bound_session_names_its_tenant(monkeypatch):
    principal = _principal_for_payload(
        monkeypatch, {"username": "ana", "role": "operator", "tenant_id": "acme"}
    )
    from kazma_core.tenant_isolation import principal_tenant_id

    assert principal_tenant_id(principal) == "acme"


@pytest.mark.parametrize("stored", [None, "", "default"])
def test_an_unbound_session_resolves_as_before(monkeypatch, stored):
    principal = _principal_for_payload(
        monkeypatch, {"username": "ana", "role": "operator", "tenant_id": stored}
    )
    from kazma_core.tenant_isolation import principal_tenant_id

    assert principal_tenant_id(principal) is None


def test_the_middleware_runs_the_request_under_the_session_tenant(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.tenant_context import get_current_tenant_id

    monkeypatch.setattr(
        auth,
        "get_request_principal",
        lambda request: {"role": "operator", "source": "session", "tenant_id": "acme"},
    )
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())

    @app.get("/whoami")
    def whoami() -> dict:
        return {"tenant": get_current_tenant_id()}

    with TestClient(app) as client:
        assert client.get("/whoami").json() == {"tenant": "acme"}


def test_a_local_user_keeps_its_tenant_through_login(monkeypatch):
    import kazma_core.security.platform_rbac as rbac

    store: dict[str, list] = {"users": []}
    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [dict(u) for u in store["users"]])
    monkeypatch.setattr(rbac, "_save_users_to_store", lambda users: store.update(users=users))

    created = rbac.create_local_user("ana", "correct horse battery", role="operator", tenant_id="acme")
    assert created.tenant_id == "acme"
    # An update that does not mention the tenant keeps it.
    rbac.create_local_user("ana", "another long password", role="viewer")
    user = rbac.authenticate_local_user("ana", "another long password")
    assert user is not None and user.tenant_id == "acme" and user.role == "viewer"

    with pytest.raises(ValueError):
        rbac.create_local_user("bob", "correct horse battery", tenant_id="../escape")


def test_oidc_tenant_claim_is_opt_in_and_validated(monkeypatch):
    from kazma_core.security.oidc import OidcConfig, oidc_tenant_from_claims

    monkeypatch.delenv("KAZMA_OIDC_TENANT_CLAIM", raising=False)
    assert oidc_tenant_from_claims({"tenant": "acme"}, OidcConfig()) is None

    monkeypatch.setenv("KAZMA_OIDC_TENANT_CLAIM", "org")
    cfg = OidcConfig()
    assert oidc_tenant_from_claims({"org": "acme"}, cfg) == "acme"
    assert oidc_tenant_from_claims({"org": ["acme"]}, cfg) == "acme"
    assert oidc_tenant_from_claims({"org": ["a", "b"]}, cfg) is None
    assert oidc_tenant_from_claims({"org": "../../etc"}, cfg) is None
    assert oidc_tenant_from_claims({}, cfg) is None
