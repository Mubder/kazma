"""A session does not outlive its user, nor the role it was minted with.

Security audit 2026-11-10. ``/api/saas/users`` deleted, disabled, re-roled
and re-passworded platform users and left every session they had: a session
carries its role, so a demoted admin stayed admin and a deleted or disabled
user stayed signed in for up to 14 days. Only the self-service password
change ended sessions (``settings.py``).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.security import platform_rbac, web_sessions
from kazma_core.security.web_sessions import SESSION_COOKIE, create_session, validate_session
from kazma_ui import saas_api
from kazma_ui.auth import SECRET_HEADER

SECRET = "a-long-enough-test-secret-for-users-0123"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("KAZMA_SECRET", SECRET)
    app = FastAPI()
    app.include_router(saas_api.create_saas_router())
    return TestClient(app, headers={SECRET_HEADER: SECRET})


def _user_with_sessions(name: str, role: str = "admin", count: int = 2) -> tuple[platform_rbac.PlatformUser, list[str]]:
    user =platform_rbac.create_local_user(name, "a-good-password-1", role=role)
    sids = [create_session(actor="login", role=role, username=user.username, user_id=user.user_id)
            for _ in range(count)]
    assert all(validate_session(s) for s in sids)
    return user, sids


def test_delete_ends_the_users_sessions(client: TestClient) -> None:
    _, sids = _user_with_sessions("del-me")
    other_user, others = _user_with_sessions("stays", count=1)
    resp = client.delete("/api/saas/users/del-me")
    assert resp.status_code == 200 and resp.json()["sessions_ended"] == 2
    assert not any(validate_session(s) for s in sids)
    assert validate_session(others[0]), "another user's session is untouched"


@pytest.mark.parametrize("change", [{"enabled": False}, {"role": "viewer"}, {"password": "new-password-22"}])
def test_disable_role_change_and_reset_end_sessions(client: TestClient, change: dict) -> None:
    _, sids = _user_with_sessions(f"u-{next(iter(change))}")
    resp = client.patch(f"/api/saas/users/u-{next(iter(change))}", json=change)
    assert resp.status_code == 200, resp.text
    assert resp.json()["sessions_ended"] == 2
    assert not any(validate_session(s) for s in sids)


def test_a_change_that_grants_nothing_new_keeps_sessions(client: TestClient) -> None:
    _, sids = _user_with_sessions("keeper")
    assert client.patch("/api/saas/users/keeper", json={"enabled": True}).json()["sessions_ended"] == 0
    assert client.patch("/api/saas/users/keeper", json={"role": "admin"}).json()["sessions_ended"] == 0
    assert all(validate_session(s) for s in sids)


def test_an_admin_resetting_their_own_password_stays_signed_in(client: TestClient) -> None:
    _, (mine, elsewhere) = _user_with_sessions("self-reset")
    resp = client.patch("/api/saas/users/self-reset", json={"password": "another-password-3"},
                        cookies={SESSION_COOKIE: mine})
    assert resp.json()["sessions_ended"] == 1
    assert validate_session(mine) and not validate_session(elsewhere)
    # Demoting yourself is not kept: the session would carry the old role.
    client.patch("/api/saas/users/self-reset", json={"role": "viewer"}, cookies={SESSION_COOKIE: mine})
    assert not validate_session(mine)


def test_a_failed_revoke_is_said(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _user_with_sessions("revoke-fails", count=1)

    def _boom(**_kw):
        raise RuntimeError("store away")

    monkeypatch.setattr(web_sessions, "revoke_user_sessions", _boom)
    resp = client.patch("/api/saas/users/revoke-fails", json={"enabled": False})
    assert resp.status_code == 503 and resp.json()["error"] == "sessions_not_ended"


def test_control_without_the_revoke_the_sessions_lived_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: the routes as they were (nothing ended)."""
    monkeypatch.setattr(web_sessions, "revoke_user_sessions", lambda **_kw: 0)
    _, sids = _user_with_sessions("old-rule")
    client.patch("/api/saas/users/old-rule", json={"role": "viewer"})
    client.delete("/api/saas/users/old-rule")
    assert all(validate_session(s) for s in sids)
