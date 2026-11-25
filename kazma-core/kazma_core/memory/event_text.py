"""Whether an event and its day are the user's to tell, and what an event's
name is (the memory plan's W4; the second review, 2026-11-24, and the third,
2026-11-25).

A model reads a whole turn: the user's message and what they pasted or quoted
in it. What it reads is filed as Kazma's reading, never as the user's own
statement, so an event is left out only when its words stand in text that is
clearly pasted or another person's (``pasted_text.told_and_pasted``) and not
in the rest. Everything else stays: what is reported ("my manager told me I
got the promotion"), supposed, long, or told in words of the model's
("decluttering", "a promotion" for "ترقيت").

* **Its words** (:func:`stands_elsewhere`). Until 2026-11-25 this asked
  ``own_words``, which is for trust and takes out whatever it is unsure of:
  12 of 135 ordinary English events were lost by it, and 60 Arabic ones. A
  thing the user points at ("I bought these yesterday" under a pasted product
  page) is theirs, and so is one memory holds and they name by "it".
* **Its day** (:func:`days_elsewhere`): a day the pasted text names and the
  rest of the message does not is not the user's day. "Check this email I got
  last week: > I bought it on 3 March 2019" names 2019 in the email only.

Words are compared as search compares them, with the letters Arabic writes
onto a word, on either side: "لدبي", "بدبي" hold "دبي", "سيارتي" is "السيارة",
and the value "دراجة بـ٩٥ دينار" holds "٩٥". Compared exactly, 30 of 30 English
travel sentences kept their event and 12 of 30 Arabic ones. Word lists and
folding; no model.
"""

from __future__ import annotations

import re
from functools import lru_cache

__all__ = ["corrects", "denies", "days_elsewhere", "is_event_name", "mentions_value", "own_text", "puts_right",
           "says_once_more", "stands_elsewhere"]

#: The share of an event's content words one text holds, at least, for the
#: event to stand in it; measured on the two pilots' replies (2026-11-24).
EVENT_WORDS = 0.5

#: The verbs of the names the deep pass gives what happened to the user
#: ("attended", "travelled_to"): a name that begins with one is an event's,
#: whatever the reply labelled it, and is never the name of a value that
#: changes ("weight", "order_status", "follower_count").
_EVENT_VERBS = frozenset(
    "accepted achieved acquired adopted applied arrived asked ate attended baked became began boarded booked "
    "borrowed bought broke brought built called came canceled cancelled caught celebrated checked cleaned climbed "
    "completed cooked cracked crossed cycled danced decorated deleted delivered dined discovered donated "
    "downloaded drank drove earned enjoyed enrolled exchanged experienced explored fell finished fixed flew found "
    "gave gifted got graduated grew had harvested helped hiked hired hosted injured installed interviewed joined "
    "kayaked launched learned left lent listened lost made married met misplaced missed moved named opened ordered "
    "organized paid painted participated passed performed picked planted played posted prepared presented "
    "published purchased quit raised ran reached read rearranged received redeemed registered renewed rented "
    "repaired replaced repotted reserved resigned returned rode sang saw sent shared shipped signed skipped sold "
    "spent spilled spoke sprained started stayed stopped studied submitted subscribed survived swam switched "
    "talked took toured traded trained traveled travelled tried underwent upgraded visited volunteered walked "
    "washed watched went won wore wrote".split())


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


@lru_cache(maxsize=512)
def _other_forms(word: str) -> frozenset[str]:
    """The forms of a value's own *word* with what Arabic writes onto its front
    taken off ("بمطعم": "مطعم", "ب95": "95"): three letters or more, or a
    number -- two letters are in anything."""
    if word.isascii():
        return frozenset()
    return frozenset(f for f in _forms(word) if len(f) >= 3 or f.isdigit())


def _share(wanted: list[str], text: str) -> float:
    forms = _forms(text)

    def said(word: str) -> bool:
        parts = [p for p in word.split("-") if p]
        return (word in forms or _stem(word) in forms or not forms.isdisjoint(_other_forms(word))
                or (len(parts) > 1 and all(p in forms or _stem(p) in forms for p in parts)))

    return sum(map(said, wanted)) / len(wanted)


def own_text(user_text: str) -> str:
    """The message without what is clearly pasted or another person's
    (``pasted_text``), what they ask, report and suppose included."""
    from kazma_core.memory.pasted_text import told_and_pasted

    return told_and_pasted(user_text)[0]


