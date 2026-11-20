"""Spaces S1-25: the admin boundary (plan 4.10).

The admin creates, resets and deletes people, and never opens a space they
are not a member of:

* every route the real app serves that a space's data answers (route_scopes
  ``space``), and every socket, refuses the admin naming a member's personal
  space -- the membership check, never a role check;
* Settings' own writers cannot write the identity store (a write there would
  add the admin to any space), once Spaces is enforced;
* a reset is recorded in the same step as the change, in a record the admin
  cannot change through Kazma (append-only, hash-chained: an edit made
  outside Kazma is found), and the member is told at their next sign-in;
* deleting a person ends their sessions and takes them out of every space;
  their personal space stays, with no member, to be erased (S1-27).

Controls: a role check alone lets the admin in; an install that does not
enforce Spaces keeps the older user routes and refuses People. The browser
half is ``tests/e2e/test_people_page_browser.py``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

ADMIN = {"source": "secret", "role": "admin", "user_id": "", "actor": "secret", "username": "admin"}


@pytest.fixture(scope="module")
def built():
    """The real app, started once for the module (its lifespan runs once)."""
    from fastapi.testclient import TestClient
    from kazma_ui.app import create_app

    app = create_app()
    with TestClient(app, client=("127.0.0.1", 40000), base_url="http://127.0.0.1:9090") as client:
        yield app, client


@pytest.fixture
def app(built):
    return built[0]


@pytest.fixture
def client(built):
    return built[1]


@pytest.fixture
def enforced(monkeypatch):
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    try:
        yield
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


def _as(monkeypatch, principal: dict[str, Any]) -> None:
    from kazma_ui import auth

    monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)
    monkeypatch.setattr(auth, "admin_decision",
                        lambda request: "ok" if principal.get("role") == "admin" else "forbidden")


def _member_principal(person: dict[str, Any]) -> dict[str, Any]:
    return {"source": "session", "role": "operator", "user_id": person["id"], "actor": "login",
            "username": person["name"]}


# ── every space route, against the admin ─────────────────────────────────


def _not_refused(app, client, space: str) -> list[str]:
    """Every space route and socket the admin reached in *space*."""
    from kazma_ui import space_binding
    from kazma_ui.route_scopes import SPACE, scope_of

    from tests.test_route_scopes import served_routes

    reached: list[str] = []
    for method, path in served_routes(app):
        if method == "MOUNT" or scope_of("GET" if method == "WEBSOCKET" else method, path) != SPACE:
            continue
        concrete = re.sub(r"\{[^}]+\}", "x1", path)
        if method == "WEBSOCKET":
            try:
                with client.websocket_connect(f"{concrete}?space={space}") as socket:
                    socket.receive_json()
                reached.append(f"WEBSOCKET {path}")
            except Exception:  # noqa: BLE001 -- the close at the handshake is the refusal
                pass
            continue
        answer = client.request(method, concrete, headers={space_binding.SPACE_HEADER: space})
        refused = answer.status_code == 403 and "not_a_member" in answer.text
        if not refused:
            reached.append(f"{method} {path} -> {answer.status_code}")
    return reached


def test_the_admin_opens_no_member_s_space_by_any_route(app, client, enforced, monkeypatch) -> None:
    person = people.create_person("dana-boundary", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    _as(monkeypatch, ADMIN)
    assert _not_refused(app, client, person["personal_space"]) == []


def test_control_a_role_check_alone_lets_the_admin_in(client, enforced, monkeypatch) -> None:
    """The membership check is what refuses the admin: with a role check in
    its place the binding admits the admin to the member's space, and a
    space route answers from it. (Only one harmless route is asked: let in,
    the others would run their handlers.)"""
    from kazma_ui import space_binding

    person = people.create_person("dana-boundary", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    space = person["personal_space"]
    _as(monkeypatch, ADMIN)
    with pytest.raises(space_binding.SpaceRefused):
        space_binding.space_for_request(ADMIN, space, legacy_tenant="default", needs_space=True)
    real = identity.is_member
    monkeypatch.setattr(identity, "is_member",
                        lambda account, space_id: identity.is_admin_account(account) or real(account, space_id))
    bound = space_binding.space_for_request(ADMIN, space, legacy_tenant="default", needs_space=True)
    assert bound.space_id == space and bound.asker == OWNER_ACCOUNT_ID
    answer = client.get("/api/chat/sessions", headers={space_binding.SPACE_HEADER: space})
    assert answer.status_code == 200, answer.text


# ── Settings' own writers cannot write the identity store ────────────────


def test_settings_cannot_write_the_identity_store_once_enforced(client, enforced, monkeypatch) -> None:
    from kazma_core.agent.tool_refusal import RefusedBeforeEffect
    from kazma_core.settings_scope import install_writer_refusal

    person = people.create_person("dana-boundary", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    _as(monkeypatch, ADMIN)
    before = get_config_store().get(identity.REGISTRY_KEY)
    forged = dict(before)
    forged["memberships"] = [*before["memberships"],
                             {"account": OWNER_ACCOUNT_ID, "space": person["personal_space"], "role": "member"}]
    for key, value in ((identity.REGISTRY_KEY, forged), (identity.ENFORCED_KEY, False)):
        answer = client.put("/api/settings/single", json={"key": key, "value": value})
        assert answer.status_code == 400 and "Kazma's own record" in answer.text, answer.text
        batch = client.put("/api/settings", json=[{"key": key, "value": value}])
        assert batch.status_code == 400, batch.text
    assert get_config_store().get(identity.REGISTRY_KEY) == before
    assert not identity.is_member(OWNER_ACCOUNT_ID, person["personal_space"])
    # The agent's config_save asks the same rule.
    assert "Kazma's own record" in install_writer_refusal(identity.REGISTRY_KEY)
    from tests._chat_space_cases import _registry

    with pytest.raises(RefusedBeforeEffect):
        _registry().get_tool("config_save").func(key=identity.REGISTRY_KEY, value="{}")


def test_control_without_spaces_the_writers_refuse_nothing_new() -> None:
    from kazma_core.settings_scope import install_writer_refusal

    assert install_writer_refusal(identity.REGISTRY_KEY) is None


# ── the record: a reset is never silent ──────────────────────────────────


def test_a_reset_is_recorded_and_told_to_the_member(client, enforced, monkeypatch) -> None:
    from kazma_core.security import platform_rbac, web_sessions

    person = people.create_person("dana-boundary", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    assert platform_rbac.authenticate_local_user("dana-boundary", "a-long-enough-password")
    session = web_sessions.create_session(role="operator", actor="login", username=person["name"],
                                          user_id=person["id"])
    _as(monkeypatch, ADMIN)
    assert client.post(f"/api/people/{person['id']}/reset",
                       json={"password": "another-long-password"}).status_code == 200
    # The new password signs in, the old does not, and the session ended.
    assert platform_rbac.authenticate_local_user("dana-boundary", "another-long-password")
    assert not platform_rbac.authenticate_local_user("dana-boundary", "a-long-enough-password")
    assert not web_sessions.validate_session(session)
    events = identity.read_registry()["events"]
    assert [(e["kind"], e["account"], e["by"]) for e in events] == [
        ("account_created", person["id"], OWNER_ACCOUNT_ID),
        ("password_reset", person["id"], OWNER_ACCOUNT_ID)]
    assert people.broken_at() is None
    # The member is told, once.
    _as(monkeypatch, _member_principal(person))
    told = client.get("/api/account/notices").json()["notices"]
    assert [n["kind"] for n in told] == ["password_reset"]
    assert client.post("/api/account/notices/seen", json={"through": told[-1]["seq"]}).status_code == 200
    assert client.get("/api/account/notices").json()["notices"] == []


def test_the_record_is_append_only_and_an_edit_outside_kazma_is_found(enforced) -> None:
    person = people.create_person("dana-boundary", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    people.reset_password(person["id"], "another-long-password", by=OWNER_ACCOUNT_ID)

    def drop_the_reset(reg: dict[str, Any]) -> None:
        reg["events"].pop()

    with pytest.raises(identity.IdentityError) as refused:
        identity.update_registry(drop_the_reset)
    assert refused.value.code == "record_is_append_only"
    assert len(identity.read_registry()["events"]) == 2
    # Edited outside Kazma (straight in the store): the chain says where.
    store = get_config_store()
    stored = store.get(identity.REGISTRY_KEY)
    stored["events"][1]["by"] = person["id"]
    store.set(identity.REGISTRY_KEY, stored, category="auth")
    assert people.broken_at() == 2


def test_control_an_untouched_record_is_whole(enforced) -> None:
    person = people.create_person("dana-boundary", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    people.reset_password(person["id"], "another-long-password", by=OWNER_ACCOUNT_ID)
    assert people.broken_at() is None


# ── delete ───────────────────────────────────────────────────────────────


def test_deleting_a_person_ends_their_access_and_keeps_their_space_closed(client, enforced,
                                                                          monkeypatch) -> None:
    from kazma_core.security import platform_rbac, web_sessions

    person = people.create_person("dana-boundary", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    session = web_sessions.create_session(role="operator", actor="login", username=person["name"],
                                          user_id=person["id"])
    _as(monkeypatch, ADMIN)
    assert client.delete(f"/api/people/{person['id']}").status_code == 200
    reg = identity.read_registry()
    assert person["id"] not in reg["accounts"]
    assert not any(m["account"] == person["id"] for m in reg["memberships"])
    space = reg["spaces"][person["personal_space"]]
    assert space["account_deleted_at"] and not identity.is_member(OWNER_ACCOUNT_ID, space["id"])
    assert not web_sessions.validate_session(session)
    assert not platform_rbac.authenticate_local_user("dana-boundary", "a-long-enough-password")
    assert reg["events"][-1]["kind"] == "account_deleted"
    # Nobody is ever added to it, the admin included.
    with pytest.raises(identity.IdentityError):
        identity.add_member(space["id"], OWNER_ACCOUNT_ID)


def test_the_owner_is_changed_in_my_account_not_on_people(client, enforced, monkeypatch) -> None:
    _as(monkeypatch, ADMIN)
    answer = client.post(f"/api/people/{OWNER_ACCOUNT_ID}/reset", json={"password": "another-long-password"})
    assert answer.status_code == 409 and answer.json()["error"] == "is_the_owner"
    assert client.delete(f"/api/people/{OWNER_ACCOUNT_ID}").json()["error"] == "is_the_owner"


def test_a_member_cannot_reach_people(client, enforced, monkeypatch) -> None:
    from kazma_core.security.platform_rbac import role_allows

    assert not role_allows("operator", "/api/people", "GET")
    assert not role_allows("operator", "/api/people", "POST")
    assert role_allows("operator", "/api/account/notices", "GET")


# ── the older mode's routes, before and after ────────────────────────────


def test_once_enforced_the_older_user_routes_refuse(client, enforced, monkeypatch) -> None:
    _as(monkeypatch, ADMIN)
    for answer in (client.post("/api/saas/users", json={"username": "x-user", "password": "a-long-password"}),
                   client.patch("/api/saas/users/x-user", json={"password": "a-long-password"}),
                   client.delete("/api/saas/users/x-user")):
        assert answer.status_code == 409 and answer.json()["error"] == "use_people_page", answer.text


def test_control_without_spaces_people_refuses_and_the_older_routes_answer(client, monkeypatch) -> None:
    _as(monkeypatch, ADMIN)
    refused = client.post("/api/people", json={"name": "x-user", "password": "a-long-password"})
    assert refused.status_code == 409 and refused.json()["error"] == "spaces_off"
    try:
        made = client.post("/api/saas/users", json={"username": "x-user", "password": "a-long-password"})
        assert made.status_code == 200, made.text
    finally:
        client.delete("/api/saas/users/x-user")
        get_config_store().delete(identity.REGISTRY_KEY)
