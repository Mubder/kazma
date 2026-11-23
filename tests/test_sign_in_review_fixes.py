"""The review of S4-02 (stronger sign-in), each fix through the real app
with sign-in on, and today's behaviour as its control.

1. **The install's secret makes no browser session once the owner has a
   second factor.** It never mints a session cookie then, and a browser
   that sends it (``Accept: text/html`` or ``Sec-Fetch-*``) is refused; a
   program -- the CLI's ``local_api``, ``scripts/deploy_live.py`` -- keeps it
   as its key. Control: the review's chain (401 at sign-in, then a browser
   with the header gets a cookie that works with no code).
2. **People's reset of a second factor** needs a signed-in admin's session,
   never the install's secret or a token, and never the admin's own.
   Control: the route without the rule, where a token switched the owner's
   own second factor off.
3. **Wrong codes pause an account's code checks**, in every mode, from every
   address, a known one too. Control: no pause.
4. **Turning a second factor on ends the account's other sessions**; the
   current one stays. Control: none ended.
5. **The owner's sessions of every kind** (signed in, the auto-login, a
   browser that sent the secret) are listed and ended in My account and on
   People; My account says the auto-login asks no code there and that the
   install's secret is not covered. Control: the list of before.
6. **An invitation's token never reaches a log**: the link is
   ``/invite#<token>`` and a path carrying one is cut from the access log.
   Control: the access line without the filter.
7. **A vault that failed to open never stops a sign-in with no second
   factor** (the marker is read first). Control: the rule that read the
   vault for every sign-in.
8. **The names told of a pause stay bounded.** Control: the dict of before.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import time
import urllib.request
from pathlib import Path
from typing import Any

import httpx
import pytest
from kazma_core.config_store import get_config_store
from kazma_core.security import second_factor
from kazma_core.spaces import people
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
from kazma_ui.routes_direct import auth as login_routes

from tests._secured_app import SECRET
from tests.test_sign_in_hardening import (  # noqa: F401 -- fixtures
    OWNER,
    PASSWORD,
    _client,
    _code,
    _local,
    _member,
    _not_a_code,
    _sign_in,
    _token,
    _turn_on,
    app,
    enforced,
)

pytestmark = pytest.mark.real_sign_in

#: Wrong codes before a pause, as shipped (a control changes the module's).
WRONG = second_factor.WRONG_CODES

#: What a browser sends: a navigation, and a page's own call.
NAVIGATION = {"accept": "text/html", "sec-fetch-mode": "navigate", "sec-fetch-site": "none"}
PAGE_CALL = {"sec-fetch-mode": "cors", "sec-fetch-site": "same-origin"}


# ── 1. The secret in a browser ───────────────────────────────────────────


def test_the_secret_makes_no_browser_session_once_the_owner_has_a_second_factor(app) -> None:
    program = _client(app)
    _turn_on(program, OWNER)
    assert _sign_in(program, secret=SECRET).status_code == 401
    browser = _client(app, "203.0.113.30")
    page = browser.get("/chat", headers={**OWNER, **NAVIGATION}, follow_redirects=False)
    assert "kazma-session" not in page.cookies, "the secret in a browser made a session with no code"
    call = browser.get("/api/account/second-factor", headers={**OWNER, **PAGE_CALL})
    assert call.status_code == 401 and call.json()["error"] == "secret_in_browser", call.text
    navigated = browser.get("/api/settings", headers={**OWNER, **NAVIGATION}, follow_redirects=False)
    assert navigated.status_code in (303, 401) and "kazma-session" not in navigated.cookies
    # A program keeps the secret as its key.
    assert program.get("/api/account/second-factor", headers=OWNER).status_code == 200


def test_negative_control_the_review_s_chain(app, monkeypatch) -> None:
    """Planted: today's rule (the secret mints a browser session whatever
    the owner set). The review's chain: 401 at sign-in, then a cookie that
    works with no code. The check above fails on its own assertion."""
    from kazma_ui import auth

    monkeypatch.setattr(auth, "_owner_second_factor_on", lambda: False)
    with pytest.raises(AssertionError, match="made a session with no code"):
        test_the_secret_makes_no_browser_session_once_the_owner_has_a_second_factor(app)


def test_the_programs_keep_the_secret_as_their_key(app, monkeypatch, tmp_path: Path) -> None:
    """The CLI's ``local_api`` and ``scripts/deploy_live.py`` send the
    secret as programs: with the owner's second factor on, both still read
    the API. Their real requests, answered by the real app."""
    program = _client(app)
    _turn_on(program, OWNER)

    def answer(method: str, path: str, headers: dict[str, str], content: bytes = b"") -> Any:
        return program.request(method, path, headers=headers, content=content)

    # The CLI: local_api.request_json through httpx.
    from kazma_core.runtime import local_api

    monkeypatch.setenv("KAZMA_SECRET", SECRET)
    real_client = httpx.Client

    def forwarding_client(**kwargs: Any) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            got = answer(request.method, request.url.path, dict(request.headers), request.content)
            return httpx.Response(got.status_code, headers=dict(got.headers), content=got.content)

        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", forwarding_client)
    assert local_api.request_json("GET", "/api/account/second-factor")["on"] is True
    monkeypatch.setattr(httpx, "Client", real_client)

    # The deploy check: deploy_live.Server through urllib, with no owner token.
    spec = importlib.util.spec_from_file_location(
        "_deploy_live_review", Path(__file__).resolve().parents[1] / "scripts" / "deploy_live.py")
    deploy_live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy_live)  # type: ignore[union-attr]
    (tmp_path / ".env").write_text(f"KAZMA_SECRET={SECRET}\n", encoding="utf-8")

    class _Opened:
        def __init__(self, got: Any) -> None:
            self.status = got.status_code
            self._body = got.content

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> _Opened:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    def urlopen(req: urllib.request.Request, timeout: float = 0) -> _Opened:
        headers = {k: v for k, v in req.header_items()}
        got = answer(req.get_method(), urllib.request.urlparse(req.full_url).path, headers, req.data or b"")
        if got.status_code >= 400:
            raise AssertionError(f"the deploy check was refused: {got.status_code} {got.text}")
        return _Opened(got)

    monkeypatch.setattr(deploy_live.urllib.request, "urlopen", urlopen)
    status, body = deploy_live.Server(tmp_path).json("GET", "/api/account/second-factor")
    assert status == 200 and body["on"] is True


# ── 2. People's reset ────────────────────────────────────────────────────


def _admin_session(app: Any, secret: str, address: str = "203.0.113.41") -> Any:
    admin = _client(app, address)
    signed = _sign_in(admin, secret=SECRET, code=_code(secret, step_offset=1))
    assert signed.status_code == 200, signed.text
    return admin


def test_the_people_reset_is_a_signed_in_admin_s_and_never_their_own(app, enforced, monkeypatch) -> None:
    owner_secret, _codes = _turn_on(_client(app), OWNER)
    member = _client(app, "203.0.113.40")
    person = _member(member, "rana-reset")
    _turn_on(member)
    # An admin's API token, on the owner's own second factor: the review's finding.
    from kazma_ui import auth

    token = {"source": "api_token", "role": "admin", "user_id": "api-token", "username": "api-token"}
    real_principal = auth.get_request_principal
    monkeypatch.setattr(auth, "get_request_principal", lambda request: token)
    by_token = _client(app).post(f"/api/people/{OWNER_ACCOUNT_ID}/second-factor/reset", headers=OWNER)
    monkeypatch.setattr(auth, "get_request_principal", real_principal)
    assert second_factor._is_on(OWNER_ACCOUNT_ID), "a token switched off the owner's second factor"
    assert by_token.status_code == 403
    # The install's secret: a program's key.
    by_secret = _client(app).post(f"/api/people/{person['id']}/second-factor/reset", headers=OWNER)
    assert by_secret.status_code == 403 and by_secret.json()["error"] == "session_only", by_secret.text
    # The admin's own, from their session: in My account, with a code.
    admin = _admin_session(app, owner_secret)
    own = admin.post(f"/api/people/{OWNER_ACCOUNT_ID}/second-factor/reset")
    assert own.status_code == 409 and own.json()["error"] == "own_in_my_account"
    assert second_factor._is_on(OWNER_ACCOUNT_ID)
    # Anyone else's, from the admin's session: done.
    assert admin.post(f"/api/people/{person['id']}/second-factor/reset").status_code == 200
    assert not second_factor._is_on(person["id"])


def test_negative_control_the_reset_without_its_rule(app, enforced, monkeypatch) -> None:
    from kazma_ui import sign_in_api

    monkeypatch.setattr(sign_in_api, "_reset_refusal", lambda principal, account_id, by: None)
    with pytest.raises(AssertionError, match="a token switched off the owner's second factor"):
        test_the_people_reset_is_a_signed_in_admin_s_and_never_their_own(app, None, monkeypatch)


# ── 3. Wrong codes pause ─────────────────────────────────────────────────


def _older_mode_user(app: Any, name: str, home: Any) -> str:
    """A user of the older multi-user mode (Spaces off) with a second factor."""
    from kazma_core.security import platform_rbac

    platform_rbac.create_local_user(name, PASSWORD)
    assert _sign_in(home, username=name, password=PASSWORD).status_code == 200
    secret, _codes = _turn_on(home)
    return secret


def _pause_ends(account_id: str) -> None:
    def _update(current: Any) -> dict[str, Any]:
        held = dict(current or {})
        entry = dict(held.get(account_id) or {})
        entry["until"] = time.time() - 1
        held[account_id] = entry
        return held

    get_config_store().atomic_update(second_factor.PAUSES_KEY, _update, category="system")


def test_wrong_codes_pause_the_account_from_every_address(app) -> None:
    home = _client(app, "198.51.100.7")
    secret = _older_mode_user(app, "nada-codes", home)
    account = second_factor.account_for_sign_in(
        next(u.user_id for u in __import__("kazma_core.security.platform_rbac", fromlist=["x"]).list_users()
             if u.username == "nada-codes"))
    for n in range(WRONG):
        wrong = _sign_in(_client(app, f"203.0.113.{60 + n}"), username="nada-codes", password=PASSWORD,
                         code=_not_a_code(secret))
        assert wrong.status_code == 401 and wrong.json()["error"] == "wrong_second_factor"
    right = _code(secret, step_offset=1)
    paused = _sign_in(_client(app, "203.0.113.90"), username="nada-codes", password=PASSWORD, code=right)
    assert paused.status_code == 429, f"five wrong codes did not pause the account: {paused.status_code}"
    assert paused.json()["error"] == "second_factor_paused"
    # A known address is no way round it.
    assert _sign_in(home, username="nada-codes", password=PASSWORD, code=right).status_code == 429
    # My account says so.
    assert home.get("/api/account/second-factor").json()["last_pause"] > 0
    # The pause runs out: the right code signs in, and the next pause is longer.
    _pause_ends(account)
    assert _sign_in(home, username="nada-codes", password=PASSWORD, code=right).status_code == 200
    for n in range(WRONG):
        _sign_in(_client(app, f"203.0.113.{70 + n}"), username="nada-codes", password=PASSWORD,
                 code=_not_a_code(secret))
    entry = get_config_store().get(second_factor.PAUSES_KEY)[account]
    assert entry["until"] - entry["last"] == pytest.approx(second_factor.PAUSE_S, abs=2), (
        "a right code starts the growth again")


def test_negative_control_no_pause(app, monkeypatch) -> None:
    monkeypatch.setattr(second_factor, "WRONG_CODES", 10**9)
    with pytest.raises(AssertionError, match="five wrong codes did not pause the account"):
        test_wrong_codes_pause_the_account_from_every_address(app)


def test_a_paused_person_is_told_where_spaces_is_enforced(app, enforced) -> None:
    member = _client(app, "203.0.113.50")
    person = _member(member, "yara-told")
    secret, _codes = _turn_on(member)
    for n in range(WRONG):
        _sign_in(_client(app, f"203.0.113.{150 + n}"), username=person["name"], password=PASSWORD,
                 code=_not_a_code(secret))
    assert "second_factor_paused" in [n["kind"] for n in people.notices_for(person["id"])]


def test_the_pauses_grow(monkeypatch) -> None:
    """Each pause twice the one before, up to the last."""
    store: dict[str, Any] = {}

    class _Store:
        def get(self, key: str) -> Any:
            return store.get(key)

        def atomic_update(self, key: str, update: Any, category: str = "") -> Any:
            store[key] = update(store.get(key))
            return store[key]

    monkeypatch.setattr(second_factor, "_settings", lambda: _Store())
    lengths = []
    for _pause in range(9):
        for _wrong in range(second_factor.WRONG_CODES):
            second_factor._count_wrong("acct")
        entry = store[second_factor.PAUSES_KEY]["acct"]
        lengths.append(round(entry["until"] - entry["last"]))
    assert lengths[:4] == [900, 1800, 3600, 7200]
    assert lengths[-1] == second_factor.PAUSE_MAX_S


# ── 4. Turning it on ends the other sessions ─────────────────────────────


def test_turning_it_on_ends_the_account_s_other_sessions(app, enforced) -> None:
    first = _client(app)
    person = _member(first, "zaid-on")
    second = _client(app, "203.0.113.71")
    assert _sign_in(second, username=person["name"], password=PASSWORD).status_code == 200
    assert second.get("/api/account/second-factor").status_code == 200
    _turn_on(first)
    assert second.get("/api/account/second-factor").status_code == 401, (
        "a session opened before the second factor stayed signed in")
    assert first.get("/api/account/second-factor").status_code == 200, "the current session stays"


def test_negative_control_none_ended(app, enforced, monkeypatch) -> None:
    from kazma_core.security import web_sessions

    monkeypatch.setattr(web_sessions, "revoke_user_sessions", lambda **kwargs: 0)
    with pytest.raises(AssertionError, match="stayed signed in"):
        test_turning_it_on_ends_the_account_s_other_sessions(app, None)


# ── 5. The owner's sessions of every kind ────────────────────────────────


def test_the_owner_s_sessions_of_every_kind_are_listed_and_ended(app, enforced) -> None:
    local = _local(app)
    assert "kazma-session" in local.get("/login", headers=NAVIGATION).cookies
    browser = _client(app, "203.0.113.80")
    assert "kazma-session" in browser.get("/chat", headers={**OWNER, **NAVIGATION},
                                          follow_redirects=False).cookies
    signed = _client(app, "203.0.113.81")
    assert _sign_in(signed, secret=SECRET).status_code == 200
    listed = _client(app).get(f"/api/people/{OWNER_ACCOUNT_ID}/sessions", headers=OWNER).json()["sessions"]
    kinds = sorted(s["via"] or s["platform"] for s in listed)
    assert kinds == ["login", "secret_header", "this_computer"], f"the owner's auto-login sessions are not listed: {kinds}"
    # My account, from the auto-login session: every kind, and one ended.
    mine = local.get("/api/settings/account/sessions").json()
    assert len(mine) == 3 and [s for s in mine if s["current"]][0]["via"] == "this_computer"
    header_one = next(s for s in mine if s["via"] == "secret_header")
    assert local.post(f"/api/account/sessions/{header_one['id']}/end").status_code == 200
    assert browser.get("/api/account/second-factor").status_code == 401
    # People ends another.
    login_one = next(s for s in listed if s["platform"] == "login")
    assert _client(app).post(f"/api/people/{OWNER_ACCOUNT_ID}/sessions/{login_one['id']}/end",
                             headers=OWNER).status_code == 200
    assert signed.get("/api/account/second-factor").status_code == 401
    # Turned on (by a program, so every session of the owner's ended), this
    # computer still signs the owner in with no code, and My account says so,
    # and that the install's secret is not covered.
    _turn_on(_client(app), OWNER)
    assert local.get("/api/account/second-factor").status_code == 401
    assert "kazma-session" in local.get("/login", headers=NAVIGATION).cookies
    said = local.get("/api/account/second-factor").json()
    assert said["auto_login_here"] is True and said["install_secret"] is True


def test_negative_control_the_list_of_before(app, enforced, monkeypatch) -> None:
    from kazma_core.security import web_sessions

    monkeypatch.setattr(web_sessions, "sessions_of_account", lambda account_id: {
        "user_id": web_sessions.OWNER_USER_ID, "username": "", "actors": ()})
    with pytest.raises(AssertionError, match="auto-login sessions are not listed"):
        test_the_owner_s_sessions_of_every_kind_are_listed_and_ended(app, None)


def test_a_member_s_page_says_nothing_of_the_owner_s_secret(app, enforced) -> None:
    member = _client(app, "203.0.113.82")
    _member(member, "salim-page")
    said = member.get("/api/account/second-factor").json()
    assert said["auto_login_here"] is False and said["install_secret"] is False


# ── 6. An invitation's token never reaches a log ─────────────────────────


def _access_line(path: str, *filters: logging.Filter) -> str:
    record = logging.LogRecord("uvicorn.access", logging.INFO, "h11_impl.py", 1,
                               '%s - "%s %s HTTP/%s" %d', ("192.0.2.9:4000", "GET", path, "1.1", 404), None)
    for one in filters:
        one.filter(record)
    return record.getMessage()


def test_an_invitation_token_never_reaches_a_log(app, enforced) -> None:
    from kazma_core.observability.log_noise import NoTokensInPaths

    made = _client(app).post("/api/people/invitations", json={"name": "Lama"}, headers=OWNER).json()
    token = _token(made["path"])
    # No route takes a token in its path.
    assert _client(app, "192.0.2.9").get(f"/invite/{token}").status_code == 404
    assert token not in _access_line(f"/invite/{token}?x=1", NoTokensInPaths()), (
        "the token is in the access line")
    assert any(isinstance(f, NoTokensInPaths) for f in logging.getLogger("uvicorn.access").filters)


def test_negative_control_the_access_line_without_the_filter(app, enforced) -> None:
    made = _client(app).post("/api/people/invitations", json={}, headers=OWNER).json()
    token = _token(made["path"])
    assert token in _access_line(f"/invite/{token}")


# ── 7. A vault that failed to open ───────────────────────────────────────


def test_a_failed_vault_never_stops_a_sign_in_with_no_second_factor(app, monkeypatch) -> None:
    from kazma_core.security import vault

    monkeypatch.setattr(vault, "get_vault", lambda: None)
    monkeypatch.setattr(vault, "vault_disabled", lambda: False)
    answer = _sign_in(_client(app), secret=SECRET)
    assert answer.status_code == 200, (
        f"a vault that failed to open stopped a sign-in with no second factor: {answer.status_code}")


def test_negative_control_the_vault_read_for_every_sign_in(app, monkeypatch) -> None:
    monkeypatch.setattr(second_factor, "_is_on",
                        lambda account: bool(second_factor._read(second_factor._name(account))))
    with pytest.raises(AssertionError, match="stopped a sign-in with no second factor"):
        test_a_failed_vault_never_stops_a_sign_in_with_no_second_factor(app, monkeypatch)


def test_the_marker_names_no_secret(app) -> None:
    secret, _codes = _turn_on(_client(app), OWNER)
    held = get_config_store().get(second_factor.ON_KEY)
    assert OWNER_ACCOUNT_ID in held and secret not in json.dumps(held)


# ── 8. The names told of a pause ─────────────────────────────────────────


def test_the_names_told_of_a_pause_stay_bounded() -> None:
    told: dict[str, float] = {}
    for n in range(5000):
        login_routes._tell_paused_once(told, f"name-{n}", 1000.0 + n * 0.001)
    assert len(told) <= login_routes._TOLD_MAX, f"{len(told)} names kept"
    assert not login_routes._tell_paused_once(told, "name-4999", 1006.0), "told twice in a window"


def test_negative_control_the_dict_of_before() -> None:
    told: dict[str, float] = {}
    for n in range(5000):
        key, now = f"name-{n}", 1000.0 + n * 0.001
        if now - told.get(key, 0.0) > login_routes.ACCOUNT_WINDOW_S:
            told[key] = now
    assert len(told) > login_routes._TOLD_MAX


def test_the_login_route_asks_the_bounded_rule(app, enforced, monkeypatch) -> None:
    asked: list[str] = []
    real = login_routes._tell_paused_once

    def recording(told: dict[str, float], key: str, now: float) -> bool:
        asked.append(key)
        return real(told, key, now)

    monkeypatch.setattr(login_routes, "_tell_paused_once", recording)
    monkeypatch.setattr(login_routes, "ACCOUNT_MAX_FAILS", 0)
    monkeypatch.setattr(login_routes, "_note_paused", lambda username: None)
    assert _sign_in(_client(app, "203.0.113.99"), username="whoever", password="nope-nope").status_code == 429
    assert asked == ["whoever"]