#: The user points at something: "I bought these", "I've already started it".
_POINTS_EN = re.compile(
    r"(?:\b(?:i|we)(?:'[a-z]{1,3})?[ \t]|(?:^|[.!?\n])[ \t]{0,4})(?:(?:have|had|just|already|also|finally|both) ){0,3}"
    r"([a-z]{3,14})(?: [a-z]{2,6})? (?:it|this|these|those|them|that|one)\b"
    r"(?! (?:email|e-mail|mail|message|text|page|article|letter|note|post))")
#: ... in Arabic, the pronoun is written onto a verb of "I" or "we" ("شريتها",
#: "خلصناه"), as it is onto a noun ("رسالتها").
_POINTS_AR = re.compile(r"(?<!\w)[وف]?(?!رسال|تغريد|صور|قصت|كتاب|محادث|سيار|بيت|بنت|اخت|خالت|عمت|جدت|زوجت|صديق|مقال)"
                        r"\w{2,8}(?:ت|نا)(?:ها|هم|هن|ه)(?!\w)")
#: "I bought the watch in this ad", "شريت الساعة اللي في هالإعلان".
_POINTS_IN_EN = re.compile(
    r"\b(?:i|we)(?:'[a-z]{1,3})?[ \t](?:(?:have|had|just|already|also|finally) ){0,3}([a-z]{3,14}) the (?:[a-z-]{2,14} ){1,3}"
    r"(?:in|from|on|of) (?:this|that|these|the) (?:ad|advert|link|picture|photo|image|listing|post|page|offer)\b")
_EVENT_OF_MINE_AR = ("رحت|ذهبت|شريت|اشتريت|بعت|زرت|حضرت|جربت|خلصت|انهيت|لبست|ركبت|طلبت|حجزت|دخلت|تبنيت|استاجرت|سكنت|"
                     "اكلت|كليت|سجلت|قدمت|فزت|خذيت|تعاقدت")
_POINTS_IN_AR = re.compile(
    rf"(?<!\w)[وف]?(?:{_EVENT_OF_MINE_AR}) ال\w{{2,14}} (?:(?:اللي|الذي|التي) )?(?:في |ب)(?:هال|هذا ال|هذه ال|هذي ال|ال)"
    r"(?:اعلان|رابط|صوره|منشور|بوست|عرض)")
_POINTS_AT_AR = re.compile(
    rf"(?<!\w)[وف]?(?:{_EVENT_OF_MINE_AR}) (?:(?:الي|حق|في) )?(?:ل|ب)?(?:هذا|هذي|هذه|هاذا|هاذي|هذيل|هذول)(?!\w)"
    r"(?! (?:ال)?(?:ايميل|رساله|نص|مقال|كلام|خبر|منشور|تغريده|ال\w))")
#: Verbs of an event that also point at a text ("I got this", "I read it").
_OF_A_TEXT = frozenset("asked checked fixed found got had read received saw sent shared took wrote".split())


def _points(own: str) -> bool:
    from kazma_core.documents.arabic import fold_for_search

    # What was pasted is blank: the line after it starts a sentence.
    lowered = re.sub(r"[ \t]{3,}", "\n", (own or "").lower().replace("’", "'"))
    for pattern in (_POINTS_EN, _POINTS_IN_EN):
        if any(m[1] in _EVENT_VERBS and m[1] not in _OF_A_TEXT for m in pattern.finditer(lowered)):
            return True
    folded = fold_for_search(own or "")
    return any(p.search(folded) for p in (_POINTS_AR, _POINTS_AT_AR, _POINTS_IN_AR))


def stands_elsewhere(value: str, user_text: str, *, told_before: str = "", day: str = "",
                     said_on: str | None = None, pasted_day: bool = False) -> bool:
    """Whether the event *value* is not the user's to tell: see the module's
    docstring. *day* is the day it was given and *said_on* the turn's (both
    ``YYYY-MM-DD`` or less exact).

    *told_before* is the fact memory holds of it, if any: "I bought it in
    2018", "I think I started it last Monday" name the thing by what was told
    before, so its words count -- when the day itself is in the rest of the
    message. A thing a paste names is the user's when they point at it ("I
    bought these yesterday"; "Just bought these!" gives no day, and none may
    be given then). *pasted_day*: its day was the
    paste's alone (:func:`days_elsewhere`); with no word of it in the rest,
    the paste is all that tells it ("Translate: I got it in 2018")."""
    from kazma_core.memory.pasted_text import told_and_pasted
    from kazma_core.memory.time_words import says_day

    own, pasted = told_and_pasted(user_text)
    wanted = _wanted(value)
    held = _share(wanted, own) if wanted else 1.0
    if not pasted.strip() or held >= EVENT_WORDS:
        return False
    if _share(wanted, pasted) < EVENT_WORDS:
        return pasted_day and not held
    day_said = bool(day) and says_day(own, day, said_on)
    if day and not day_said:
        return True
    if _points(own):
        return False
    return not (day_said and told_before and _share(wanted, f"{own} {told_before}") >= EVENT_WORDS)


