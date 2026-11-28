"""The time a question asks about (framework audit F2, step 3; part F).

Recall matched "last week", "in March" and "before" as ordinary words: "What
did I tell you about Pixel in August?" ranked a May turn about Pixel above
the August one, "Where was I living in June?" showed where the user lives
now, and a month's name was a content word no memory held, which lowered the
word coverage of the memory that answered. :func:`read_time` reads the time
words of a question, in English and Arabic (Gulf and MSA), with no model:

* **A period**: "yesterday", "last week", "last month", "this month", "two
  weeks ago", "قبل أسبوعين", "قبل ثلاث أسابيع", "past 10 days", "the last 3
  weeks", "آخر عشر أيام", "الأيام العشرة الماضية", "this year", "السنة هذي",
  "هذه السنة", "هذا العام", "in August", "في يوليو", "بداية أغسطس",
  "بداية شهر أغسطس", "in 2025". Read only in a question about the past
  (``_PAST``: "did", "was", "told", "قلت", "كان"): "What is on March 22?"
  asks about a day ahead, not about what was said on one, and is left alone.
  A word that only shares those letters ("the last 3 emails", "قبل ثلاث
  صفحات", "السنة الدراسية") is not a period. A counted stretch includes
  today through now. A word that says when it was said ("yesterday",
  "last week", "two weeks ago", "أمس", "الأسبوع الماضي", "قبل أسبوعين",
  "in August", "في أغسطس", "ذكرت في شهر أغسطس", "August 2024", "in 2025")
  wins over a stretch that is only the topic; the stretch decides the
  window only when no when-said reader matched. One tied to a month ("the
  last 3 days of August", "آخر ٣ أيام من أغسطس", "آخر خمس أيام من الشهر
  الماضي") is that month's last days, and the "last month" that only
  closes it is not a when-said of its own. A stretch that is not tied
  keeps its own window. A month or a year after "about" / "عن", or after
  that stretch ("in the last 3 days about my trip in August", "in 2025"
  there), is left out of the time words, so recall keeps it as a search
  word. "كم" and
  "how many" are not counts, except "كم" right after "آخر" ("آخر كم يوم",
  "هالكم يوم", "الكم يوم اللي فاتت"), which is a few: the three days
  "قبل كم يوم" counts, read as "the last few days". A few tied to a noun
  that is not a time ("the last few weeks of school", "آخر كم أسبوع من
  المدرسة") is not a period.
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
from datetime import MAXYEAR, MINYEAR, UTC, date, datetime, timedelta
from typing import Any

__all__ = ["TIME_AFTER_EN", "TimeAsk", "is_weekday", "present_ar_words", "read_time", "says_day", "says_to_come",
           "says_today_or_yesterday"]

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
#: "past 10 days", "the last 3 weeks": a number is required, so "past week"
#: and "last week" stay on :data:`_RELATIVE_EN`, and "weekends" is not "weeks".
_SPAN_EN_WORDS = "|".join(sorted(
    ("a couple of", "a few", "few", "three", "seven", "eight", "four", "five", "nine", "one", "two", "six", "ten"),
    key=len, reverse=True,
))
_SPAN_EN = re.compile(
    rf"\b(?:the\s+)?(?:past|last)\s+(\d{{1,3}}|{_SPAN_EN_WORDS})\s+(days?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)
_MONTH_WORDS = "|".join(sorted(_EN_MONTHS, key=len, reverse=True))
#: "the last 3 days of August": the stretch is tied to the month it names.
_OF_MONTH_EN = re.compile(
    rf"\s+of\s+({_MONTH_WORDS})\b(?:\s+((?:19|20)\d\d))?",
    re.IGNORECASE,
)
#: "the last 5 days of last month": the stretch is tied to the month before
#: today, not to a month's name.
# One space: read_time and the fold collapse whitespace, and an unbounded
# \s+ searched over a run of tabs grows faster than the input (the sweep).
_OF_LAST_MONTH_EN = re.compile(r"\sof\slast\smonth\b", re.IGNORECASE)
#: "the last few weeks of school": "few" tied to a noun that is not a time.
#: A month ("of August", "of last month") is a time and is not one of these.
_OF_A_THING_EN = re.compile(r"\sof\b", re.IGNORECASE)
_FEW_EN = frozenset({"few", "a few"})
#: A month named in context ("in June", "June 2025", "3 June", "June 3"): a
#: bare "may" or "march" is a verb.
_MONTH_EN = re.compile(
    rf"\b(?:(?:in|during|on|since|until|through|of|from|by|early|mid|late)\s+({_MONTH_WORDS})\b"
    rf"(?:\s+(\d{{4}}))?|({_MONTH_WORDS})\s+(\d{{4}})\b|(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_WORDS})\b"
    rf"|({_MONTH_WORDS})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b)",
    re.IGNORECASE,
)
_YEAR_EN = re.compile(r"\b(?:in|during|since)\s+((?:19|20)\d\d)\b", re.IGNORECASE)
#: "in August", "during 2025", "August 2024": when it was said. "from",
#: "of" and "on" are not these: "from August" beside a stretch is the
#: topic, and "of August" ties the stretch. One space, as the other ties:
#: the question is already collapsed, and ``\s+`` over a run of tabs is
#: not linear.
_WHEN_SAID_IN_EN = ("in", "during", "since")
_WHEN_MONTH_EN = re.compile(
    rf"\b(?:in|during|since)\s({_MONTH_WORDS})\b(?:\s((?:19|20)\d\d))?"
    rf"|\b({_MONTH_WORDS})\s((?:19|20)\d\d)\b",
    re.IGNORECASE,
)
#: "في أغسطس", "خلال شهر أغسطس", "ب أغسطس". "من" is "from" / the tie.
_WHEN_SAID_IN_AR = ("خلال", "في", "ب")
#: "في 2025", "في عام 2025", "في سنة 2025" (folded "سنه"), on folded text.
_YEAR_AR = re.compile(
    r"(?:^|\s)في\s(?:عام\s|سنه\s)?((?:19|20)\d\d)(?=\s|$|[؟?.,،])"
)
_TOPIC_EN = re.compile(r"\babout\b", re.IGNORECASE)
_TOPIC_AR = re.compile(r"(?:^|\s)عن(?=\s|$|[؟?.,،])")
#: Letters ``fold_for_search`` folds away (alef with hamza or madda, taa
#: marbuta, alef maqsura): a month's name with one is not its folded form.
_FOLDED_AWAY = frozenset(map(chr, (0x0623, 0x0625, 0x0622, 0x0629, 0x0649)))
#: What was true before (the history of a fact).
#: "At the start" alone is "at first"; "at the start of" a period ("this
#: month", "the week") or a month reads that start, and "at the start of"
#: anything else (a story, a trip, Ramadan) names no time: one rule with the
#: Arabic "في البداية" / "في بداية ..." (the sixth follow-up review's L2).
_START_OF_EN = (rf"(?:at the start|in the beginning)(?! of\b)"
                rf"|(?:at the start|in the beginning) of(?= (?:this|the|last) (?:month|week|year)\b"
                rf"| (?:{_MONTH_WORDS})\b)")
_HISTORY_EN = re.compile(
    rf"\b(?:at first|{_START_OF_EN}|originally|original|used to|previously|"
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
    r"قبل ما|قبل لا|قبل التغيير|قبل التاجيل|"
    # "at the start of" a period, as English "at the start of this month"
    # is: "في بداية هذا الشهر", "أول هذا الشهر" (the step 5 follow-up's L3);
    # only before a period's word made definite ("الشهر", "هالشهر", "هذا
    # الشهر"), never a bare "بداية", "أول هذا الشي" (the fifth review's L-a)
    # or "أول أسبوع" ("the first week": benchmark q1402)
    r"(?:في بدايه|ببدايه|بدايه|في اول|باول|اول)"
    r"(?= (?:هذا |هذي |هذه )?(?:ال|هال)(?:شهر|اسبوع|سنه|عام)(?:\s|$|[؟?.,،])"
    # ... or a month's name, as English "at the start of January" is.
    # "شهر" may sit between the word and the month ("بداية شهر أغسطس").
    rf"| (?:شهر )?(?:{'|'.join(k for k in _AR_MONTHS_RAW if not set(k) & _FOLDED_AWAY)})(?:\s|$|[؟?.,،])))"
    r"(?=\s|$|[؟?.,،])"
)
#: Folded, the masculine and the feminine; "a few" as English counts it.
#: Longest first where one word begins another ("ثلاثه" before "ثلاث").
_AGO_NUMBERS_AR = {"ثلاثه": 3, "ثلاث": 3, "اربعه": 4, "اربع": 4, "خمسه": 5, "خمس": 5, "سته": 6, "ست": 6, "سبعه": 7,
                   "سبع": 7, "ثمانيه": 8, "ثماني": 8, "ثمان": 8, "تسعه": 9, "تسع": 9, "عشره": 10, "عشر": 10, "كم": 3,
                   "بضعه": 3, "عده": 3, "كذا": 3}
_AR_NUMBER = "|".join(sorted(_AGO_NUMBERS_AR, key=len, reverse=True))
#: "كم" (how many) and "كذا" count as a few after "قبل" ("قبل كم يوم").
#: In a counted stretch they are the question, not the count. "كم" right
#: after "آخر" is the exception, and it lives in :data:`_SPAN_AR_FEW`.
_AR_QUESTION_NUMBERS = frozenset({"كم", "كذا"})
_AR_SPAN_NUMBER = "|".join(sorted(
    (word for word in _AGO_NUMBERS_AR if word not in _AR_QUESTION_NUMBERS), key=len, reverse=True))
_AR_UNIT = "|".join(sorted(
    ("يومين", "اسبوعين", "شهرين", "سنتين", "ايام", "اسابيع", "شهور", "اشهر", "سنوات", "سنين",
     "يوم", "اسبوع", "شهر", "سنه"),
    key=len, reverse=True,
))
_AR_AGO = re.compile(
    r"(?:^|\s)قبل\s+(?:حوالي\s+)?"
    rf"(?:({_AR_NUMBER}|\d{{1,3}})\s+)?"
    rf"({_AR_UNIT})(?=\s|$|[؟?.,،])"
)
_AR_UNITS = {"يوم": 1.0, "ايام": 1.0, "اسبوع": 7.0, "اسابيع": 7.0, "شهر": 30.0, "شهور": 30.0,
             "اشهر": 30.0, "سنه": 365.0, "سنين": 365.0, "سنوات": 365.0}
_AR_DUALS = {"يومين": (2, 1.0), "اسبوعين": (2, 7.0), "شهرين": (2, 30.0), "سنتين": (2, 365.0)}
#: "This year", Gulf and MSA, including the phrase with the year first
#: ("السنة هذي", "العام هذا"). A bare "العام" is not one of them.
_THIS_YEAR = frozenset({
    "this year", "هالسنه", "هذي السنه", "هذه السنه", "السنه هذي", "السنه هذه",
    "هذا العام", "العام هذا", "هالعام",
})
_AR_RELATIVE = re.compile(
    r"(?:^|\s)(امس|البارحه|الاسبوع الماضي|الاسبوع اللي فات|الاسبوع اللي طاف|الاسبوع السابق|"
    r"الشهر الماضي|الشهر اللي فات|الشهر اللي طاف|الشهر السابق|السنه الماضيه|السنه اللي فاتت|"
    r"العام الماضي|هالاسبوع|هذا الاسبوع|هالشهر|هذا الشهر|هالسنه|هذي السنه|هذه السنه|"
    r"السنه هذي|السنه هذه|هذا العام|العام هذا|هالعام)(?=\s|$|[؟?.,،])"
)
#: A counted stretch ending today: "آخر عشر أيام", "الأيام العشرة الماضية",
#: "ثلاث أسابيع اللي فاتت". A number is required, so "آخر شهر أغسطس" stays
#: August and "الأسبوع الماضي" stays last week. A dual needs no number
#: ("آخر أسبوعين"). Fixed words and one bounded number: linear.
_AR_SPAN_PLURAL = "|".join(sorted(("ايام", "اسابيع", "شهور", "اشهر", "سنوات", "سنين"), key=len, reverse=True))
_SPAN_EDGE = r"(?=\s|$|[؟?.,،!])"
_SPAN_AR_LAST = re.compile(
    rf"(?:^|\s)اخر\s+(?:({_AR_SPAN_NUMBER}|\d{{1,3}})\s+)?({_AR_UNIT}){_SPAN_EDGE}"
)
_SPAN_AR_MSA = re.compile(
    rf"(?:^|\s)(?:ال)?({_AR_SPAN_PLURAL})\s+(?:ال)?({_AR_SPAN_NUMBER}|\d{{1,3}})\s+"
    rf"(?:الماضيه|الماضي){_SPAN_EDGE}"
)
_SPAN_AR_GONE = re.compile(
    rf"(?:^|\s)(?:اخر\s+)?({_AR_SPAN_NUMBER}|\d{{1,3}})\s+({_AR_UNIT})\s+"
    rf"(?:اللي\s+)?(?:فاتت|فات|طافت|طاف){_SPAN_EDGE}"
)
#: "كم" directly after "آخر" is "a few", not the question: "آخر كم يوم",
#: "هالكم يوم", "الكم يوم اللي فاتت". "كم يوم فات" (no آخر) stays the question
#: and is not one of these. The count is the 3 of "قبل كم يوم".
_SPAN_AR_FEW = (
    re.compile(rf"(?:^|\s)اخر\s+كم\s+({_AR_UNIT}){_SPAN_EDGE}"),
    re.compile(rf"(?:^|\s)هالكم\s+({_AR_UNIT}){_SPAN_EDGE}"),
    re.compile(
        rf"(?:^|\s)الكم\s+({_AR_UNIT})\s+(?:اللي\s+)?(?:فاتت|فات|طافت|طاف){_SPAN_EDGE}"
    ),
)
#: "آخر خمس أيام من الشهر الماضي": the stretch is tied to the previous month.
#: The same phrases :func:`_last_month_phrases` skips when they only close it.
_LAST_MONTH_AR = (
    "الشهر الماضي", "الشهر اللي فات", "الشهر اللي طاف", "الشهر اللي راح", "الشهر السابق",
)
_OF_LAST_MONTH_AR = re.compile(r"\sمن\s(?:" + "|".join(_LAST_MONTH_AR) + ")" + _SPAN_EDGE)
#: "آخر كم أسبوع من المدرسة": كم-as-a-few tied to a noun that is not a time.
_OF_A_THING_AR = re.compile(r"\sمن(?=\s|$)")

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
#: Counted stretches too ("the last 3 weeks"), non-capturing: this fragment
#: is interpolated into the quick pass's place ending. A number is required,
#: so "last month" stays the alternative below it.
_SPAN_EN_TIME = (
    r"(?:the\s+)?(?:past|last)\s+"
    r"(?:\d{1,3}|a\s+couple\s+of|a\s+few|few|ten|nine|eight|seven|six|five|four|three|two|one)\s+"
    r"(?:days?|weeks?|months?|years?)"
)
TIME_AFTER_EN = (
    r"right\s+now|nowadays|now|currently|these\s+days|at\s+the\s+moment|at\s+present|for\s+now|lately|"
    r"recently|today|yesterday|" + _SPAN_EN_TIME + r"|(?:last|this|past)\s+(?:week|month|year)|"
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
    # "بداية يناير", "ببداية يناير", "أول يناير" read January, as "at the
    # start of January" does (the seventh follow-up review's L-b)
    return re.compile(rf"(?:^|\s)(?:في|ب|خلال|من|لين|الي|شهر|بدايه|ببدايه|اول)\s+(?:شهر\s+)?({names})(?:\s+(\d{{4}}))?"
                      rf"(?=\s|$|[؟?.,،])")


_AR_MONTH_RE: list[re.Pattern[str]] = []


def _ar_month_re() -> re.Pattern[str]:
    if not _AR_MONTH_RE:
        _AR_MONTH_RE.append(_ar_month_pattern())
    return _AR_MONTH_RE[0]


_WHEN_MONTH_AR: list[re.Pattern[str]] = []


def _when_month_ar() -> re.Pattern[str]:
    """"في أغسطس" / "خلال شهر أغسطس" on folded text. "من" is not when-said."""
    if not _WHEN_MONTH_AR:
        if not _AR_MONTH_CACHE:
            _AR_MONTH_CACHE.update(_ar_months())
        names = "|".join(sorted(_AR_MONTH_CACHE, key=len, reverse=True))
        prefixes = "|".join(_WHEN_SAID_IN_AR)
        _WHEN_MONTH_AR.append(re.compile(
            rf"(?:^|\s)(?:{prefixes})\s(?:شهر\s)?({names})(?:\s((?:19|20)\d\d))?(?=\s|$|[؟?.,،])"
        ))
    return _WHEN_MONTH_AR[0]


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


def _previous_month(today: datetime) -> tuple[int, int]:
    """The month before *today*, as ``(month, year)``."""
    if today.month == 1:
        return 12, today.year - 1
    return today.month - 1, today.year


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
    # This year: the phrases in _THIS_YEAR ("هالسنه", "السنة هذي", "هذه السنة",
    # "هذا العام"). The patterns above are the only callers, and each of
    # them names this year.
    return datetime(today.year, 1, 1, tzinfo=zone).timestamp(), now + 1.0


def _ago(count: float, unit_days: float, now: float) -> tuple[float, float]:
    """"N units ago": the unit around that point."""
    centre = now - count * unit_days * DAY
    half = unit_days * DAY / 2 + DAY
    return centre - half, centre + half


def _span_window(count: float, unit_days: float, today: datetime, now: float) -> tuple[float, float]:
    """"past N days", "the last 3 weeks": that many units back from today's
    start, through now. Today is in it, as "this week" runs to now + 1."""
    start_today = _day_start(today)
    return (start_today - timedelta(days=count * unit_days)).timestamp(), now + 1.0


def _counted(raw: str | None, unit: str, *, arabic: bool) -> tuple[float, float] | None:
    """A counted stretch's (how many, the unit's days). None when it names
    no count ("آخر شهر", which is a month, not a stretch)."""
    if arabic:
        if unit in _AR_DUALS:
            return None if raw else (float(_AR_DUALS[unit][0]), _AR_DUALS[unit][1])
        if not raw or unit not in _AR_UNITS or raw in _AR_QUESTION_NUMBERS:
            return None
        if raw.isdigit():
            count = float(raw)
        elif raw in _AGO_NUMBERS_AR:
            count = float(_AGO_NUMBERS_AR[raw])
        else:
            return None
        return count, _AR_UNITS[unit]
    if not raw:
        return None
    key = raw.lower()
    if key.isdigit():
        count = float(key)
    elif key in _AGO_NUMBERS_EN:
        count = float(_AGO_NUMBERS_EN[key])
    else:
        return None
    unit_key = unit.lower()
    if unit_key not in _EN_UNITS:
        return None
    return count, _EN_UNITS[unit_key]


_OF_MONTH_AR: list[re.Pattern[str]] = []


def _of_month_ar() -> re.Pattern[str]:
    """"من أغسطس" / "من شهر أغسطس" after a counted stretch, on folded text."""
    if not _OF_MONTH_AR:
        if not _AR_MONTH_CACHE:
            _AR_MONTH_CACHE.update(_ar_months())
        names = "|".join(sorted(_AR_MONTH_CACHE, key=len, reverse=True))
        _OF_MONTH_AR.append(re.compile(
            rf"\s+من\s+(?:شهر\s+)?({names})(?:\s+((?:19|20)\d\d))?(?=\s|$|[؟?.,،])"))
    return _OF_MONTH_AR[0]


def _has_month_tie(text: str, end: int, *, arabic: bool) -> bool:
    """Whether a counted stretch ending at *end* is tied to a month."""
    if arabic:
        return _OF_LAST_MONTH_AR.match(text, end) is not None or _of_month_ar().match(text, end) is not None
    return _OF_LAST_MONTH_EN.match(text, end) is not None or _OF_MONTH_EN.match(text, end) is not None


def _month_tied(text: str, end: int, *, arabic: bool, today: datetime) -> tuple[int, int | None] | None:
    """The month a counted stretch is of, or none.

    "of August" / "من أغسطس" is that month. "of last month" / "من الشهر
    الماضي" is the month before *today*."""
    if arabic and _OF_LAST_MONTH_AR.match(text, end):
        return _previous_month(today)
    if not arabic and _OF_LAST_MONTH_EN.match(text, end):
        return _previous_month(today)
    found = (_of_month_ar() if arabic else _OF_MONTH_EN).match(text, end)
    if found is None:
        return None
    if arabic:
        return _AR_MONTH_CACHE[found.group(1)], int(found.group(2)) if found.group(2) else None
    return _EN_MONTHS[found.group(1).lower()], int(found.group(2)) if found.group(2) else None


