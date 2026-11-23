"""Stronger sign-in (Spaces S4-02), through the real app with sign-in on.

* **A second factor**: turned on in My account (a code from an
  authenticator app, RFC 6238), a right password -- or, for the owner, the
  install's secret -- does not sign in alone; a code does, once; a recovery
  code does, once. Its secret is kept in the vault, never in settings. No
  API token, whatever its role, turns it on or off. The admin resets a
  person's, who is told. An install with no vault has none and signs in as
  before; a vault that is not ready refuses (503), never "none".
* **Sign-in limits per account**: wrong passwords for one account from many
  addresses pause sign-ins to it, but from an address it signed in from
  before; its person is told.
* **Invitation links that expire**: one use, a few hours or days, revoked
  at will; only the token's digest is kept.
* **Each person's sessions**: the admin lists a person's sessions and ends
  one (they are told); a person ends one of their own.
* **No auto-login once a second person has an account** (Spaces enforced):
  a browser on this machine gets no session for nothing, a socket from it
  is refused, and the sign-in page says why; nor does a peer on a private
  network with ``KAZMA_TRUST_LAN`` (a Docker bridge address here). Before
  Spaces, or with one account, the auto-login is as before.

Negative controls: a password alone signs in when a second factor is
required -- the sign-in route without its check (the plan's); today's
auto-login, which hands this machine -- and with ``KAZMA_TRUST_LAN`` a
bridge peer -- the owner's session whoever has an account.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from kazma_core.config_store import get_config_store
from kazma_core.security import second_factor, totp
from kazma_core.spaces import identity, invitations, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
from kazma_ui.routes_direct import auth as login_routes

from tests._secured_app import SECRET, secured_app

pytestmark = pytest.mark.real_sign_in

OWNER = {"X-Kazma-Secret": SECRET}
PASSWORD = "a-long-enough-password"


def _code(secret: str, *, step_offset: int = 0) -> str:
    return totp._code_at(secret, int(time.time() // totp.PERIOD) + step_offset)


def _not_a_code(secret: str) -> str:
    """Six digits no step near now has (certain, not likely)."""
    near = {_code(secret, step_offset=n) for n in range(-3, 4)}
    return next(f"{n:06d}" for n in range(10) if f"{n:06d}" not in near)


@pytest.fixture
def app(monkeypatch):
    built = secured_app(monkeypatch)
    try:
        yield built
    finally:
        store = get_config_store()
        for key in (identity.REGISTRY_KEY, second_factor.STEPS_KEY, invitations.INVITATIONS_KEY,
                    login_routes.KNOWN_ADDRESSES_KEY, people.LAST_SIGN_IN_KEY):
            store.delete(key)
        try:
            second_factor.forget(OWNER_ACCOUNT_ID)
        except second_factor.SecondFactorUnavailable:
            pass  # a test that took the vault away: nothing was kept
        from kazma_core.security.vault import reset_posture_cache

        reset_posture_cache()


def _client(app: Any, address: str = "203.0.113.5") -> TestClient:
    """A browser away from this machine (no loopback auto-login)."""
    return TestClient(app, base_url="http://kazma.example", client=(address, 40000))


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)
    yield


def _turn_on(client: TestClient, headers: dict[str, str] | None = None) -> tuple[str, list[str]]:
    begun = client.post("/api/account/second-factor/begin", headers=headers or {})
    assert begun.status_code == 200, begun.text
    secret = begun.json()["secret"]
    assert begun.json()["uri"].startswith("otpauth://totp/")
    confirmed = client.post("/api/account/second-factor/confirm", json={"code": _code(secret)},
                            headers=headers or {})
    assert confirmed.status_code == 200, confirmed.text
    codes = confirmed.json()["recovery_codes"]
    assert len(codes) == second_factor.RECOVERY_CODES
    return secret, codes


def _sign_in(client: TestClient, **body: Any) -> Any:
    return client.post("/api/auth/login", json=body)


# ── RFC 6238 ──────────────────────────────────────────────────────────────


def test_codes_are_rfc_6238_s() -> None:
    key = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
    assert [totp._code_at(key, t // 30) for t in (59, 1111111109, 1234567890, 2000000000)] == [
        "287082", "081804", "005924", "279037"]
    assert totp.verify(key, "287082", now=59) == 1
    assert totp.verify(key, "287082", now=59 + 30) == 1, "a step before now is taken"
    assert totp.verify(key, "287082", now=59 + 90) is None, "two steps before is not"
    assert totp.verify(key, "287082", now=59, after_step=1) is None, "a code is used once"


# ── The second factor, the owner's (no Spaces needed) ─────────────────────


def test_a_second_factor_is_asked_after_the_secret(app) -> None:
    client = _client(app)
    secret, codes = _turn_on(client, OWNER)
    alone = _sign_in(client, secret=SECRET)
    assert alone.status_code == 401 and alone.json().get("second_factor_required"), (
        f"a password alone signed in: {alone.status_code} {alone.text}")
    assert "kazma-session" not in alone.cookies
    wrong = _sign_in(client, secret=SECRET, code=_not_a_code(secret))
    assert wrong.status_code == 401 and wrong.json()["error"] == "wrong_second_factor"
    # The step the first code turned it on with is spent: the next one.
    right = _sign_in(client, secret=SECRET, code=_code(secret, step_offset=1))
    assert right.status_code == 200 and "kazma-session" in right.cookies, right.text
    again = _sign_in(client, secret=SECRET, code=_code(secret, step_offset=1))
    assert again.status_code == 401, "a code is taken once"
    recovered = _sign_in(client, secret=SECRET, code=codes[0])
    assert recovered.status_code == 200, recovered.text
    assert _sign_in(client, secret=SECRET, code=codes[0]).status_code == 401, "a recovery code is used once"
    assert second_factor.state(OWNER_ACCOUNT_ID)["recovery_left"] == second_factor.RECOVERY_CODES - 1


def test_negative_control_a_password_alone_signs_in_without_the_check(app, monkeypatch) -> None:
    """Planted: the sign-in route without its second-factor check. The
    secret alone signs in to an account that has one: the check above fails
    on its own assertion."""
    monkeypatch.setattr(login_routes, "_second_factor_verdict", lambda user_id, code: None)
    with pytest.raises(AssertionError, match="a password alone signed in"):
        test_a_second_factor_is_asked_after_the_secret(app)


def test_the_secret_is_kept_in_the_vault_never_in_settings(app) -> None:
    client = _client(app)
    secret, _codes = _turn_on(client, OWNER)
    rows = repr(get_config_store().get_all())
    assert secret not in rows
    from kazma_core.security.vault import get_vault, secret_scope

    assert secret in (get_vault().retrieve(f"account.2fa.{OWNER_ACCOUNT_ID}") or "")
    assert secret_scope(f"account.2fa.{OWNER_ACCOUNT_ID}") == ("install", None)


def test_turning_it_off_needs_a_code(app) -> None:
    client = _client(app)
    secret, _codes = _turn_on(client, OWNER)
    refused = client.post("/api/account/second-factor/off", json={"code": "12"}, headers=OWNER)
    assert refused.status_code == 400 and refused.json()["error"] == "wrong_code"
    done = client.post("/api/account/second-factor/off", json={"code": _code(secret, step_offset=1)},
                       headers=OWNER)
    assert done.status_code == 200, done.text
    assert _sign_in(client, secret=SECRET).status_code == 200


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_no_token_turns_it_on_or_off(app, monkeypatch, role) -> None:
    from kazma_ui import auth

    token = {"source": "api_token", "role": role, "user_id": "", "actor": "token", "username": "agent"}
    monkeypatch.setattr(auth, "get_request_principal", lambda request: token)
    client = _client(app)
    for path in ("/api/account/second-factor/begin", "/api/account/second-factor/off"):
        answer = client.post(path, json={"code": "123456"}, headers=OWNER)
        assert answer.status_code == 403 and answer.json()["error"] == "own_sign_in_only", answer.text


def test_an_install_with_no_vault_signs_in_as_before(app, monkeypatch) -> None:
    from kazma_core.security import vault

    monkeypatch.setattr(vault, "get_vault", lambda: None)
    monkeypatch.setattr(vault, "vault_disabled", lambda: True)
    assert _sign_in(_client(app), secret=SECRET).status_code == 200


def test_a_vault_not_ready_refuses_never_guesses_none(app, monkeypatch) -> None:
    from kazma_core.security import vault

    monkeypatch.setattr(vault, "get_vault", lambda: None)
    monkeypatch.setattr(vault, "vault_disabled", lambda: False)
    answer = _sign_in(_client(app), secret=SECRET)
    assert answer.status_code == 503 and answer.headers.get("retry-after"), answer.text


# ── A member's second factor, sessions, the admin's reset ────────────────


def _member(client: TestClient, name: str = "dana-signin") -> dict[str, Any]:
    person = people.create_person(name, PASSWORD, by=OWNER_ACCOUNT_ID)
    signed = _sign_in(client, username=name, password=PASSWORD)
    assert signed.status_code == 200, signed.text
    return person


def test_a_member_s_second_factor_and_the_admin_s_reset(app, enforced) -> None:
    member = _client(app)
    person = _member(member)
    secret, _codes = _turn_on(member)
    other = _client(app, "203.0.113.9")
    alone = _sign_in(other, username=person["name"], password=PASSWORD)
    assert alone.status_code == 401 and alone.json()["second_factor_required"]
    assert _sign_in(other, username=person["name"], password=PASSWORD,
                    code=_code(secret, step_offset=1)).status_code == 200
    # The admin resets it: the password alone signs in again, and they are told.
    reset = _client(app).post(f"/api/people/{person['id']}/second-factor/reset", headers=OWNER)
    assert reset.status_code == 200, reset.text
    assert _sign_in(_client(app, "203.0.113.10"), username=person["name"], password=PASSWORD).status_code == 200
    told = [n["kind"] for n in people.notices_for(person["id"])]
    assert "second_factor_reset" in told
    listed = next(p for p in people.list_people() if p["id"] == person["id"])
    assert listed["second_factor"] is False


def test_the_admin_lists_and_ends_one_session_of_a_person(app, enforced) -> None:
    first = _client(app)
    person = _member(first, "omar-sessions")
    second = _client(app, "203.0.113.11")
    assert _sign_in(second, username=person["name"], password=PASSWORD).status_code == 200
    admin = _client(app)
    listed = admin.get(f"/api/people/{person['id']}/sessions", headers=OWNER).json()["sessions"]
    assert len(listed) == 2 and all("id" in s for s in listed)
    ended = admin.post(f"/api/people/{person['id']}/sessions/{listed[0]['id']}/end", headers=OWNER)
    assert ended.status_code == 200, ended.text
    assert len(admin.get(f"/api/people/{person['id']}/sessions", headers=OWNER).json()["sessions"]) == 1
    assert "session_ended" in [n["kind"] for n in people.notices_for(person["id"])]
    # Never another person's: an id of theirs is no session of the owner's.
    left = admin.get(f"/api/people/{person['id']}/sessions", headers=OWNER).json()["sessions"][0]
    assert admin.post(f"/api/people/{OWNER_ACCOUNT_ID}/sessions/{left['id']}/end",
                      headers=OWNER).status_code == 404


def test_a_person_ends_one_of_their_own_sessions(app, enforced) -> None:
    first = _client(app)
    person = _member(first, "lina-own")
    second = _client(app, "203.0.113.12")
    assert _sign_in(second, username=person["name"], password=PASSWORD).status_code == 200
    mine = first.get("/api/settings/account/sessions").json()
    other = next(s for s in mine if not s["current"])
    assert first.post(f"/api/account/sessions/{other['id']}/end").status_code == 200
    assert second.get("/api/account/second-factor").status_code == 401, "the ended session signs in no more"
    assert first.get("/api/account/second-factor").status_code == 200


# ── Sign-in limits per account ────────────────────────────────────────────


def test_wrong_passwords_from_many_addresses_pause_the_account(app, enforced, monkeypatch) -> None:
    monkeypatch.setattr(login_routes, "ACCOUNT_MAX_FAILS", 3)
    home = _client(app, "198.51.100.1")
    person = _member(home, "sara-limit")
    for n in range(3):
        wrong = _sign_in(_client(app, f"203.0.113.{100 + n}"), username=person["name"], password="nope-nope-nope")
        assert wrong.status_code == 401
    paused = _sign_in(_client(app, "203.0.113.200"), username=person["name"], password=PASSWORD)
    assert paused.status_code == 429, f"a spread guess was not paused: {paused.status_code}"
    # From where they signed in before, they still can.
    assert _sign_in(home, username=person["name"], password=PASSWORD).status_code == 200
    assert "sign_in_paused" in [n["kind"] for n in people.notices_for(person["id"])]


def test_negative_control_without_the_account_limit_a_spread_guess_goes_on(app, enforced, monkeypatch) -> None:
    monkeypatch.setattr(login_routes, "ACCOUNT_MAX_FAILS", 10**6)
    monkeypatch.setattr(login_routes, "_known_address", lambda username, address: False)
    with pytest.raises(AssertionError, match="a spread guess was not paused"):
        test_wrong_passwords_from_many_addresses_pause_the_account(app, None, _Keep(monkeypatch))


class _Keep:
    """The control's monkeypatch: keeps the limit it was given."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def setattr(self, target: Any, name: str, value: Any) -> None:
        if name == "ACCOUNT_MAX_FAILS":
            return
        self._real.setattr(target, name, value)


