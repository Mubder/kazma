"""The deep pass keeps the events a turn tells with their day (the memory
plan's W4, dated event lines, 2026-11-23).

LongMemEval's dev set asks "how many days between the pottery exhibition and
the bike purchase", and recall had only the conversations to show: the deep
pass kept who the user is and what they prefer, never what they did. Now the
prompt asks for an event as a fact of its own -- a past-tense predicate, the
object in the user's words with its name, place and amount -- and "on": the
day it happened, worked out from the turn's date. Kazma keeps a real date no
later than the turn beside the fact (``metadata.started``), marked as the day
it happened (``metadata.happened``), and recall shows "[on 30 May 2026]", not
"[since ...]". Negative control: the writer without the mark shows the day as
a start.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC
from zoneinfo import ZoneInfo

import pytest
from kazma_core.memory import belief_extractor, belief_mutation
from kazma_core.memory import recall as recall_mod
from kazma_core.memory.belief_extractor import _sanitize_belief
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

#: 2026-05-31 10:00 UTC, a Sunday.
SUNDAY_THE_31ST = 1780221600.0


def test_the_prompt_asks_for_events_with_their_day():
    prompt = belief_extractor._EXTRACT_SYSTEM
    assert '"on": "optional: for an event, the day it happened' in prompt
    assert "- Events: when this turn tells of something the user did" in prompt
    assert 'Leave "on"\n  out when the turn does not say when it happened; never guess one.' in prompt


@pytest.mark.parametrize(("given", "kept"), [
    ("2026-05-30", "2026-05-30"), ("2026-05", "2026-05"), ("٢٠٢٦-٠٥-٣٠", "2026-05-30"),
    ("last Saturday", None), ("السبت الماضي", None), ("2026-02-30", None), (None, None),
])
def test_an_events_day_is_a_real_date_never_words(given, kept):
    belief = {"subject": "user", "predicate": "attended", "object": "a pottery exhibition", "on": given}
    assert _sanitize_belief(belief).get("on") == kept


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr(recall_mod, "_memory_zone", lambda: UTC)
    monkeypatch.setattr(belief_extractor, "_turn_zone", lambda: ZoneInfo("Asia/Kuwait"))
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), isolation_level=None)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"), isolation_level=None)
    ensure_ops_schema(ops)
    yield conn, ops
    conn.close()
    ops.close()


def _apply(memory, beliefs, *, user_text, turn=1, at=SUNDAY_THE_31ST):
    conn, ops = memory
    belief_extractor._apply_beliefs_to_v2(beliefs, conn, ops, session_id="chat-1", turn=turn, tenant_id="default",
                                          now=at, private=True, user_text=user_text)


def _block(conn, question) -> str:
    result = recall_mod.recall(question, conn=conn, tenant_id="default", local_only=True)
    return recall_mod.format_recall_block(result)


def _marks(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "SELECT json_extract(metadata_json, '$.started'), json_extract(metadata_json, '$.happened'), valid_from "
        "FROM beliefs WHERE predicate != 'related_to' ORDER BY rowid")]


EXHIBITION = {"subject": "user", "predicate": "attended", "predicate_type": "set",
              "object": "a pottery exhibition at the craft centre", "on": "2026-05-30", "confidence": 0.9}
EXHIBITION_AR = {"subject": "user", "predicate": "attended", "predicate_type": "set",
                 "object": "معرض فخار في مركز الحرف", "on": "2026-05-30", "confidence": 0.9}


@pytest.mark.parametrize(("belief", "said", "asked"), [
    (EXHIBITION, "I went to a pottery exhibition at the craft centre last Saturday.",
     "When did I go to the pottery exhibition?"),
    (EXHIBITION_AR, "رحت معرض فخار في مركز الحرف السبت الماضي.", "متى رحت معرض الفخار؟"),
])
def test_an_event_is_kept_with_its_day_and_shown_on_it(memory, belief, said, asked):
    conn, _ops = memory
    _apply(memory, [belief], user_text=said)
    # When it was said stays the turn's time (W1); the day sits beside it.
    assert _marks(conn) == [("2026-05-30", 1, SUNDAY_THE_31ST)]
    block = _block(conn, asked)
    assert f"[on 30 May 2026] user attended {belief['object']}" in block
    assert "[since" not in block.split("## Known Facts")[1].split("##")[0]


def test_a_day_after_the_turn_is_never_kept(memory):
    conn, _ops = memory
    _apply(memory, [{**EXHIBITION, "on": "2026-06-07"}], user_text="I'm going to a pottery exhibition next week.")
    assert _marks(conn) == [(None, None, SUNDAY_THE_31ST)]
    assert "[since May 2026] user attended a pottery exhibition" in _block(conn, "the pottery exhibition")


def test_an_event_told_again_gains_its_day(memory):
    conn, _ops = memory
    told = "I went to a pottery exhibition at the craft centre."
    _apply(memory, [{k: v for k, v in EXHIBITION.items() if k != "on"}], user_text=told)
    _apply(memory, [EXHIBITION], user_text=told + " It was last Saturday.", turn=2)
    assert _marks(conn) == [("2026-05-30", 1, SUNDAY_THE_31ST)]


def test_a_lasting_facts_start_stays_a_start(memory):
    conn, _ops = memory
    _apply(memory, [{"subject": "user", "predicate": "lives_in", "predicate_type": "functional",
                     "object": "Rotterdam", "since": "2026-04", "confidence": 0.9}],
           user_text="We moved to Rotterdam last month.")
    assert _marks(conn) == [("2026-04", None, SUNDAY_THE_31ST)]
    assert "[since Apr 2026] user lives in Rotterdam" in _block(conn, "Where do I live?")


def test_control_without_the_mark_the_day_reads_as_a_start(memory, monkeypatch):
    """Negative control: the writer that keeps the day but not that it is an
    event's shows "[since 30 May 2026]", which reads as a fact that began."""
    conn, _ops = memory
    bind = belief_mutation._bind_write_context
    monkeypatch.setattr(belief_mutation, "_bind_write_context",
                        lambda said_by, started, happened=False: bind(said_by, started))
    _apply(memory, [EXHIBITION], user_text="I went to a pottery exhibition at the craft centre last Saturday.")
    assert _marks(conn) == [("2026-05-30", None, SUNDAY_THE_31ST)]
    assert "[since 30 May 2026] user attended" in _block(conn, "When did I go to the pottery exhibition?")


