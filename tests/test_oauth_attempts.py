"""Every OAuth sign-in is one attempt: started by POST, bound to its browser, taken once.

Security audit 2026-11-10. GitHub's sign-in kept ONE install-wide state
(``connectors.github.oauth_state``) compared with ``!=``: a second start
replaced the first, and the state was good in any browser. The Gmail, Google
Calendar, Microsoft mail and Outlook Calendar sign-ins kept theirs in process
memory, unbounded and bound to no browser. Every start was a GET that wrote
state. Now (``kazma_core.security.oauth_attempts``, ``kazma_ui.oauth_sign_in``):

* a start is a POST from Kazma's own page that answers the provider's URL and
  sets a cookie naming the attempt, sent to the callback's path only;
* an attempt lives ten minutes, at most 32 at once, stored as its state's hash;
* the callback takes it once, with the browser's cookie compared in constant
  time: a state used twice, started in another browser, expired, or of another
  provider finishes nothing.

Through the real app, with stand-in providers (no network). The negative
control is the old single install-wide GitHub state, run through the same
scenarios.
"""

from __future__ import annotations

import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from kazma_core.config_store import get_config_store
from kazma_core.security import oauth_attempts
from kazma_core.security.oauth_attempts import (
    MAX_PENDING,
    PENDING_KEY,
    AttemptRefused,
    begin,
    binding_cookie_name,
    safe_return_path,
    take,
)

ORIGIN = "http://testserver"
PAGE = {"origin": ORIGIN, "x-requested-with": "XMLHttpRequest"}
GH_CALLBACK = "/api/github/oauth/callback"


@pytest.fixture(scope="module")
def app():
    from kazma_ui.app import create_app

    return create_app()


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch, app):
    """Stand-in GitHub, Google and Microsoft: what each finish was handed."""
    from kazma_gateway.routers import github_client
    from kazma_skills.native.calendar import oauth_google
    from kazma_skills.native.email_manager import oauth_gmail, oauth_ms_browser

    monkeypatch.setenv("KAZMA_PUBLIC_URL", ORIGIN)
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "Iv1.standin")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "standin-secret")
    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_ID", "standin.apps.googleusercontent.com")
    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_SECRET", "GOCSPX-standin")
    monkeypatch.setenv("EMAIL_MS_CLIENT_ID", "standin-azure-app")
    for var in ("EMAIL_GMAIL_REDIRECT_URI", "EMAIL_MS_REDIRECT_URI", "KAZMA_PRODUCTION"):
        monkeypatch.delenv(var, raising=False)
    seen: dict[str, list] = {"github_tokens": [], "gmail": [], "google_calendar": [], "microsoft": []}

    async def exchange(code: str, redirect_uri: str) -> dict:
        assert redirect_uri == f"{ORIGIN}{GH_CALLBACK}"
        return {"access_token": f"gho_{code}", "scope": "repo"}

    monkeypatch.setattr(github_client, "exchange_code_for_token", exchange)
    monkeypatch.setattr(github_client, "store_oauth_token", lambda data: seen["github_tokens"].append(data))

    def finisher(name: str):
        async def finish(code: str, attempt: dict) -> dict:
            seen[name].append(dict(attempt))
            return {"ok": True, "email": "me@example.com", "account": attempt.get("account") or None}
        return finish

    monkeypatch.setattr(oauth_gmail, "finish_gmail_oauth", finisher("gmail"))
    monkeypatch.setattr(oauth_google, "finish_google_calendar_oauth", finisher("google_calendar"))
    monkeypatch.setattr(oauth_ms_browser, "finish_ms_browser_oauth", finisher("microsoft"))
    return seen


def _browser(app) -> TestClient:
    """A browser: its own cookie jar. The TestClient's peer is not loopback,
    so the open-mode Host check (auth.py) is not what is tested here."""
    return TestClient(app)


def _state_of(answer) -> str:
    return parse_qs(urlsplit(answer.json()["authorize_url"]).query)["state"][0]


def _start(client: TestClient, url: str, body: dict | None = None, **headers):
    return client.post(url, json=body or {}, headers={**PAGE, **headers})


def _callback(client: TestClient, path: str, state: str, *, cookie: str | None = None, **params):
    headers = {"cookie": f"{binding_cookie_name(state)}={cookie}"} if cookie is not None else {}
    return client.get(path, params={"code": "c1", "state": state, **params}, headers=headers,
                      follow_redirects=False)


def _pending() -> dict:
    value = get_config_store().get(PENDING_KEY, {})
    return value if isinstance(value, dict) else {}


# ── the store ───────────────────────────────────────────────────────────