def test_without_spaces_there_is_no_account_limit(app, monkeypatch) -> None:
    monkeypatch.setattr(login_routes, "ACCOUNT_MAX_FAILS", 0)
    assert _sign_in(_client(app), secret=SECRET).status_code == 200


# ── Invitation links ──────────────────────────────────────────────────────


def test_an_invitation_makes_one_account_then_stops(app, enforced) -> None:
    admin = _client(app)
    made = admin.post("/api/people/invitations", json={"name": "Huda", "hours": 2}, headers=OWNER)
    assert made.status_code == 200, made.text
    path = made.json()["path"]
    token = path.rsplit("/", 1)[1]
    assert token not in repr(get_config_store().get(invitations.INVITATIONS_KEY)), "only its digest is kept"
    guest = _client(app, "192.0.2.50")
    page = guest.get(path)
    assert page.status_code == 200 and 'id="inviteForm"' in page.text and "Huda" in page.text
    accepted = guest.post("/api/invitations/accept", json={"token": token, "name": "huda-invited",
                                                          "password": PASSWORD})
    assert accepted.status_code == 200, accepted.text
    assert _sign_in(guest, username="huda-invited", password=PASSWORD).status_code == 200
    second = guest.post("/api/invitations/accept", json={"token": token, "name": "huda-again",
                                                        "password": PASSWORD})
    assert second.status_code == 410, f"a link made a second account: {second.text}"
    assert 'id="invite-closed"' in guest.get(path).text
    statuses = [i["status"] for i in admin.get("/api/people/invitations", headers=OWNER).json()["invitations"]]
    assert statuses == ["used"]


