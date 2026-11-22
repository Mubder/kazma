"""The recall block's budget is the WHOLE block's (2026-11-22, the memory
plan's W1a review).

``format_recall_block``'s ``max_tokens`` (1,500 from the supervisor) bounded
a quarter of the lines' characters only: the recall rules, the speakers note,
the fence and Arabic's shorter tokens fell outside it, and a block passed
1,500 with or without W1a (the product measures' long store: 1,922 at most).
Now every block, rules and fence included, is counted the way the context
trim counts (``summarizer.estimate_text_tokens``, Arabic at about 2.5
characters a token) and stays inside the budget.

Negative control: Arabic counted as Latin (a quarter of its characters, the
old rule's count) lets an Arabic block pass its budget.
"""

from __future__ import annotations

import random

import pytest
from kazma_core.memory.recall import RecallHit, RecallResult, _shown, format_recall_block
from kazma_core.summarizer import estimate_text_tokens

FILLER_EN = "We talked about the garden, the weather and the week ahead. "
FILLER_AR = "تكلمنا عن الحديقة والطقس والأسبوع القادم. "


def _result(filler: str, *, sides: bool) -> RecallResult:
    episodes = []
    for i in range(5):
        question, answer = f"{filler} {i}", filler * 40 + f" {i}"
        meta = _shown(question, answer, "")
        if sides:
            meta["sides"], meta["beyond"] = (question, answer, ""), (0, 0, 0)
        episodes.append(RecallHit(id=f"e_{i}", content=question, score=1.0, metadata=meta))
    facts = [RecallHit(id=f"b_{i}", content=(filler * 3).strip(), score=1.0, kind="belief", metadata={})
             for i in range(5)]
    return RecallResult(beliefs=facts, episodes=episodes, focus_terms=frozenset())


@pytest.mark.parametrize("filler", [FILLER_EN, FILLER_AR], ids=["en", "ar"])
@pytest.mark.parametrize("sides", [True, False], ids=["room_by_rank", "fixed_bounds"])
@pytest.mark.parametrize("budget", [700, 1000, 1500])
def test_the_whole_block_stays_inside_its_budget(filler, sides, budget):
    block = format_recall_block(_result(filler, sides=sides), max_tokens=budget)
    assert block, "something fits"
    assert estimate_text_tokens(block) <= budget
    # The rules and the fence are inside it.
    assert block.startswith("## Memory recall rules") and "</kazma:data>" in block


def test_negative_control_arabic_counted_as_latin_passes_the_budget(monkeypatch):
    from kazma_core import summarizer

    monkeypatch.setattr(summarizer, "estimate_text_tokens", lambda text: len(text or "") // 4)
    block = format_recall_block(_result(FILLER_AR, sides=True), max_tokens=1000)
    monkeypatch.undo()
    assert estimate_text_tokens(block) > 1000


def test_a_budget_the_frame_fills_shows_nothing():
    """The rules and the fence alone are about 450 tokens: a budget below
    them has room for no line."""
    assert format_recall_block(_result(FILLER_EN, sides=False), max_tokens=200) == ""


def _old_estimate(text: str) -> int:
    """``summarizer._estimate_text_tokens`` as it was: a character loop."""
    ranges = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
    if not text:
        return 0
    arabic = sum(1 for ch in text if any(lo <= ord(ch) <= hi for lo, hi in ranges))
    return (len(text) - arabic) // 4 + (arabic * 2) // 5


def test_the_estimator_counts_as_it_did():
    """``estimate_text_tokens`` counts Arabic with one pattern now: the same
    count as the character loop it replaced, on random text of every script
    the ranges border."""
    draw = random.Random(20261122)
    alphabet = ("abc XYZ 123 .,!?\n" + "".join(chr(c) for c in range(0x05F0, 0x0710))
                + "".join(chr(c) for c in range(0x0740, 0x0790)) + "".join(chr(c) for c in range(0x0890, 0x0910))
                + "".join(chr(c) for c in range(0xFB40, 0xFB60)) + "".join(chr(c) for c in range(0xFDF0, 0xFE10))
                + "".join(chr(c) for c in range(0xFE60, 0xFF10)) + "🙂€ü")
    for _ in range(300):
        text = "".join(draw.choice(alphabet) for _ in range(draw.randint(0, 400)))
        assert estimate_text_tokens(text) == _old_estimate(text)
