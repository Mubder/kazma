"""Spaces S2-12, piece 2: who is in a shared space (plan 4.11, N36).

The admin adds a person to a shared space, and they are told at their next
page load. A member leaves when they choose; the space's owner or the admin
takes one out. When a member goes -- leaving, taken out, or their account
deleted -- every fact filed under their own memory entry in the space is
forgotten (decision N36): what they said about themself and what others
said about them, through memory's own forget step. Their messages stay.
The other members' entries and the space's shared facts are untouched. The
share of the space's documents they were given is taken back; the documents
they own stay theirs.

Through the real memory store: a turn through the real post-turn pipeline,
recall through the real ``recall``. Control: the same flow without the
forget call, after which the member's fact is still recalled.
"""

from __future__ import annotations

from typing import Any

import pytest
from kazma_core.spaces import identity, people
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests.test_memory_speakers import OMAR, SARA, TEAM, _current, _say, _shown, team  # noqa: F401


def _entry_facts(db, account: str) -> int:
    return int(db.execute(
        "SELECT COUNT(*) FROM beliefs WHERE tenant_id = ? AND subject = ? AND object != ''",
        (TEAM, f"member_{account}"),
    ).fetchone()[0])


def _messages_of(db, account: str) -> int:
    return int(db.execute(
        "SELECT COUNT(*) FROM episodes WHERE tenant_id = ? AND tier != 'forgotten' "
        "AND json_extract(metadata_json, '$.said_by') = ?", (TEAM, account),
    ).fetchone()[0])


def _both_said_where_they_live(team) -> None:
    _say(SARA, "chat-sara", "I live in Kuwait City.", at=1_780_000_000.0)
    _say(OMAR, "chat-omar", "I live in Doha.", n=1, at=1_780_000_600.0)
    team.db.commit()
    assert _current(team.db, "lives_in") == {f"member_{SARA}": "Kuwait City", f"member_{OMAR}": "Doha"}
    assert _messages_of(team.db, SARA) == 1


def _members(space: str = TEAM) -> set[str]:
    return {str(m["account"]) for m in identity.read_registry()["memberships"] if m.get("space") == space}


def _gone_but_messages_stay(team, gone: str, stays: str, stays_city: str) -> None:
    assert _entry_facts(team.db, gone) == 0
    for asker in (stays, OWNER_ACCOUNT_ID):
        facts, _turns = _shown(asker, "Where does Sara live? Where does Omar live?")
        assert not any(("Kuwait City" if gone == SARA else "Doha") in f for f in facts), (asker, facts)
    assert _messages_of(team.db, gone) == 1, "their messages stay, as the conversation's history"
    assert _current(team.db, "lives_in") == {f"member_{stays}": stays_city}, "the others' entries stay"


# ── Leaving, being taken out, being deleted ─────────────────────────────


def test_a_member_who_leaves_loses_their_entry_and_keeps_their_messages(team) -> None:
    _both_said_where_they_live(team)
    went = people.leave_space(TEAM, by=SARA)
    assert went["facts"] == 1
    assert SARA not in _members()
    _gone_but_messages_stay(team, SARA, OMAR, "Doha")
    event = identity.read_registry()["events"][-1]
    assert (event["kind"], event["account"], event["space"]) == ("member_left", SARA, TEAM)
    assert people.notices_for(SARA) == [], "leaving is their own doing: nothing to tell them"
    # A turn of hers that memory handles after she went (a queued pass)
    # files nothing under her: memory reads who is in the room when it files.
    _say(SARA, "chat-sara", "I live in Riyadh.", n=2, at=1_780_001_000.0)
    assert _entry_facts(team.db, SARA) == 0


def test_a_member_taken_out_by_the_owner_is_told_and_loses_their_entry(team) -> None:
    _both_said_where_they_live(team)
    people.remove_member(TEAM, OMAR, by=OWNER_ACCOUNT_ID)
    assert OMAR not in _members()
    _gone_but_messages_stay(team, OMAR, SARA, "Kuwait City")
    told = people.notices_for(OMAR)
    assert [(n["kind"], n["space_name"]) for n in told] == [("member_removed", "Team")]


def test_deleting_an_account_forgets_its_entry_in_every_shared_space(team) -> None:
    _both_said_where_they_live(team)
    people.delete_person(SARA, by=OWNER_ACCOUNT_ID)
    _gone_but_messages_stay(team, SARA, OMAR, "Doha")


def test_control_without_the_forget_the_leavers_fact_is_still_recalled(team, monkeypatch) -> None:
    from kazma_core.memory import forget

    _both_said_where_they_live(team)
    monkeypatch.setattr(forget, "forget_entry", lambda entry, *, tenant_id: {"ok": True, "facts": []})
    people.leave_space(TEAM, by=SARA)
    assert _entry_facts(team.db, SARA) == 1
    facts, _turns = _shown(OMAR, "Where does Sara live?")
    assert any("Kuwait City" in f for f in facts), facts


def test_a_forget_that_fails_is_said_not_hidden(team, monkeypatch) -> None:
    import sqlite3

    from kazma_core.memory import forget

    def broken(entry: str, *, tenant_id: str) -> dict[str, Any]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(forget, "forget_entry", broken)
    with pytest.raises(people.PeopleRefused) as refused:
        people.leave_space(TEAM, by=SARA)
    assert (refused.value.code, refused.value.status) == ("forget_failed", 503)