def _ar_few_matches(text: str):
    """"آخر كم يوم", "هالكم يوم", "الكم يوم اللي فاتت": كم is a few (3).

    "كم يوم فات" is the question and is not one of these. The 3 is the count
    "قبل كم يوم" already uses; here it is the last few days."""
    for pattern in _SPAN_AR_FEW:
        for match in pattern.finditer(text):
            unit = match.group(1)
            if unit in _AR_UNITS:
                yield match, (3.0, _AR_UNITS[unit])


def _few_of_a_thing(text: str, match: re.Match[str], *, arabic: bool) -> bool:
    """Whether *match* is a few tied to a noun that is not a time.

    "the last few weeks of school", "آخر كم سنة من وظيفتي" name no period.
    "of August" / "من أغسطس" / "of last month" is a time and stays. A
    numbered stretch ("the last 3 weeks of school") is not a few."""
    if _has_month_tie(text, match.end(), arabic=arabic):
        return False
    if arabic:
        return _OF_A_THING_AR.match(text, match.end()) is not None
    if match.group(1).lower() not in _FEW_EN:
        return False
    return _OF_A_THING_EN.match(text, match.end()) is not None


def _counted_spans(text: str, *, arabic: bool):
    """Each counted stretch in *text*, with its (how many, the unit's days).

    A few tied to a noun that is not a time is left out."""
    if not arabic:
        for match in _SPAN_EN.finditer(text):
            window = _counted(match.group(1), match.group(2), arabic=False)
            if window and not _few_of_a_thing(text, match, arabic=False):
                yield match, window
        return
    for pattern, number_at, unit_at in (
        (_SPAN_AR_LAST, 1, 2), (_SPAN_AR_GONE, 1, 2), (_SPAN_AR_MSA, 2, 1),
    ):
        for match in pattern.finditer(text):
            window = _counted(match.group(number_at), match.group(unit_at), arabic=True)
            if window:
                yield match, window
    for match, window in _ar_few_matches(text):
        if not _few_of_a_thing(text, match, arabic=True):
            yield match, window


