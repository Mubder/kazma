"""V1 migration copies of turns memory holds, found by one rule and retired
without being forgotten (``legacy_tables.legacy_copies``, ``forget.retire_copy``).

The V1-to-V2 migration wrote each old memory as a one-turn ``legacy-*``
session, a turn as "User: ... Assistant: ..." with the answer cut at about 300
characters and marked "…". Turn reconcile later wrote the same turns in full
from the chat store: on live 2026-09-27, 181 of 269 copies repeated a turn
memory holds, and recall could show one turn twice. The weekly summaries and
the live cleanup (``scripts/cleanup_live_leftovers.py``) use the same rule.

Held here: a copy is the same question AND the start of the same answer (a
different answer is another occasion, a note is never a copy); retiring one
empties it but writes no forget-ledger row and keeps the facts; the legacy
restore never puts it back -- and, as the negative control, a hard-deleted
copy would come back.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from kazma_core.memory import forget, legacy_tables
from kazma_core.memory.schema_v2 import ensure_primary_schema

FULL = "You have fifteen private repos: kazma, shipx, kca, cortexswarm and eleven more, all on GitHub."


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"))
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    _episode(conn, "orig", "s1", "what private repos do I have", FULL)
    _episode(conn, "copy", "legacy-aaaa1111", f"User: What private repos do I have\nAssistant: {FULL[:30]}…")
    _episode(conn, "other_time", "legacy-bbbb2222",
             "User: What private repos do I have\nAssistant: I indexed them earlier: nine.")
    _episode(conn, "note", "legacy-cccc3333", "User prefers teal for the dashboard.")
    yield conn
    conn.close()


def _episode(conn, eid, session, user, answer=None, *, tier="archived"):
    conn.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "created_at, metadata_json) VALUES (?, 'default', ?, 1, ?, ?, ?, ?, '{\"source\": \"backfill\"}')",
        (eid, session, user, answer, tier, time.time()),
    )
    conn.commit()


def test_a_copy_is_the_same_question_and_the_start_of_the_same_answer(db):
    assert legacy_tables.legacy_copies(db, "default") == {"copy": "orig"}


def test_without_its_original_a_copy_is_the_only_record(db):
    db.execute("UPDATE episodes SET tier = 'forgotten', user_text = '' WHERE id = 'orig'")
    db.commit()
    assert legacy_tables.legacy_copies(db, "default") == {}


def test_retiring_a_copy_empties_it_and_forgets_nothing(db):
    db.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, object, predicate_type, valid_from, "
        "ingested_at, confidence, source_session, source_turn) VALUES ('b1', 'default', 'user', "
        "'has_repos', 'fifteen', 'set', 1, 1, 0.9, 'legacy-aaaa1111', 1)"
    )
    db.commit()
    out = forget.retire_copy("copy", original_id="orig", conn=db)
    assert out["ok"] and out["duplicate_of"] == "orig"
    row = db.execute("SELECT tier, user_text, embedding, metadata_json FROM episodes WHERE id = 'copy'").fetchone()
    assert (row["tier"], row["user_text"], row["embedding"]) == ("forgotten", "", None)
    assert json.loads(row["metadata_json"])["forgotten"]["duplicate_of"] == "orig"
    assert db.execute("SELECT COUNT(*) FROM memory_forgotten").fetchone()[0] == 0  # no ledger row
    fact = db.execute("SELECT object, invalidated_at FROM beliefs WHERE id = 'b1'").fetchone()
    assert (fact["object"], fact["invalidated_at"]) == ("fifteen", None)  # the facts stay
    assert db.execute("SELECT user_text FROM episodes WHERE id = 'orig'").fetchone()[0] == "what private repos do I have"
    assert forget.retire_copy("copy", original_id="orig", conn=db)["already"] is True


def test_a_copy_is_not_retired_when_the_original_is_not_held(db):
    db.execute("UPDATE episodes SET tier = 'forgotten', user_text = '' WHERE id = 'orig'")
    db.commit()
    assert forget.retire_copy("copy", original_id="orig", conn=db) == {"ok": False, "error": "original_not_held"}
    assert db.execute("SELECT tier FROM episodes WHERE id = 'copy'").fetchone()[0] == "archived"


def _strand_copy(db):
    """The copy as the legacy table also holds it (item J restored it from there)."""
    cols = [r[1] for r in db.execute("PRAGMA table_info(episodes)") if r[1] != "embedding"]
    db.execute(f"CREATE TABLE episodes_archive AS SELECT {', '.join(cols)} FROM episodes WHERE id = 'copy'")
    db.commit()


def test_the_legacy_restore_never_puts_a_retired_copy_back(db):
    _strand_copy(db)
    forget.retire_copy("copy", original_id="orig", conn=db)
    legacy_tables.restore_legacy_episode_archive(db)
    assert db.execute("SELECT tier FROM episodes WHERE id = 'copy'").fetchone()[0] == "forgotten"


def test_a_deleted_copy_would_come_back(db):
    """Negative control: why the copy is emptied, not deleted."""
    _strand_copy(db)
    db.execute("DELETE FROM episodes WHERE id = 'copy'")
    db.commit()
    assert legacy_tables.restore_legacy_episode_archive(db)["restored"] == 1
