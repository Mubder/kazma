"""The date guard names a subject by whole words, not by substrings.

``relative_time._match_events`` decides which stored belief a reminder is
about, and a false match drags an unrelated reminder into that belief's date
check. It matched aliases as raw substrings while its alias table promised word
boundaries, so the head alias ``cursor`` matched "a cursory look"; and because
substrings made every short head dangerous, heads under five letters were banned
outright -- ``tax_due`` could only be found by the exact words "tax due".

Latin-script aliases now match as whole words (common English endings allowed),
which makes three-letter heads safe. Arabic aliases stay substring: Arabic
attaches clitics (و، ب، ل، ال) to the word, so there is no boundary to find.
"""

from __future__ import annotations

import pytest
from kazma_core.safety.commitment import relative_time as rt

BELIEFS = [
    {"predicate": "cursor_next_reset", "object": "2026-10-01"},
    {"predicate": "tax_due", "object": "2026-10-15"},
    {"predicate": "grok_next_reset", "object": "2026-10-03"},
    {"predicate": "subscription_ends", "object": "2026-11-01"},
    {"predicate": "due_date", "object": "2026-12-01"},
]


def _subjects(text: str) -> set[str]:
    return {pred for pred, _at, _alias in rt._match_events(text, BELIEFS)}


@pytest.mark.parametrize("text,expected", [
    ("remind me before the cursor reset", {"cursor_next_reset"}),
    ("remind me to pay the tax in 2 days", {"tax_due"}),
    ("grok resets on friday, remind me the day before", {"grok_next_reset"}),
    ("tell me when my subscription renewed", {"subscription_ends"}),
    ("ذكرني قبل وانتهاء الاشتراك بيومين", {"subscription_ends"}),
])
def test_a_named_subject_is_found(text, expected):
    assert _subjects(text) == expected


@pytest.mark.parametrize("text", [
    "take a cursory look at the report tomorrow",
    "the syntax review is in 2 days",
    "remind me about the recursor job in 3 hours",
])
def test_a_word_that_merely_contains_an_alias_is_not_a_subject(text):
    assert _subjects(text) == set()


def test_a_generic_short_head_is_never_an_alias():
    aliases = rt.event_aliases("due_date")
    assert "due" not in aliases
    assert "due date" in aliases, "the spelled-out predicate still matches"


def test_negative_control_substring_matching_was_the_false_positive(monkeypatch):
    """With the old raw-substring test, "cursory" was the cursor reset."""
    monkeypatch.setattr(rt, "_alias_in_text", lambda alias, norm: alias.lower() in norm)
    assert "cursor_next_reset" in _subjects("take a cursory look at the report tomorrow")
