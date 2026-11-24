"""A changed value's history names the conversations that said each value
(the memory plan's W4).

"Where do I live?" was shown "user lives in London; before that: Paris (Jun
2026 to Oct 2026)": the model knew that the value changed, and nothing of
where the user said either. Each value now names the conversation that said
it ("(memory e_...)"), as every recalled conversation line does, so the model
can open it with ``memory_read`` ("why did I move?", "when did I say so?").

Held through the benchmark's seeding (the product's writers) and the real
``recall`` and block, English and Arabic:

* both conversations named on the fact's line; control: the line as it was;
* a conversation the user forgot is not named (the fact keeps its line);
* a fact whose value never changed names nothing (the line stays as short).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC

import pytest
from kazma_core.memory import benchmark
from kazma_core.memory import recall as recall_mod
from kazma_core.memory.recall import format_recall_block, recall
from kazma_core.memory.schema_v2 import ensure_primary_schema

DAY = 86400.0


def _dataset(language: str) -> dict:
    said = {
        "en": ("I live in Paris, near the canal.", "We moved to London, I live there now.",
               "My favourite tea is jasmine.", "Where do I live?"),
        "ar": ("أسكن في باريس، قريب من القناة.", "انتقلنا إلى لندن، أسكن هناك الحين.",
               "الشاي المفضل عندي ياسمين.", "وين أسكن؟"),
    }[language]
    paris, london = ("Paris", "London") if language == "en" else ("باريس", "لندن")
    return {
        "question": said[3],
        "sessions": [
            {"id": "p1", "day": 0, "kind": "persona", "turns": [{"user": said[0], "assistant": "OK."}]},
            {"id": "p2", "day": 140, "kind": "persona", "turns": [{"user": said[1], "assistant": "OK."}]},
            {"id": "tea", "day": 100, "kind": "persona", "turns": [{"user": said[2], "assistant": "OK."}]},
        ],
        "facts": [
            {"id": "f1", "day": 0, "session": "p1", "turn": 1, "subject": "user", "predicate": "lives_in",
             "object": paris, "predicate_type": "functional", "extraction_method": "user_stated", "importance": 4},
            {"id": "f2", "day": 140, "session": "p2", "turn": 1, "subject": "user", "predicate": "lives_in",
             "object": london, "predicate_type": "functional", "extraction_method": "user_stated", "importance": 4},
            {"id": "f3", "day": 100, "session": "tea", "turn": 1, "subject": "user", "predicate": "favourite_tea",
             "object": "jasmine", "predicate_type": "functional", "extraction_method": "user_stated",
             "importance": 3},
        ],
        "summaries": [],
    }


@pytest.fixture(params=["en", "ar"])
def memory(request, tmp_path, monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr(recall_mod, "_memory_zone", lambda: UTC)
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"))
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    data = _dataset(request.param)
    refs = benchmark._seed(conn, data)
    yield conn, refs, data
    conn.close()


def _home_line(conn, data) -> str:
    result = recall(data["question"], conn=conn, tenant_id=benchmark.BENCH_TENANT, local_only=True,
                    now=benchmark.BASE_TIME + 150 * DAY)
    assert not result.failed
    lines = [line for line in format_recall_block(result).splitlines() if "before that:" in line]
    assert len(lines) == 1, format_recall_block(result)
    return lines[0]


def test_each_value_names_the_conversation_that_said_it(memory):
    conn, refs, data = memory
    line = _home_line(conn, data)
    now_part, before_part = line.split("before that:")
    assert f"(memory {refs['p2#1']})" in now_part, line
    assert f"(memory {refs['p1#1']})" in before_part, line


def test_control_the_line_as_it_was_names_no_conversation(memory, monkeypatch):
    """Negative control: without the sources, the line holds no memory id."""
    conn, _refs, data = memory
    monkeypatch.setattr(recall_mod, "_stated_in", lambda *_a, **_k: {})
    assert "(memory " not in _home_line(conn, data)


def test_a_conversation_the_space_no_longer_remembers_is_not_named(memory):
    """Only a turn recall could show is named: its tombstone (a forget that
    left the fact, as an older build's did) names nothing."""
    conn, refs, data = memory
    conn.execute("UPDATE episodes SET tier = 'forgotten' WHERE id = ?", (refs["p1#1"],))
    conn.commit()
    line = _home_line(conn, data)
    assert refs["p1#1"] not in line and f"(memory {refs['p2#1']})" in line, line


def _fact_lines(block: str) -> list[str]:
    facts = block.split("## Known Facts", 1)[1].split("##", 1)[0] if "## Known Facts" in block else ""
    return [line for line in facts.splitlines() if line.startswith("- ")]


def test_a_value_that_never_changed_names_nothing(memory):
    conn, _refs, data = memory
    # The fact's value is English in both stores ("jasmine"): asked in English,
    # the line's shape is what is checked here.
    result = recall("What is my favourite tea?", conn=conn, tenant_id=benchmark.BENCH_TENANT, local_only=True,
                    now=benchmark.BASE_TIME + 150 * DAY)
    facts = _fact_lines(format_recall_block(result))
    assert any("jasmine" in line for line in facts), facts
    assert not any("(memory " in line for line in facts), facts