def _tied_month_names(text: str) -> list[tuple[int, int]]:
    """Spans of the month name that closes a counted stretch.

    "of August" there names the stretch's month, not the whole of August.
    "في أغسطس عن آخر ٣ أيام" does not: the month stands on its own."""
    found: list[tuple[int, int]] = []
    for arabic in (False, True):
        tail_re = _of_month_ar() if arabic else _OF_MONTH_EN
        for match, _window in _counted_spans(text, arabic=arabic):
            tail = tail_re.match(text, match.end())
            if tail is not None:
                found.append(tail.span(1))
    return found


def _closes_counted_stretch(at: int, spans: list[tuple[int, int]]) -> bool:
    """Whether *at* sits inside a month name from :func:`_tied_month_names`."""
    for start, end in spans:
        if start <= at < end:
            return True
    return False


def _last_of_month(
    count: float, unit_days: float, month: int, year: int | None, today: datetime, zone: Any,
) -> tuple[float, float]:
    """The last *count* units of *month*: ending when the month ends."""
    end = _latest_month(month, year, today, zone)[1]
    return end - count * unit_days * DAY, end


def _span_inside(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    """Whether ``[start, end)`` sits inside one of *spans*."""
    for a, b in spans:
        if a <= start and end <= b:
            return True
    return False


def _last_month_tie_spans(text: str, *, arabic: bool) -> list[tuple[int, int]]:
    """Spans of "of last month" / "من الشهر الماضي" that close a counted stretch."""
    tail_re = _OF_LAST_MONTH_AR if arabic else _OF_LAST_MONTH_EN
    found: list[tuple[int, int]] = []
    for match, _window in _counted_spans(text, arabic=arabic):
        tail = tail_re.match(text, match.end())
        if tail is not None:
            found.append(tail.span())
    return found


def _note_counted(
    text: str, folded: str, today: datetime, now: float, zone: Any, took: Any,
) -> None:
    """The counted stretches of *text*, each handed to *took*.

    One tied to a month ("of August", "من أغسطس", "من الشهر الماضي") is that
    month's last days. A stretch that is not tied keeps its own window
    through now. The when-said readers run first and win; this sets the
    window only when they matched nothing. A month named beside an untied
    stretch is not added here."""
    for source, arabic in ((text, False), (folded, True)):
        for match, window in _counted_spans(source, arabic=arabic):
            tied = _month_tied(source, match.end(), arabic=arabic, today=today)
            if tied:
                took(*_last_of_month(*window, tied[0], tied[1], today, zone), match.group(0).strip())
            else:
                took(*_span_window(*window, today, now), match.group(0).strip())


def _year_span(year: int, zone: Any) -> tuple[float, float]:
    """1 January of *year* through 1 January of the next."""
    return (datetime(year, 1, 1, tzinfo=zone).timestamp(),
            datetime(year + 1, 1, 1, tzinfo=zone).timestamp())


def _topic_cut(text: str, *, arabic: bool) -> int | None:
    """Where the topic starts, or None when *text* names no topic.

    "about" / "عن", or a counted stretch, whichever comes first. A month
    before this, attached to the saying, says when it was said."""
    cut: int | None = None
    for match, _window in _counted_spans(text, arabic=arabic):
        cut = match.start()
        break
    marker = (_TOPIC_AR if arabic else _TOPIC_EN).search(text)
    if marker is not None and (cut is None or marker.start() < cut):
        return marker.start()
    return cut


def _before_topic(at: int, cut: int | None) -> bool:
    """Whether *at* sits before the topic, including when there is none."""
    if cut is None:
        return True
    return at < cut


def _en_when_month(match: re.Match[str]) -> tuple[str, int | None]:
    """The month and year an English when-said month match names."""
    name = match.group(1) or match.group(3)
    raw = match.group(2) or match.group(4)
    return name, int(raw) if raw else None


def _take_when_said_calendar(
    text: str, folded: str, today: datetime, zone: Any, took: Any,
) -> None:
    """"in August", "في أغسطس", "August 2024", "in 2025": when it was said.

    These win over a counted stretch, as "last week" does, and are the
    period. One after "about" / "عن" or after the stretch is the topic and
    is left for :func:`_take_months`, which keeps it a search word when the
    stretch owns the window. Within one language the earlier phrase wins."""
    en_cut = _topic_cut(text, arabic=False)
    ar_cut = _topic_cut(folded, arabic=True)
    english: list[tuple[int, float, float, str]] = []
    for match in _WHEN_MONTH_EN.finditer(text):
        if not _before_topic(match.start(), en_cut):
            continue
        name, year = _en_when_month(match)
        start, end = _latest_month(_EN_MONTHS[name.lower()], year, today, zone)
        english.append((match.start(), start, end, match.group(0)))
    for match in _YEAR_EN.finditer(text):
        if not _before_topic(match.start(), en_cut):
            continue
        start, end = _year_span(int(match.group(1)), zone)
        english.append((match.start(), start, end, match.group(0)))
    english.sort(key=lambda item: item[0])
    for _at, start, end, words in english:
        took(start, end, words)
    arabic: list[tuple[int, float, float, str]] = []
    for match in _when_month_ar().finditer(folded):
        if not _before_topic(match.start(), ar_cut):
            continue
        raw = match.group(2)
        start, end = _latest_month(
            _AR_MONTH_CACHE[match.group(1)], int(raw) if raw else None, today, zone)
        arabic.append((match.start(), start, end, match.group(0).strip()))
    for match in _YEAR_AR.finditer(folded):
        if not _before_topic(match.start(), ar_cut):
            continue
        start, end = _year_span(int(match.group(1)), zone)
        arabic.append((match.start(), start, end, match.group(0).strip()))
    arabic.sort(key=lambda item: item[0])
    for _at, start, end, words in arabic:
        took(start, end, words)


def _recorded_when_said(
    pattern: re.Pattern[str], at: int, cut: int | None, text: str,
) -> bool:
    """Whether the when-said reader already took *pattern* at *at*."""
    if not _before_topic(at, cut):
        return False
    return pattern.match(text, at) is not None


def _note_loose_years(
    text: str, folded: str, zone: Any, took: Any, *, stretch_owns: bool,
) -> None:
    """A year that is the period and was not already read as when-said.

    When the stretch owns the window, the year is the topic and stays a
    search word ("in the last 3 days about the trip in 2025")."""
    if stretch_owns:
        return
    en_cut = _topic_cut(text, arabic=False)
    ar_cut = _topic_cut(folded, arabic=True)
    for match in _YEAR_EN.finditer(text):
        if _before_topic(match.start(), en_cut):
            continue
        took(*_year_span(int(match.group(1)), zone), match.group(0))
    for match in _YEAR_AR.finditer(folded):
        if _before_topic(match.start(), ar_cut):
            continue
        took(*_year_span(int(match.group(1)), zone), match.group(0).strip())


def _take_when_said(
    text: str, folded: str, today: datetime, now: float, zone: Any, took: Any,
) -> None:
    """When it was said: "yesterday", "last week", "قبل أسبوعين".

    These win over a counted stretch that is only the topic. A "last month"
    that only closes a stretch ("the last 5 days of last month") is not one
    of them: its words are kept and its window is not, so the stretch still
    names that month's last days."""
    en_ties = _last_month_tie_spans(text, arabic=False)
    ar_ties = _last_month_tie_spans(folded, arabic=True)
    for m in _RELATIVE_EN.finditer(text):
        closes = _span_inside(m.start(1), m.end(1), en_ties)
        took(*_relative(m.group(1).lower(), today, now, zone), m.group(1), window=not closes)
    for m in _AR_RELATIVE.finditer(folded):
        closes = _span_inside(m.start(1), m.end(1), ar_ties)
        took(*_relative(m.group(1), today, now, zone), m.group(1), window=not closes)
    for m in _AGO_EN.finditer(text):
        raw = m.group(1).lower()
        count = float(raw) if raw.isdigit() else float(_EN_NUMBERS.get(raw, 1))
        took(*_ago(count, _EN_UNITS[m.group(2).lower()], now), m.group(0))
    for m in _AR_AGO.finditer(folded):
        unit = m.group(2)
        raw = m.group(1)
        if unit in _AR_DUALS:
            count, unit_days = _AR_DUALS[unit]
        elif raw and not raw.isdigit():
            count, unit_days = float(_AGO_NUMBERS_AR[raw]), _AR_UNITS[unit]
        else:
            count, unit_days = (float(raw) if raw else 1.0), _AR_UNITS[unit]
        took(*_ago(float(count), unit_days, now), m.group(0).strip())


def _month_name_at(match: re.Match[str], groups: tuple[int, ...]) -> int:
    """Where *match* names the month, or -1 when it does not."""
    for i in groups:
        if match.group(i):
            return match.start(i)
    return -1


def _take_months(
    text: str, folded: str, today: datetime, zone: Any, took: Any, *, stretch_owns: bool,
) -> None:
    """A named month or a year that the when-said reader did not take.

    When a counted stretch already took the window, a month that does not
    close that stretch was overridden. It stays a search word and is not
    added ("in the last 3 days about my trip in August"). The month that
    closes the stretch ("of August") is the period and is added. A year the
    stretch overrode stays a search word too ("about the trip in 2025")."""
    tied_en = _tied_month_names(text)
    tied_ar = _tied_month_names(folded)
    en_cut = _topic_cut(text, arabic=False)
    ar_cut = _topic_cut(folded, arabic=True)
    when_ar = _when_month_ar()
    for m in _MONTH_EN.finditer(text):
        at = _month_name_at(m, (1, 3, 6, 7))
        if stretch_owns and not _closes_counted_stretch(at, tied_en):
            continue
        if _recorded_when_said(_WHEN_MONTH_EN, m.start(), en_cut, text):
            continue
        name = next(g for g in (m.group(1), m.group(3), m.group(6), m.group(7)) if g)
        year = m.group(2) or m.group(4)
        month = _EN_MONTHS[name.lower()]
        took(*_latest_month(month, int(year) if year else None, today, zone), m.group(0))
    for m in _ar_month_re().finditer(folded):
        if stretch_owns and not _closes_counted_stretch(m.start(1), tied_ar):
            continue
        if _recorded_when_said(when_ar, m.start(), ar_cut, folded):
            continue
        took(*_latest_month(_AR_MONTH_CACHE[m.group(1)], int(m.group(2)) if m.group(2) else None,
                            today, zone), m.group(0).strip())
    _note_loose_years(text, folded, zone, took, stretch_owns=stretch_owns)


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

    def took(a: float, b: float, words: str, *, window: bool = True) -> None:
        nonlocal start, end
        if window and start is None:
            start, end = a, b
        spans.append(words)

    if past:
        # When it was said (a relative, an ago, then "in August" / "in
        # 2025"), then a counted stretch only if nothing set the window,
        # then a month or year that is the period. A "last month" that only
        # closes a stretch does not set the window (_take_when_said). A
        # month or year the stretch overrode stays a search word.
        _take_when_said(text, folded, today, now, zone, took)
        _take_when_said_calendar(text, folded, today, zone, took)
        held = start is not None
        _note_counted(text, folded, today, now, zone, took)
        _take_months(text, folded, today, zone, took, stretch_owns=not held and start is not None)
    history = bool(_HISTORY_EN.search(text) or _HISTORY_AR.search(folded))
    for m in _HISTORY_EN.finditer(text):
        spans.append(m.group(0))
    # The Arabic words too: they are the time asked about, not what a memory
    # says (English's were; "في بداية هذا الشهر" kept "بداية" as a topic).
    for m in _HISTORY_AR.finditer(folded):
        spans.append(m.group(0).strip())
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
                    "الخميس": 3, "الجمعة": 4, "السبت": 5, "الأحد": 6, "الربوع": 2, "الاربع": 2, "الثلوث": 1}
