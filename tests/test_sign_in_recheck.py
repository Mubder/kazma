"""The re-check of S4-02's review fixes (9acfc46e9, e04bc80d4), through the
real secured app, each with today's behaviour as its control.

* **Medium -- an auto-login session outlives its peer trust.** A session the
  peer address made for nothing (this computer, or the local network with
  ``KAZMA_TRUST_LAN``) ends when a second person gets an account, and when
  Spaces is switched on with one: it answered for its 14 days, from any
  address, after the trust that made it was gone.
* **The wrong-code pause, counted under the lock**: 40 wrong codes at once
  make one pause of its first length, not one that ran to a day.
* **No LAN auto-login once the owner has a second factor**: a device on the
  network held the owner's session with no code; this computer keeps its
  auto-login, as My account says.
* **A way back on the machine**: ``kazma account second-factor off`` takes
  an account's factor off with no code and no server, refused while a
  server holds the runtime lock, recorded.
* **The marker and the vault cannot drift a factor off**: a factor the vault
  holds is on whatever the marker says (a settings restore from before it),
  and a vault that cannot be read with the marker off stops no one.
* **``/ws/chat`` takes no legacy secret cookie once the owner has a factor.**
"""

from __future__ import annotations

import concurrent.futures
import subprocess
import sys
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from kazma_core.config_store import get_config_store
from kazma_core.security import second_factor, web_sessions
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._secured_app import SECRET
from tests.test_sign_in_hardening import (
    OWNER,
    PASSWORD,
    _client,
    _code,
    _not_a_code,
    _sign_in,
    _turn_on,
    app,  # noqa: F401 -- the fixture
    enforced,  # noqa: F401 -- the fixture
)

pytestmark = pytest.mark.real_sign_in

#: A peer on Docker's default bridge: private, not loopback.
BRIDGE = "172.17.0.1"
NAVIGATION = {"accept": "text/html", "sec-fetch-mode": "navigate"}


def _local(app: Any) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:9090", client=("127.0.0.1", 40000))


def _bridge(app: Any) -> TestClient:
    return TestClient(app, base_url="http://localhost:9090", client=(BRIDGE, 40000))


def _auto_cookie(client: TestClient) -> str:
    """The session a page load gets for nothing, or ''."""
    return client.get("/login", headers=NAVIGATION).cookies.get("kazma-session") or ""


def _with_cookie(app: Any, cookie: str, address: str) -> TestClient:
    client = TestClient(app, base_url="http://kazma.example", client=(address, 40100))
    client.cookies.set("kazma-session", cookie)
    return client


# ── Medium: the sessions the peer address made end with its trust ────────