def test_negative_control_a_link_that_is_never_spent_works_twice(app, enforced, monkeypatch) -> None:
    real = invitations._public

    def never_spent(digest: str, entry: dict[str, Any], now: float) -> dict[str, Any]:
        seen = real(digest, entry, now)
        if seen["status"] in ("used", "claimed"):
            seen["status"] = "open"
        return seen

    monkeypatch.setattr(invitations, "_public", never_spent)
    with pytest.raises(AssertionError, match="a link made a second account"):
        test_an_invitation_makes_one_account_then_stops(app, None)


def test_a_link_runs_out_and_is_revoked(app, enforced, monkeypatch) -> None:
    admin = _client(app)
    soon = admin.post("/api/people/invitations", json={"hours": 1}, headers=OWNER).json()
    later = admin.post("/api/people/invitations", json={}, headers=OWNER).json()
    real_time = time.time
    monkeypatch.setattr(invitations.time, "time", lambda: real_time() + 2 * 3600)
    guest = _client(app, "192.0.2.51")
    ran_out = guest.post("/api/invitations/accept", json={"token": soon["path"].rsplit("/", 1)[1],
                                                         "name": "late-one", "password": PASSWORD})
    assert ran_out.status_code == 410
    monkeypatch.setattr(invitations.time, "time", real_time)
    revoked = admin.post(f"/api/people/invitations/{later['invitation']['id']}/revoke", headers=OWNER)
    assert revoked.status_code == 200
    closed = guest.post("/api/invitations/accept", json={"token": later["path"].rsplit("/", 1)[1],
                                                        "name": "revoked-one", "password": PASSWORD})
    assert closed.status_code == 410
    assert admin.post("/api/people/invitations", json={"hours": 73}, headers=OWNER).status_code == 400