#: A weekday without its article is one only beside a word that says so:
#: "خميس" is also a name, "احد" is "someone".
_BARE_WEEKDAY_AR = re.compile(
    r"(?:(?<!\w)(?:يوم|ليله)\s+)(اثنين|ثلاثاء?|اربعاء?|ربوع|خميس|جمعه|سبت|احد)(?!\w)"
    r"|(?<!\w)(اثنين|ثلاثاء?|اربعاء?|ربوع|خميس|جمعه|سبت|احد)"
    r"(?=\s+(?:الماضي|الماضيه|اللي|الفايت|الفايته|الفائت|السابق|القادم|القادمه|الجاي|الجايه|هذا|هذي)(?!\w))")
#: Days counted from the turn's own, as written; folded before they are read.
_SAID_DAYS_EN = {"today": 0, "tonight": 0, "this morning": 0, "this afternoon": 0, "this evening": 0,
                 "yesterday": -1, "last night": -1, "the day before yesterday": -2, "day before yesterday": -2,
                 "tomorrow": 1, "the day after tomorrow": 2, "day after tomorrow": 2}
_SAID_DAYS_AR_RAW = {"اليوم": 0, "الليلة": 0, "هاليوم": 0, "أمس": -1, "امبارح": -1, "مبارح": -1, "البارحة": -1,
                     "البارح": -1, "أول أمس": -2, "أمس الأول": -2, "أول امبارح": -2, "قبل أمس": -2, "بكرة": 1,
                     "باجر": 1, "باكر": 1, "غدا": 1, "أول البارحة": -2, "أول البارح": -2, "قبل البارحة": -2,
                     "قبل البارح": -2, "هالصبح": 0, "الصبح": 0, "هالليلة": 0, "أمس الصبح": -1, "البارحة الصبح": -1,
                     "بكرة الصبح": 1, "بعد بكرة": 2, "عقب بكرة": 2, "بعد باجر": 2, "عقب باجر": 2, "بعد باكر": 2, "بعد غد": 2,
                 "عقب باكر": 2}
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
                "العام الماضي": "last year", "هالسنة": "this year", "هذي السنة": "this year",
                "السنة هذي": "this year", "السنة هذه": "this year", "هذه السنة": "this year",
                "هذا العام": "this year", "العام هذا": "this year", "هالعام": "this year"}
