"""Asking Kazma to recall what was said is not booking a reminder (the
memory plan's finding 13, 2026-11-22).

The intent heuristics read every "remind me" as a reminder to book: "Can you
remind me what you said about the sealant?" was a `remind` act, so the turn
was told to use `schedule_task`, and the act put it in the gray zone that
waits for the 4 s refinement call (15 of the 200 LongMemEval dev questions,
every one of them a recall question). "remind me" followed by a question
word, "of" or "about" (Arabic: a question word, or "ب" before a noun) asks
to recall, unless the message names a time to book at.

Each case runs through the classifier a turn calls
(``intent.classify.classify_turn``), in English and in Arabic, with a
stand-in model that only counts the refinement calls; today's rule is the
negative control.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from kazma_core.agent.intent import classify, heuristics
from kazma_core.agent.intent.types import ActKind

#: Asks to recall what was said: no reminder, no refinement, either language.
RECALL = [
    ("Can you remind me what you said about the sealant?", "ذكرني شنو قلت عن المادة العازلة؟"),
    ("Remind me of the website you recommended for free courses.", "ذكرني بالموقع اللي نصحتني فيه للدورات المجانية."),
    ("Could you remind me which one is implemented in the library?", "ذكرني أي واحد منهم مستخدم في المكتبة؟"),
    ("Remind me again who won the match we talked about.", "ذكرني مرة ثانية مين فاز بالمباراة اللي تكلمنا عنها."),
]
#: Books a reminder: still one, in either language.
BOOKING = [
    ("Remind me at 9 to call my mother.", "ذكرني الساعة 9 أتصل على أمي."),
    ("Remind me about the dentist appointment tomorrow.", "ذكرني بموعد الدكتور بكرة."),
    ("Remind me to buy milk on the way home.", "ذكرني أشتري حليب وأنا راجع البيت."),
    ("Remind me in two hours to take the medicine.", "ذكرني بعد ساعتين آخذ الدواء."),
]
#: The rule as it was before 2026-11-22: the negative control.
_OLD_RULE = re.compile(
    r"\bremind\s+me\b|\bschedule\s+a\s+(?:reminder|task)\b|\bset\s+a\s+reminder\b"
    r"|ذكرني|تذكير",
    re.IGNORECASE,
)


class _CountingModel:
    """A stand-in for the chat model: counts the refinement calls and
    answers with the heuristics' kinds unchanged (no act of its own)."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *args, **kwargs):
        self.calls += 1

        class _Reply:
            content = '{"acts": []}'
        return _Reply()


def _classify(text: str) -> tuple[bool, int]:
    """(a remind act, refinement calls) for *text*, through the classifier."""
    model = _CountingModel()
    decision = asyncio.run(classify.classify_turn(text, llm=model, use_embedding_drift=False, focus="normal"))
    return any(a.kind == ActKind.REMIND for a in decision.acts), model.calls


@pytest.mark.parametrize(("en", "ar"), RECALL)
def test_asking_to_recall_is_no_reminder_and_waits_for_no_refinement(en, ar):
    assert _classify(en) == (False, 0)
    assert _classify(ar) == (False, 0)


@pytest.mark.parametrize(("en", "ar"), BOOKING)
def test_a_reminder_to_book_is_still_one(en, ar):
    assert _classify(en)[0] is True
    assert _classify(ar)[0] is True


def test_control_today_s_rule_books_a_reminder_for_every_recall_ask(monkeypatch):
    """Every "remind me" a reminder: each recall ask a `remind` act, and the
    gray zone asks the refinement for it."""
    monkeypatch.setattr(heuristics, "_books_a_reminder", lambda text: bool(_OLD_RULE.search(text)))
    for en, ar in RECALL:
        assert _classify(en) == (True, 1)
        assert _classify(ar) == (True, 1)


def test_the_rules_take_long_input_in_linear_time():
    """Fixed words and one bounded word: a long message of near-misses is
    read at once."""
    import time

    text = ("remind me " * 4000) + ("ذكرني ب" * 4000)
    started = time.perf_counter()
    heuristics._books_a_reminder(text)
    assert time.perf_counter() - started < 1.0
