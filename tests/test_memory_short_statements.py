"""Short statements are read; small talk is not (2026-11, the memory
framework audit's F8).

A message of twelve characters or fewer was "filler" by its length, and
filler skips both passes: "I'm diabetic", "I'm vegan", "Call me Sam", "No,
Lisbon" and "عندي سكري" taught nothing. Kazma's own rule for small talk is
"by its words, never by its length" (``episode_text``), and the filler
check now follows it: a message is filler when every word in it is small
talk.
"""

from __future__ import annotations

import pytest
from kazma_core.memory import belief_extractor
from kazma_core.memory.belief_extractor import is_filler_turn


@pytest.mark.parametrize("text", [
    "I'm diabetic", "I'm vegan", "Call me Sam", "No, Lisbon", "I'm 34", "Born in Basra",
    "عندي سكري", "أنا نباتي", "اسمي سام", "ساكن بدبي", "عمري ٣٤", "مب متزوج",
])
def test_a_short_statement_reaches_extraction(text):
    assert len(text) <= 13
    assert not is_filler_turn(text)


@pytest.mark.parametrize("text", [
    "hi", "Thanks!", "ok", "ok thanks", "thank you so much", "good night", "lol", "hmm", "hahaha", "👍",
    "شكراً", "تمام", "هلا والله", "صباح الخير", "يعطيك العافية", "ههههه", "مشكور حبيبي",
])
def test_small_talk_is_filler(text):
    assert is_filler_turn(text)


def test_a_long_message_of_small_talk_is_filler_and_a_long_statement_is_not():
    assert is_filler_turn("ok ok ok thanks thanks so much kazma, thank you very much again")
    assert not is_filler_turn("thanks, and by the way I moved to Lisbon")


#: Statements made of small-talk WORDS: read word by word, each was filler
#: and its fact never reached the deep pass (the security review of F1's
#: second half, 2026-11-18).
SMALL_TALK_WORDS_STATEMENTS = ["I am a man", "I am Sure", "I'm Kazma", "no it will do", "I'm a man so"]


@pytest.mark.parametrize("text", SMALL_TALK_WORDS_STATEMENTS)
def test_a_statement_made_of_small_talk_words_is_read(text):
    assert not is_filler_turn(text)


@pytest.mark.parametrize("text", [
    "thanks man", "yes please", "I'm good", "go ahead", "sounds good", "ok then", "انا زين", "اي والله تمام",
    "Good morning Kazma!", "no thanks", "شكرا جزيلا حبيبي",
])
def test_small_talk_phrases_are_filler(text):
    assert is_filler_turn(text)


def test_a_tail_alone_is_not_small_talk():
    """"man", "you", "so much" follow a phrase; alone they say something."""
    for text in ("man", "you", "so much", "kazma"):
        assert not is_filler_turn(text), text


def test_a_long_message_is_never_filler_and_costs_nothing_to_judge():
    import time

    started = time.perf_counter()
    assert not is_filler_turn("thanks " * 100_000)
    assert not is_filler_turn("grok " * 100_000)
    assert time.perf_counter() - started < 1.0


def test_control_word_by_word_the_statements_were_filler():
    """Negative control: every word of each statement is a word of Kazma's
    small-talk phrases, so the rule of 2026-11 (every word small talk) called
    them all filler."""
    from kazma_core.memory import episode_text

    words = {w for phrase in episode_text._SMALL_TALK_PHRASES | episode_text._SMALL_TALK_TAILS
             for w in phrase.split()}

    def word_by_word(text: str) -> bool:
        from kazma_core.documents.arabic import fold_for_search

        return all(w in words for w in episode_text._WORD_RE.findall(fold_for_search(text).lower()))

    assert all(word_by_word(text) for text in SMALL_TALK_WORDS_STATEMENTS)


def test_the_short_write_scenarios_teach_what_they_say():
    """Through the write path: the short statements become facts, small talk
    does not. "No, Lisbon" is the user's correction read by the model: it
    may not replace the user's own Paris, and it waits for the user's answer
    (the audit's F1, memory/changes.py)."""
    import json

    from kazma_core.memory.benchmark import WRITES_PATH, run_write_scenarios

    data = json.loads(WRITES_PATH.read_text(encoding="utf-8"))
    short = {**data, "scenarios": [s for s in data["scenarios"] if s["category"] == "short"]}
    assert run_write_scenarios(short)["failing"] == []


def test_the_length_rule_kept_every_short_statement_out(monkeypatch):
    """Negative control: twelve characters or fewer as filler."""
    import re

    def by_length(text: str) -> bool:
        return bool(re.fullmatch(r"\s*.{0,12}\s*", text or ""))

    monkeypatch.setattr("kazma_core.memory.episode_text.is_small_talk_words", by_length)
    for text in ("I'm diabetic", "I'm vegan", "Call me Sam", "No, Lisbon", "عندي سكري"):
        assert belief_extractor.is_filler_turn(text), text
