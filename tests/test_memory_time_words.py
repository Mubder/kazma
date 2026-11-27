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

import re
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


def _dates(ask) -> tuple[str, str]:
    return (datetime.fromtimestamp(ask.start, UTC).date().isoformat(),
            datetime.fromtimestamp(ask.end, UTC).date().isoformat())


def _day_of(iso: str) -> float:
    y, m, d = (int(p) for p in iso.split("-"))
    return _day(y, m, d)


@pytest.mark.parametrize("arabic", [
    "شنو سويت قبل ثلاث أسابيع؟",
    "ماذا فعلت قبل ثلاثة أسابيع؟",
    "شنو سويت قبل حوالي ثلاث أسابيع؟",
])
def test_three_weeks_ago_is_the_same_period_from_the_arabic_number(arabic):
    """قبل ثلاث أسابيع is three weeks ago (the word, and the MSA ثلاثة)."""
    en = _read("What did I do three weeks ago?")
    ar = _read(arabic)
    assert en is not None and ar is not None
    assert (ar.start, ar.end, ar.said, ar.history) == (en.start, en.end, False, False)
    assert _dates(en) == ("2026-08-31", "2026-09-09")


@pytest.mark.parametrize("arabic", [
    "شنو تكلمنا عنه السنة هذي؟",
    "شنو تكلمنا عنه هذه السنة؟",
    "شنو تكلمنا عنه السنة هذه؟",
    "شنو تكلمنا عنه هذا العام؟",
    "شنو تكلمنا عنه العام هذا؟",
    "شنو تكلمنا عنه هالعام؟",
])
def test_this_year_is_read_with_the_year_first_and_in_msa(arabic):
    en = _read("What did we talk about this year?")
    ar = _read(arabic)
    assert ar is not None and (ar.start, ar.end, ar.said, ar.history) == (en.start, en.end, True, False)
    assert _dates(en) == ("2026-01-01", "2026-09-25")


@pytest.mark.parametrize(("english", "arabic", "start"), [
    ("What did we talk about in the past 10 days?", "شنو تكلمنا عنه آخر عشر أيام؟", "2026-09-15"),
    ("What did we talk about in the past 10 days?", "شنو تكلمنا عنه في الأيام العشرة الماضية؟", "2026-09-15"),
    ("What did we talk about in the last 3 weeks?", "شنو تكلمنا عنه آخر ثلاث أسابيع؟", "2026-09-04"),
    ("What did we talk about in the last 3 weeks?", "شنو تكلمنا عنه آخر ثلاثة أسابيع؟", "2026-09-04"),
    ("What did we talk about in the last 3 weeks?", "شنو تكلمنا عنه في الأسابيع الثلاثة الماضية؟", "2026-09-04"),
    ("What did we talk about in the last 3 weeks?", "شنو تكلمنا عنه ثلاث أسابيع اللي فاتت؟", "2026-09-04"),
    ("What did we talk about over the last two weeks?", "شنو تكلمنا عنه آخر أسبوعين؟", "2026-09-11"),
])
def test_a_counted_stretch_includes_today(english, arabic, start):
    """The stretch runs through now, as "this week" does, in both languages."""
    en, ar = _read(english), _read(arabic)
    assert en is not None and ar is not None
    assert (en.start, en.end) == (ar.start, ar.end) == (_day_of(start), NOW + 1)
    assert en.said is ar.said is True


@pytest.mark.parametrize("question", [
    "What did I do with the last 3 emails?",
    "What did we do on weekends?",
    "What did I do in my pastime?",
    "What did I paste on the form?",
    "شنو سويت قبل ثلاث صفحات؟",
    "شنو تكلمنا عنه في السنة الدراسية؟",
    "شنو تكلمنا عنه هذي السيارة؟",
    "شنو تكلمنا عن ثلاثية؟",
])
def test_the_same_letters_are_not_a_period(question):
    """Ordinary words that share the letters of a period are not one."""
    assert _read(question) is None, question


