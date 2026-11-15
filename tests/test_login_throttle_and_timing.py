"""A sign-in's throttle counts an IPv6 network, stays bounded, and every
refusal costs the same work (2026-11-15).

The address bucket and the username@address bucket of ``POST
/api/auth/login`` were keyed by the full client address: one IPv6 subscriber
holds a /64 (2**64 addresses), so every guess could come from an address of
its own and none reached the limit; and the tables were pruned of stale keys
only, so fresh keys grew them without bound inside the five-minute window.
``authenticate_local_user`` refused a legacy three-field hash after 200,000
PBKDF2 iterations and an empty one after none, against 600,000 for an unknown
name: the time of a refusal said which names exist.

Through ``create_app()``'s real sign-in route; the old keying, no cap and the
refusal without its top-up are the negative controls.
"""

from __future__ import annotations

import json

import pytest
from kazma_core.security import platform_rbac
from kazma_ui.routes_direct import auth as login_routes

from tests._secured_app import call, secured_app

JSON = {"content-type": "application/json"}


@pytest.fixture
def app(monkeypatch):
    return secured_app(monkeypatch)


def _wrong(app, address: str):
    return call(app, "POST", "/api/auth/login", body=b'{"secret": "wrong"}', headers=JSON,
                client=(address, 40000))


def test_an_ipv6_address_counts_as_its_64():
    key = login_routes._login_throttle_key
    assert key("2001:db8:1:2::1") == key("2001:db8:1:2:ffff:ffff:ffff:fffe") == "2001:db8:1:2::/64"
    assert key("2001:db8:1:3::1") != key("2001:db8:1:2::1")
    assert key("::ffff:203.0.113.7") == key("203.0.113.7") == "203.0.113.7"
    assert key("fe80::1%eth0") == "fe80::/64"
    assert key("testclient") == "testclient"


def test_one_64_reaches_the_limit_from_many_addresses(app):
    statuses = [_wrong(app, f"2001:db8:1:2::{i:x}").status for i in range(1, 12)]
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429
    # Another network is counted on its own.
    assert _wrong(app, "2001:db8:9:9::1").status == 401


def test_negative_control_keyed_by_the_full_address_none_reached_the_limit(app, monkeypatch):
    monkeypatch.setattr(login_routes, "_login_throttle_key", lambda address: address)
    statuses = [_wrong(app, f"2001:db8:1:2::{i:x}").status for i in range(1, 15)]
    assert 429 not in statuses


def _failure_tables(app):
    for route in app.routes:
        if getattr(route, "path", "") == "/api/auth/login":
            cells = dict(zip(route.endpoint.__code__.co_freevars, route.endpoint.__closure__ or ()))
            return cells["_login_failures"].cell_contents, cells["_login_failures_user"].cell_contents
    raise AssertionError("no sign-in route")


def test_the_tables_stay_bounded(app, monkeypatch):
    monkeypatch.setattr(login_routes, "LOGIN_TABLE_MAX", 20)
    by_address, by_user = _failure_tables(app)
    for i in range(60):
        # A username with the secret field: counted per username@address,
        # with no password hashed.
        body = json.dumps({"username": f"user{i}", "secret": "wrong"}).encode()
        call(app, "POST", "/api/auth/login", body=body, headers=JSON, client=(f"2001:db8:{i:x}::1", 1))
    assert len(by_address) <= 20 and len(by_user) <= 20
    # The newest are the ones kept.
    assert "2001:db8:3b::/64" in by_address


def test_negative_control_without_the_cap_they_grew(app, monkeypatch):
    monkeypatch.setattr(login_routes, "LOGIN_TABLE_MAX", 10**9)
    by_address, _by_user = _failure_tables(app)
    for i in range(60):
        _wrong(app, f"2001:db8:{i:x}::1")
    assert len(by_address) == 60


# -- every refusal costs one full verification -----------------------------------


@pytest.fixture
def counted(monkeypatch):
    spent: list[int] = []

    def _count(password: str, salt: str, iterations: int) -> str:
        spent.append(iterations)
        return "0" * 64

    monkeypatch.setattr(platform_rbac, "_pbkdf2_hex", _count)
    return spent


def _users(monkeypatch, *rows):
    monkeypatch.setattr(platform_rbac, "_users_for_sign_in", lambda: [dict(r) for r in rows])


LEGACY = "pbkdf2_sha256$saltsalt$" + "1" * 64
CURRENT = f"pbkdf2_sha256${platform_rbac._PBKDF2_ITERATIONS}$saltsalt$" + "1" * 64


@pytest.mark.parametrize("stored", [CURRENT, LEGACY, "", "not-a-hash", "pbkdf2_sha256$0$s$00"])
def test_every_refusal_costs_one_full_verification(monkeypatch, counted, stored):
    _users(monkeypatch, {"username": "layla", "password_hash": stored})
    assert platform_rbac.authenticate_local_user("layla", "wrong") is None
    assert sum(counted) == platform_rbac._PBKDF2_ITERATIONS, (stored, counted)


def test_an_unknown_and_a_disabled_name_cost_the_same(monkeypatch, counted):
    _users(monkeypatch, {"username": "layla", "password_hash": CURRENT, "enabled": False})
    assert platform_rbac.authenticate_local_user("layla", "wrong") is None
    assert sum(counted) == platform_rbac._PBKDF2_ITERATIONS
    counted.clear()
    assert platform_rbac.authenticate_local_user("nobody", "wrong") is None
    assert sum(counted) == platform_rbac._PBKDF2_ITERATIONS


def test_a_right_password_still_signs_in(monkeypatch):
    stored = platform_rbac._hash_password("correct horse")
    _users(monkeypatch, {"username": "layla", "password_hash": stored, "role": "operator"})
    assert platform_rbac.authenticate_local_user("layla", "correct horse").username == "layla"


@pytest.mark.parametrize("stored,spent", [(LEGACY, 200_000), ("", 0)])
def test_negative_control_without_the_top_up_a_refusal_said_what_was_stored(
    monkeypatch, counted, stored, spent,
):
    monkeypatch.setattr(platform_rbac, "_spend_the_rest", lambda password, spent: None)
    _users(monkeypatch, {"username": "layla", "password_hash": stored})
    assert platform_rbac.authenticate_local_user("layla", "wrong") is None
    assert sum(counted) == spent
