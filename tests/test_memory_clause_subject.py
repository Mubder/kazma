"""A fact about someone else is never filed as the user's (2026-11, the
memory framework audit's F1, the speaker rule).

The Arabic quick pass read "أختي انتقلت إلى روتردام" (my sister moved to
Rotterdam) as ``user lives_in روتردام``, at the trust of the user's own words:
"انتقلت" is "I moved" and "she moved" alike, and the pass took every trigger
but a participle as first person. Its participle rule had the same gap one
step further: "أخوي اسمه أحمد وساكن في دبي" (my brother is called Ahmad and
lives in Dubai) gave the user a home in Dubai, because a participle joined by
"و" counted as the user's own clause whoever the clause was about.

``memory/clause_subject.py`` says who the words before a word last named -- the
speaker, someone else, or no one -- and ``arabic_facts`` keeps a trigger that
does not mark its own person only when no one else is named before it. Every
trigger is declared as marking its person or not, so a new one cannot skip
the rule.
"""

from __future__ import annotations

import pytest
from kazma_core.memory import arabic_facts
from kazma_core.memory.arabic_facts import extract_arabic_facts
from kazma_core.memory.belief_extractor import extract_beliefs_heuristic
from kazma_core.memory.clause_subject import OTHER, SPEAKER, subjects


def _facts(text: str) -> set[tuple[str, str]]:
    return {(f["predicate"], f["object"]) for f in extract_beliefs_heuristic(text)}


def person_of(word: str) -> str | None:
    """Who *word* names, standing at the start of a clause."""
    return subjects([word, "."])[1]


# ── who a word names ─────────────────────────────────────────────────────


@pytest.mark.parametrize("word", ["أنا", "وأنا", "احنا", "نحن", "اسمي", "I", "I'm", "we", "We're"])
def test_the_speaker_is_named_by_a_first_person_word(word):
    assert person_of(word) == SPEAKER


@pytest.mark.parametrize("word", [
    "أختي", "وأختي", "أخته", "أخوي", "أخوك", "أمي", "أبوي", "زوجتي", "صديقتي", "لصديقي",
    "مديري", "زميلتها", "عيالي", "خالتي", "عمي", "جاري", "ربعي", "أهلي", "أخت", "بنت",
    "هي", "وهي", "هم", "اسمها", "اسمه", "قالت", "يقول",
    "sister", "Sister's", "brothers", "she", "They", "her", "manager", "colleagues",
])
def test_someone_else_is_named_by_a_relative_a_companion_or_a_pronoun(word):
    assert person_of(word) == OTHER


@pytest.mark.parametrize("word", [
    "أبي",     # also "I want" in the Gulf
    "أم",      # "أم صلال", "أم كلثوم"
    "جدة",     # Jeddah
    "أبها",    # Abha
    "ربع",     # a quarter
    "خال",     # empty
    "الكويت", "دبي", "ساكن", "انتقلت", "Rotterdam", "moved", "my",
])
def test_a_word_that_is_also_something_else_names_no_one(word):
    assert person_of(word) is None


def test_a_pronoun_inside_a_clause_is_the_copula():
    """"اسمي هو أحمد": the "هو" links the user's name to Ahmad. Where its
    clause begins, the same word is someone else."""
    words = ["اسمي", "هو", "أحمد", "و", "ساكن"]
    assert subjects(words)[-1] == SPEAKER
    assert subjects(["عندي", "صديق", ".", "هو", "ساكن"])[-1] == OTHER
    assert subjects(["قابلت", "نورة", ".", "هي", "معلمة", "وساكنة"])[-1] == OTHER
    assert subjects(["قابلت", "نورة", "وهي", "ساكنة"])[-1] == OTHER


def test_the_nearest_named_person_decides():
    words = ["أختي", "في", "جدة", "وأنا", "ساكن"]
    assert subjects(words) == [None, OTHER, OTHER, OTHER, SPEAKER]
    assert subjects(["ساكن", "في", "دبي"]) == [None, None, None]


# ── the Arabic quick pass ────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "أختي انتقلت إلى روتردام.",
    "عندي أخت اسمها نورة وانتقلت إلى روتردام",
    "عندي أخت اسمها نورة. انتقلت إلى روتردام الشهر الماضي",
    "أخوي اسمه أحمد وساكن في دبي",
    "صديقتي معلمة. ساكنة في جدة",
    "تزوجت أختي وانتقلت إلى الرياض",
    "My sister is a nurse. ساكنة في دبي",
    "زوجي مهندس وشغال في شركة زين",
])
def test_a_fact_about_someone_else_is_not_the_users(text):
    assert extract_arabic_facts(text) == []


