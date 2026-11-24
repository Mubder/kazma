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
from kazma_core.memory.time_words import read_time, says_day, says_today_or_yesterday

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


#: A Sunday.
TOLD_ON = "2026-05-31"


@pytest.mark.parametrize(("english", "arabic", "day"), [
    ("I got it in 2018.", "في عام ٢٠١٨", "2018"),
    ("It was on May 3rd", "يوم ٣ مايو", "2026-05-03"),
    ("on 3 March 2019", "يوم 3 مارس 2019", "2019-03-03"),
    ("in April", "في أبريل", "2026-04"), ("in May", "بمايو", "2026-05"),
    ("I think I started it last Monday.", "الاثنين اللي فات", "2026-05-25"),
    ("it was last Saturday", "كان السبت الماضي", "2026-05-30"), ("back on Thursday", "وبالخميس رجعت", "2026-05-28"),
    ("I went yesterday", "رحت أمس", "2026-05-30"), ("the day before yesterday", "أمس الأول", "2026-05-29"),
    ("I went today", "رحت اليوم", "2026-05-31"),
    ("two weeks ago", "اشتريتها قبل أسبوعين", "2026-05-17"), ("two weeks ago", "كانت من أسبوعين", "2026-05-18"),
    ("three weeks ago", "قبل 3 أسابيع", "2026-05-10"), ("two months ago", "قبل شهرين", "2026-03-30"),
    ("last week", "الأسبوع اللي فات", "2026-05-23"), ("last month", "الشهر الماضي", "2026-04"),
    ("last year", "السنة اللي فاتت", "2025"), ("on the weekend", "هالويكند", "2026-05-30"),
    ("the 29th", "يوم ٢٩", "2026-05-29"),
])
def test_a_message_says_a_day_in_either_language(english, arabic, day):
    assert (says_day(english, day, TOLD_ON), says_day(arabic, day, TOLD_ON)) == (True, True)


@pytest.mark.parametrize(("english", "arabic", "day"), [
    # It names a day, another one.
    ("Check this email I got last week", "شوف هذا الإيميل اللي وصلني الأسبوع الماضي", "2019-03-03"),
    ("It came today", "وصلني اليوم", "2019-03-03"), ("I went yesterday", "رحت أمس", "2026-05-29"),
    ("last Saturday", "السبت الماضي", "2026-05-29"), ("in 2018", "في عام 2018", "2018-05-04"),
    ("the 23rd", "يوم 23", "2018"), ("on Monday", "الاثنين", "2025-05-26"),
    # It names none.
    ("Fix these 2 sentences", "صحح هالجملتين", "2019-03-03"), ("Yes, that one. Thank you!", "اي والله، شكرا", "2018"),
    ("I may march on", "ابوي يحب الشاي", "2026-03"), ("", "", "2026-05-30"),
])
def test_a_message_that_says_another_day_or_none(english, arabic, day):
    assert (says_day(english, day, TOLD_ON), says_day(arabic, day, TOLD_ON)) == (False, False)


def test_a_day_said_by_counting_from_the_turns_own():
    for text in ("I went yesterday", "last night", "رحت أمس", "امبارح"):
        assert says_today_or_yesterday(text, "2026-05-30", TOLD_ON), text
    for text in ("last Saturday", "on the 30th", "السبت الماضي", "أمس الأول", "the day before yesterday"):
        assert not says_today_or_yesterday(text, "2026-05-30", TOLD_ON), text
    assert not says_today_or_yesterday("bika", "2026-05-30", TOLD_ON)
