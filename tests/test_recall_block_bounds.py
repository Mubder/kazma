"""The recall block's bounds measurement (``scripts/recall_block_bounds.py``,
2026-11-21), on a few long memories of our own (no dataset text).

* "focus" cuts a long side around the stretch holding the question's words;
  cut from its start, the answer at the end of a long message is lost.
* "fit" gives each recalled turn the widest share of the block's budget that
  leaves no line out. Negative control: fixed wide bounds (memory_search's)
  overrun the budget and the block leaves whole lines out.
* A list's compact line keeps its own bounds.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def bounds():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import recall_block_bounds
    finally:
        sys.path.pop(0)
    return recall_block_bounds


FILLER = "We talked about the weather and the traffic on the way to work that morning. " * 12


def test_focus_keeps_the_stretch_with_the_question_words(bounds):
    text = FILLER + "My sister's new apartment is in Lisbon, near the river."
    terms = set(bounds._words("Where is my sister's apartment?"))
    assert "Lisbon" in bounds.focused(text, 200, terms)
    # Negative control: the start of the text, today's cut, does not hold it.
    assert "Lisbon" not in text[:199]
    assert len(bounds.focused(text, 200, terms)) <= 200


def test_focus_cuts_from_the_start_when_no_word_is_there(bounds):
    assert bounds.focused(FILLER, 120, {"lisbon"}).startswith("We talked")


def _store(n: int = 5) -> tuple[sqlite3.Connection, list[str]]:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE episodes (id TEXT, user_text TEXT, assistant_text TEXT, summary_text TEXT, "
                 "created_at REAL, tenant_id TEXT)")
    ids = []
    for i in range(n):
        ids.append(f"e_{i:04d}")
        conn.execute("INSERT INTO episodes VALUES (?,?,?,?,?,?)",
                     (ids[-1], FILLER + f" question {i}", FILLER * 2 + f" answer {i}", "", float(i), "bench"))
    return conn, ids


def _result(ids: list[str]):
    from kazma_core.memory.recall import RecallHit, RecallResult

    return RecallResult(beliefs=[], episodes=[RecallHit(id=i, content="", score=1.0, kind="episode",
                                                        metadata={"display": "x"}) for i in ids])


def test_fit_fills_the_budget_and_leaves_no_line_out(bounds):
    conn, ids = _store()
    result = _result(ids)
    block = bounds.render(result, bounds.VARIANTS["fit"], "question", conn)
    assert bounds.lines_shown(block) == len(ids)
    shares = [len(h.metadata["display"]) for h in result.episodes]
    assert min(shares) > 500  # more than today's 200 + 300
    # Negative control: memory_search's fixed bounds overrun the same budget.
    conn2, ids2 = _store()
    wide = _result(ids2)
    display = bounds.variant_display({"q": 600, "a": 900, "one": 1500, "cut": "start"}, "question")
    for h in wide.episodes:
        row = bounds._row_texts(conn2, h.id)
        h.metadata["display"], h.metadata["more"] = display(*row)
    from kazma_core.memory.recall import format_recall_block

    assert bounds.lines_shown(format_recall_block(wide, explain=False)) < len(ids2)


def test_a_list_line_keeps_its_own_bounds(bounds):
    display = bounds.variant_display(bounds.VARIANTS["whole"], "anything")
    text, _more = display(FILLER, FILLER, "", question_max=50, answer_max=50, one_max=80)
    assert len(text) < 140