def test_an_attempt_is_taken_once_and_kept_only_as_a_hash() -> None:
    state = begin("github", redirect_uri="https://k.test/cb", return_to="/workspace")
    raw = repr(get_config_store().get(PENDING_KEY))
    assert state not in raw, "the state is kept in the browser only"
    attempt = take(state, bound_state=state, providers=("github",))
    assert attempt == {"provider": "github", "redirect_uri": "https://k.test/cb", "return_to": "/workspace"}
    with pytest.raises(AttemptRefused) as twice:
        take(state, bound_state=state, providers=("github",))
    assert twice.value.reason == "unknown"


def test_another_browser_is_refused_before_anything_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    state = begin("github", redirect_uri="https://k.test/cb")
    compared: list[tuple[bytes, bytes]] = []
    real = oauth_attempts.hmac.compare_digest
    monkeypatch.setattr(oauth_attempts.hmac, "compare_digest",
                        lambda a, b: compared.append((a, b)) or real(a, b))
    for cookie in ("", "x" * len(state), state[:-1] + ("A" if state[-1] != "A" else "B")):
        with pytest.raises(AttemptRefused) as other:
            take(state, bound_state=cookie, providers=("github",))
        assert other.value.reason == "other_browser"
    assert compared, "the cookie is compared in constant time"
    # The browser that started it still finishes: the refusals spent nothing.
    assert take(state, bound_state=state, providers=("github",))["provider"] == "github"


def test_an_expired_attempt_and_another_providers_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    import time as real_time

    old = begin("gmail", redirect_uri="https://k.test/cb")
    start = real_time.time()
    monkeypatch.setattr(oauth_attempts.time, "time", lambda: start + oauth_attempts.TTL_S + 1)
    with pytest.raises(AttemptRefused) as late:
        take(old, bound_state=old, providers=("gmail",))
    assert late.value.reason == "expired"
    monkeypatch.setattr(oauth_attempts.time, "time", real_time.time)
    gmail = begin("gmail", redirect_uri="https://k.test/cb")
    with pytest.raises(AttemptRefused) as other:
        take(gmail, bound_state=gmail, providers=("github",))
    assert other.value.reason == "other_provider"


def test_attempts_are_bounded_the_one_closest_to_expiry_goes_first() -> None:
    get_config_store().set(PENDING_KEY, {}, category="auth")
    states = [begin("github", redirect_uri="https://k.test/cb") for _ in range(MAX_PENDING + 3)]
    assert len(_pending()) == MAX_PENDING
    for evicted in states[:3]:
        with pytest.raises(AttemptRefused):
            take(evicted, bound_state=evicted, providers=("github",))
    assert take(states[-1], bound_state=states[-1], providers=("github",))


def test_the_record_is_runtime_state_never_restored() -> None:
    from kazma_core.settings_restore import RUNTIME, classify

    assert classify(PENDING_KEY) == RUNTIME


@pytest.mark.parametrize("raw", [
    "", "workspace", "//evil.example/x", "/\\evil.example", "https://evil.example/workspace",
    "/work\nspace", "/work\tspace", "javascript:alert(1)",
])
def test_a_return_path_names_this_server_only(raw: str) -> None:
    assert safe_return_path(raw, default="/workspace") == "/workspace"


def test_a_return_path_keeps_its_path_and_query() -> None:
    assert safe_return_path("/workspace?tab=pulls#x") == "/workspace?tab=pulls"


# ── GitHub through the real app ─────────────────────────────────────────


def test_a_github_sign_in_comes_back_to_the_page_it_started_from(app, providers) -> None:
    browser = _browser(app)
    started = _start(browser, "/api/github/oauth/start", {"return_to": "/workspace"})
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["ok"] and body["authorize_url"].startswith("https://github.com/login/oauth/authorize?")
    assert "state" not in body, "the state travels in the cookie and the provider's URL only"
    state = _state_of(started)
    cookie = started.headers["set-cookie"]
    for part in (f"{binding_cookie_name(state)}={state}", "HttpOnly", f"Path={GH_CALLBACK}",
                 "Max-Age=600"):
        assert part in cookie, (part, cookie)
    assert "samesite=lax" in cookie.lower()

    # GitHub sends the browser back: the jar holds the cookie for that path.
    back = browser.get(GH_CALLBACK, params={"code": "c1", "state": state}, follow_redirects=False)
    assert back.status_code == 302
    assert back.headers["location"] == "/workspace?github_oauth=ok"
    assert [t["access_token"] for t in providers["github_tokens"]] == ["gho_c1"]
    assert binding_cookie_name(state) in back.headers.get("set-cookie", ""), "the cookie is expired"


