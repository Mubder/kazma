"""A question about the user's own past is not a change of subject (the
memory plan's finding 11, 2026-11-22).

Kazma's own chat turn on the LongMemEval dev set read "For the
coffee-to-water ratio in my French press, did I switch to more water per
tablespoon of coffee, or less?" as an explicit topic shift: the rule matched
"switch to" anywhere, the supervisor suppressed memory recall for the turn,
and the question was about the user's own memory. "anyway", "unrelated",
"forget it" and "مو مهم" anywhere were pivots too.

A pivot names the subject it leaves ("let's switch topics", "new topic",
"خلنا نغير الموضوع") or opens the message with a word that drops it ("never
mind, ...", "مو مهم، ..."). Each case runs through the classifier the
supervisor calls (``turn_input.classify_turn_intent``), in English and in
Arabic, after a turn of earlier work; today's rule is the negative control.
"""

from __future__ import annotations

import re

import pytest
from kazma_core.agent import turn_input
from kazma_core.agent.turn_input import classify_turn_intent

#: Asked about the user's own past, in both languages: never a pivot.
OWN_PAST = [
    ("For the coffee-to-water ratio in my French press, did I switch to more water per tablespoon of "
     "coffee, or less?",
     "بالنسبة لنسبة القهوة للماي في الفرنش برس، هل حولت لماي أكثر لكل ملعقة قهوة ولا أقل؟"),
    ("Did I change my job last year?", "هل غيرت شغلي السنة اللي طافت؟"),
    ("When did I move to Porto?", "متى انتقلت إلى بورتو؟"),
    ("Did I forget it at the office?", "نسيته في المكتب؟"),
    ("Was the meeting unrelated to the budget?", "الاجتماع كان مو مهم للميزانية؟"),
    ("Did I go to the gym anyway that week?", "رحت النادي مع ذلك هالأسبوع؟"),
]
#: Real pivots, in both languages: still pivots.
PIVOTS = [
    ("Let's switch topics.", "خلنا نغير الموضوع."),
    ("New topic: what's the weather in Kuwait?", "موضوع جديد: كيف الجو في الكويت؟"),
    ("Never mind, what time is it?", "مو مهم، كم الساعة؟"),
    ("Forget that. Different question: who won the match?", "خلاص غير هالموضوع، مين فاز بالمباراة؟"),
    ("Can we switch to something else?", "ممكن نغير السالفة؟"),
]
#: The rule as it was before 2026-11-22: the negative control.
_OLD_RULE = re.compile(
    r"(?is)"
    r"("
    r"\b(?:new\s+topic|different\s+(?:topic|question|subject)|change\s+(?:of\s+)?subject)\b"
    r"|"
    r"\b(?:forget\s+(?:that|this|it)|never\s*mind|nvm|unrelated|anyway)\b"
    r"|"
    r"\b(?:switch(?:ing)?\s+(?:to|topics?)|moving\s+on|side\s+question)\b"
    r"|"
    r"\b(?:by\s+the\s+way|btw)\b.{0,20}\b(?:unrelated|different|new)\b"
    r"|"
    r"(?:موضوع\s*ثاني|موضوع\s*جديد|غير\s*موضوع|خلنا\s*نغير|نغير\s*الموضوع|"
    r"اسأل\s*سؤال\s*ثاني|سؤال\s*ثاني|مو\s*مهم|خلاص\s*غير\s*هالموضوع)"
    r")"
)
#: A turn of earlier work, as the supervisor's history holds it.
_HISTORY = [
    {"role": "user", "content": "Please clean up the duplicate entities in my memory graph and merge them."},
    {"role": "assistant", "content": "Done: three duplicates merged."},
]


def _mode(text: str) -> str:
    return classify_turn_intent(text, messages=[*_HISTORY, {"role": "user", "content": text}],
                                task_status="completed", use_embedding_drift=False)


@pytest.mark.parametrize(("en", "ar"), OWN_PAST)
def test_a_question_about_the_user_s_own_past_is_no_pivot(en, ar):
    assert (_mode(en), _mode(ar)) != ("shift_explicit", "shift_explicit")
    assert _mode(en) != "shift_explicit" and _mode(ar) != "shift_explicit"


@pytest.mark.parametrize(("en", "ar"), PIVOTS)
def test_a_real_pivot_is_still_one_in_either_language(en, ar):
    assert (_mode(en), _mode(ar)) == ("shift_explicit", "shift_explicit")


def test_control_today_s_rule_suppresses_recall_for_the_user_s_own_past(monkeypatch):
    """The rule before 2026-11-22: the W1c question, "unrelated" and "مو مهم"
    inside a question, "forget it" and "anyway" inside one, all pivots."""
    monkeypatch.setattr(turn_input, "_TOPIC_SHIFT_RE", _OLD_RULE)
    wrong = [text for pair in OWN_PAST for text in pair if _mode(text) == "shift_explicit"]
    assert OWN_PAST[0][0] in wrong and OWN_PAST[4][1] in wrong
    assert {OWN_PAST[3][0], OWN_PAST[4][0], OWN_PAST[5][0]} <= set(wrong)


def test_recall_is_kept_for_the_user_s_own_past():
    """What the shift decides: recall suppressed for a pivot, kept for the
    user's own past (the supervisor's ``should_suppress_memory_recall``)."""
    from kazma_core.agent.turn_input import should_suppress_memory_recall

    assert should_suppress_memory_recall(intent_mode=_mode(OWN_PAST[0][0])) is False
    assert should_suppress_memory_recall(intent_mode=_mode(PIVOTS[0][0])) is True