def days_elsewhere(day: str, user_text: str, said_on: str | None) -> bool:
    """Whether *day* stands in what is clearly pasted or another person's in
    *user_text* and not in the rest."""
    from kazma_core.memory.pasted_text import told_and_pasted
    from kazma_core.memory.time_words import says_day

    own, pasted = told_and_pasted(user_text)
    return bool(day) and says_day(pasted, day, said_on) and not says_day(own, day, said_on)


def mentions_value(value: str, user_text: str) -> bool:
    """Whether the user's message, pastes left out, names *value*: half of its
    words."""
    wanted = _wanted(value)
    return bool(wanted) and _share(wanted, own_text(user_text)) >= EVENT_WORDS


# ── Denials ──────────────────────────────────────────────────────────────

#: Words that put something right whatever else is said: "sorry, I meant ...".
_CORRECTING_EN = frozenset("sorry wrong mistake mistaken mistyped typo correction correcting meant oops misspoke "
                           "apologies".split())
_CORRECTING_AR_RAW = ("غلط", "أقصد", "قصدي", "آسف", "آسفة", "عفوا", "عفواً", "تصحيح", "صححها", "أخطأت", "غلطت",
                      "معليش", "السموحة", "متأسف", "متأسفة", "أعني", "معذرة", "أعتذر", "خربطت", "لخبطت", "تلخبطت",
                      "التبس", "عدلها", "سامحني")
#: ... said in more than a word; "No, it was ..." as a message begins.
_CORRECTING_PHRASES = re.compile(
    r"\b(?:my bad|i apologi[sz]e|scratch that|forgive me|let me rephrase|change (?:it|that) to|mixed (?:it|that|them|him|her) up"
    r"|confused (?:it|that|them|him|her) with)\b|\bi said\b[^.!?\n]{1,60}\bbut (?:it|that) (?:was|is)\b"
    r"|^سوري(?!\w)|(?<!\w)(?:بالغلط قلت|خلطت بين\w{0,4})(?!\w)")
_NO_IT_WAS = re.compile(
    r"^(?:(?:oh|well|ah|hmm|wait|actually|اوه|اه|لحظه|والله)[ ,،.]+)?(?:(?:no|nope|لا)[ ,،.!]+){1,3}"
    r"(?:(?:wait|لحظه|صبر)[ ,،]+)?(?:(?:it|that) was|كان|كانت|بل)(?!\w)|^لا[ ،,]+لا[ ،,]+\w")
#: Words that deny, and may deny something else: "not Paris", "مو علي".
_DENIALS_EN = frozenset("not nope never wasn't wasnt isn't isnt didn't didnt weren't werent actually correct instead "
                        "rather".split())
_DENIALS_AR_RAW = ("مو", "مب", "موب", "مهب", "مهو", "ماهو", "ماهي", "مش", "ليس", "ليست", "لم", "بل", "الصحيح", "ماكان",
                   "ماكانت")
#: "No" and "لا" as an answer, never before a noun ("no cavities", "لا تسوس"):
#: they begin a sentence, or a comma, a stop or "wait" follows.
_NO = re.compile(r"(?:^|[.!?؟\n][ \t]{0,4})(?:(?:oh|well|ah|hmm|اوه|اه|والله)[ ,،]+)?(?:no|لا)(?!\w)"
                 r"|(?<!\w)(?:no|لا)(?=[ \t]{0,3}(?:[,،.!]|$)|[ \t]{1,3}(?:wait|لحظه|صبر)(?!\w))")
#: ... as a message's first word, and nothing but an answer: "No, ...".
_NO_FIRST = re.compile(r"^(?:(?:oh|well|ah|hmm|wait|اوه|اه|لحظه|والله)[ ,،.]+)?(?:no|nope|لا)"
                       r"(?:[ \t]{0,3}[,،.!]|[ \t]{1,3}(?:wait|it|that|he|she|they|كان|كانت|بل|هو|هي|لحظه|لا)(?!\w))")
