"""Every sign-in is an account, or none (Spaces S1-04).

The shared secret, the loopback auto-login and an API token were "an admin
with no account behind them", and a session of the older multi-user mode
named a user and nothing more: nothing said WHO asked. Now
``space_binding.account_of`` answers for every principal: the owner's own
entries are the owner's account, a local user's session is that user's
account, and a sign-in Kazma has no account for is none -- which an install
that enforces Spaces refuses.

Through the real identity store and the real tenant middleware. The
principal as it was -- no account anywhere in it -- is the negative control.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.config_store import get_config_store
from kazma_core.security import platform_rbac
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
from kazma_ui import auth, space_binding

SECRET = {"username": "operator", "role": "admin", "user_id": "shared-secret", "source": "secret"}
API_TOKEN = {"username": "api-token", "role": "operator", "user_id": "api-token",
             "token_id": "t1", "source": "api_token"}
AUTO_LOGIN = {"username": "auto-cookie", "role": "admin", "user_id": "", "actor": "auto-cookie",
              "source": "session"}
SECRET_LOGIN = {"username": "operator", "role": "admin", "user_id": "shared-secret",
                "actor": "login", "source": "session"}
OIDC = {"username": "ana@example.org", "role": "operator", "user_id": "oidc-subject-1",
        "actor": "oidc", "source": "session"}

OWNER_ENTRIES = [SECRET, API_TOKEN, AUTO_LOGIN, SECRET_LOGIN]


@pytest.fixture(autouse=True)
def _fresh_identity():
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    yield
    store.delete(identity.REGISTRY_KEY)


def _session_of(user: platform_rbac.PlatformUser) -> dict[str, Any]:
    return {"username": user.username, "role": user.role, "user_id": user.user_id,
            "actor": "login", "source": "session"}


# ── Which account ────────────────────────────────────────────────────────


@pytest.mark.parametrize("principal", OWNER_ENTRIES, ids=lambda p: p.get("actor") or p["source"])
def test_the_owners_own_entries_are_the_owners_account(principal) -> None:
    assert space_binding.account_of(principal) == OWNER_ACCOUNT_ID


def test_an_api_token_keeps_its_role_and_is_the_owners() -> None:
    """A token made as operator is the owner's account acting as operator:
    the account says whose it is, the role what it may do."""
    assert space_binding.account_of(API_TOKEN) == OWNER_ACCOUNT_ID
    assert API_TOKEN["role"] == "operator"


def test_a_local_users_session_is_their_account() -> None:
    layla = platform_rbac.create_local_user("layla", "password-number-1", role="operator")
    omar = platform_rbac.create_local_user("omar", "password-number-2", role="viewer")
    by_name = {a["name"]: a["id"] for a in identity.list_accounts()}
    assert space_binding.account_of(_session_of(layla)) == by_name["layla"]
    assert space_binding.account_of(_session_of(omar)) == by_name["omar"]
    assert by_name["layla"] != by_name["omar"] != OWNER_ACCOUNT_ID


def test_a_disabled_users_session_is_no_account() -> None:
    layla = platform_rbac.create_local_user("layla", "password-number-1")

    def _disable(users: list[dict]) -> None:
        users[0]["enabled"] = False

    platform_rbac._update_users(_disable)
    assert space_binding.account_of(_session_of(layla)) is None


@pytest.mark.parametrize("principal", [
    None,
    OIDC,
    {"source": "session", "role": "admin", "user_id": "", "actor": "login"},
    {"source": "session", "role": "admin", "user_id": "shared-secret", "actor": "elsewhere"},
    {"source": "session", "role": "admin", "user_id": "nobody-here", "actor": "login"},
    {"source": "something-new", "role": "admin"},
], ids=["none", "oidc", "no-user", "secret-id-other-actor", "unknown-user", "unknown-source"])
def test_a_sign_in_kazma_has_no_account_for_is_none(principal) -> None:
    assert space_binding.account_of(principal) is None


def test_a_local_user_cannot_take_the_secrets_user_id() -> None:
    """A local user created with the id the secret sign-in is given is that
    user, never the owner."""
    mallory = platform_rbac.create_local_user(
        "mallory", "password-number-1", role="admin", user_id="shared-secret")
    account = space_binding.account_of(_session_of(mallory))
    assert account is not None and account != OWNER_ACCOUNT_ID


# ── Through the middleware ───────────────────────────────────────────────


def _whoami_app(monkeypatch: pytest.MonkeyPatch, principal: dict | None) -> TestClient:
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())

    @app.get("/api/whoami")
    def whoami() -> dict:
        bound = scope.current_scope()
        return {"space": scope.require_space_id(), "asker": bound.asker if bound else None}

    return TestClient(app)


@pytest.mark.parametrize("principal", OWNER_ENTRIES, ids=lambda p: p.get("actor") or p["source"])
def test_one_operator_every_owner_entry_is_bound_as_the_owner(monkeypatch, principal) -> None:
    with _whoami_app(monkeypatch, principal) as client:
        assert client.get("/api/whoami").json() == {"space": "default", "asker": OWNER_ACCOUNT_ID}


def test_an_unsigned_request_binds_no_asker(monkeypatch) -> None:
    with _whoami_app(monkeypatch, None) as client:
        assert client.get("/api/whoami").json() == {"space": "default", "asker": None}


def test_not_enforced_a_sign_in_with_no_account_runs_as_before(monkeypatch) -> None:
    """The older multi-user mode with OIDC: the tenant the session carries,
    and no asker -- exactly what it did before Spaces."""
    with _whoami_app(monkeypatch, {**OIDC, "tenant_id": "acme"}) as client:
        assert client.get("/api/whoami").json() == {"space": "acme", "asker": None}


def test_enforced_a_sign_in_with_no_account_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)
    with _whoami_app(monkeypatch, OIDC) as client:
        answer = client.get("/api/whoami")
    assert answer.status_code == 403 and answer.json()["code"] == "no_account"


@pytest.mark.parametrize("principal", OWNER_ENTRIES, ids=lambda p: p.get("actor") or p["source"])
def test_enforced_every_owner_entry_is_still_the_owner(monkeypatch, principal) -> None:
    monkeypatch.setattr(scope, "_enforced", True)
    with _whoami_app(monkeypatch, principal) as client:
        assert client.get("/api/whoami").json() == {"space": "default", "asker": OWNER_ACCOUNT_ID}


def test_enforced_an_unreadable_identity_store_is_503_never_a_guess(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)

    def _down(*_a, **_k):
        raise identity.IdentityUnavailable("the store is away")

    monkeypatch.setattr(space_binding, "space_for_request", _down)
    with _whoami_app(monkeypatch, SECRET) as client:
        answer = client.get("/api/whoami")
    assert answer.status_code == 503 and answer.json()["code"] == "identity_unavailable"


# ── Negative control ─────────────────────────────────────────────────────


def test_control_the_old_principal_named_no_account(monkeypatch) -> None:
    """The middleware as it was: the tenant alone. Nothing in a request said
    who asked, for any of the owner's entries."""
    from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

    app = FastAPI()

    @app.middleware("http")
    async def old_tenant_middleware(request, call_next):
        token = set_current_tenant_id("default")
        try:
            return await call_next(request)
        finally:
            reset_current_tenant_id(token)

    @app.get("/api/whoami")
    def whoami() -> dict:
        bound = scope.current_scope()
        return {"space": scope.require_space_id(), "asker": bound.asker if bound else None}

    with TestClient(app) as client:
        assert client.get("/api/whoami").json() == {"space": "default", "asker": None}
    for principal in OWNER_ENTRIES:
        assert "account" not in principal and "account_id" not in principal
