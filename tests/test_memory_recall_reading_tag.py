"""A recalled fact Kazma inferred says so (2026-11-18).

Under rule A of the second security review of F1, a fact the model read
from a turn stays the model's (``llm_inferred``), and so do the facts a
memory tool or a system tool wrote. The recall block injected into a turn
showed every fact the same way, so the model could not tell the facts the
user stated from Kazma's readings. A fact whose source is not the user's own
words now carries "(Kazma's reading)" after it, or "(قراءة Kazma)" when the
fact is Arabic (``recall._reading_tag``).
"""

from __future__ import annotations

import sqlite3

import pytest
from kazma_core.memory import recall as recall_mod
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.memory.recall import READING_TAG_AR, READING_TAG_EN, RecallHit, RecallResult, format_recall_block
from kazma_core.memory.schema_v2 import ensure_primary_schema


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), isolation_level=None)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    yield conn
    conn.close()


def _seed(conn: sqlite3.Connection) -> None:
    for predicate, value, method in [
        ("lives_in", "Rotterdam", "user_stated"),
        ("works_at", "Initech", "llm_inferred"),
        ("favorite_city", "Lisbon", "agent_asserted"),
        ("prefers", "القهوة", "llm_inferred"),
        ("allergic_to", "الفول", "user_explicit"),
    ]:
        mutate_belief(conn, "user", predicate, value, predicate_type="functional", extraction_method=method,
                      source_session="s1", source_turn=1, private=True)


def _block(conn: sqlite3.Connection, query: str) -> str:
    result = recall_mod.recall(query, conn=conn, local_only=True)
    return format_recall_block(result, explain=False)


@pytest.mark.parametrize(("query", "line"), [
    ("Rotterdam", "user lives in Rotterdam"),
    ("Initech", f"user works at Initech {READING_TAG_EN}"),
    ("Lisbon", f"user favorite city Lisbon {READING_TAG_EN}"),
    ("القهوة", f"user prefers القهوة {READING_TAG_AR}"),
    ("الفول", "user allergic to الفول"),
])
def test_a_fact_kazma_inferred_says_so_and_the_users_own_does_not(db, query, line):
    _seed(db)
    block = _block(db, query)
    lines = [ln for ln in block.splitlines() if ln.startswith("- ")]
    assert any(ln.endswith(line) for ln in lines), (query, lines)
    if not line.endswith(")"):
        assert not any(READING_TAG_EN in ln or READING_TAG_AR in ln for ln in lines), lines


def test_the_shared_store_path_reads_who_stated_it_from_this_install(db):
    """The Postgres mirror holds no source: the local copy says it."""
    _seed(db)
    ids = {r["predicate"]: r["id"] for r in db.execute("SELECT id, predicate FROM beliefs")}
    hits = [RecallHit(id=ids["works_at"], content="user works at Initech", score=1.0, kind="belief",
                      metadata={"object": "Initech"}),
            RecallHit(id=ids["lives_in"], content="user lives in Rotterdam", score=0.9, kind="belief",
                      metadata={"object": "Rotterdam"}),
            RecallHit(id="b_elsewhere", content="user knows Dana", score=0.8, kind="belief",
                      metadata={"object": "Dana"})]
    recall_mod._add_local_methods(hits, tenant_id="default")
    shown = format_recall_block(RecallResult(hits, []), explain=False)
    assert f"user works at Initech {READING_TAG_EN}" in shown
    assert "user lives in Rotterdam\n" in shown + "\n" and "Rotterdam (" not in shown
    assert "user knows Dana (" not in shown  # not held here: its source is not known


def test_a_thin_inferred_fact_says_both(db):
    hit = RecallHit(id="b", content="user works at Initech", score=0.1, kind="belief",
                    metadata={"strength": "weak", "extraction_method": "llm_inferred", "object": "Initech"})
    assert f"- (possibly related) user works at Initech {READING_TAG_EN}" in format_recall_block(
        RecallResult([hit], []), explain=False)


def test_an_episode_is_never_tagged():
    hit = RecallHit(id="e", content="We moved to Rotterdam", score=1.0, kind="episode",
                    metadata={"extraction_method": "llm_inferred"})
    assert READING_TAG_EN not in format_recall_block(RecallResult([], [hit]), explain=False)


def test_control_without_the_source_every_fact_reads_as_the_users(db, monkeypatch):
    """Negative control: the hydrate of before (no ``extraction_method``)
    gives the model no way to tell the facts apart."""
    _seed(db)
    original = recall_mod._hydrate_beliefs

    def without_source(conn, ids, tenant_id):
        return [{k: row[k] for k in row.keys() if k != "extraction_method"} for row in original(conn, ids, tenant_id)]

    monkeypatch.setattr(recall_mod, "_hydrate_beliefs", without_source)
    for query in ("Initech", "Lisbon", "القهوة"):
        block = _block(db, query)
        assert READING_TAG_EN not in block and READING_TAG_AR not in block, block