# ── Who may do what ─────────────────────────────────────────────────────


def test_a_space_keeps_its_owner_and_members_do_not_take_each_other_out(team) -> None:
    with pytest.raises(people.PeopleRefused) as last:
        people.leave_space(TEAM, by=OWNER_ACCOUNT_ID)
    assert last.value.code == "last_owner"
    with pytest.raises(people.PeopleRefused) as theirs:
        people.remove_member(TEAM, OMAR, by=SARA)
    assert (theirs.value.code, theirs.value.status) == ("not_yours", 403)
    with pytest.raises(people.PeopleRefused) as personal:
        people.leave_space("sara-own", by=SARA)
    assert personal.value.code == "not_shared"
    assert _members() == {OWNER_ACCOUNT_ID, SARA, OMAR}


def test_the_admin_adds_a_person_who_is_told_at_their_next_page_load(team) -> None:
    def newcomer(reg: dict[str, Any]) -> None:
        reg["accounts"]["layla"] = {"id": "layla", "name": "Layla", "role": "member", "enabled": True}

    identity.update_registry(newcomer)
    with pytest.raises(people.PeopleRefused) as refused:
        people.add_member(TEAM, "layla", by=SARA)
    assert refused.value.code == "admin_only"
    people.add_member(TEAM, "layla", by=OWNER_ACCOUNT_ID)
    assert "layla" in _members()
    assert [(n["kind"], n["space_name"]) for n in people.notices_for("layla")] == [("member_added", "Team")]
    with pytest.raises(people.PeopleRefused) as again:
        people.add_member(TEAM, "layla", by=OWNER_ACCOUNT_ID)
    assert again.value.code == "already_member"


# ── The documents share is taken back ───────────────────────────────────


def test_the_share_of_the_spaces_documents_is_taken_back(team, tmp_path) -> None:
    from kazma_core.documents.ingestion import set_ingestion_service

    from tests._documents_seed import seed_document, seeded_service

    service = seeded_service(tmp_path / "documents")
    set_ingestion_service(service)
    try:
        owners = seed_document(service, tenant_id=TEAM, owner=OWNER_ACCOUNT_ID, marker="the owner's plan")
        hers = seed_document(service, tenant_id=TEAM, owner=SARA, marker="Sara's own notes")
        assert service.grant_member_access(tenant_id=TEAM, member=SARA, force=True) == 1
        repo = service.repository
        assert repo.has_access(tenant_id=TEAM, document_id=owners.document_id, actor_id=SARA, permission="read")
        went = people.leave_space(TEAM, by=SARA)
        assert went["documents"] == 1
        assert not repo.has_access(tenant_id=TEAM, document_id=owners.document_id, actor_id=SARA,
                                   permission="read")
        record = repo.get_document(tenant_id=TEAM, document_id=hers.document_id)
        assert record.owner_id == SARA, "the documents they own stay theirs"
        events = service.audit.list_events(tenant_id=TEAM, document_id=owners.document_id)["events"]
        assert [e["action"] for e in events].count("revoke_member") == 1
    finally:
        set_ingestion_service(None)
        service.close()


# ── The routes ──────────────────────────────────────────────────────────


def _client(monkeypatch, account: str):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth, space_binding
    from kazma_ui.spaces_api import create_spaces_router

    principal = {"source": "session", "role": "admin" if account == OWNER_ACCOUNT_ID else "operator",
                 "user_id": account, "actor": "login", "username": account}
    monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)
    monkeypatch.setattr(auth, "admin_decision",
                        lambda request: "ok" if account == OWNER_ACCOUNT_ID else "forbidden")
    monkeypatch.setattr(space_binding, "account_of", lambda p: p.get("user_id") if p else None)
    app = FastAPI()
    app.include_router(create_spaces_router())
    return TestClient(app)


def test_the_routes_list_add_take_out_and_leave(team, monkeypatch) -> None:
    def newcomer(reg: dict[str, Any]) -> None:
        reg["accounts"]["layla"] = {"id": "layla", "name": "Layla", "role": "member", "enabled": True}

    identity.update_registry(newcomer)
    sara = _client(monkeypatch, SARA)
    listed = sara.get(f"/api/spaces/{TEAM}/members").json()
    assert {m["id"] for m in listed["members"]} == {OWNER_ACCOUNT_ID, SARA, OMAR}
    assert listed["role"] == "member"
    assert sara.post(f"/api/spaces/{TEAM}/members", json={"account_id": "layla"}).status_code == 403
    assert sara.delete(f"/api/spaces/{TEAM}/members/{OMAR}").status_code == 403
    admin = _client(monkeypatch, OWNER_ACCOUNT_ID)
    assert admin.post(f"/api/spaces/{TEAM}/members", json={"account_id": "layla"}).status_code == 201
    taken = admin.delete(f"/api/spaces/{TEAM}/members/{OMAR}")
    assert taken.status_code == 200 and taken.json()["ok"]
    sara = _client(monkeypatch, SARA)
    assert sara.post(f"/api/spaces/{TEAM}/leave").status_code == 200
    # Out now: the space's members are no longer theirs to read.
    assert sara.get(f"/api/spaces/{TEAM}/members").status_code == 403
    assert _members() == {OWNER_ACCOUNT_ID, "layla"}