def test_a_github_state_used_twice_finishes_once(app, providers) -> None:
    browser = _browser(app)
    state = _state_of(_start(browser, "/api/github/oauth/start", {"return_to": "/workspace"}))
    first = _callback(browser, GH_CALLBACK, state, cookie=state)
    again = _callback(browser, GH_CALLBACK, state, cookie=state)
    assert first.headers["location"].endswith("github_oauth=ok")
    assert again.headers["location"] == "/workspace?github_oauth=error&github_oauth_reason=unknown"
    assert len(providers["github_tokens"]) == 1


def test_a_github_state_from_another_browser_finishes_nothing(app, providers) -> None:
    owner = _browser(app)
    state = _state_of(_start(owner, "/api/github/oauth/start", {"return_to": "/workspace"}))
    stranger = _browser(app)  # no cookie: the link was sent elsewhere
    refused = stranger.get(GH_CALLBACK, params={"code": "c1", "state": state}, follow_redirects=False)
    assert refused.headers["location"].endswith("github_oauth_reason=other_browser")
    assert providers["github_tokens"] == []
    # The owner's browser still finishes it.
    done = owner.get(GH_CALLBACK, params={"code": "c2", "state": state}, follow_redirects=False)
    assert done.headers["location"].endswith("github_oauth=ok")


def test_an_expired_github_state_finishes_nothing(app, providers, monkeypatch) -> None:
    import time as real_time

    browser = _browser(app)
    state = _state_of(_start(browser, "/api/github/oauth/start"))
    later = real_time.time() + oauth_attempts.TTL_S + 5
    monkeypatch.setattr(oauth_attempts.time, "time", lambda: later)
    late = _callback(browser, GH_CALLBACK, state, cookie=state)
    assert late.headers["location"] == "/workspace?github_oauth=error&github_oauth_reason=expired"
    assert providers["github_tokens"] == []


def test_another_providers_state_finishes_nothing(app, providers) -> None:
    gmail = begin("gmail", redirect_uri=f"{ORIGIN}/api/email/oauth/gmail/callback")
    at_github = _callback(_browser(app), GH_CALLBACK, gmail, cookie=gmail)
    assert at_github.headers["location"].endswith("github_oauth_reason=other_provider")
    github = begin("github", redirect_uri=f"{ORIGIN}{GH_CALLBACK}", return_to="/workspace")
    at_gmail = _callback(_browser(app), "/api/email/oauth/gmail/callback", github, cookie=github)
    assert at_gmail.headers["location"].endswith("email_oauth=error&reason=other_provider")
    assert providers["github_tokens"] == [] and providers["gmail"] == []


def test_two_sign_ins_in_progress_both_finish(app, providers) -> None:
    """The old single state: the second start replaced the first."""
    browser = _browser(app)
    first = _state_of(_start(browser, "/api/github/oauth/start"))
    second = _state_of(_start(browser, "/api/github/oauth/start"))
    for state in (first, second):
        assert _callback(browser, GH_CALLBACK, state, cookie=state).headers["location"].endswith("=ok")
    assert len(providers["github_tokens"]) == 2


@pytest.mark.parametrize("return_to", ["//evil.example/x", "/\\evil.example", "https://evil.example/"])
def test_the_github_callback_never_sends_the_browser_elsewhere(app, providers, return_to) -> None:
    browser = _browser(app)
    state = _state_of(_start(browser, "/api/github/oauth/start", {"return_to": return_to}))
    back = _callback(browser, GH_CALLBACK, state, cookie=state)
    assert back.headers["location"] == "/workspace?github_oauth=ok"


def test_starts_are_posts_from_this_page_only(app, providers) -> None:
    browser = _browser(app)
    for path in ("/api/github/oauth/start", "/api/email/oauth/gmail/start",
                 "/api/email/oauth/microsoft/start", "/api/calendar/oauth/google/start",
                 "/api/calendar/oauth/microsoft/start"):
        assert browser.get(path, follow_redirects=False).status_code == 405, path
        crossed = browser.post(path, json={}, headers={"origin": "https://evil.example",
                                                       "x-requested-with": "XMLHttpRequest"})
        assert crossed.status_code == 403, (path, crossed.status_code)
    for gone in ("/api/email/oauth/gmail/start.json", "/api/email/oauth/microsoft/start.json",
                 "/api/calendar/oauth/google/start.json", "/api/calendar/oauth/microsoft/start.json"):
        assert browser.get(gone).status_code in (404, 405), gone