#: The same thing once more is another occasion, not a correction. "Too" only
#: where a clause ends or goes on ("too tired" is none), "بعد" only as "also"
#: ("بعد الدوام" is "after work").
_ONCE_MORE = re.compile(r"\b(?:also|again|another|as well|second time)\b|(?:^|[.,;!\n][ \t]?)plus\b"
                        r"|\btoo\b(?=[ \t]{0,3}(?:[^\w\s]|$|\n)|[ \t](?:and|but|at|in|on|with|from|yesterday|today|"
                        r"last|this|when|because|so)\b)"
                        r"|(?<!\w)و?(?:كمان|ايضا|كذلك|برضه|برضو|بردو|مره ثانيه|مره اخري|مره ثالثه|بعد مره|بالاضافه|"
                        r"زائد)(?!\w)"
                        r"|(?<!\w)\w{2,8}(?:ت|نا)(?: \w{2,14}){0,2} بعد(?=[ \t]{0,3}(?:[^\w\s]|$))"
                        r"|(?<!\w)\w{2,8}(?:ت|نا) بعد (?!ال|ما |يوم|اسبوع|شهر|سنه|ساع|كذا|ذلك|هذا|هال|صلا|\d)"
                        r"|(?<!\w)و?هم (?=\w{2,8}(?:ت|نا)(?!\w))")


@lru_cache(maxsize=2)
def _folded_words(raw: tuple[str, ...]) -> frozenset[str]:
    from kazma_core.documents.arabic import fold_for_search

    return frozenset(fold_for_search(w) for w in raw)


def _says_one_of(folded: str, english: frozenset[str], arabic: frozenset[str]) -> bool:
    words = re.findall(r"[\w']+", folded)
    return any(w in english or w in arabic or (w[:1] in "وف" and w[1:] in arabic) for w in words)


def _own_folded(user_text: str) -> str:
    from kazma_core.documents.arabic import fold_for_search

    return fold_for_search(own_text(user_text).replace("’", "'")).lower().strip()


def corrects(user_text: str) -> bool:
    """Whether the user says they put something right: "sorry, I meant Omar",
    "آسف أقصد عمر", "No, it was Omar". Enough by itself with the fact it
    replaces named; a denial (:func:`denies`) is not."""
    folded = _own_folded(user_text)
    return (_says_one_of(folded, _CORRECTING_EN, _folded_words(_CORRECTING_AR_RAW))
            or bool(_CORRECTING_PHRASES.search(folded) or _NO_IT_WAS.search(folded)))


#: What a denial says of the old value: "not (at) Paris", "instead of Ali".
_CLAUSE = re.compile(r"[^.,،;؛!?؟\n]+")
_DENIES_IT_REACH = 4


def _denies_value(folded: str, value: str) -> bool:
    """Whether *folded* denies *value* by name: a denial and, within a few
    words of its clause, a word of it ("not Paris", "مو علي"). "I visited the
    dentist, no cavities" denies no dentist."""
    wanted = _wanted(value)
    arabic = _folded_words(_DENIALS_AR_RAW)
    denials = (_DENIALS_EN - {"actually", "correct", "rather", "never"}) | (arabic - _folded_words(("بل", "الصحيح")))
    for clause in _CLAUSE.findall(folded) if wanted else ():
        words = re.findall(r"[\w']+", clause)
        for at, word in enumerate(words):
            if word not in denials and not (word[:1] in "وف" and word[1:] in denials):
                continue
            after = " ".join(words[at + 1:at + 1 + _DENIES_IT_REACH])
            if after and _share(wanted, after) >= EVENT_WORDS:
                return True
    return False


def puts_right(user_text: str, value: str) -> bool:
    """Whether the user puts right what memory holds as *value*: says so
    (:func:`corrects`) or denies it by name ("Not Paris, it was Lyon again":
    "again" and "too" take nothing back of that)."""
    return corrects(user_text) or _denies_value(_own_folded(user_text), value)


def says_once_more(user_text: str) -> bool:
    """Whether the user tells another one, not the same again: "I also bought
    a lamp", "وشريت بعد لمبة", "شريت لمبة برضه"."""
    return bool(_ONCE_MORE.search(_own_folded(user_text)))


def denies(user_text: str, value: str = "") -> bool:
    """Whether the user puts something right or denies it: "No, it was ...",
    "not Paris, Lyon", "sorry, I meant ...", "لا، كان ...", "مو علي، عمر".
    Not a "no" before a noun, and not a denial beside "also" or "again": "I
    also visited the dentist on 2 May, no cavities" is another visit -- unless
    the message begins with the no, or it denies *value* by name."""
    folded = _own_folded(user_text)
    if corrects(user_text) or _NO_FIRST.search(folded) or (value and _denies_value(folded, value)):
        return True
    if says_once_more(user_text):
        return False
    return bool(_NO.search(folded)) or _says_one_of(folded, _DENIALS_EN, _folded_words(_DENIALS_AR_RAW))
