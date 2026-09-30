"""The login throttle limits each address and each username, never everyone
(audit 2026-09-30, AUD-020), and the password check runs off the event loop
(AUD-002/003).

200 failures from any addresses used to refuse EVERY login for five minutes,
the owner's included; behind a proxy twenty addresses (one IPv6 /64) could
renew it forever. And the route ran a user-store read plus PBKDF2 at 600,000
iterations (0.2 s) on the event loop, freezing every chat stream per attempt.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SECRET = "k" * 32


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


@pytest.fixture
def login(monkeypatch):
    """The real login/logout routes, a client address from a test header,
    and a fake user store with one user (alice / right)."""
    from kazma_core.security import platform_rbac, web_sessions
    from kazma_ui import auth
    from kazma_ui.routes_direct.auth import register_auth_routes

    monkeypatch.setenv("KAZMA_SECRET", SECRET)
    monkeypatch.setattr(auth, "client_address", lambda request: request.headers.get("x-test-addr", "?"))
    seen = {"auth_on_loop": [], "revoke_on_loop": []}

    def authenticate_local_user(username: str, password: str):
        seen["auth_on_loop"].append(_loop_running())
        if (username, password) == ("alice", "right"):
            return SimpleNamespace(username="alice", role="admin", user_id="u1", tenant_id=None)
        return None

    def revoke_session(sid: str) -> None:
        seen["revoke_on_loop"].append(_loop_running())

    monkeypatch.setattr(platform_rbac, "authenticate_local_user", authenticate_local_user)
    monkeypatch.setattr(web_sessions, "revoke_session", revoke_session)
    stub = SimpleNamespace(app=FastAPI())
    register_auth_routes(stub)
    client = TestClient(stub.app)

    def attempt(addr: str, **body):
        return client.post("/api/auth/login", json=body, headers={"x-test-addr": addr}).status_code

    return SimpleNamespace(attempt=attempt, client=client, seen=seen)


def test_a_distributed_attack_does_not_lock_the_owner_out(login) -> None:
    failures = 0
    for n in range(25):                      # 25 addresses x 10 failures each
        for _ in range(10):
            assert login.attempt(f"10.0.0.{n}", secret="wrong") == 401
            failures += 1
        assert login.attempt(f"10.0.0.{n}", secret="wrong") == 429, "each address is limited"
    assert failures >= 200, "the old global cap (200 in 5 minutes) would now refuse everyone"
    assert login.attempt("203.0.113.9", secret=SECRET) == 200, "the owner still gets in"


def test_failures_against_one_username_never_lock_another(login) -> None:
    for n in range(10):                      # ten addresses, one username
        assert login.attempt(f"10.1.0.{n}", username="alice", password="wrong") == 401
    assert login.attempt("10.1.1.1", username="alice", password="right") == 429, "alice is limited"
    assert login.attempt("10.1.1.2", username="bob", password="wrong") == 401, "bob is not"
    assert login.attempt("10.1.1.3", secret=SECRET) == 200, "nor is the shared secret"


def test_a_successful_login_clears_the_username_lock(login) -> None:
    """Nine wrong tries then the right one must not leave the account locked
    for the rest of the window."""
    for n in range(9):
        assert login.attempt(f"10.3.0.{n}", username="alice", password="wrong") == 401
    assert login.attempt("10.3.1.1", username="alice", password="right") == 200, "the right password gets in"
    # The counter is cleared: nine fresh failures are needed before a lock again.
    for n in range(9):
        assert login.attempt(f"10.3.2.{n}", username="alice", password="wrong") == 401, n
    assert login.attempt("10.3.3.1", username="alice", password="right") == 200, "still not locked after nine"


def test_the_password_check_and_logout_run_off_the_event_loop(login) -> None:
    assert login.attempt("10.2.0.1", username="alice", password="right") == 200
    assert login.seen["auth_on_loop"] == [False], login.seen
    login.client.cookies.set("kazma-session", "some-session")
    assert login.client.post("/api/auth/logout").status_code == 200
    assert login.seen["revoke_on_loop"] == [False], login.seen


def test_negative_control_the_probe_sees_a_call_on_the_loop() -> None:
    async def inline() -> bool:
        return _loop_running()

    assert asyncio.run(inline()) is True
    out: list[bool] = []
    t = threading.Thread(target=lambda: out.append(_loop_running()))
    t.start()
    t.join()
    assert out == [False]


@pytest.mark.parametrize(("value", "warned"), [
    ("short-secret", True),
    ("x" * 19, True),
    ("x" * 20, False),
    ("", False),
])
def test_a_short_secret_set_by_hand_is_said_at_boot(monkeypatch, caplog, value, warned) -> None:
    from kazma_ui.auth import warn_if_weak_secret

    if value:
        monkeypatch.setenv("KAZMA_SECRET", value)
    else:
        monkeypatch.delenv("KAZMA_SECRET", raising=False)
    with caplog.at_level(logging.WARNING, logger="kazma_ui.auth"):
        assert warn_if_weak_secret() is warned
    assert any("KAZMA_SECRET is" in r.getMessage() for r in caplog.records) is warned
    assert value not in caplog.text or not value, "the secret itself is never logged"
