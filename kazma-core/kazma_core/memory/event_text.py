"""Whether an event and its day are the user's own words, and what an event's
name is (the memory plan's W4; the second review, 2026-11-24).

A model reads a whole turn: the user's message, what they pasted or quoted in
it, and the assistant's reply. Only what the user tells is something the user
did. ``own_words`` says which part of the message that is; these say where an
event's words, and its day, stand:

* **Its words** (:func:`stands_elsewhere`). An event is somebody else's when
  its words stand in the part ``own_words`` removed (a paste, a quote, a text
  to translate) and not in what it kept. Words in neither are the model's way
  of saying it ("decluttering", "عشاء" for "تعشينا"): such an event is the
  user's when they tell something of it -- one of its words, or its day --
  and is not when the message says nothing of it at all (the assistant's
  reply told it).
* **Its day** (:func:`days_elsewhere`): a day the pasted text names and the
  user's own words do not is not the user's day. "Check this email I got last
  week: I bought it on 3 March 2019" names 2019 in the email only.

Words are compared as search compares them, with the letters Arabic writes
onto a word: "لدبي", "بدبي" hold "دبي" and "سيارتي" is "السيارة", as "to Dubai"
and "my car" are. Compared exactly, 30 of 30 English travel sentences kept
their event and 12 of 30 Arabic ones. Word lists and folding; no model.
"""

from __future__ import annotations

import re
from functools import lru_cache

__all__ = ["denies", "days_elsewhere", "is_event_name", "mentions_value", "own_text",
           "stands_elsewhere"]

#: The share of an event's content words one text holds, at least, for the
#: event to stand in it; measured on the two pilots' replies (2026-11-24).
EVENT_WORDS = 0.5

#: The verbs of the names the deep pass gives what happened to the user
#: ("attended", "travelled_to"): a name that begins with one is an event's,
#: whatever the reply labelled it, and is never the name of a value that
#: changes ("weight", "order_status", "follower_count").
_EVENT_VERBS = frozenset(
    "accepted achieved adopted arrived asked ate attended baked became began booked borrowed bought broke brought built "
    "called came canceled cancelled caught celebrated checked cleaned climbed completed cooked crossed cycled "
    "danced decorated deleted delivered discovered donated drank drove earned enjoyed enrolled exchanged explored "
    "fell finished fixed flew found gave got graduated grew had harvested hiked hired hosted installed interviewed "
    "joined kayaked launched lost learned left lent listened made married met moved named opened ordered organized "
    "paid painted participated passed performed picked planted played posted prepared presented published purchased "
    "quit raised ran reached read received redeemed registered renewed repaired replaced reserved resigned returned "
    "rode sang saw sent shared shipped signed skipped sold spent spoke started stayed stopped studied submitted "
    "subscribed survived swam switched talked took toured traded trained travelled traveled tried underwent "
    "upgraded visited volunteered walked washed watched went won wrote".split())


@lru_cache(maxsize=512)
def is_event_name(predicate: str) -> bool:
    """Whether *predicate* names something that happened: it begins with an
    event's verb ("attended", "travelled_to", "bought from")."""
    from kazma_core.memory.fact_names import normalised_name

    words = normalised_name(predicate).split("_")
    return bool(words) and words[0] in _EVENT_VERBS


# ── Words ────────────────────────────────────────────────────────────────

_WORD = re.compile(r"[^\W_]+(?:-[^\W_]+)*", re.UNICODE)
#: What Arabic writes onto a word's end: "my car" is one word.
_AR_SUFFIX = re.compile(r"(?:ت?(?:هما|كما|نا|كم|هم|ها|ي|ك)|ه|ت)$")
_EN_SUFFIX = re.compile(r"(?:ing|ed|es|s|e)$")


def _stem(word: str) -> str:
    """*word* without what its grammar adds at its end, when three letters
    are left: "سيارتي" and "سياره" are "سيار", "organized" and "organizing"
    "organiz"."""
    bare = (_EN_SUFFIX if word.isascii() else _AR_SUFFIX).sub("", word)
    return bare if len(bare) >= 3 else word


@lru_cache(maxsize=256)
def _forms(text: str) -> frozenset[str]:
    """Every form the words of *text* are compared under, folded: the word,
    its stem, its hyphened parts, and -- Arabic -- what follows a letter
    written onto its front ("لدبي": "دبي")."""
    from kazma_core.documents.arabic import fold_for_search
    from kazma_core.memory.query_terms import normalised_word

    out: set[str] = set()
    for token in _WORD.findall(fold_for_search((text or "").replace("_", " "))):
        for part in {token, *token.split("-")}:
            if not part:
                continue
            for word in {part, normalised_word(part)}:
                out.update((word, _stem(word)))
                if not word.isascii():
                    for at in (1, 2):
                        if len(word) - at >= 2 and word[at - 1] in "وفبلك":
                            rest = normalised_word(word[at:])
                            out.update((rest, _stem(rest)))
                        else:
                            break
    return frozenset(out)