_FIXED_DAYS = {"christmas eve": (12, 24), "christmas": (12, 25), "new year's eve": (12, 31), "new year": (1, 1),
               "valentine": (2, 14), "halloween": (10, 31), "الكريسماس": (12, 25), "راس السنه": (1, 1)}
_AGO_NUMBERS_EN = {**_EN_NUMBERS, "couple of": 2, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_AGO_SAID_EN = re.compile(
    r"\b(\d{1,3}|a couple of|couple of|a few|an|a|one|two|three|four|five|six|seven|eight|nine|ten|few) "
    r"(days?|weeks?|months?|years?) (?:ago|back)\b")
_AGO_SAID_AR = re.compile(
    r"(?:^|\s)(?:و|ف)?(?:قبل|من|صار لي|صارلي)\s+(?:حوالي\s+)?"
    r"(?:(\d{1,3}|" + "|".join(_AGO_NUMBERS_AR) + r")\s+)?(يومين|اسبوعين|شهرين|سنتين|يوم|ايام|"
    r"اسبوع|اسابيع|شهر|شهور|اشهر|سنه|سنين|سنوات)(?=\s|$|[؟?.,،!])")
#: A day of a month, a month, a year; a day of the month alone ("the 23rd").
_NUM_MONTH_EN = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?: of)? ({_MONTH_WORDS})\b(?:,? ((?:19|20)\d\d))?")
_MONTH_NUM_EN = re.compile(rf"\b({_MONTH_WORDS}) (\d{{1,2}})(?:st|nd|rd|th)?\b(?:,? ((?:19|20)\d\d))?")
_NUMERIC_DAY = re.compile(r"\b((?:19|20)\d\d)-(\d{1,2})(?:-(\d{1,2}))?\b|\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b")
_YEAR_SAID = re.compile(r"\b((?:19|20)\d\d)\b")
_DOM_EN = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b")
_DOM_AR = re.compile(r"يوم (\d{1,2})(?!\d)")
#: A month by its number: "شهر ٥".
_MONTH_NUMBER_AR = re.compile(r"(?<!\w)(?:[بفو])?شهر (\d{1,2})(?!\d)(?:\s+((?:19|20)\d\d))?")
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
    for m in _BARE_WEEKDAY_AR.finditer(text):
        bare = m[1] or m[2]
        out.update(n for name, n in arabic.items() if name == "ال" + bare)
    return out


