"""Settings -> Account changes what sign-in checks, and lists real sessions.

Found by the dead-control gate (``tests/test_settings_keys_have_readers.py``,
the 2026-11 functional audit's FA-250 class): Change password wrote
``account.password_hash``, which sign-in never reads -- sign-in checks the
platform user store -- so the old password kept working after a "Password
changed" toast. The session list read ``account.sessions``, which nothing
writes: it always said there were none.

Through the real routes, with real sessions minted the way sign-in mints them.
"""

from __future__ import annotations

import pytest

OLD, NEW = "old-password-1", "new-password-2"


@pytest.fixture()
def env(tmp_path):
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_core.config_store import get_config_store
    from kazma_core.security import platform_rbac
    from kazma_ui.settings import create_settings_router

    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(MagicMock(), get_config_store(),
                                              Jinja2Templates(directory=str(tmp_path / "templates"))))
    user = platform_rbac.create_local_user("layla", OLD, role="admin")
    return TestClient(app), user


def _session(user) -> str:
    from kazma_core.security.web_sessions import create_session

    return create_session(role=user.role, actor="web", username=user.username, user_id=user.user_id)


def _put(client, sid, old, new):
    from kazma_core.security.web_sessions import SESSION_COOKIE

    client.cookies.set(SESSION_COOKIE, sid)
    return client.put("/api/settings/account/password", json={"old_password": old, "new_password": new})


def test_a_password_change_is_the_one_sign_in_checks(env):
    from kazma_core.security.platform_rbac import authenticate_local_user
    from kazma_core.security.web_sessions import validate_session

    client, user = env
    here, elsewhere = _session(user), _session(user)
    resp = _put(client, here, OLD, NEW)
    assert resp.status_code == 200 and resp.json()["other_sessions_ended"] == 1
    assert authenticate_local_user("layla", NEW) is not None
    assert authenticate_local_user("layla", OLD) is None
    assert validate_session(here) and not validate_session(elsewhere)


def test_a_wrong_current_password_changes_nothing(env):
    from kazma_core.security.platform_rbac import authenticate_local_user

    client, user = env
    resp = _put(client, _session(user), "not-it-at-all", NEW)
    assert resp.status_code == 400 and resp.json()["error"] == "current_password_incorrect"
    assert authenticate_local_user("layla", OLD) is not None


def test_the_sign_in_secret_is_not_a_password_here(env):
    """A session minted from KAZMA_SECRET has no local user: the route says so
    (the page words it: the secret is in .env)."""
    from kazma_core.security.web_sessions import create_session

    client, _user = env
    sid = create_session(role="admin", actor="web", username="operator", user_id="shared-secret")
    resp = _put(client, sid, OLD, NEW)
    assert resp.status_code == 409 and resp.json()["error"] == "sign_in_secret"


def test_the_session_list_is_the_signed_in_persons(env):
    from kazma_core.security.web_sessions import SESSION_COOKIE, create_session

    client, user = env
    here, other = _session(user), _session(user)
    create_session(role="viewer", actor="web", username="someone", user_id="u-2")
    client.cookies.set(SESSION_COOKIE, here)
    rows = client.get("/api/settings/account/sessions").json()
    assert len(rows) == 2 and sum(r["current"] for r in rows) == 1
    assert all(here not in r["id"] and other not in r["id"] for r in rows)  # never the id itself


def test_the_old_change_left_the_old_password_working(env):
    """Negative control: what Change password did (a hash in
    account.password_hash) -- sign-in still takes the old password."""
    from kazma_core.config_store import get_config_store
    from kazma_core.security.platform_rbac import authenticate_local_user

    get_config_store().set("account.password_hash", "pbkdf2:600000:00:00", category="account")
    assert authenticate_local_user("layla", OLD) is not None