def _wanted(value: str) -> list[str]:
    """The words *value* is known by: its content words, or every word when it
    has none (the name "علي" folds to "on")."""
    from kazma_core.documents.arabic import fold_for_search
    from kazma_core.memory.query_terms import content_terms

    return content_terms(value) or _WORD.findall(fold_for_search(value or ""))


def _share(wanted: list[str], text: str) -> float:
    forms = _forms(text)

    def said(word: str) -> bool:
        parts = [p for p in word.split("-") if p]
        return (word in forms or _stem(word) in forms
                or (len(parts) > 1 and all(p in forms or _stem(p) in forms for p in parts)))

    return sum(map(said, wanted)) / len(wanted)


def own_text(user_text: str) -> str:
    """The user's own words of a whole message, what they ask included
    ("I bought a bike yesterday, which lock should I get?"); the end of one
    longer than ``own_words`` reads too."""
    from kazma_core.memory.own_words import last_words, own_words

    return f"{own_words(user_text, questions=True)}\n{last_words(user_text)}".strip()


def stands_elsewhere(value: str, user_text: str, *, told_before: str = "", day: str = "",
                     said_on: str | None = None) -> bool:
    """Whether the event *value* is not the user's to tell: see the module's
    docstring. *day* is the day it was given and *said_on* the turn's (both
    ``YYYY-MM-DD`` or less exact).

    *told_before* is the fact memory holds of it, if any: "I bought it in
    2018", "I think I started it last Monday" name the thing by what was told
    before, so its words count -- when the day itself is in the user's own
    words."""
    from kazma_core.memory.own_words import own_words
    from kazma_core.memory.time_words import says_day

    wanted = _wanted(value)
    if not wanted:
        return False
    own = own_text(user_text)
    held = _share(wanted, own)
    if held >= EVENT_WORDS:
        return False
    day_said = bool(day) and says_day(own, day, said_on)
    if day_said and told_before and _share(wanted, f"{own} {told_before}") >= EVENT_WORDS:
        return False
    if _share(wanted, user_text) >= EVENT_WORDS:
        return True
    # A day in what they ask is none they tell: "What should I do this weekend?"
    return not held and not (day_said and says_day(own_words(user_text), day, said_on))


def days_elsewhere(day: str, user_text: str, said_on: str | None) -> bool:
    """Whether *day* stands in the part of *user_text* that is not the user's
    own words (a paste, a quote) and not in what is."""
    from kazma_core.memory.time_words import says_day

    return bool(day) and says_day(user_text, day, said_on) and not says_day(own_text(user_text), day, said_on)


def mentions_value(value: str, user_text: str) -> bool:
    """Whether the user's own words name *value*: half of its words."""
    wanted = _wanted(value)
    return bool(wanted) and _share(wanted, own_text(user_text)) >= EVENT_WORDS


# ── Denials ──────────────────────────────────────────────────────────────

_DENIALS_EN = frozenset("no not nope never wasn't wasnt isn't isnt didn't didnt weren't werent actually sorry wrong "
                        "mistake mistaken correction correct instead meant rather oops".split())
_DENIALS_AR_RAW = ("لا", "مو", "مب", "مش", "ليس", "ليست", "لم", "غلط", "أقصد", "قصدي", "آسف", "آسفة", "عفوا", "عفواً",
                   "بل", "تصحيح", "الصحيح", "صححها", "أخطأت", "غلطت", "ماكان", "ماكانت", "معليش", "السموحة")


@lru_cache(maxsize=1)
def _denials_ar() -> frozenset[str]:
    from kazma_core.documents.arabic import fold_for_search

    return frozenset(fold_for_search(w) for w in _DENIALS_AR_RAW)


def denies(user_text: str) -> bool:
    """Whether the user's own words put something right: "No, it was ...",
    "not Paris, Lyon", "sorry, I meant ...", "لا، كان ...", "مو علي، عمر"."""
    from kazma_core.documents.arabic import fold_for_search

    words = re.findall(r"[\w']+", fold_for_search(own_text(user_text).replace("’", "'")))
    return any(w in _DENIALS_EN or w in _denials_ar() or (w[:1] in "وف" and w[1:] in _denials_ar()) for w in words)