def test_a_month_after_min_is_the_month_not_one_month_ago():
    """من is not قبل: "من شهر أغسطس" is August, and "آخر شهر أغسطس" is too."""
    august = (_day(2026, 8, 1), _day(2026, 9, 1))
    for question in ("وين كنت ساكن من شهر أغسطس؟", "شنو تكلمنا عنه آخر شهر أغسطس؟"):
        ask = _read(question)
        assert ask is not None and (ask.start, ask.end) == august, question


@pytest.mark.parametrize("arabic", [
    "شنو تكلمنا عنه بداية أغسطس؟",
    "شنو تكلمنا عنه بداية شهر أغسطس؟",
    "شنو تكلمنا عنه في بداية شهر أغسطس؟",
])
def test_the_start_of_august_is_that_month_and_its_history(arabic):
    """بداية أغسطس and بداية شهر أغسطس are "at the start of August"."""
    en = _read("What did we talk about at the start of August?")
    ar = _read(arabic)
    assert en is not None and ar is not None
    assert ar.history is en.history is True
    assert (ar.start, ar.end) == (en.start, en.end)
    assert round((en.end - en.start) / 86400, 1) == 31.0


@pytest.mark.parametrize(("text", "day", "says"), [
    ("past 10 days", "2026-05-31", True),
    ("past 10 days", "2026-05-21", True),
    ("past 10 days", "2026-05-20", False),
    ("the last 3 weeks", "2026-05-10", True),
    ("the last 3 weeks", "2026-05-09", False),
    ("آخر 10 أيام", "2026-05-21", True),
    ("آخر 10 أيام", "2026-05-20", False),
    ("الأيام العشرة الماضية", "2026-05-21", True),
    ("آخر ثلاث أسابيع", "2026-05-10", True),
    ("الأسابيع الثلاثة الماضية", "2026-05-10", True),
    ("ثلاث أسابيع اللي فاتت", "2026-05-09", False),
    ("the last 3 emails", "2026-05-21", False),
    ("قبل ثلاث صفحات", "2026-05-10", False),
])
def test_a_counted_stretch_says_the_days_it_covers(text, day, says):
    assert says_day(text, day, TOLD_ON) is says, (text, day)


@pytest.mark.parametrize("question", [
    "كم يوم فات من آخر مرة قلت لك عن السفر؟",
    "كم شهر طاف على آخر موعد قلت لك عنه؟",
    "كذا يوم فات من آخر مرة قلت لك؟",
    "How many days since I told you about the trip?",
    "How many days ago did I tell you?",
])
def test_a_question_word_is_not_a_counted_stretch(question):
    """كم / كذا / "how many" ask how long, and are not the last 3 days.

    كم still counts as a few after قبل ("قبل كم يوم")."""
    ask = _read(question)
    assert ask is None or not ask.period, question
    assert says_day(question, "2026-09-25", "2026-09-25") is False, question
    assert says_day("كم يوم فات وانا انتظر", "2026-11-28", "2026-11-28") is False
    assert says_day("قبل كم يوم", "2026-05-28", TOLD_ON) is True


@pytest.mark.parametrize(("english", "arabic"), [
    ("What did I say in the last 3 days of August?", "شنو قلت لك آخر ٣ أيام من أغسطس؟"),
    ("What did I say in the last 3 days of August?", "شنو قلت لك آخر ثلاث أيام من أغسطس؟"),
    ("What did I say in the last 3 days of August?", "شنو قلت لك في الأيام الثلاثة الماضية من أغسطس؟"),
    ("What did I say in the last 3 days of August?", "شنو قلت لك ثلاث أيام اللي فاتت من أغسطس؟"),
])
def test_the_last_days_of_a_named_month_are_that_months(english, arabic):
    """A stretch tied to a month is that month's last days, in both languages."""
    en, ar = _read(english), _read(arabic)
    assert en is not None and ar is not None
    assert (en.start, en.end) == (ar.start, ar.end) == (_day(2026, 8, 29), _day(2026, 9, 1))
    assert says_day(english, "2026-08-29", "2026-09-25") is True
    assert says_day(arabic, "2026-08-31", "2026-09-25") is True
    assert says_day(english, "2026-08-28", "2026-09-25") is False
    assert says_day(arabic, "2026-08-28", "2026-09-25") is False
    assert says_day(english, "2026-09-25", "2026-09-25") is False
    assert says_day(arabic, "2026-09-25", "2026-09-25") is False