def test_a_start_from_another_origin_than_the_callback_is_refused_first(app, providers) -> None:
    """A page on http://localhost while the provider sends the browser back to
    KAZMA_PUBLIC_URL: the cookie would never come back. Said at the start,
    with the origin to open, and nothing kept."""
    before = set(_pending())
    # The page is served from http://localhost:9090 (CSRF: its own origin).
    page = TestClient(app, base_url="http://localhost:9090")
    for path in ("/api/github/oauth/start", "/api/email/oauth/gmail/start"):
        answer = _start(page, path, origin="http://localhost:9090")
        assert answer.status_code == 409, (path, answer.text)
        assert answer.json()["code"] == "sign_in_from_callback_origin"
        assert answer.json()["origin"] == ORIGIN
        assert "kazma-oauth-" not in answer.headers.get("set-cookie", "")
    assert set(_pending()) == before


# ── mail and calendar through the real app ──────────────────────────────


def test_each_mail_and_calendar_sign_in_reaches_its_finish(app, providers) -> None:
    cases = [
        ("/api/email/oauth/gmail/start", {"account": "Work"}, "/api/email/oauth/gmail/callback",
         "gmail", "email_oauth=ok&provider=gmail"),
        ("/api/calendar/oauth/google/start", {}, "/api/email/oauth/gmail/callback",
         "google_calendar", "calendar_oauth=ok&provider=google"),
        ("/api/email/oauth/microsoft/start", {}, "/api/email/oauth/microsoft/callback",
         "microsoft", "email_oauth=ok&provider=microsoft"),
        ("/api/calendar/oauth/microsoft/start", {}, "/api/email/oauth/microsoft/callback",
         "microsoft", "calendar_oauth=ok&provider=outlook"),
    ]
    for start, body, callback, finish, says in cases:
        browser = _browser(app)
        started = _start(browser, start, body)
        assert started.status_code == 200, (start, started.text)
        assert f"Path={callback}" in started.headers["set-cookie"], start
        state = _state_of(started)
        back = browser.get(callback, params={"code": "c1", "state": state}, follow_redirects=False)
        assert back.status_code == 302 and says in back.headers["location"], (start, back.headers["location"])
        stranger = _browser(app).get(callback, params={"code": "c1", "state": state},
                                     follow_redirects=False)
        assert "reason=other_browser" in stranger.headers["location"], start
    assert providers["gmail"][0]["account"] == "work"
    assert [a.get("purpose") for a in providers["microsoft"]] == ["mail", "calendar"]
    assert len(providers["google_calendar"]) == 1


def test_a_used_or_expired_mail_state_finishes_nothing(app, providers, monkeypatch) -> None:
    import time as real_time

    browser = _browser(app)
    state = _state_of(_start(browser, "/api/email/oauth/gmail/start"))
    first = _callback(browser, "/api/email/oauth/gmail/callback", state, cookie=state)
    again = _callback(browser, "/api/email/oauth/gmail/callback", state, cookie=state)
    assert "email_oauth=ok" in first.headers["location"]
    assert again.headers["location"].endswith("email_oauth=error&reason=unknown")
    late = _state_of(_start(browser, "/api/email/oauth/microsoft/start"))
    future = real_time.time() + oauth_attempts.TTL_S + 5
    monkeypatch.setattr(oauth_attempts.time, "time", lambda: future)
    expired = _callback(browser, "/api/email/oauth/microsoft/callback", late, cookie=late)
    assert expired.headers["location"].endswith("email_oauth=error&reason=expired")
    assert len(providers["gmail"]) == 1 and providers["microsoft"] == []


# ── the negative control: the old single install-wide GitHub state ──────


def _old_start() -> str:
    """The start as it was: ONE state for the install, in settings."""
    state = secrets.token_urlsafe(16)
    get_config_store().set("connectors.github.oauth_state", state, category="connectors")
    return state


def _old_callback_accepts(state: str) -> bool:
    """The callback as it was: the state equal to the one stored (``!=``),
    whoever's browser brings it."""
    expected = get_config_store().get("connectors.github.oauth_state", "")
    return bool(state) and state == expected


def test_control_the_old_state_let_another_browser_in_and_lost_the_first_start() -> None:
    first = _old_start()
    assert _old_callback_accepts(first), "another browser with no cookie at all"
    second = _old_start()
    assert not _old_callback_accepts(first), "the second start replaced the first"
    assert _old_callback_accepts(second)
    # The same two scenarios on the new store: refused, and both kept.
    a = begin("github", redirect_uri="https://k.test/cb")
    b = begin("github", redirect_uri="https://k.test/cb")
    with pytest.raises(AttemptRefused):
        take(a, bound_state="", providers=("github",))
    assert take(a, bound_state=a, providers=("github",)) and take(b, bound_state=b, providers=("github",))
