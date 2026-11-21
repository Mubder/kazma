"""Suggested About-me lines (the memory framework audit's F13, decision 10).

"About me" is the user's own text, shown to the model on every call; nothing
Kazma infers goes there. Kazma offers a line made from a fact the user stated
themself, and it joins About me only on the user's yes:

* only the user's own words (``user_stated``, ``user_explicit``), never
  Kazma's reading of a turn or a tool's fact; control: a suggester that
  takes every source offers the inference;
* nothing joins About me without a yes: listing changes nothing, Add appends
  the line, Dismiss keeps it from coming back, a line already in About me is
  not offered;
* in a shared space only the asker's own facts, into the asker's own About
  me, never another member's facts or another member's words about them;
* the line is in the language the user said it in.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from types import MappingProxyType

import pytest
from kazma_core.memory import profile_suggestions as ps
from kazma_core.memory.profile import get_about, set_about
from kazma_core.memory.schema_v2 import ensure_primary_schema
from kazma_core.memory.speakers import Member, Room, entry_id, private_room


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    yield conn
    conn.close()


def _fact(conn, predicate: str, value: str, *, source: str = "user_stated", subject: str = "user",
          tenant: str = "home", said: str | None = None, at: float | None = None) -> str:
    bid = f"b_{uuid.uuid4().hex[:12]}"
    session, turn = (f"s-{bid}", 1) if said else (None, None)
    now = at or time.time()
    conn.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, valid_from, "
        "ingested_at, extraction_method, source_session, source_turn) VALUES (?, ?, ?, ?, 'functional', ?, ?, ?, ?, ?, ?)",
        (bid, tenant, subject, predicate, value, now, now, source, session, turn),
    )
    if said:
        conn.execute(
            "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
            "created_at, last_accessed) VALUES (?, ?, ?, 1, ?, 'ok', 'episodic', ?, ?)",
            (f"e_{bid}", tenant, session, said, now, now),
        )
    conn.commit()
    return bid


def _lines(conn, tenant="home", account=None) -> list[str]:
    return [s["line"] for s in ps.list_suggestions(conn, tenant_id=tenant, account=account)]


def test_a_line_is_made_only_from_the_users_own_words(db):
    _fact(db, "lives_in", "Porto", at=100)
    _fact(db, "works_at", "Aurelia Bridges", source="llm_inferred", at=200)
    _fact(db, "prefers", "dark roast coffee", source="system_tool", at=300)
    _fact(db, "allergic_to", "shellfish", source="user_explicit", at=400)
    assert _lines(db) == ["I am allergic to shellfish.", "I live in Porto."]


def test_control_a_suggester_that_takes_every_source_offers_the_inference(db, monkeypatch):
    """Negative control: without the user-sources rule Kazma's reading of a
    turn would be offered as the user's own line."""
    monkeypatch.setattr("kazma_core.memory.profile_suggestions._user_sources",
                        lambda: frozenset({"user_stated", "user_explicit", "llm_inferred", "system_tool"}))
    _fact(db, "works_at", "Aurelia Bridges", source="llm_inferred")
    assert "I work at Aurelia Bridges." in _lines(db)


def test_nothing_joins_about_me_without_a_yes(db):
    set_about("home", "I run before work.", conn=db)
    porto = _fact(db, "lives_in", "Porto")
    shellfish = _fact(db, "allergic_to", "shellfish")
    for _ in range(3):
        _lines(db)
    assert get_about("home", conn=db)["about"] == "I run before work."
    assert ps.add_suggestion(db, tenant_id="home", account=None, belief_id=porto)["ok"]
    assert get_about("home", conn=db)["about"] == "I run before work.\nI live in Porto."
    assert ps.dismiss_suggestion(db, tenant_id="home", account=None, belief_id=shellfish)["ok"]
    assert get_about("home", conn=db)["about"] == "I run before work.\nI live in Porto."
    assert _lines(db) == []  # added: in About me now; dismissed: not again


def test_a_line_already_in_about_me_is_not_offered(db):
    set_about("home", "I live in Porto and work as an engineer.", conn=db)
    _fact(db, "lives_in", "Porto")
    assert _lines(db) == []