@pytest.mark.parametrize(("english", "arabic"), [
    ("What did I say in August about the last 3 days?", "شنو قلت لك في أغسطس عن آخر ٣ أيام؟"),
])
def test_a_named_month_wins_when_the_stretch_is_not_of_it(english, arabic):
    """On main, a named month was the period. A stretch that is not "of" that
    month still leaves the month as the period."""
    en, ar = _read(english), _read(arabic)
    assert en is not None and ar is not None
    august = (_day(2026, 8, 1), _day(2026, 9, 1))
    assert (en.start, en.end) == (ar.start, ar.end) == august


def test_a_counted_stretch_includes_today_through_now():
    """Asked at 15:00, "past 10 days" ends at now, and says_day says today."""
    now = datetime(2026, 11, 28, 15, tzinfo=UTC).timestamp()
    questions = (
        "What did I say in the past 10 days?",
        "شنو قلت في آخر عشر أيام؟",
        "شنو قلت في الأيام العشرة الماضية؟",
        "What did I say in the last 3 weeks?",
        "شنو قلت آخر ثلاث أسابيع؟",
    )
    for question in questions:
        ask = read_time(question, now=now, zone=UTC)
        assert ask is not None and ask.end == now + 1, question
    ten = read_time("What did I say in the past 10 days?", now=now, zone=UTC)
    assert ten is not None and ten.start == _day(2026, 11, 18)
    ar = read_time("شنو قلت في آخر عشر أيام؟", now=now, zone=UTC)
    assert ar is not None and (ar.start, ar.end) == (ten.start, ten.end)
    assert says_day("I was sick the past 10 days", "2026-11-28", "2026-11-28") is True
    assert says_day("كنت مريض آخر عشر أيام", "2026-11-28", "2026-11-28") is True
    assert says_day("I was sick the past 10 days", "2026-11-18", "2026-11-28") is True
    assert says_day("I was sick the past 10 days", "2026-11-17", "2026-11-28") is False


def test_every_arabic_number_word_is_an_ago_and_every_this_year_phrase_is_read():
    """From source: each number word قبل can take, and each "this year"
    phrase says_day knows, is a period read_time reads. The old digit-only
    قبل and the old this-year list are caught."""
    from kazma_core.documents.arabic import fold_for_search
    from kazma_core.memory import time_words as tw

    missing = [w for w in tw._AGO_NUMBERS_AR if w not in tw._AR_AGO.pattern]
    assert missing == [], missing
    old_ago = r"قبل\s+(?:(\d{1,3})\s+)?"
    assert "ثلاث" not in old_ago
    for phrase, back in tw._LAST_AR_RAW.items():
        if back != "this year":
            continue
        folded = fold_for_search(phrase)
        assert folded in tw._AR_RELATIVE.pattern, phrase
        assert folded in tw._THIS_YEAR, phrase
    old_year = "هالسنه|هذي السنه"
    assert "السنه هذي" not in old_year and "هذه السنه" not in old_year
    assert tw._SPAN_EN.search("past 10 days")
    assert tw._SPAN_EN.search("weekends") is None
    for word in tw._AR_QUESTION_NUMBERS:
        assert word in tw._AR_AGO.pattern, word
        assert word not in tw._SPAN_AR_GONE.pattern, word
        assert word not in tw._SPAN_AR_MSA.pattern, word
        assert word not in tw._SPAN_AR_LAST.pattern, word
    planted = re.compile(rf"(?:اخر\s+)?({tw._AR_NUMBER}|\d{{1,3}})\s+يوم\s+فات")
    assert planted.search("كم يوم فات")
    assert tw._SPAN_AR_GONE.search(" كم يوم فات") is None
    assert re.search(tw.TIME_AFTER_EN, "past 10 days")
    old_place = r"(?:last|this|past)\s+(?:week|month|year)"
    assert re.search(old_place, "past 10 days") is None