def test_invitations_are_the_admin_s_and_need_spaces(app, monkeypatch) -> None:
    assert _client(app).post("/api/people/invitations", json={}, headers=OWNER).status_code == 409
    accepted = _client(app, "192.0.2.52").post("/api/invitations/accept",
                                               json={"token": "x", "name": "n", "password": PASSWORD})
    assert accepted.status_code == 410


# ── No auto-login once a second person has an account ────────────────────


def _local(app: Any) -> TestClient:
    """A browser on this machine: loopback, a local host name."""
    return TestClient(app, base_url="http://127.0.0.1:9090", client=("127.0.0.1", 40000))


def _auto_session(app: Any) -> bool:
    """Whether a page load on this machine gets a session for nothing."""
    answer = _local(app).get("/login", headers={"accept": "text/html", "sec-fetch-mode": "navigate"})
    return "kazma-session" in answer.cookies


def test_one_account_keeps_the_auto_login(app, enforced) -> None:
    assert _auto_session(app), "with one account the auto-login is as before"


def test_before_spaces_a_second_account_keeps_the_auto_login(app) -> None:
    from kazma_core.security import platform_rbac

    platform_rbac.create_local_user("older-mode-user", PASSWORD)
    assert _auto_session(app), "before Spaces nothing changes"


def test_a_second_account_ends_the_auto_login(app, enforced) -> None:
    people.create_person("noor-local", PASSWORD, by=OWNER_ACCOUNT_ID)
    assert not _auto_session(app), "this machine got the owner's session with a second account"
    local = _local(app)
    assert local.get("/api/account/second-factor").status_code == 401
    page = local.get("/login")
    assert 'id="everyone-signs-in"' in page.text
    # The owner signs in like anyone, here too.
    signed = local.post("/api/auth/login", json={"secret": SECRET})
    assert signed.status_code == 200 and "kazma-session" in signed.cookies
    assert local.get("/api/account/second-factor").status_code == 200
    # A socket from this machine with no credential is refused.
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as closed,             TestClient(app, base_url="http://127.0.0.1:9090", client=("127.0.0.1", 40001)
                       ).websocket_connect("/ws/dashboard") as socket:
        socket.receive_text()
    assert closed.value.code == 4003


