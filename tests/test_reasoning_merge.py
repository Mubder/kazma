"""merge_reasoning_part against the shared fixture.

tests/js/test_reasoning_merge.js reads the same file, so the two
implementations cannot drift apart (UNIFIED_TURN_BLOCK.md section 6).

Live 2026-09-24 (turn e99d06a0b33f): a durable checkpoint had stored a cut of
the post-approval narration; the full narration then arrived and was
APPENDED, because the whole old text (earlier notes + the cut) was not
contained in it. Thoughts showed the notes twice, the first copy stopping
mid-sentence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kazma_ui.turn_document import REASONING_MIN_OVERLAP, merge_parts, merge_reasoning_part

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures" / "unified_turn" / "merge" / "reasoning_merge.json"
)
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_and_the_code_agree_on_the_threshold():
    assert DATA["min_overlap"] == REASONING_MIN_OVERLAP


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
def test_reasoning_merge(case):
    got = merge_reasoning_part(
        {"type": "reasoning", "text": case["old"]},
        {"type": "reasoning", "text": case["new"]},
    )
    assert got["text"] == case["expect"]


def test_the_persisted_path_does_not_print_the_notes_twice():
    """Through merge_parts, the way reply_sink writes a turn's parts."""
    live = DATA["cases"][0]
    merged = merge_parts(
        [{"type": "reasoning", "text": live["old"]}],
        [{"type": "reasoning", "text": live["new"]}],
    )
    notes = next(p["text"] for p in merged if p.get("type") == "reasoning")
    assert notes == live["expect"]
    assert notes.count("The fix worked") == 1
