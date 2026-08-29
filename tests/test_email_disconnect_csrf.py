"""Disconnect logged the operator out of Kazma. Every time.

Reported live on 2026-08-29 while trying to re-consent to Google:
"disconnect log me out of kazma and re-login and disconnect still log me
out". Three defects lined up:

  1. `disconnectGmail()` sent no `X-Requested-With` header
  2. `_verify_same_origin` requires it, so the endpoint returned 403
  3. auth-guard.js redirected to /login on ANY 403

Individually small. Together, a one-line missing header presented as a
broken session, and re-logging in could never help because the header was
still missing on the next click.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_JS = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "static" / "js"


def _source(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


# ── every guarded call must carry the header ──────────────────────────


def _mutating_email_fetches() -> list[tuple[int, str, bool]]:
    src = _source("settings_integrations.js")
    found = []
    for m in re.finditer(
        r"fetch\(\s*'([^']*\/api\/email\/[^']*)'\s*,\s*\{(.*?)\}\s*\)", src, re.S
    ):
        url, body = m.group(1), m.group(2)
        if "method:" not in body:
            continue  # GETs are not behind the CSRF guard
        line = src[: m.start()].count("\n") + 1
        found.append((line, url, "X-Requested-With" in body))
    return found


def test_every_mutating_email_call_sends_the_csrf_header():
    """The guard's own docstring says the frontend sets this header. Three
    call sites did not, and each one was a 403 waiting to happen."""
    missing = [(ln, url) for ln, url, has in _mutating_email_fetches() if not has]
    assert not missing, f"fetches missing X-Requested-With: {missing}"


def test_the_scan_actually_found_calls():
    """A regex that matches nothing would make the test above vacuously
    pass forever."""
    calls = _mutating_email_fetches()
    assert len(calls) >= 5, f"expected several mutating calls, found {len(calls)}"


@pytest.mark.parametrize("endpoint", [
    "/api/email/gmail/disconnect",
    "/api/email/oauth/microsoft/disconnect",
    "/api/email/oauth/microsoft/device/start",
])
def test_the_three_reported_endpoints_are_fixed(endpoint):
    hit = [c for c in _mutating_email_fetches() if c[1] == endpoint]
    assert hit, f"{endpoint} is no longer called -- update this test"
    assert all(has for _, _, has in hit), f"{endpoint} still omits the header"


# ── and a forbidden response must not end the session ─────────────────


def test_only_401_redirects_to_login():
    """Nothing in Kazma returns 403 for an expired session: it is the CSRF
    guard, RBAC ("Forbidden for your role"), and the email same-origin
    guard. All of them mean "signed in, but not allowed" -- the opposite of
    a session expiry. Redirecting on 403 turned a missing header into a
    logout the operator could not escape by logging back in."""
    src = _source("auth-guard.js")
    assert "res.status === 401" in src
    assert "res.status === 403" not in src, (
        "a forbidden response must not log the operator out"
    )


def test_the_auth_endpoints_are_still_excluded():
    """Their 401 belongs to the login form, not the session guard."""
    assert '"/api/auth/"' in _source("auth-guard.js")


def test_the_redirect_still_exists_for_real_expiry():
    """Narrowing to 401 must not have removed session-expiry handling."""
    src = _source("auth-guard.js")
    assert "session_expired" in src
    assert "/login?next=" in src


# ── the server side is unchanged and still guarded ────────────────────


def test_the_endpoint_is_still_csrf_protected():
    """The fix belongs in the caller. Relaxing the guard would trade a
    logout bug for a CSRF hole."""
    api = (Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui"
           / "email_api.py").read_text(encoding="utf-8")
    assert "/gmail/disconnect" in api
    block = api[api.index("/gmail/disconnect") - 200: api.index("/gmail/disconnect") + 200]
    assert "_verify_same_origin" in block


def test_disconnect_clears_only_email_credentials():
    """A disconnect that reached further than email would be a second way
    to lose a session."""
    import inspect

    from kazma_skills.native.email_manager.protocol_connect import disconnect_protocol

    src = inspect.getsource(disconnect_protocol)
    assert "web_session" not in src
    assert "email.gmail" in src
