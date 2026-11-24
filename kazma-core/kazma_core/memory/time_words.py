"""The time a question asks about (framework audit F2, step 3; part F).

Recall matched "last week", "in March" and "before" as ordinary words: "What
did I tell you about Pixel in August?" ranked a May turn about Pixel above
the August one, "Where was I living in June?" showed where the user lives
now, and a month's name was a content word no memory held, which lowered the
word coverage of the memory that answered. :func:`read_time` reads the time
words of a question, in English and Arabic (Gulf and MSA), with no model:

* **A period**: "yesterday", "last week", "last month", "this month", "two
  weeks ago", "قبل أسبوعين", "in August", "في يوليو", "in 2025". Read only in
  a question about the past (``_PAST``: "did", "was", "told", "قلت", "كان"):
  "What is on March 22?" asks about a day ahead, not about what was said on
  one, and is left alone.
* **What was true before**: "before we moved", "at first", "originally",
  "used to", "previously", "قبل ما نغيره", "في البداية", "سابقاً". Recall
  then shows the values a fact had before its current one (its history), the
  earlier first.
* **Now**: "الحين", "الآن", "دحين" (the memory plan's finding 10,
  2026-11-22). Not a period: recall keeps what is true now, as with no time
  word. Not a content word either, as English "now" is not (a stopword):
  "وين أسكن الحين؟" counted "حين" as half of what it asked. Only the words
  for "now": "these days", "lately", "هالأيام" stay content words in both
  languages ("days" in "How often do I exercise these days?" holds its
  answer, "three days a week", above the rest; measured), and so does
  "الحين" after "until" ("لحد الحين", so far: "وإلى الحين صرفنا" answers
  "كم صرفنا لحد الحين؟"; measured).
* **The present after a statement** ends a place the quick fact pass reads:
  "I live in Lisbon these days", "أسكن في لشبونة هالأيام" (it kept
  nothing), and a period named after it ("I moved to Porto last month"):
  :data:`TIME_AFTER_EN`, :func:`present_ar_words`.

* **Whether a message says a day** (:func:`says_day`): the day itself, written
  out, by its weekday or counted from the turn's own ("3 March", "last
  Saturday", "قبل أسبوعين") -- whose words a day stands in.

The words read here are not content words of the question
(:attr:`TimeAsk.words`): a month's name matched nothing a memory says about
the thing asked. Linear patterns only (``tests/test_regexes_stay_linear``):
every pattern is a bounded alternation of fixed words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

__all__ = ["TIME_AFTER_EN", "TimeAsk", "present_ar_words", "read_time", "says_day", "says_today_or_yesterday"]

DAY = 86400.0


@dataclass(frozen=True, slots=True)
class TimeAsk:
    """What a question's time words ask for.

    ``start``/``end`` bound the period (epoch seconds, end exclusive), or
    are None. ``history``: the question asks what was true before.
    ``said``: the question asks about what was SAID in the period ("what did
    I tell you"), not about what was true in it ("where was I living").
    ``words``: the question's words that were time words, folded, for the
    content-word reader to leave out. ``named``: the words as written."""

    start: float | None
    end: float | None
    history: bool
    said: bool
    words: frozenset[str]
    named: str

    @property
    def period(self) -> bool:
        return self.start is not None and self.end is not None


_EN_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9,
    "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
#: Arabic month names, folded (``fold_for_search``): the Gulf/Egyptian names
#: and the Levantine ones.
_AR_MONTHS_RAW = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "ابريل": 4, "أبريل": 4, "مايو": 5, "يونيو": 6, "يونيه": 6,
    "يوليو": 7, "يوليه": 7, "اغسطس": 8, "أغسطس": 8, "سبتمبر": 9, "اكتوبر": 10, "أكتوبر": 10,
    "نوفمبر": 11, "ديسمبر": 12, "شباط": 2, "اذار": 3, "آذار": 3, "نيسان": 4, "ايار": 5, "أيار": 5,
    "حزيران": 6, "تموز": 7, "اب": 8, "آب": 8, "ايلول": 9, "أيلول": 9,
}

#: A question about the past: a past-tense word. Only then is a month or a
#: day read as the time the memory was said or true.
_PAST_EN = re.compile(
    r"\b(?:did|was|were|had|told|said|asked|talked|mentioned|discussed|decided|used|"
    r"went|got|bought|booked|lived|worked|wrote|ago|last|yesterday|before|previously|"
    r"originally|earlier)\b",
    re.IGNORECASE,
)
_PAST_AR = re.compile(
    r"(?:^|\s)(?:قلت|قلتلك|قلنا|كان|كانت|كنت|كنا|كانو|كانوا|سولفنا|تكلمنا|حكيت|ذكرت|سالت|طلبت|"
    r"قررنا|قررت|غيرنا|غيرت|حجزنا|اشترينا|امس|البارحه|الماضي|اللي فات|اللي طاف|قبل)(?=\s|$|[؟?.,،])"
)
#: About what was said, not what was true: "what did I tell you".
_SAID_EN = re.compile(r"\b(?:tell|told|say|said|ask|asked|talk|talked|mention|mentioned|discuss|discussed)\b",
                      re.IGNORECASE)
_SAID_AR = re.compile(r"(?:^|\s)(?:قلت|قلتلك|قلنا|سولفنا|تكلمنا|حكيت|ذكرت|سالت|طلبت|قلت لك)(?=\s|$|[؟?.,،])")

_EN_UNITS = {"day": 1.0, "days": 1.0, "week": 7.0, "weeks": 7.0, "month": 30.0, "months": 30.0,
             "year": 365.0, "years": 365.0}
_EN_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
               "a couple of": 2, "a few": 3, "few": 3}
_AGO_EN = re.compile(
    r"\b(\d{1,3}|a couple of|a few|an|a|one|two|three|four|five|six|few)\s+"
    r"(days?|weeks?|months?|years?)\s+ago\b",
    re.IGNORECASE,
)
_RELATIVE_EN = re.compile(
    r"\b(yesterday|last\s+week|last\s+month|last\s+year|this\s+week|this\s+month|this\s+year|"
    r"past\s+week|past\s+month)\b",
    re.IGNORECASE,
)
_MONTH_WORDS = "|".join(sorted(_EN_MONTHS, key=len, reverse=True))
#: A month named in context ("in June", "June 2025", "3 June", "June 3"): a
#: bare "may" or "march" is a verb.
_MONTH_EN = re.compile(
    rf"\b(?:(?:in|during|on|since|until|through|of|from|by|early|mid|late)\s+({_MONTH_WORDS})\b"
    rf"(?:\s+(\d{{4}}))?|({_MONTH_WORDS})\s+(\d{{4}})\b|(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_WORDS})\b"
    rf"|({_MONTH_WORDS})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b)",
    re.IGNORECASE,
)
_YEAR_EN = re.compile(r"\b(?:in|during|since)\s+((?:19|20)\d\d)\b", re.IGNORECASE)
#: What was true before (the history of a fact).
_HISTORY_EN = re.compile(
    r"\b(?:at first|in the beginning|at the start|originally|original|used to|previously|"
    r"formerly|former|back then|at the time|before that|my old|the old|first one|earlier)\b",
    re.IGNORECASE,
)
#: "before" followed by an event ("before we moved", "before joining", "before
#: the move") or a name ("before Braga") -- never a part of the day ("before
#: work", "before breakfast": a routine, not a time gone by).
_BEFORE_EN = re.compile(
    r"\bbefore\s+(?:(?:we|i|you|he|she|they|it)\s+[a-z']+|[a-z]+ing\b|the\s+(?:change|move|switch|"
    r"update|wedding|new)\b|[A-Z][\w-]+)",
)
_ROUTINE_AFTER_BEFORE = re.compile(
    r"\bbefore\s+(?:work|school|bed|breakfast|lunch|dinner|class|practice|training|noon|midnight|"
    r"dawn|sunrise|sunset)\b",
    re.IGNORECASE,
)
_HISTORY_AR = re.compile(
    r"(?:^|\s)(?:في البدايه|بالبدايه|في الاول|بالاول|اول شي|سابقا|سابق|من قبل|القديم|القديمه|"
    r"قبل ما|قبل لا|قبل التغيير|قبل التاجيل)(?=\s|$|[؟?.,،])"
)
_AR_AGO = re.compile(
    r"(?:^|\s)قبل\s+(?:(\d{1,3})\s+)?(يومين|اسبوعين|شهرين|سنتين|يوم|ايام|اسبوع|اسابيع|شهر|شهور|اشهر|"
    r"سنه|سنين|سنوات)(?=\s|$|[؟?.,،])"
)
_AR_UNITS = {"يوم": 1.0, "ايام": 1.0, "اسبوع": 7.0, "اسابيع": 7.0, "شهر": 30.0, "شهور": 30.0,
             "اشهر": 30.0, "سنه": 365.0, "سنين": 365.0, "سنوات": 365.0}
_AR_DUALS = {"يومين": (2, 1.0), "اسبوعين": (2, 7.0), "شهرين": (2, 30.0), "سنتين": (2, 365.0)}
_AR_RELATIVE = re.compile(
    r"(?:^|\s)(امس|البارحه|الاسبوع الماضي|الاسبوع اللي فات|الاسبوع اللي طاف|الاسبوع السابق|"
    r"الشهر الماضي|الشهر اللي فات|الشهر اللي طاف|الشهر السابق|السنه الماضيه|السنه اللي فاتت|"
    r"العام الماضي|هالاسبوع|هذا الاسبوع|هالشهر|هذا الشهر|هالسنه|هذي السنه)(?=\s|$|[؟?.,،])"
)

#: "Now" in Arabic (Gulf and MSA), as written; read folded: never a content
#: word of a question (English "now" is a stopword).
_NOW_AR_RAW = ("الحين", "هالحين", "الحينه", "دحين", "ذحين", "الآن")
#: "Until" before "now": "لحد الحين", "إلى الحين" is "so far", which a memory
#: says too ("وإلى الحين صرفنا اثنعش"): a content word still, as English
#: "so far" is. Folded.
_UNTIL_AR = frozenset({"لحد", "لحدي", "لين", "الين", "الي", "حتي", "لغايه", "لغايت"})
#: The present after a statement, Arabic, as written; read folded: where the
#: quick pass ends a place (one-word forms; a phrase's first word, "هذي",
#: already ends one).
_PRESENT_AR_RAW = (*_NOW_AR_RAW, "حالياً", "هالأيام", "هالفترة", "هالوقت", "مؤخراً")
#: A time phrase that may follow the value a statement names, lower case:
#: the present, a period named from today, "N units ago". Where the quick
#: pass's English place ends ("I live in Lisbon these days", "I moved to
#: Porto last month"). Fixed words and one bounded number: linear.
TIME_AFTER_EN = (
    r"right\s+now|nowadays|now|currently|these\s+days|at\s+the\s+moment|at\s+present|for\s+now|lately|"
    r"recently|today|yesterday|(?:last|this|past)\s+(?:week|month|year)|"
    r"(?:\d{1,3}|a\s+couple\s+of|a\s+few|an?|one|two|three|four|five|six|few)\s+"
    r"(?:days?|weeks?|months?|years?)\s+ago"
)
_PRESENT_AR_CACHE: list[tuple[frozenset[str], re.Pattern[str]]] = []


def _present_ar() -> tuple[frozenset[str], re.Pattern[str]]:
    """The Arabic present after a statement, folded, and a pattern for
    "now" in the folded text of a question (an optional "و"/"ف" before it)."""
    if not _PRESENT_AR_CACHE:
        from kazma_core.documents.arabic import fold_for_search

        present = frozenset(fold_for_search(p) for p in _PRESENT_AR_RAW)
        now = sorted({fold_for_search(p) for p in _NOW_AR_RAW}, key=len, reverse=True)
        words = "|".join(re.escape(p) for p in now) or "(?!)"
        pattern = re.compile(r"(?:^|\s)[وف]?(" + words + r")(?=\s|$|[؟?.,،!])")
        _PRESENT_AR_CACHE.append((present, pattern))
    return _PRESENT_AR_CACHE[0]


def present_ar_words() -> frozenset[str]:
    """The one-word forms of the Arabic present, folded ("الحين",
    "هالايام", "حاليا"): where the Arabic quick pass ends a place."""
    return _present_ar()[0]


def _ar_months() -> dict[str, int]:
    from kazma_core.documents.arabic import fold_for_search

    return {fold_for_search(name): month for name, month in _AR_MONTHS_RAW.items()}


_AR_MONTH_CACHE: dict[str, int] = {}


def _ar_month_pattern() -> re.Pattern[str]:
    if not _AR_MONTH_CACHE:
        _AR_MONTH_CACHE.update(_ar_months())
    names = "|".join(sorted(_AR_MONTH_CACHE, key=len, reverse=True))
    return re.compile(rf"(?:^|\s)(?:في|ب|خلال|من|لين|الى|شهر)\s+(?:شهر\s+)?({names})(?:\s+(\d{{4}}))?"
                      rf"(?=\s|$|[؟?.,،])")


_AR_MONTH_RE: list[re.Pattern[str]] = []


def _ar_month_re() -> re.Pattern[str]:
    if not _AR_MONTH_RE:
        _AR_MONTH_RE.append(_ar_month_pattern())
    return _AR_MONTH_RE[0]


def _day_start(when: datetime) -> datetime:
    return when.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_span(year: int, month: int, zone: Any) -> tuple[float, float]:
    start = datetime(year, month, 1, tzinfo=zone)
    nxt = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=zone)
    return start.timestamp(), nxt.timestamp()


def _latest_month(month: int, year: int | None, today: datetime, zone: Any) -> tuple[float, float]:
    """The month named: of the year given, else the latest one not after today."""
    if year is None:
        year = today.year if month <= today.month else today.year - 1
    return _month_span(year, month, zone)


def _relative(phrase: str, today: datetime, now: float, zone: Any) -> tuple[float, float]:
    """A period named relative to today: "last week", "الشهر الماضي"."""
    p = " ".join(phrase.split())
    start_today = _day_start(today)
    if p in ("yesterday", "امس", "البارحه"):
        return (start_today - timedelta(days=1)).timestamp(), start_today.timestamp()
    if p in ("last week", "past week", "الاسبوع الماضي", "الاسبوع اللي فات", "الاسبوع اللي طاف",
             "الاسبوع السابق"):
        return (start_today - timedelta(days=7)).timestamp(), start_today.timestamp()
    if p in ("this week", "هالاسبوع", "هذا الاسبوع"):
        return (start_today - timedelta(days=6)).timestamp(), now + 1.0
    if p in ("last month", "past month", "الشهر الماضي", "الشهر اللي فات", "الشهر اللي طاف", "الشهر السابق"):
        year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        return _month_span(year, month, zone)
    if p in ("this month", "هالشهر", "هذا الشهر"):
        return _month_span(today.year, today.month, zone)[0], now + 1.0
    if p in ("last year", "السنه الماضيه", "السنه اللي فاتت", "العام الماضي"):
        return datetime(today.year - 1, 1, 1, tzinfo=zone).timestamp(), datetime(
            today.year, 1, 1, tzinfo=zone).timestamp()
    # this year / هالسنه / هذي السنه
    return datetime(today.year, 1, 1, tzinfo=zone).timestamp(), now + 1.0


def _ago(count: float, unit_days: float, now: float) -> tuple[float, float]:
    """"N units ago": the unit around that point."""
    centre = now - count * unit_days * DAY
    half = unit_days * DAY / 2 + DAY
    return centre - half, centre + half


def read_time(question: str, *, now: float, zone: Any, past: bool | None = None) -> TimeAsk | None:
    """The time *question* asks about, read at *now* in *zone*; None when it
    names none. See the module's docstring for what is read. *past*: read
    it as a question about the past whatever its verbs say (``memory_list``'s
    "in August"); None reads its verbs."""
    from kazma_core.documents.arabic import fold_for_search

    text = " ".join(str(question or "").split())
    if not text:
        return None
    folded = fold_for_search(text).lower()
    today = datetime.fromtimestamp(now, zone)
    if past is None:
        past = bool(_PAST_EN.search(text) or _PAST_AR.search(folded))
    said = bool(_SAID_EN.search(text) or _SAID_AR.search(folded))
    spans: list[str] = []
    start = end = None

    def took(a: float, b: float, words: str) -> None:
        nonlocal start, end
        if start is None:
            start, end = a, b
        spans.append(words)

    if past:
        for m in _RELATIVE_EN.finditer(text):
            took(*_relative(m.group(1).lower(), today, now, zone), m.group(1))
        for m in _AR_RELATIVE.finditer(folded):
            took(*_relative(m.group(1), today, now, zone), m.group(1))
        for m in _AGO_EN.finditer(text):
            raw = m.group(1).lower()
            count = float(raw) if raw.isdigit() else float(_EN_NUMBERS.get(raw, 1))
            took(*_ago(count, _EN_UNITS[m.group(2).lower()], now), m.group(0))
        for m in _AR_AGO.finditer(folded):
            unit = m.group(2)
            if unit in _AR_DUALS:
                count, unit_days = _AR_DUALS[unit]
            else:
                count, unit_days = (float(m.group(1)) if m.group(1) else 1.0), _AR_UNITS[unit]
            took(*_ago(float(count), unit_days, now), m.group(0).strip())
        for m in _MONTH_EN.finditer(text):
            name = next(g for g in (m.group(1), m.group(3), m.group(6), m.group(7)) if g)
            year = m.group(2) or m.group(4)
            month = _EN_MONTHS[name.lower()]
            took(*_latest_month(month, int(year) if year else None, today, zone), m.group(0))
        for m in _ar_month_re().finditer(folded):
            took(*_latest_month(_AR_MONTH_CACHE[m.group(1)], int(m.group(2)) if m.group(2) else None,
                                today, zone), m.group(0).strip())
        for m in _YEAR_EN.finditer(text):
            year = int(m.group(1))
            took(datetime(year, 1, 1, tzinfo=zone).timestamp(),
                 datetime(year + 1, 1, 1, tzinfo=zone).timestamp(), m.group(0))
    history = bool(_HISTORY_EN.search(text) or _HISTORY_AR.search(folded))
    for m in _HISTORY_EN.finditer(text):
        spans.append(m.group(0))
    before = _BEFORE_EN.search(text)
    if before and not _ROUTINE_AFTER_BEFORE.search(text):
        history = True
    # "Now" names no period: its words are left out, nothing else.
    now_words = [m.group(1) for m in _present_ar()[1].finditer(folded)
                 if not set(folded[:m.start(1)].split()[-1:]) & _UNTIL_AR]
    if not spans and not history and not now_words:
        return None
    from kazma_core.memory.query_terms import content_terms

    words = frozenset(w for span in (*spans, *now_words) for w in content_terms(span))
    return TimeAsk(start=start, end=end, history=history, said=said and start is not None,
                   words=words, named=", ".join(spans))

# ── The day a message says ───────────────────────────────────────────────

_WEEKDAYS_EN = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
                "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4}
_WEEKDAYS_AR_RAW = {"الاثنين": 0, "الإثنين": 0, "الثلاثاء": 1, "الثلاثا": 1, "الأربعاء": 2, "الاربعا": 2,
                    "الخميس": 3, "الجمعة": 4, "السبت": 5, "الأحد": 6}
#: Days counted from the turn's own, as written; folded before they are read.
_SAID_DAYS_EN = {"today": 0, "tonight": 0, "this morning": 0, "this afternoon": 0, "this evening": 0,
                 "yesterday": -1, "last night": -1, "the day before yesterday": -2, "day before yesterday": -2,
                 "tomorrow": 1}
_SAID_DAYS_AR_RAW = {"اليوم": 0, "الليلة": 0, "هاليوم": 0, "أمس": -1, "امبارح": -1, "مبارح": -1, "البارحة": -1,
                     "البارح": -1, "أول أمس": -2, "أمس الأول": -2, "أول امبارح": -2, "قبل أمس": -2, "بكرة": 1,
                     "باجر": 1, "غدا": 1}
_WEEKEND_EN = frozenset({"weekend"})
_WEEKEND_AR_RAW = ("الويكند", "هالويكند", "ويكند", "نهاية الأسبوع", "عطلة الأسبوع")
#: A period before the turn's: the days back it covers, (nearest, farthest);
#: a calendar month or year ("last", "this") by its name.
_LAST_EN: dict[str, tuple[int, int] | str] = {"last week": (1, 14), "past week": (0, 8), "this week": (0, 7),
            "last month": "last month", "past month": (0, 31), "this month": "this month", "last year": "last year", "this year": "this year"}
_LAST_AR_RAW: dict[str, tuple[int, int] | str] = {"الأسبوع الماضي": (1, 14), "الأسبوع اللي فات": (1, 14), "الأسبوع اللي طاف": (1, 14),
                "الأسبوع اللي راح": (1, 14), "الأسبوع السابق": (1, 14), "هالأسبوع": (0, 7), "هذا الأسبوع": (0, 7),
                "الشهر الماضي": "last month", "الشهر اللي فات": "last month", "الشهر اللي طاف": "last month",
                "الشهر اللي راح": "last month", "الشهر السابق": "last month", "هالشهر": "this month", "هذا الشهر": "this month",
                "السنة الماضية": "last year", "السنة اللي فاتت": "last year", "السنة اللي طافت": "last year",
                "العام الماضي": "last year", "هالسنة": "this year", "هذي السنة": "this year"}
_FIXED_DAYS = {"christmas eve": (12, 24), "christmas": (12, 25), "new year's eve": (12, 31), "new year": (1, 1),
               "valentine": (2, 14), "halloween": (10, 31), "الكريسماس": (12, 25), "راس السنه": (1, 1)}
_AGO_NUMBERS_EN = {**_EN_NUMBERS, "couple of": 2, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_AGO_SAID_EN = re.compile(
    r"\b(\d{1,3}|a couple of|couple of|a few|an|a|one|two|three|four|five|six|seven|eight|nine|ten|few) "
    r"(days?|weeks?|months?|years?) (?:ago|back)\b")
_AGO_SAID_AR = re.compile(
    r"(?:^|\s)(?:و|ف)?(?:قبل|من|صار لي|صارلي)\s+(?:حوالي\s+)?(?:(\d{1,3})\s+)?(يومين|اسبوعين|شهرين|سنتين|يوم|ايام|"
    r"اسبوع|اسابيع|شهر|شهور|اشهر|سنه|سنين|سنوات)(?=\s|$|[؟?.,،!])")
#: A day of a month, a month, a year; a day of the month alone ("the 23rd").
_NUM_MONTH_EN = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?: of)? ({_MONTH_WORDS})\b(?:,? ((?:19|20)\d\d))?")
_MONTH_NUM_EN = re.compile(rf"\b({_MONTH_WORDS}) (\d{{1,2}})(?:st|nd|rd|th)?\b(?:,? ((?:19|20)\d\d))?")
_NUMERIC_DAY = re.compile(r"\b((?:19|20)\d\d)-(\d{1,2})(?:-(\d{1,2}))?\b|\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b")
_YEAR_SAID = re.compile(r"\b((?:19|20)\d\d)\b")
_DOM_EN = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b")
_DOM_AR = re.compile(r"يوم (\d{1,2})(?!\d)")
_PLAIN_MONTH_EN = re.compile(rf"\b(?:in|during|on|since|of|from|by|early|mid|late|last|this) ({_MONTH_WORDS})\b"
                             rf"(?: ((?:19|20)\d\d))?")


class _SaidWords:
    """The Arabic word tables, folded once."""

    weekdays: dict[str, int] = {}
    days: dict[str, int] = {}
    weekend: tuple[str, ...] = ()
    last: dict[str, tuple[int, int] | str] = {}
    months: dict[str, int] = {}
    num_month: re.Pattern[str] | None = None
    month: re.Pattern[str] | None = None


def _said_words() -> type[_SaidWords]:
    if not _SaidWords.weekdays:
        from kazma_core.documents.arabic import fold_for_search as fold

        _SaidWords.days = {fold(w): n for w, n in _SAID_DAYS_AR_RAW.items()}
        _SaidWords.weekend = tuple(fold(w) for w in _WEEKEND_AR_RAW)
        _SaidWords.last = {fold(w): n for w, n in _LAST_AR_RAW.items()}
        _SaidWords.months = _ar_months()
        names = "|".join(sorted(_SaidWords.months, key=len, reverse=True))
        _SaidWords.num_month = re.compile(rf"(\d{{1,2}})\s+(?:من\s+)?(?:شهر\s+)?({names})(?!\w)(?:\s+((?:19|20)\d\d))?")
        _SaidWords.month = re.compile(
            rf"(?:^|\s)(?:في|خلال|شهر|من)\s+(?:شهر\s+)?({names})(?!\w)(?:\s+((?:19|20)\d\d))?"
            rf"|(?:^|\s)[وف]?ب({names})(?!\w)(?:\s+((?:19|20)\d\d))?")
        weekdays: dict[str, int] = {}
        for word, n in _WEEKDAYS_AR_RAW.items():
            weekdays[fold(word)] = n
        _SaidWords.weekdays = weekdays
    return _SaidWords


def _weekdays_said(text: str) -> set[int]:
    words = re.findall(r"\w+", text)
    arabic = _said_words().weekdays
    out: set[int] = set()
    for word in words:
        if word in _WEEKDAYS_EN:
            out.add(_WEEKDAYS_EN[word])
            continue
        for _prefix in range(3):  # "والسبت", "وبالخميس"; "للخميس"
            if word in arabic:
                out.add(arabic[word])
                break
            if word.startswith("لل") and "ال" + word[2:] in arabic:
                out.add(arabic["ال" + word[2:]])
                break
            if word[:1] not in ("و", "ف", "ب", "ل"):
                break
            word = word[1:]
    return out


def _days_counted(text: str) -> set[int]:
    """The days counted from the turn's own that *text* (folded) says:
    "today" 0, "أمس" -1. The longer phrase is read, not the word in it."""
    out: set[int] = set()
    for table in (_SAID_DAYS_EN, _said_words().days):
        for phrase in sorted(table, key=len, reverse=True):
            pattern = r"(?<!\w)(?:[وف](?=[\u0621-\u064a]))?" + re.escape(phrase) + r"(?!\w)"
            if re.search(pattern, text):
                out.add(table[phrase])
                text = re.sub(pattern, " ", text)
    return out


def _folded(text: str) -> str:
    from kazma_core.documents.arabic import fold_for_search

    return fold_for_search(text or "").replace("’", "'")


def says_today_or_yesterday(text: str, day: str, said_on: str | None) -> bool:
    """Whether *text* says *day* by counting from the turn's own (*said_on*):
    "today", "last night", "أمس". The surest way there is to say a day."""
    when, said = _as_date(day), _as_date(said_on)
    return bool(when and said) and (when - said).days in _days_counted(_folded(text)) and when <= said


def _as_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _dates_said(text: str) -> list[tuple[int | None, int | None, int | None]]:
    """Every date *text* (folded) writes out, as ``(year, month, day)`` with
    None for what it leaves out. "3/4" is read both ways."""
    words = _said_words()
    out: list[tuple[int | None, int | None, int | None]] = []
    for m in _NUM_MONTH_EN.finditer(text):
        out.append((int(m[3]) if m[3] else None, _EN_MONTHS[m[2]], int(m[1])))
    for m in _MONTH_NUM_EN.finditer(text):
        out.append((int(m[3]) if m[3] else None, _EN_MONTHS[m[1]], int(m[2])))
    for m in words.num_month.finditer(text):  # type: ignore[union-attr]
        out.append((int(m[3]) if m[3] else None, words.months[m[2]], int(m[1])))
    for m in _NUMERIC_DAY.finditer(text):
        if m[1]:
            out.append((int(m[1]), int(m[2]), int(m[3]) if m[3] else None))
            continue
        year = int(m[6]) + (2000 if len(m[6]) == 2 else 0) if m[6] else None
        out += [(year, int(m[5]), int(m[4])), (year, int(m[4]), int(m[5]))]
    for m in _PLAIN_MONTH_EN.finditer(text):
        out.append((int(m[2]) if m[2] else None, _EN_MONTHS[m[1]], None))
    for m in words.month.finditer(text):  # type: ignore[union-attr]
        name, year = m[1] or m[3], m[2] or m[4]
        out.append((int(year) if year else None, words.months[name], None))
    for m in _YEAR_SAID.finditer(text):
        out.append((int(m[1]), None, None))
    for pattern in (_DOM_EN, _DOM_AR):
        out += [(None, None, int(m[1])) for m in pattern.finditer(text)]
    for phrase, (month, dom) in _FIXED_DAYS.items():
        if phrase in text:
            out.append((None, month, dom))
    return out


def says_day(text: str, day: str, said_on: str | None) -> bool:
    """Whether *text*, told on *said_on*, says *day* (``YYYY-MM-DD``, or a
    month or a year): writes it out ("3 March 2019", "٣ مارس"), names its
    weekday near the turn ("last Saturday", "السبت اللي فات"), or counts to
    it ("yesterday", "two weeks ago", "قبل أسبوعين"), in English or Arabic.
    A word list and a calendar, no model.

    Not merely that it names a day: "check this email I got last week" names
    one, and the email's own "3 March 2019" passed for the user's day (the
    second review of W4, 2026-11-24)."""
    folded = _folded(text)
    if not folded or not day:
        return False
    want = tuple(int(p) for p in str(day).split("-"))
    year, month, dom = (*want, 0, 0)[:3]
    for y, m, d in _dates_said(folded):
        if y is not None and y != year:
            continue
        if m is not None and month and m != month:
            continue
        if d is not None and dom and d != dom:
            continue
        if not dom and d is not None and m is None:
            continue  # "the 23rd" says no month and no year
        if not month and y is None:
            continue
        if dom and d is None and m is None:
            continue  # "2018" says no day of it
        return True
    said, first = _as_date(said_on), _as_date(f"{year:04d}-{month or 1:02d}-{dom or 1:02d}")
    if said is None or first is None:
        return False
    if not dom:
        last = date(year + (month == 12 or not month), month % 12 + 1 if month else 1, 1) - timedelta(days=1)
    else:
        last = first
    # How many days before the turn it was: (nearest, farthest).
    near, far = (said - last).days, (said - first).days
    if dom and abs(near) <= 21 and first.weekday() in _weekdays_said(folded):
        return True
    if any(near <= -n <= far for n in _days_counted(folded)):
        return True
    weekend = any(w in folded for w in (*_WEEKEND_EN, *_said_words().weekend))
    if dom and weekend and abs(near) <= 14 and first.weekday() in (3, 4, 5, 6):
        return True
    for table in (_LAST_EN, _said_words().last):
        for phrase, back in table.items():
            lo, hi = _days_back(back, said)
            if phrase in folded and near <= hi and far >= lo:
                return True
    for count, unit in _counts_back(folded):
        slack = 1.0 if unit == 1.0 else unit * 0.75
        if near <= count * unit + slack and far >= count * unit - slack:
            return True
    return False


def _days_back(back: tuple[int, int] | str, said: date) -> tuple[int, int]:
    """The days back from *said* a period of :data:`_LAST_EN` covers."""
    if not isinstance(back, str):
        return back
    first = said.replace(day=1) if back.endswith("month") else said.replace(month=1, day=1)
    if back.startswith("this"):
        return 0, (said - first).days
    end = first - timedelta(days=1)
    start = end.replace(day=1) if back.endswith("month") else end.replace(month=1, day=1)
    return (said - end).days, (said - start).days


def _counts_back(folded: str) -> list[tuple[float, float]]:
    """"Two weeks ago", "قبل أسبوعين": (how many, the unit's days)."""
    out: list[tuple[float, float]] = []
    for m in _AGO_SAID_EN.finditer(folded):
        raw = m[1]
        out.append((float(raw) if raw.isdigit() else float(_AGO_NUMBERS_EN.get(raw, 1)), _EN_UNITS[m[2]]))
    for m in _AGO_SAID_AR.finditer(folded):
        if m[2] in _AR_DUALS:
            count, unit = _AR_DUALS[m[2]]
            out.append((float(count), unit))
        else:
            out.append((float(m[1]) if m[1] else 1.0, _AR_UNITS[m[2]]))
    return out
