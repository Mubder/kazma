"""What Kazma knows about you (the memory framework audit's F14, part H;
``memory/about_you.py``, ``GET /api/memory/v2/you``).

* The page lists the asker's own facts only -- ``user`` in a personal space,
  the asker's entry in a shared one -- each with its source, since when it
  holds, and the values it had before. Another member's facts, another
  space's and the facts about other things are not listed. Negative control:
  a lister that reads every subject lists the other member's facts.
* A correction is the asker's own words (``user_explicit``): the new value
  is current, the old one is in the history, and the change is one the
  Memory page can undo. A model's reading corrected becomes the asker's.
* A list entry corrected is ended and the new value added; the other
  entries stay.
* "No longer true" ends the fact.
* Each refuses with a code the page words, and changes nothing: another
  member's fact, another space's, an unknown id, an empty value, one too
  long, the same value, a shared space with no asker. Negative control: the
  ownership check taken out lets one member correct another's fact.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from types import MappingProxyType

import pytest
from kazma_core.memory import about_you
from kazma_core.memory.schema_v2 import ensure_primary_schema
from kazma_core.memory.speakers import Member, Room, entry_id, private_room

TEAM = Room(space="team", shared=True, members=MappingProxyType({
    "omar": Member("omar", "Omar"), "sara": Member("sara", "Sara")}))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    yield conn
    conn.close()


def _fact(conn, predicate: str, value: str, *, source: str = "user_stated", subject: str = "user",
          tenant: str = "home", kind: str = "functional", at: float | None = None,
          until: float | None = None) -> str:
    bid = f"b_{uuid.uuid4().hex[:12]}"
    now = at or time.time()
    conn.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, valid_from, "
        "valid_until, ingested_at, extraction_method) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (bid, tenant, subject, predicate, kind, value, now, until, now, source),
    )
    conn.commit()
    return bid


def _listed(conn, tenant="home", account=None) -> dict[str, dict]:
    out = about_you.facts_about(conn, tenant_id=tenant, account=account)
    assert out["ok"], out
    return {f["value"]: f for f in out["facts"]}


def _current(conn, tenant="home", subject="user") -> set[str]:
    return {str(r[0]) for r in conn.execute(
        "SELECT object FROM beliefs WHERE tenant_id = ? AND subject = ? AND valid_until IS NULL "
        "AND invalidated_at IS NULL AND object != ''", (tenant, subject))}


# ── what the page lists ────────────────────────────────────────────────────


def test_the_asker_sees_their_own_facts_with_source_dates_and_history(db):
    _fact(db, "lives_in", "Paris", at=1000, until=2000)
    _fact(db, "lives_in", "London", at=2000)
    _fact(db, "works_at", "Aurelia Bridges", source="llm_inferred", at=3000)
    _fact(db, "pet_name", "Pixel", subject="dog", at=3000)          # about something else
    _fact(db, "lives_in", "Lisbon", tenant="elsewhere", at=3000)     # another space
    facts = _listed(db)
    assert set(facts) == {"London", "Aurelia Bridges"}
    london = facts["London"]
    assert london["source"] == "you" and london["since"] == 2000 and london["label"] == "lives in"
    assert [h["value"] for h in london["history"]] == ["Paris"]
    assert facts["Aurelia Bridges"]["source"] == "reading"


def test_in_a_shared_space_each_member_sees_only_their_own(db):
    _fact(db, "lives_in", "Muscat", subject=entry_id("omar"), tenant="team")
    _fact(db, "lives_in", "Kuwait City", subject=entry_id("sara"), tenant="team")
    with private_room(TEAM):
        assert set(_listed(db, "team", "omar")) == {"Muscat"}
        assert set(_listed(db, "team", "sara")) == {"Kuwait City"}
        assert about_you.facts_about(db, tenant_id="team", account=None) == {"ok": False, "error": "no_account"}


def test_control_a_lister_that_reads_every_subject_shows_another_members_facts(db, monkeypatch):
    _fact(db, "lives_in", "Muscat", subject=entry_id("omar"), tenant="team")
    _fact(db, "lives_in", "Kuwait City", subject=entry_id("sara"), tenant="team")
    monkeypatch.setattr(about_you, "_whose", lambda tenant, account: ("%", account or ""))
    real_execute = db.execute

    class _Like:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, params=()):
            return real_execute(sql.replace("subject = ?", "subject LIKE ?"), params)

    with private_room(TEAM):
        out = about_you.facts_about(_Like(db), tenant_id="team", account="omar")
    assert {f["value"] for f in out["facts"]} == {"Muscat", "Kuwait City"}


# ── correcting ─────────────────────────────────────────────────────────────


def test_a_correction_is_the_askers_and_can_be_undone(db):
    from kazma_core.memory import changes

    old = _fact(db, "lives_in", "London", at=1000)
    out = about_you.correct_fact(db, old, "Porto", tenant_id="home", now=5000)
    assert out["ok"], out
    assert _current(db) == {"Porto"}
    porto = _listed(db)["Porto"]
    assert porto["source"] == "you" and [h["value"] for h in porto["history"]] == ["London"]
    row = db.execute("SELECT extraction_method FROM beliefs WHERE id = ?", (out["belief_id"],)).fetchone()
    assert row[0] == "user_explicit"
    update = next(c for c in changes.list_changes(db, tenant_id="home") if c["kind"] == "update")
    assert changes.undo(db, update["id"], tenant_id="home")["ok"]
    assert _current(db) == {"London"}


def test_a_models_reading_corrected_becomes_the_askers(db):
    reading = _fact(db, "works_at", "Aurelia", source="llm_inferred")
    assert about_you.correct_fact(db, reading, "Gulf Bank", tenant_id="home")["ok"]
    assert _listed(db)["Gulf Bank"]["source"] == "you"


def test_a_list_entry_corrected_is_ended_and_the_others_stay(db):
    _fact(db, "speaks", "English", kind="set")
    fr = _fact(db, "speaks", "Frnech", kind="set")
    assert about_you.correct_fact(db, fr, "French", tenant_id="home")["ok"]
    assert _current(db) == {"English", "French"}


def test_no_longer_true_ends_the_fact(db):
    bid = _fact(db, "works_at", "Initech", at=1000)
    assert about_you.end_fact(db, bid, tenant_id="home", now=4000) == {"ok": True, "ended": bid}
    assert _current(db) == set()
    assert about_you.end_fact(db, bid, tenant_id="home")["error"] == "not_found"


@pytest.mark.parametrize("case", ["other_member", "other_space", "unknown", "empty", "too_long", "unchanged"])
def test_a_correction_refused_changes_nothing(db, case):
    mine = _fact(db, "lives_in", "Muscat", subject=entry_id("omar"), tenant="team")
    hers = _fact(db, "lives_in", "Kuwait City", subject=entry_id("sara"), tenant="team")
    _fact(db, "lives_in", "Lisbon", tenant="elsewhere")
    target, value, code = {
        "other_member": (hers, "Basra", "not_found"),
        "other_space": (db.execute("SELECT id FROM beliefs WHERE tenant_id = 'elsewhere'").fetchone()[0],
                        "Basra", "not_found"),
        "unknown": ("b_nothing", "Basra", "not_found"),
        "empty": (mine, "   ", "empty"),
        "too_long": (mine, "x" * 5000, "too_long"),
        "unchanged": (mine, "  muscat ", "unchanged"),
    }[case]
    before = db.execute("SELECT id, object, valid_until FROM beliefs ORDER BY id").fetchall()
    with private_room(TEAM):
        out = about_you.correct_fact(db, target, value, tenant_id="team", account="omar")
        assert out == {"ok": False, "error": code}
        assert about_you.end_fact(db, hers, tenant_id="team", account="omar")["error"] == "not_found"
    assert db.execute("SELECT id, object, valid_until FROM beliefs ORDER BY id").fetchall() == before


def test_control_without_the_ownership_check_one_member_corrects_anothers_fact(db, monkeypatch):
    hers = _fact(db, "lives_in", "Kuwait City", subject=entry_id("sara"), tenant="team")

    def anyones(conn, belief_id, tenant_id, subject):
        return conn.execute("SELECT * FROM beliefs WHERE id = ?", (belief_id,)).fetchone()

    monkeypatch.setattr(about_you, "_own_current", anyones)
    with private_room(TEAM):
        assert about_you.correct_fact(db, hers, "Basra", tenant_id="team", account="omar")["ok"]
    assert "Kuwait City" not in _current(db, "team", entry_id("sara"))