@pytest.mark.parametrize("dated", [True, False])
def test_an_event_told_again_leaves_no_write_lock(tmp_path, dated):
    """On a connection in Python's default mode (the fixture's is autocommit)
    an UPDATE that matches nothing opens a transaction: an event told again
    that already has its day left the memory database's write lock held."""
    path = tmp_path / "default_mode.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE beliefs (id TEXT, tenant_id TEXT, metadata_json TEXT)")
    held = '{"started": "2026-05-30", "happened": true}' if dated else "{}"
    conn.execute("INSERT INTO beliefs VALUES ('b1', 'default', ?)", (held,))
    conn.commit()
    tokens = belief_mutation._bind_write_context(None, "2026-05-30", True)
    try:
        gained = belief_mutation._add_named_start(conn, "b1", "default", began=True)
    finally:
        belief_mutation._unbind_write_context(tokens)
    assert (gained is None) is dated
    assert not conn.in_transaction
    other = sqlite3.connect(str(path), timeout=0, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    other.execute("ROLLBACK")
    other.close()
    conn.close()


def test_a_callers_own_transaction_is_left_to_it(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "callers.db"))
    conn.execute("CREATE TABLE beliefs (id TEXT, tenant_id TEXT, metadata_json TEXT)")
    conn.execute("INSERT INTO beliefs VALUES ('b1', 'default', '{\"started\": \"2026-05-30\"}')")
    assert conn.in_transaction
    tokens = belief_mutation._bind_write_context(None, "2026-05-30", True)
    try:
        assert belief_mutation._add_named_start(conn, "b1", "default", began=False) is None
    finally:
        belief_mutation._unbind_write_context(tokens)
    assert conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0] == 1
    conn.close()