def test_negative_control_today_s_auto_login(app, enforced, monkeypatch) -> None:
    """Planted: today's peer trust, whoever has an account. This machine gets
    the owner's session with a second account: the check above fails on its
    own assertion."""
    from kazma_ui import auth

    monkeypatch.setattr(auth, "everyone_signs_in", lambda: False)
    with pytest.raises(AssertionError, match="got the owner's session with a second account"):
        test_a_second_account_ends_the_auto_login(app, None)


# ── KAZMA_TRUST_LAN: a private-network peer's auto-login ends too ────────

#: A peer on Docker's default bridge: private, not loopback.
BRIDGE = "172.17.0.1"


def _bridge(app: Any, port: int = 40000) -> TestClient:
    """A browser reaching this machine over a Docker bridge, by a local name."""
    return TestClient(app, base_url="http://localhost:9090", client=(BRIDGE, port))


def _bridge_session(app: Any) -> bool:
    answer = _bridge(app).get("/login", headers={"accept": "text/html", "sec-fetch-mode": "navigate"})
    return "kazma-session" in answer.cookies


def test_trust_lan_gives_a_bridge_peer_the_auto_login_with_one_account(app, enforced, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_TRUST_LAN", "1")
    assert _bridge_session(app), "with one account KAZMA_TRUST_LAN is as before"
    monkeypatch.setenv("KAZMA_TRUST_LAN", "0")
    assert not _bridge_session(app), "a bridge peer got a session with KAZMA_TRUST_LAN off"


def test_a_second_account_ends_the_lan_auto_login(app, enforced, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_TRUST_LAN", "1")
    people.create_person("noor-lan", PASSWORD, by=OWNER_ACCOUNT_ID)
    assert not _bridge_session(app), "a bridge peer got the owner's session with a second account"
    assert _bridge(app).get("/api/account/second-factor").status_code == 401
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as closed, _bridge(app, 40001).websocket_connect(
            "/ws/dashboard") as socket:
        socket.receive_text()
    assert closed.value.code == 4003


def test_negative_control_today_s_lan_auto_login(app, enforced, monkeypatch) -> None:
    """Planted: today's peer trust, whoever has an account. A bridge peer gets
    the owner's session with a second account: the check above fails on its
    own assertion."""
    from kazma_ui import auth

    monkeypatch.setattr(auth, "everyone_signs_in", lambda: False)
    with pytest.raises(AssertionError, match="bridge peer got the owner's session"):
        test_a_second_account_ends_the_lan_auto_login(app, None, monkeypatch)