@pytest.mark.parametrize(("text", "expected"), [
    ("انتقلت إلى جدة قبل شهرين", {("lives_in", "جدة")}),
    ("أنا انتقلت إلى الرياض", {("lives_in", "الرياض")}),
    ("انتقلنا إلى الرياض الشهر الماضي", {("lives_in", "الرياض")}),
    ("أختي في جدة وأنا ساكن في الرياض", {("lives_in", "الرياض")}),
    ("اسمي هو أحمد وساكن في دبي", {("name_is", "أحمد"), ("lives_in", "دبي")}),
    ("اسمي ليلى وساكنة في دبي", {("name_is", "ليلى"), ("lives_in", "دبي")}),
    ("تزوجت وانتقلت إلى الرياض", {("lives_in", "الرياض")}),
])
def test_a_fact_about_the_user_is_kept(text, expected):
    assert _facts(text) == expected


# ── every trigger declares whether it marks its own person ────────────────


def _triggers() -> set[str]:
    return (arabic_facts._NAME_TRIGGERS | arabic_facts._HOME_TRIGGERS | arabic_facts._MOVE_TRIGGERS
            | arabic_facts._WORK_TRIGGERS | arabic_facts._LIKE_TRIGGERS | arabic_facts._USE_TRIGGERS
            | arabic_facts._HAVE | {"افضل"})


def _undeclared() -> list[str]:
    marked, unmarked = arabic_facts._MARKED, arabic_facts._UNMARKED
    return sorted(t for t in _triggers() if (t in marked) == (t in unmarked))


def test_every_trigger_is_declared_once():
    assert not _undeclared(), (
        "a trigger must be in exactly one of arabic_facts._MARKED (its form says the user "
        "is its subject) and arabic_facts._UNMARKED (it does not: the speaker rule applies): "
        + ", ".join(_undeclared()))
    assert arabic_facts._MARKED | arabic_facts._UNMARKED == _triggers()


def test_the_declaration_gate_sees_a_new_trigger(monkeypatch):
    """Control: a trigger added to a list and declared nowhere fails."""
    monkeypatch.setattr(arabic_facts, "_HOME_TRIGGERS", arabic_facts._HOME_TRIGGERS | {"تسكن"})
    assert _undeclared() == ["تسكن"]


def _complement(trigger: str) -> str:
    if trigger in arabic_facts._MOVE_TRIGGERS:
        return "إلى دبي"
    if trigger in arabic_facts._WORK_TRIGGERS:
        return "في شركة زين"
    return "في دبي"


@pytest.mark.parametrize("trigger", sorted(arabic_facts._UNMARKED))
def test_every_unmarked_trigger_follows_the_speaker_rule(trigger):
    """Each unmarked trigger keeps its fact said alone or after "أنا", and
    keeps nothing after a sentence, or a clause joined by "و", about someone
    else."""
    tail = f"{trigger} {_complement(trigger)}"
    assert extract_arabic_facts(tail), tail
    assert extract_arabic_facts(f"أختي في جدة وأنا {tail}"), tail
    for before in ("أختي ", "عندي أخت اسمها نورة و", "عندي أخت اسمها نورة. ", "زوجتي تحب القهوة. "):
        assert extract_arabic_facts(before + tail) == [], before + tail


# ── negative controls: the rules as they were ─────────────────────────────


def test_without_the_unmarked_past_verb_the_sister_moves_the_user(monkeypatch):
    """The live fault: "انتقلت" taken as first person wherever it stands."""
    monkeypatch.setattr(arabic_facts, "_UNMARKED", arabic_facts._UNMARKED - {"انتقلت"})
    assert _facts("أختي انتقلت إلى روتردام.") == {("lives_in", "روتردام")}


def test_without_the_speaker_rule_a_joined_clause_is_the_users(monkeypatch):
    """The participle rule alone: a clause joined by "و" counted as the
    user's own whoever it was about."""
    monkeypatch.setattr(arabic_facts, "subjects", lambda words: [None] * len(words))
    assert _facts("أخوي اسمه أحمد وساكن في دبي") == {("lives_in", "دبي")}
    assert _facts("عندي أخت اسمها نورة وانتقلت إلى روتردام") == {("lives_in", "روتردام")}