def is_weekday(word: str) -> bool:
    """Whether *word* (folded) is an Arabic weekday, with its article."""
    return word in _said_words().weekdays


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
    tied = _tied_month_names(text)
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
        if _closes_counted_stretch(m.start(1), tied):
            continue
        out.append((int(m[2]) if m[2] else None, _EN_MONTHS[m[1]], None))
    for m in words.month.finditer(text):  # type: ignore[union-attr]
        name, year = m[1] or m[3], m[2] or m[4]
        at = m.start(1) if m[1] else m.start(3)
        if _closes_counted_stretch(at, tied):
            continue
        out.append((int(year) if year else None, words.months[name], None))
    for m in _MONTH_NUMBER_AR.finditer(text):
        if 1 <= int(m[1]) <= 12:
            out.append((int(m[2]) if m[2] else None, int(m[1]), None))
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
    if not MINYEAR < year < MAXYEAR:
        return False  # the calendar ends there ("9999": the review of 0bbecc655)
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
    # A tied stretch says the days of its own month. Another phrase in the
    # same text ("two weeks ago", "last month") still says its day.
    if _day_of_tied_month(folded, day, said_on):
        return True
    if _last_period_says(folded, said, near, far):
        return True
    for count, unit in _counts_back(folded):
        slack = 1.0 if unit == 1.0 else unit * 0.75
        if near <= count * unit + slack and far >= count * unit - slack:
            return True
    # A counted stretch is the window it covers, not a point: "past 10 days"
    # says today and the day ten back, and not the day before that.
    if _in_counted_span(folded, near, far):
        return True
    return False