def test_auto_login_sessions_end_when_a_second_person_gets_an_account(app, enforced, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_TRUST_LAN", "1")
    here = _auto_cookie(_local(app))
    lan = _auto_cookie(_bridge(app))
    assert here and lan, "with one account the auto-login is as before"
    for cookie in (here, lan):
        assert _with_cookie(app, cookie, "203.0.113.9").get("/api/people").status_code == 200
    people.create_person("noor-ends", PASSWORD, by=OWNER_ACCOUNT_ID)
    for cookie in (here, lan):
        refused = _with_cookie(app, cookie, "203.0.113.9").get("/api/people")
        assert refused.status_code == 401, "an auto-login session outlived the second account"
    monkeypatch.setenv("KAZMA_TRUST_LAN", "0")
    assert _with_cookie(app, lan, BRIDGE).get("/api/people").status_code == 401


def test_a_session_signed_in_with_the_secret_stays(app, enforced) -> None:
    signed = _client(app).post("/api/auth/login", json={"secret": SECRET})
    cookie = signed.cookies.get("kazma-session")
    assert cookie
    people.create_person("noor-stays", PASSWORD, by=OWNER_ACCOUNT_ID)
    assert _with_cookie(app, cookie, "203.0.113.9").get("/api/people").status_code == 200


def test_negative_control_today_s_sessions_outlive_the_trust(app, enforced, monkeypatch) -> None:
    """Planted: today's account creation, which ended no session."""
    monkeypatch.setattr(web_sessions, "end_peer_trust_sessions", lambda: 0)
    with pytest.raises(AssertionError, match="outlived the second account"):
        test_auto_login_sessions_end_when_a_second_person_gets_an_account(app, None, monkeypatch)


def test_switching_spaces_on_with_an_account_ends_them(app, monkeypatch) -> None:
    from kazma_core.spaces import preconditions

    here = _auto_cookie(_local(app))
    assert here
    monkeypatch.setattr(preconditions, "require", lambda **_kw: None)
    monkeypatch.setattr("kazma_core.spaces.chat_links.adopt_owner_ids", lambda **_kw: [])

    def _plant(reg: dict[str, Any]) -> None:
        reg["accounts"]["pplanted0001"] = {"id": "pplanted0001", "name": "Planted", "role": "member",
                                          "enabled": True}

    identity.update_registry(_plant)
    try:
        preconditions.switch_on(by="test")
        assert _with_cookie(app, here, "203.0.113.9").get("/api/people").status_code == 401, \
            "an auto-login session outlived the switch"
    finally:
        get_config_store().delete(identity.ENFORCED_KEY)
        monkeypatch.setattr(scope, "_enforced", False)


def test_negative_control_today_s_switch(app, monkeypatch) -> None:
    monkeypatch.setattr(web_sessions, "end_peer_trust_sessions", lambda: 0)
    with pytest.raises(AssertionError, match="outlived the switch"):
        test_switching_spaces_on_with_an_account_ends_them(app, monkeypatch)


# ── Low 1: wrong codes counted under the lock ────────────────────────────


def _forty_wrong_codes(monkeypatch, check: Any) -> dict[str, Any]:
    """Turn the owner's factor on, slow the store, send 40 wrong codes at
    once; the owner's pause record after."""
    secret = second_factor.begin(OWNER_ACCOUNT_ID, "owner")["secret"]
    second_factor.confirm(OWNER_ACCOUNT_ID, _code(secret))
    store = get_config_store()
    real = store.atomic_update

    def slow(key: str, *args: Any, **kwargs: Any) -> Any:
        if key == second_factor.PAUSES_KEY:
            time.sleep(0.02)
        return real(key, *args, **kwargs)

    monkeypatch.setattr(store, "atomic_update", slow)
    wrong = _not_a_code(secret)
    gate = threading.Barrier(40)

    def one(_n: int) -> None:
        gate.wait()
        try:
            check(OWNER_ACCOUNT_ID, wrong)
        except second_factor.SecondFactorPaused:
            pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as pool:
        list(pool.map(one, range(40)))
    return dict((store.get(second_factor.PAUSES_KEY) or {}).get(OWNER_ACCOUNT_ID) or {})


def test_forty_wrong_codes_at_once_make_one_pause(app, monkeypatch) -> None:
    pause = _forty_wrong_codes(monkeypatch, second_factor.check)
    assert int(pause.get("pauses") or 0) == 1, f"the pause grew: {pause}"
    assert float(pause["until"]) - float(pause["last"]) == pytest.approx(second_factor.PAUSE_S, abs=1)


def test_negative_control_counting_after_the_lock(app, monkeypatch) -> None:
    """Planted: today's order, the count after the lock."""
    sf = second_factor

    def counted_after(account_id: str, code: str) -> bool:
        typed = sf._normal(code)
        with sf._lock:
            held = sf._held(account_id)
            until = float(sf._pause_of(account_id).get("until") or 0)
            if until > time.time():
                raise sf.SecondFactorPaused(until)
            right = sf._matches(account_id, held, typed)
        if right:
            sf._clear_wrong(account_id)
            return True
        sf._count_wrong(account_id)
        return False

    pause = _forty_wrong_codes(monkeypatch, counted_after)
    assert int(pause.get("pauses") or 0) > 1, pause


# ── Low 2: no LAN auto-login once the owner has a second factor ──────────


def test_the_owner_s_factor_ends_the_lan_auto_login_not_this_computer_s(app, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_TRUST_LAN", "1")
    assert _auto_cookie(_bridge(app)), "TRUST_LAN gives a bridge peer the auto-login"
    _turn_on(_client(app), OWNER)
    assert not _auto_cookie(_bridge(app)), "a network device got the owner's session with no code"
    assert _auto_cookie(_local(app)), "this computer keeps its auto-login"


def test_negative_control_today_s_lan_auto_login(app, monkeypatch) -> None:
    from kazma_ui import auth

    monkeypatch.setattr(auth, "_lan_auto_login",
                        lambda peer: auth._trust_lan_enabled() and auth._is_private_lan_client(peer))
    with pytest.raises(AssertionError, match="network device got the owner's session"):
        test_the_owner_s_factor_ends_the_lan_auto_login_not_this_computer_s(app, monkeypatch)


# ── Low 3: a way back on the machine ─────────────────────────────────────


def _run_cli(*args: str) -> int:
    from kazma_cli.account import run

    return run(list(args))


def test_the_machine_takes_a_lost_factor_off(app, enforced) -> None:
    _turn_on(_client(app), OWNER)
    assert _sign_in(_client(app), secret=SECRET).json().get("error") == "second_factor_required"
    assert _run_cli("second-factor", "off", "--account", OWNER_ACCOUNT_ID, "--yes") == 0
    assert _sign_in(_client(app), secret=SECRET).status_code == 200
    kinds = [e["kind"] for e in identity.read_registry()["events"]]
    assert "second_factor_reset" in kinds


def test_the_machine_takes_off_a_factor_whose_vault_was_reset(app, enforced) -> None:
    _turn_on(_client(app), OWNER)
    second_factor._delete(second_factor._name(OWNER_ACCOUNT_ID))  # the vault reset
    assert _sign_in(_client(app), secret=SECRET).status_code == 503
    with pytest.raises(second_factor.SecondFactorUnavailable):
        second_factor.reset(OWNER_ACCOUNT_ID, by=OWNER_ACCOUNT_ID)  # today: no way back
    assert _run_cli("second-factor", "off", "--account", OWNER_ACCOUNT_ID, "--yes") == 0
    assert _sign_in(_client(app), secret=SECRET).status_code == 200


def test_the_machine_s_command_is_refused_while_a_server_runs(app) -> None:
    """The command in its own process, as it runs, while this process holds
    the runtime lock as a server does: refused, the factor still on."""
    from pathlib import Path

    from kazma_core.paths import data_dir
    from kazma_core.runtime_writer import RuntimeWriterLease

    lease = RuntimeWriterLease(Path(data_dir()))
    lease.acquire()
    try:
        _turn_on(_client(app), OWNER)
        ran = subprocess.run(
            [sys.executable, "-c", "import sys; from kazma_cli.account import run; sys.exit(run(sys.argv[1:]))",
             "second-factor", "off", "--account", OWNER_ACCOUNT_ID, "--yes"],
            capture_output=True, text=True, timeout=120)
        assert ran.returncode == 2, ran.stdout + ran.stderr
        assert "Stop it first" in ran.stdout
        assert second_factor.factor_on(OWNER_ACCOUNT_ID), "the factor went off under a running server"
    finally:
        lease.release()


# ── Low 4: the marker and the vault cannot drift a factor off ────────────


def test_a_factor_the_vault_holds_is_on_whatever_the_marker_says(app) -> None:
    _turn_on(_client(app), OWNER)
    get_config_store().delete(second_factor.ON_KEY)  # a restore from before the factor
    assert _sign_in(_client(app), secret=SECRET).json().get("error") == "second_factor_required", \
        "the secret signed in alone after the marker was lost"
    assert second_factor._is_on(OWNER_ACCOUNT_ID), "the marker was put back"


def test_negative_control_the_marker_alone(app, monkeypatch) -> None:
    """Planted: today's rule, the marker alone."""
    sf = second_factor

    def marker_alone(account_id: str) -> dict[str, Any] | None:
        if not sf._is_on(account_id):
            return None
        return sf._read(sf._name(account_id))

    monkeypatch.setattr(sf, "_held", marker_alone)
    with pytest.raises(AssertionError, match="signed in alone after the marker was lost"):
        test_a_factor_the_vault_holds_is_on_whatever_the_marker_says(app)


def test_a_vault_that_cannot_be_read_stops_no_one_without_a_marker(app, monkeypatch) -> None:
    def broken() -> Any:
        raise second_factor.SecondFactorUnavailable("the vault is not ready")

    monkeypatch.setattr(second_factor, "_vault", broken)
    assert _sign_in(_client(app), secret=SECRET).status_code == 200


# ── Low 6: /ws/chat's principal takes no legacy secret cookie with a factor ──


class _Socket:
    """What get_websocket_principal reads of a handshake: a browser on this
    machine sending only the legacy secret cookie."""

    def __init__(self) -> None:
        from starlette.datastructures import Headers, QueryParams

        self.headers = Headers({"host": "127.0.0.1:9090", "origin": "http://127.0.0.1:9090"})
        self.cookies = {"kazma-secret": SECRET}
        self.query_params = QueryParams("")
        self.client = type("C", (), {"host": "127.0.0.1", "port": 40000})()
        self.url = type("U", (), {"path": "/ws/chat/x", "hostname": "127.0.0.1", "port": 9090})()
        self.scope = {"type": "websocket", "path": "/ws/chat/x", "headers": [], "client": ("127.0.0.1", 40000)}


def test_ws_chat_takes_no_legacy_secret_cookie_with_the_owner_s_factor(app, monkeypatch) -> None:
    from kazma_ui import auth

    monkeypatch.setattr(auth, "_accept_legacy_secret_cookie", lambda: True)
    assert auth.get_websocket_principal(_Socket()) is not None, "before a factor the cookie is the owner"
    _turn_on(_client(app), OWNER)
    assert auth.get_websocket_principal(_Socket()) is None, "the legacy cookie was the owner with a factor on"


def test_negative_control_today_s_socket_principal(app, monkeypatch) -> None:
    from kazma_ui import auth

    def today(websocket: Any) -> dict[str, Any] | None:
        provided = auth.extract_provided_credential(websocket)
        if provided:
            return auth.get_request_principal(websocket)
        return None

    monkeypatch.setattr(auth, "get_websocket_principal", today)
    with pytest.raises(AssertionError, match="legacy cookie was the owner with a factor on"):
        test_ws_chat_takes_no_legacy_secret_cookie_with_the_owner_s_factor(app, monkeypatch)