def test_a_new_value_is_a_new_suggestion(db):
    old = _fact(db, "lives_in", "Porto", at=100)
    assert ps.dismiss_suggestion(db, tenant_id="home", account=None, belief_id=old)["ok"]
    db.execute("UPDATE beliefs SET valid_until = 150 WHERE id = ?", (old,))
    _fact(db, "lives_in", "Lisbon", at=200)
    assert _lines(db) == ["I live in Lisbon."]


def test_a_forgotten_fact_is_never_offered_and_leaves_no_value(db):
    bid = _fact(db, "lives_in", "Porto")
    assert ps.dismiss_suggestion(db, tenant_id="home", account=None, belief_id=bid)["ok"]
    db.execute("UPDATE beliefs SET object = '', valid_until = 1 WHERE id = ?", (bid,))
    assert _lines(db) == []
    row = db.execute("SELECT * FROM memory_profile_suggestions").fetchone()
    assert "Porto" not in " ".join(str(v) for v in tuple(row))


def test_add_refuses_what_is_not_offered(db):
    inferred = _fact(db, "works_at", "Aurelia", source="llm_inferred")
    out = ps.add_suggestion(db, tenant_id="home", account=None, belief_id=inferred)
    assert out == {"ok": False, "error": "not_found", "code": "not_found"}
    assert get_about("home", conn=db)["about"] == ""


def test_add_never_cuts_about_me(db):
    set_about("home", "x" * 1990, conn=db)
    bid = _fact(db, "lives_in", "Porto")
    out = ps.add_suggestion(db, tenant_id="home", account=None, belief_id=bid)
    assert out["ok"] is False and out["code"] == "about_too_long"
    assert get_about("home", conn=db)["about"] == "x" * 1990
    assert _lines(db) == ["I live in Porto."]  # still offered: nothing was decided


def test_the_line_is_in_the_language_the_user_said_it_in(db):
    _fact(db, "lives_in", "الكويت", said="أنا أسكن في الكويت", at=200)
    _fact(db, "works_at", "Zain", said="أشتغل في Zain من سنتين", at=100)
    _fact(db, "name_is", "Sami", said="My name is Sami", at=50)
    assert _lines(db) == ["أسكن في الكويت.", "أشتغل في Zain.", "My name is Sami."]


TEAM = Room(space="team", shared=True, members=MappingProxyType({
    "omar": Member("omar", "Omar"), "sara": Member("sara", "Sara")}))


def test_in_a_shared_space_only_the_askers_own_facts_into_their_own_about_me(db):
    with private_room(TEAM):
        omar_home = _fact(db, "lives_in", "Porto", subject=entry_id("omar"), tenant="team")
        _fact(db, "lives_in", "Lisbon", subject=entry_id("sara"), tenant="team")
        # Sara's words about Omar: never Omar's own statement.
        _fact(db, "works_at", "Aurelia", subject=entry_id("omar"), tenant="team", source="member_stated")
        assert _lines(db, "team", "omar") == ["I live in Porto."]
        assert _lines(db, "team", "sara") == ["I live in Lisbon."]
        assert _lines(db, "team", None) == []
        assert ps.add_suggestion(db, tenant_id="team", account="sara", belief_id=omar_home)["code"] == "not_found"
        assert ps.add_suggestion(db, tenant_id="team", account="omar", belief_id=omar_home)["ok"]
        assert get_about("team", conn=db, account="omar")["about"] == "I live in Porto."
        assert get_about("team", conn=db, account="sara")["about"] == ""


def test_control_a_shared_space_read_as_personal_offers_nobodys_facts(db, monkeypatch):
    """Negative control: read as a personal space (subject ``user``), the
    members' facts would be nobody's -- and a fact filed under ``user`` in a
    shared space would go to whoever asked."""
    with private_room(TEAM):
        _fact(db, "lives_in", "Porto", subject="user", tenant="team")
        monkeypatch.setattr(ps, "_whose", lambda tenant, account: ("user", ""))
        assert _lines(db, "team", "sara") == ["I live in Porto."]