#: A named day, no weekday: "Black Friday" told in May is last November's.
_NAMED_FRIDAYS = re.compile(r"\b(?:black|good|white) friday\b|\bcyber monday\b"
                            r"|الجمعه (?:البيضاء|البيضا|السوداء|السودا|العظيمه)")
#: Words that say it is past ("last Friday", "الجمعة اللي فاتت").
_PAST_MARK = re.compile(r"\b(?:last|past|ago|yesterday|earlier|previous|recently|already)\b"
                        r"|(?<!\w)(?:امس|البارح|البارحه|امبارح|الماضي|الماضيه|فات|فاتت|طاف|طافت|اللي راح|"
                        r"اللي راحت|السابق|السابقه)(?!\w)")
#: "I attended", "we just got", "بعت", "كنت رايح": what is told is done,
#: whatever day it names.
_TOLD_DONE = re.compile(r"\b(?:i|we)(?:'ve| have| had| just| also| actually| finally| only){0,3} "
                        r"(?:\w{2,12}ed|got|bought|went|had|made|met|ran|saw|sold|took|won|found|ate|came|did)\b"
                        r"|(?<!\w)[وف]?(?:(?:كن|بع|شري|اشتري|رح|ذهب|زر|حضر|خلص|انهي|قابل|التقي|شف|راي|سافر|دفع|استلم|حصل|"
                        r"فز|اخذ|خذي|سوي|عمل|فعل|قدم|وصل|رجع|عد|كمل|اكمل|تخرج|تزوج|انتقل|بدي|بدا|ركب|جري|اكل|طلع|دخل|"
                        r"حجز|سجل|قرر|لقي|وجد)(?:ت|نا)(?:ه|ها|هم)?|كان|كانت)(?!\w)")
#: Words that say it is to come: "I will", "I'm visiting", "راح أبيع", "بزور".
_TO_COME = re.compile(
    r"\b(?:will|shall|gonna|upcoming|next|coming|planning|plan to|about to|scheduled|booked|intend to|intending to)\b"
    r"|\b(?:i|we|it|he|she|they)'ll\b"
    r"|\b(?:i'm|i am|we're|we are) (?:going|\w{2,12}ing)\b"
    r"|(?<!اللي )(?<!\w)راح (?=[انتي]\w)|(?<!\w)(?:سوف|رح|بنروح|بروح|بزور|بنزور|بسافر|بنسافر|بشتري|باشتري|ببيع|بابيع|"
    r"بحضر|باحضر|بشوف|بقابل|باخذ|رايح|رايحه|رايحين|ناوي|ناويه|ناوين|القادم|القادمه|المقبل|المقبله|الجاي|الجايه|"
    r"بكون|باكون|بنكون|بنحضر|بنبيع|بنشتري|بنشوف|بنقابل|بناخذ|بطلع|بنطلع|برجع|بنرجع|بنتقل|بننتقل|بتزوج|ببدا|بنبدا|"
    r"مسافر|مسافره|مسافرين|انوي|ننوي|اعتزم|نعتزم|اخطط|نخطط|وسوف|فسوف)(?!\w)"
    r"|(?<!\w)[وف]?س[ان](?:سافر|زور|حضر|بيع|شتري|ذهب|كون|قابل|لتقي|تزوج|نتقل|بدا|شارك|غادر|سكن|درس|جري|خضع|ستلم|"
    r"لتحق|تخرج|فتح|نهي|قدم|عود|رجع|روح|شوف|اخذ|سجل|دفع|قيم|طير|حجز|عمل|نضم|حتفل|ستضيف|زوج|كمل)\w{0,3}(?!\w)")
#: "سأ" is the future's of "I" and begins no noun; folded, "سا" begins many.
_WILL_AR = re.compile(r"(?<!\w)([وف]?)سأ(?=\w\w)(?!ل(?!تق))")
#: Days counted ahead by their number: "in two days", "بعد ٣ أيام".
_IN_DAYS_EN = re.compile(r"\bin (\d{1,2}|an?|one|two|three|four|five|six|seven|eight|nine|ten|a couple of|a few) "
                         r"(days?|weeks?)\b(?! ago)")
_IN_DAYS_AR = re.compile(r"(?<!\w)(?:بعد|عقب) (?:(\d{1,2}|" + "|".join(_AGO_NUMBERS_AR) + r") )?"
                         r"(يومين|يوم|ايام|اسبوعين|اسبوع|اسابيع)(?!\w)")
