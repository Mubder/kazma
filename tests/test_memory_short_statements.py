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


def test_the_short_write_scenarios_teach_what_they_say():
    """Through the write path: the short statements become facts, small talk
    does not. "No, Lisbon" is the user's correction read by the model, an
    inference that may not replace the user's own Paris (the audit's F1)."""
    import json

    from kazma_core.memory.benchmark import WRITES_PATH, run_write_scenarios

    data = json.loads(WRITES_PATH.read_text(encoding="utf-8"))
    short = {**data, "scenarios": [s for s in data["scenarios"] if s["category"] == "short"]}
    assert run_write_scenarios(short)["failing"] == ["short-en-no-city"]


def test_the_length_rule_kept_every_short_statement_out(monkeypatch):
    """Negative control: twelve characters or fewer as filler."""
    import re

    def by_length(text: str) -> bool:
        return bool(re.fullmatch(r"\s*.{0,12}\s*", text or ""))

    monkeypatch.setattr("kazma_core.memory.episode_text.is_small_talk_words", by_length)
    for text in ("I'm diabetic", "I'm vegan", "Call me Sam", "No, Lisbon", "عندي سكري"):
        assert belief_extractor.is_filler_turn(text), text
