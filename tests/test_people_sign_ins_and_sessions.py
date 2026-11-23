"""Spaces S2-10: the People page shows each person's spaces, last sign-in
and open sessions, and ends their sessions.

* ``people.list_people`` gives each person the spaces they are a member of
  (their personal one, a shared one with their role), how many sessions they
  have open and when they last signed in: the newer of the sign-in Kazma
  recorded and their newest session's start. Never a session id.
* A sign-in with a name and password through the real login route records
  it (``people.note_sign_in``), and it stays once the session has ended.
* The admin ends a person's sessions (``POST /api/people/{id}/sessions/end``):
  every session ends, the change is recorded, and the person is told at
  their next sign-in. The owner's own sessions are ended in My account; a
  member cannot end anyone's; nothing without Spaces.

The page itself, with features, tools, limit and models beside these, is
``tests/e2e/test_people_page_browser_s210.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._space_matrix import Matrix

PASSWORD = "a-long-enough-password"


@pytest.fixture
def matrix(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture(autouse=True)
def _no_sign_ins_left():
    yield
    from kazma_core.config_store import get_config_store

    get_config_store().delete(people.LAST_SIGN_IN_KEY)


def _listed(account_id: str) -> dict[str, Any]:
    return next(p for p in people.list_people() if p["id"] == account_id)


def _session_of(person: dict[str, Any]) -> str:
    """A session as the login route mints one for *person*."""
    from kazma_core.security.web_sessions import create_session

    account = identity.read_registry()["accounts"][person["id"]]
    return create_session(actor="login", username=person["name"], role="operator",
                          user_id=account["legacy_user_id"])


def test_each_person_s_spaces_sessions_and_last_sign_in(matrix, enforced) -> None:
    person = people.create_person("dana-s210", PASSWORD, by=OWNER_ACCOUNT_ID)
    shared = people.create_shared_space("Garden", by=OWNER_ACCOUNT_ID)
    people.add_member(shared["id"], person["id"], by=OWNER_ACCOUNT_ID)

    fresh = _listed(person["id"])
    assert fresh["sessions"] == 0 and fresh["last_sign_in"] == ""
    assert [(s["kind"], s["name"], s["role"]) for s in fresh["spaces"]] == [
        ("personal", "", "owner"), ("shared", "Garden", "member")]

    _session_of(person)
    _session_of(person)
    listed = _listed(person["id"])
    assert listed["sessions"] == 2 and listed["last_sign_in"]
    assert "session" not in str(listed["spaces"])  # no session id anywhere
    owner = _listed(OWNER_ACCOUNT_ID)
    assert any(s["id"] == OWNER_SPACE_ID for s in owner["spaces"])


def test_a_sign_in_through_the_login_route_is_recorded_and_outlives_its_session(matrix, enforced,
                                                                                monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from kazma_core.security.web_sessions import revoke_user_sessions
    from kazma_ui.app import create_app

    monkeypatch.setenv("KAZMA_SECRET", "people-sign-in-test-secret-" + "x" * 24)  # sign-in on
    person = people.create_person("erin-s210", PASSWORD, by=OWNER_ACCOUNT_ID)
    with TestClient(create_app(), base_url="http://127.0.0.1:9090") as client:
        # The app read the stored switch (off) when it was built and started.
        monkeypatch.setattr(scope, "_enforced", True)
        answer = client.post("/api/auth/login", json={"username": "erin-s210", "password": PASSWORD})
    assert answer.status_code == 200, answer.text
    listed = _listed(person["id"])
    assert listed["sessions"] == 1 and listed["last_sign_in"]
    signed_in = listed["last_sign_in"]

    revoke_user_sessions(user_id=identity.read_registry()["accounts"][person["id"]]["legacy_user_id"])
    after = _listed(person["id"])
    assert after["sessions"] == 0 and after["last_sign_in"] == signed_in


def test_ending_a_person_s_sessions_is_recorded_and_told(matrix, enforced) -> None:
    person = people.create_person("finn-s210", PASSWORD, by=OWNER_ACCOUNT_ID)
    _session_of(person)
    assert people.end_sessions(person["id"], by=OWNER_ACCOUNT_ID) == 1
    assert _listed(person["id"])["sessions"] == 0
    assert identity.read_registry()["events"][-1]["kind"] == "sessions_ended"
    assert [n["kind"] for n in people.notices_for(person["id"])][-1] == "sessions_ended"
    assert people.broken_at() is None


def test_the_owner_s_own_sessions_are_ended_in_my_account(matrix, enforced) -> None:
    with pytest.raises(people.PeopleRefused) as refused:
        people.end_sessions(OWNER_ACCOUNT_ID, by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "owner_in_my_account" and refused.value.status == 409


def test_nothing_is_recorded_without_spaces(matrix, monkeypatch) -> None:
    from kazma_core.config_store import get_config_store

    monkeypatch.setattr(scope, "_enforced", False)
    people.note_sign_in("shared-secret", "login")
    assert get_config_store().get(people.LAST_SIGN_IN_KEY) is None
    with pytest.raises(people.PeopleRefused) as refused:
        people.end_sessions(matrix.account_a, by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "spaces_off"


def test_a_sign_in_that_cannot_be_recorded_never_refuses_the_sign_in(matrix, enforced, monkeypatch) -> None:
    def away() -> Any:
        raise identity.IdentityUnavailable("the database is away")

    monkeypatch.setattr(identity, "read_registry", away)
    people.note_sign_in("shared-secret", "login")  # said, never raised


# ── the route ────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.people_api import create_people_router

    who: dict[str, Any] = {"principal": {"source": "secret", "role": "admin", "user_id": "",
                                         "actor": "secret", "username": "admin"}}
    monkeypatch.setattr(auth, "get_request_principal", lambda request: who["principal"])
    monkeypatch.setattr(auth, "admin_decision",
                        lambda request: "ok" if who["principal"].get("role") == "admin" else "forbidden")
    app = FastAPI()
    app.include_router(create_people_router())
    with TestClient(app) as made:
        made.who = who
        yield made


def test_the_route_ends_sessions_and_lists_the_fields(matrix, enforced, client) -> None:
    person = people.create_person("gale-s210", PASSWORD, by=OWNER_ACCOUNT_ID)
    _session_of(person)
    listed = next(p for p in client.get("/api/people").json()["people"] if p["id"] == person["id"])
    assert listed["sessions"] == 1 and listed["spaces"] and listed["last_sign_in"]
    done = client.post(f"/api/people/{person['id']}/sessions/end")
    assert done.status_code == 200 and done.json() == {"ok": True, "ended": 1}
    refused = client.post(f"/api/people/{OWNER_ACCOUNT_ID}/sessions/end")
    assert refused.status_code == 409 and refused.json()["error"] == "owner_in_my_account"
    assert client.post("/api/people/no-such-person/sessions/end").status_code == 404


def test_a_member_cannot_end_anyone_s_sessions(matrix, enforced, client) -> None:
    person = people.create_person("hale-s210", PASSWORD, by=OWNER_ACCOUNT_ID)
    _session_of(person)
    client.who["principal"] = dict(matrix.principal_a)
    assert client.post(f"/api/people/{person['id']}/sessions/end").status_code == 403
    assert _listed(person["id"])["sessions"] == 1