_SENTENCE = re.compile(r"[^.!?؟:;؛\n]+")


def _days_ahead(folded: str) -> set[int]:
    """The days a sentence counts ahead by number: "in two days", "بعد ٣
    أيام". Exact for days; a week is its seventh day."""
    out: set[int] = set()
    for m in _IN_DAYS_EN.finditer(folded):
        count = int(m[1]) if m[1].isdigit() else _AGO_NUMBERS_EN.get(m[1].removeprefix("a "), 1)
        out.add(count * (7 if m[2].startswith("week") else 1))
    for m in _IN_DAYS_AR.finditer(folded):
        if m[2] in _AR_DUALS:
            count, unit = _AR_DUALS[m[2]]
        else:
            said = m[1] or "1"
            count, unit = int(said) if said.isdigit() else _AGO_NUMBERS_AR[said], _AR_UNITS[m[2]]
        out.add(int(count * unit))
    return out


def says_to_come(text: str, day: str, said_on: str | None) -> bool:
    """Whether *text*, told on *said_on*, says *day* as one to come: counts
    ahead to it ("tomorrow", "in two days", "بكرة", "بعد يومين"), or says it in a sentence that speaks of
    what is to come ("I'm visiting Dubai on 3 June", "راح أبيع سيارتي الجمعة
    الجاية") and of nothing done. A date ahead told as done is none: "I
    finished it on 20 March", told on 15 March, is a year left out or a slip
    (29 of the pilots' 40 were)."""
    said = _as_date(said_on)
    first = _as_date((str(day) + "-01-01")[:10] if len(str(day)) < 10 else day)
    if said is None or first is None or str(day) <= str(said_on)[: len(str(day))]:
        return False
    ahead = (first - said).days
    exact = len(str(day)) == 10
    whole = _WILL_AR.sub(r"\1سوف أ", text or "")
    for sentence in _SENTENCE.findall(_NAMED_FRIDAYS.sub(" ", _folded(whole))):
        if exact and ahead in _days_counted(sentence):
            return True
        if _PAST_MARK.search(sentence) or _TOLD_DONE.search(sentence):
            continue
        if exact and ahead in _days_ahead(sentence):
            return True
        if _TO_COME.search(sentence) and says_day(sentence, day, said_on):
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


_LAST_MONTH_PHRASES_EN = frozenset({"last month"})
_LAST_MONTH_PHRASES_AR: list[frozenset[str]] = []


def _last_month_phrases(arabic: bool) -> frozenset[str]:
    """The "last month" phrases that can close a counted stretch, folded."""
    if not arabic:
        return _LAST_MONTH_PHRASES_EN
    if not _LAST_MONTH_PHRASES_AR:
        from kazma_core.documents.arabic import fold_for_search

        _LAST_MONTH_PHRASES_AR.append(frozenset(fold_for_search(p) for p in _LAST_MONTH_AR))
    return _LAST_MONTH_PHRASES_AR[0]


def _every_occurrence_inside(text: str, phrase: str, spans: list[tuple[int, int]]) -> bool:
    """Whether every time *phrase* occurs in *text* it sits inside *spans*."""
    if not spans:
        return False
    start = 0
    while True:
        at = text.find(phrase, start)
        if at < 0:
            return True
        if not _span_inside(at, at + len(phrase), spans):
            return False
        start = at + len(phrase)


def _last_period_says(folded: str, said: date, near: int, far: int) -> bool:
    """Whether a "last week" / "الشهر الماضي" phrase covers the day.

    The "last month" that only closes a counted stretch does not say the
    whole month ("the last 5 days of last month"). A separate "last month"
    in the same text still does."""
    tables = (
        (False, _LAST_EN, _last_month_tie_spans(folded, arabic=False)),
        (True, _said_words().last, _last_month_tie_spans(folded, arabic=True)),
    )
    for _arabic, table, ties in tables:
        phrases = _last_month_phrases(_arabic)
        for phrase, back in table.items():
            if phrase not in folded:
                continue
            if phrase in phrases and _every_occurrence_inside(folded, phrase, ties):
                continue
            lo, hi = _days_back(back, said)
            if near <= hi and far >= lo:
                return True
    return False


def _day_of_tied_month(folded: str, day: str, said_on: str | None) -> bool | None:
    """Whether *day* is one of "the last N of <month>" in *folded*.

    True when it is. False when a stretch is tied and the day is outside
    those days, so the other readers still run. None when no stretch is
    tied. The same days :func:`_last_of_month` gives ``read_time``. "last
    month" in the tie does not say the whole month:
    :func:`_last_period_says` skips that phrase."""
    said = _as_date(said_on)
    want = _as_date(str(day)[:10]) if len(str(day)) >= 10 else None
    if said is None or want is None:
        return None
    today = datetime(said.year, said.month, said.day, tzinfo=UTC)
    want_ts = datetime(want.year, want.month, want.day, tzinfo=UTC).timestamp()
    saw = False
    for arabic in (False, True):
        for match, window in _counted_spans(folded, arabic=arabic):
            tied = _month_tied(folded, match.end(), arabic=arabic, today=today)
            if tied is None:
                continue
            saw = True
            start, end = _last_of_month(*window, tied[0], tied[1], today, UTC)
            if start <= want_ts < end:
                return True
    if saw:
        return False
    return None


def _in_counted_span(folded: str, near: int, far: int) -> bool:
    """Whether the day sits inside a counted stretch of *folded*.

    The same window :func:`_span_window` reads: from that many units before
    the turn's day through the turn's day itself. *near* and *far* are the
    day's nearest and farthest day-counts before the turn."""
    for count, unit in _span_counts(folded):
        if near >= 0 and far <= count * unit:
            return True
    return False


def _span_counts(folded: str) -> list[tuple[float, float]]:
    """"past 10 days", "آخر ثلاث أسابيع", "آخر كم يوم": (how many, the unit's days).

    The stretch through the turn's day, not the point :func:`_counts_back`
    reads ("three weeks ago"). A question word ("كم يوم فات", "how many") is
    not a count here. "كم" right after "آخر" is a few. A stretch tied to a
    month is not also this relative window."""
    found: list[tuple[float, float]] = []
    for arabic in (False, True):
        for match, got in _counted_spans(folded, arabic=arabic):
            if not _has_month_tie(folded, match.end(), arabic=arabic):
                found.append(got)
    return found


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
            said = m[1] or "1"
            out.append((float(said) if said.isdigit() else float(_AGO_NUMBERS_AR[said]), _AR_UNITS[m[2]]))
    return out
