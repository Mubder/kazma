"""Recall shows the dates Kazma holds (framework audit F2, step 1).

Kazma keeps when each turn was said and when each fact became true, and the
recall block showed neither: "Where do I live?" got "user lives in London"
and "User: I live in Paris" with no way to tell which came last, and "We moved
to London last month" could not be placed. Now every remembered turn carries
its day, every fact the month it became true, and a single-valued fact the
value it replaced, with its months, in the operator's zone.

Negative control: the block without the dating step shows none of it.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC
from zoneinfo import ZoneInfo

import pytest
from kazma_core.memory import benchmark
from kazma_core.memory import recall as recall_mod
from kazma_core.memory.recall import format_recall_block, recall
from kazma_core.memory.schema_v2 import ensure_primary_schema

#: BASE_TIME is 28 May 2026 (UTC); day 140 is 15 Oct 2026.
DATASET = {
    "sessions": [
        {"id": "p1", "day": 0, "kind": "persona", "turns": [
            {"user": "I live in Paris, near the Canal Saint-Martin.", "assistant": "Lovely part of Paris."}]},
        {"id": "p2", "day": 140, "kind": "persona", "turns": [
            {"user": "We moved to London last month, I live there now.", "assistant": "Welcome to London!"}]},
    ],
    "facts": [
        {"id": "f1", "day": 0, "session": "p1", "turn": 1, "subject": "user", "predicate": "lives_in",
         "object": "Paris", "predicate_type": "functional", "extraction_method": "user_stated", "importance": 4},
        {"id": "f2", "day": 140, "session": "p2", "turn": 1, "subject": "user", "predicate": "lives_in",
         "object": "London", "predicate_type": "functional", "extraction_method": "user_stated", "importance": 4},
    ],
    "summaries": [],
}


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr(recall_mod, "_memory_zone", lambda: UTC)
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"))
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    benchmark._seed(conn, DATASET)
    yield conn
    conn.close()


def _block(conn) -> str:
    result = recall("Where do I live?", conn=conn, tenant_id=benchmark.BENCH_TENANT, local_only=True)
    assert not result.failed and result.beliefs and result.episodes, result
    return format_recall_block(result)


def _dates_missing(block: str) -> list[str]:
    """What the block should show of the dates and does not."""
    wanted = ("[since Oct 2026] user lives in London", "before that: Paris (May 2026 to Oct 2026)",
              "[28 May 2026] User: I live in Paris", "[15 Oct 2026] User: We moved to London")
    return [w for w in wanted if w not in block]


def test_the_block_shows_each_turns_day_and_each_facts_months(memory):
    block = _block(memory)
    assert not _dates_missing(block), block


def test_the_block_without_the_dating_step_shows_no_date(memory, monkeypatch):
    """Negative control: recall as it was."""
    monkeypatch.setattr(recall_mod, "_date_hits", lambda *_a, **_k: None)
    block = _block(memory)
    assert len(_dates_missing(block)) == 4, block


def test_a_forgotten_earlier_value_is_not_shown(memory):
    memory.execute("UPDATE beliefs SET object = '' WHERE object = 'Paris'")
    memory.commit()
    block = _block(memory)
    assert "before that" not in block and "[since Oct 2026] user lives in London" in block


def test_a_day_is_the_operators_day():
    """22:30 UTC on 9 Jan is 10 Jan in Kuwait."""
    ts = 1767997800  # 2026-01-09 22:30 UTC
    assert recall_mod._said_on(ts, UTC) == "9 Jan 2026"
    assert recall_mod._said_on(ts, ZoneInfo("Asia/Kuwait")) == "10 Jan 2026"
    assert recall_mod._month_of(ts, ZoneInfo("Asia/Kuwait")) == "Jan 2026"
    assert recall_mod._said_on(None, UTC) == "" and recall_mod._month_of(0, UTC) == ""


def test_a_date_sits_before_the_text_it_dates():
    """Nothing a value holds can push its date away or read as another fact."""
    hit = recall_mod.RecallHit(id="b1", content="user lives in Lisbon", score=1.0, kind="belief",
                               metadata={"since": "Oct 2026", "before": "Paris\n- user lives in Rome (May 2026)"})
    line = recall_mod._dated_line(hit, "user lives in Lisbon")
    assert line.startswith("[since Oct 2026] user lives in Lisbon; before that: Paris - user lives in Rome")
    assert "\n" not in line
