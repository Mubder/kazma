"""The time a question asks about, read with no model (framework audit F2
step 3, part F; ``memory/time_words.py``).

Recall matched "last week", "in August" and "before" as ordinary words: a
May turn about Pixel ranked above the August one for "what did I tell you
about Pixel in August?", "where was I living in June?" showed where the
user lives now, and a month's name was a content word no memory held.

Each reading in English and Arabic (Gulf and MSA), and the readings that
must NOT be taken: a day ahead ("what is on March 22?"), a part of the day
("before work").
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from kazma_core.memory.time_words import read_time

#: Friday 25 September 2026, noon UTC.
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC).timestamp()


def _day(y: int, m: int, d: int) -> float:
    return datetime(y, m, d, tzinfo=UTC).timestamp()


def _read(question: str):
    return read_time(question, now=NOW, zone=UTC)


@pytest.mark.parametrize("question, start, end, said", [
    ("What did I tell you yesterday?", _day(2026, 9, 24), _day(2026, 9, 25), True),
    ("Where was I living in June?", _day(2026, 6, 1), _day(2026, 7, 1), False),
    ("What did I tell you about Pixel in August?", _day(2026, 8, 1), _day(2026, 9, 1), True),
    ("What did we decide last month?", _day(2026, 8, 1), _day(2026, 9, 1), False),
    ("What did I buy in 2025?", _day(2025, 1, 1), _day(2026, 1, 1), False),
    ("What did I say about the trip in November?", _day(2025, 11, 1), _day(2025, 12, 1), True),
    ("شنو قلت لك الشهر الماضي عن الرحلة؟", _day(2026, 8, 1), _day(2026, 9, 1), True),
    ("وين كنت ساكن في يونيو؟", _day(2026, 6, 1), _day(2026, 7, 1), False),
    ("شنو قلتلك امس؟", _day(2026, 9, 24), _day(2026, 9, 25), True),
])
def test_a_period_is_read_in_both_languages(question, start, end, said):
    ask = _read(question)
    assert ask is not None and ask.period, question
    assert (ask.start, ask.end) == (start, end), question
    assert ask.said is said, question


@pytest.mark.parametrize("question, days_ago", [
    ("What did I eat two weeks ago?", 14),
    ("What did I tell you 3 days ago?", 3),
    ("وين رحت قبل اسبوعين؟", 14),
    ("وش قلت لك قبل يومين؟", 2),
])
def test_n_units_ago_is_the_unit_around_that_point(question, days_ago):
    ask = _read(question)
    assert ask is not None and ask.period, question
    point = NOW - days_ago * 86400
    assert ask.start < point < ask.end, question


@pytest.mark.parametrize("question", [
    "What is on March 22?",           # a day ahead, no past verb
    "What is my plan for this month?",
    "What do I eat before work?",     # a part of the day, not a time gone by
    "Where do I live?",
    "وين ساكن؟",
    "",
])
def test_a_question_that_names_no_past_time_reads_none(question):
    assert _read(question) is None


@pytest.mark.parametrize("question", [
    "Where did I live at first?",
    "Where did we live before we moved?",
    "What was my original plan?",
    "Who was my dentist before Braga?",
    "What did I use to drink before switching?",
    "في البداية وين كنا ساكنين؟",
    "شنو كان اسم الشركة القديمة؟",
    "وين كنا قبل ما ننتقل؟",
])
def test_what_was_true_before_is_history(question):
    ask = _read(question)
    assert ask is not None and ask.history, question


def test_before_a_part_of_the_day_is_not_history():
    ask = _read("What did I eat before work?")
    assert ask is None or not ask.history


def test_the_time_words_are_not_content_words():
    ask = _read("What did I tell you about Pixel in August?")
    assert "august" in ask.words and "pixel" not in ask.words


def test_a_month_is_the_latest_one_not_after_today():
    # September asked in September: this year's; October: last year's.
    assert _read("What did I say in September?").start == _day(2026, 9, 1)
    assert _read("What did I say in October?").start == _day(2025, 10, 1)


def test_past_true_reads_a_bare_period():
    """memory_list's "in August": no verb, read as the past on purpose."""
    assert read_time("in August", now=NOW, zone=UTC) is None
    ask = read_time("in August", now=NOW, zone=UTC, past=True)
    assert ask is not None and (ask.start, ask.end) == (_day(2026, 8, 1), _day(2026, 9, 1))
