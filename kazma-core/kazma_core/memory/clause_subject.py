"""Who a clause is about: the speaker, someone else, or no one named.

A fact goes under the user only when the user said it about themself. "My
sister moved to Rotterdam" holds a place in the user's own message, but the
clause's subject is the sister. This module answers one question for each
word of a message -- who did the words before it last name? -- in Arabic,
English and the two mixed:

* walking back from the word, the nearest word that names a person decides;
* the speaker is named by a first-person pronoun or possessive ("أنا",
  "احنا", "نحن", "I", "we", "my", "me"), "اسمي" (my name), "عندي"/"لدي"
  (I have), or a first-person verb ("أشتغل" written with its hamza,
  "انتقلنا");
* someone else is named by a relative or a companion ("أختي", "أخوي",
  "صديقه", "زوجتي", "my manager", "sisters"), a third-person pronoun where
  its clause begins ("هي", "هم", "she", "they"), a name given as someone
  else's ("اسمها"), or a verb reporting what someone else said ("قالت");
* some words name no one where they stand:
  - an Arabic "هو"/"هي" inside a clause is the copula ("اسمي هو أحمد");
  - a person noun with neither article nor possessive right after the
    speaker describes the speaker or what they have: "أنا مدير مشاريع",
    "أنا ابن الكويت", "عندي بنت", "I'm a teacher";
  - a definite word is never read as possessed ("الأهلي" is a name, not
    "my family"), and "جاري" is "in progress" as often as "my neighbour";
  - an English object pronoun ("I told her", "I met him") is no subject;
* a sentence end closes the sentence's people: the next sentence starts
  with no one named ("صديقي نصحني بكازما. ساكن في الكويت" is the user),
  except someone the user introduced by name ("عندي أخت اسمها نورة.
  انتقلت إلى روتردام"), who stays named until the speaker or someone else
  is;
* with no one named, each caller decides: the Arabic quick pass reads a
  participle or a past verb with no subject of its own as the speaker's
  ("ساكن في دبي"); a pending change (:func:`names_no_one_else`) accepts it
  too, and the user answers.

The rule leans one way where it must choose. A word is someone else's on a
wide list and the speaker's on a narrow one: keeping nothing loses a fact the
deep pass can still read, while filing another person's fact under the user
replaces what the user said.

``subjects`` takes the message as words, each punctuation mark a word of its
own, and answers for every word in one pass.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from typing import NamedTuple

__all__ = ["OTHER", "SPEAKER", "names_no_one_else", "subjects"]

SPEAKER = "speaker"
OTHER = "other"

_PUNCTUATION = set(".,!?،؛؟:;()")
#: Where a sentence ends: its people end with it.
_SENTENCE_END = {".", "!", "?", "؟", "\n"}
#: How a question ends.
_QUESTION_END = {"?", "؟"}
#: Words that deny what follows them in their sentence ("I don't live in
#: Paris", "I never worked at Google", "أنا ما أسكن في دبي"), written folded
#: and without an apostrophe. "No" and "لا" deny only when a word follows
#: them: "No, Lisbon." answers, it denies nothing.
_NEGATIONS = {
    "not", "never", "no", "nor", "neither", "cannot", "dont", "doesnt", "didnt", "isnt", "arent", "wasnt",
    "werent", "wont", "cant", "couldnt", "wouldnt", "shouldnt", "havent", "hasnt", "hadnt", "aint",
    "ما", "مو", "موب", "مب", "مش", "لا", "لم", "لن", "ليس", "ليست", "ماني", "مانيب",
}
#: How a sentence that asks, or asks Kazma to act on a text, begins: nothing
#: in it is the user saying something of themself ("Should I move to
#: Rotterdam?", "How do I say my name is Pierre in French?", "Summarize this
#: page about Dubai", "هل أنتقل إلى دبي").
_ASKING_STARTS = {
    "should", "shall", "can", "could", "would", "do", "does", "did", "how", "what", "why", "which", "who",
    "whom", "whose", "summarize", "summarise", "translate", "explain", "rewrite", "proofread", "review",
    "draft", "compose",
    "هل", "كيف", "وش", "شنو", "ليش", "متى", "وين", "شلون", "لخص", "ترجم", "اشرح", "صحح", "راجع",
}

# Arabic, written folded (``documents.arabic.fold_for_search``).
_AR_SPEAKER = {"انا", "احنا", "نحن", "اسمي", "عندي", "لدي"}
#: First-person verbs written with their hamza ("أشتغل", "أسكن"): without it
#: each is also the imperative ("اشتغل على التقرير"), so it names no one.
#: ("أفضل" and "أشجع" are left out: also "better" and "braver".)
_AR_I_VERBS = {"اسكن", "اعيش", "اقيم", "اشتغل", "اعمل", "اداوم", "ادرس", "احب", "اعشق", "استخدم",
               "استعمل", "املك", "انتقل", "اروح", "اسافر", "اكون", "اصير", "اتابع", "اكره", "اسوق",
               "اتكلم"}
#: "We" verbs, which carry their person whatever the spelling.
_AR_WE_VERBS = {"نسكن", "نعيش", "نقيم", "نشتغل", "نعمل", "ندرس", "ننتقل", "نروح", "نسافر", "نكون",
                "انتقلنا", "سكنا", "اشتغلنا", "رحنا", "سافرنا", "تزوجنا", "نقلنا"}
#: Third-person pronouns: someone else where their clause begins.
_AR_PRONOUNS = {"هو", "هي", "هم", "هما", "هن"}
#: Someone introduced by name: they stay named past the sentence's end.
_AR_INTRODUCERS = {"اسمه", "اسمها", "اسمهم", "اسمهن"}
#: Someone else wherever they stand: reporting verbs, and a grandmother
#: ("جدة" alone is also Jeddah).
_AR_OTHER_WORDS = {"قال", "قالت", "قالوا", "قالو", "يقول", "تقول", "يقولون", "يقولو", "جدتي", "جدته",
                   "جدتها"}
#: People named in any form: bare, or with a possessive ("أختي", "أخته").
_AR_PEOPLE = {"اخت", "اخو", "اخوان", "اخوات", "خوات", "ابن", "بنت", "زوج", "زوجه", "والد", "والده",
              "صديق", "صديقه", "صاحب", "صاحبه", "رفيق", "رفيقه", "زميل", "زميله", "مدير", "مديره",
              "اصدقاء", "اصدقا", "عيال", "اطفال", "اولاد", "بنات", "حفيد", "حفيده", "نسيب", "نسيبه",
              "خطيب", "خطيبه", "دكتور", "دكتوره", "استاذ", "استاذه", "خوي", "جيران"}
#: People named only with a possessive. Bare, each is also another word:
#: "ام" in "أم صلال", "اخ" an exclamation, "خال" empty, "ربع" a quarter.
#: Left out: "جد" ("جدة" is also Jeddah), "اب" ("أبها"), "جار" ("جاري
#: العمل" is "work in progress").
_AR_PEOPLE_OWNED = {"اخ", "ام", "ابو", "ولد", "عم", "عمه", "خال", "خاله", "جاره", "ربع", "اهل",
                    "عايله", "عائله"}
#: A possessive: my, your, his, her, our, their.
_AR_SUFFIXES = ("ي", "ك", "كي", "كم", "ه", "ها", "هم", "هن", "نا")
#: Prepositions and conjunctions written onto a word, longest first; those
#: holding the article make a definite word, which takes no possessive.
_AR_PREFIXES = ("وال", "فال", "بال", "لل", "ال", "و", "ف", "ب", "ل")
_AR_ARTICLE_PREFIXES = {"وال", "فال", "بال", "لل", "ال"}
#: Never someone else: "أبي" is also "I want" in the Gulf.
_AR_NEVER = {"ابي"}

_EN_SPEAKER = {"i", "we", "me", "us", "my", "our", "mine", "ours", "myself", "ourselves"}
_EN_PRONOUNS = {"he", "she", "they"}
#: Never a subject: "I told her", "I met him", "we asked them".
_EN_OBJECTS = {"him", "them"}
_EN_POSSESSIVES = {"his", "her", "hers", "their", "theirs"}
_EN_PEOPLE = {"sister", "brother", "sibling", "mother", "mom", "mum", "father", "dad", "parent",
              "wife", "husband", "partner", "son", "daughter", "child", "children", "kid",
              "friend", "boss", "manager", "colleague", "coworker", "co-worker", "neighbour", "neighbor",
              "cousin", "uncle", "aunt", "grandma", "grandpa", "grandmother", "grandfather",
              "boyfriend", "girlfriend", "fiance", "fiancé", "fiancee", "fiancée", "roommate", "flatmate",
              "teacher", "nephew", "niece", "doctor", "engineer", "student", "nurse"}
#: After "her", these mean "her" was the object ("I told her that ...").
_EN_AFTER_OBJECT = {"that", "to", "about", "and", "or", "but", "so", "because", "if", "when", "i", "we",
                    "you", "again", "too", "yesterday", "today", "earlier"}
_EN_ARTICLES = {"a", "an"}


class _Word(NamedTuple):
    """What one word says about who is named."""

    person: str | None
    #: A bare Arabic pronoun: the copula when it stands inside a clause.
    copula: bool = False
    #: A person noun with neither article nor possessive: it describes the
    #: speaker when it follows them ("أنا مدير", "عندي بنت").
    bare_noun: bool = False
    #: Someone introduced by name ("اسمها"): named past the sentence's end.
    introduces: bool = False
    #: English "her": an object unless a word follows it ("her house").
    her: bool = False


def _is_person_stem(stem: str) -> bool:
    if stem in _AR_PEOPLE or stem in _AR_PEOPLE_OWNED:
        return True
    # A noun ending in taa marbuta takes "ت" before a possessive: "زوجتي".
    return stem.endswith("ت") and (stem[:-1] + "ه") in (_AR_PEOPLE | _AR_PEOPLE_OWNED)


def _arabic_verb_person(word: str, written: str) -> str | None:
    """SPEAKER for a first-person verb: a "we" form, or an "I" form written
    with its hamza (*written* is the word as typed, without "و"/"ف")."""
    for form in (word, word[1:] if len(word) > 3 and word[0] in "وف" else ""):
        if form in _AR_WE_VERBS:
            return SPEAKER
        if form in _AR_I_VERBS and written.startswith("أ"):
            return SPEAKER
    return None


def _arabic_word(word: str, written: str = "") -> _Word:
    if _arabic_verb_person(word, written):
        return _Word(SPEAKER)
    forms = [(word, "")] + [(word[len(p):], p) for p in _AR_PREFIXES
                            if word.startswith(p) and len(word) - len(p) >= 2]
    for form, prefix in forms:
        if form in _AR_NEVER:
            return _Word(None)
        if form in _AR_SPEAKER:
            return _Word(SPEAKER)
        if form in _AR_PRONOUNS:
            # Written with "و"/"ف" ("وهي") it begins a clause of its own.
            return _Word(OTHER, copula=not prefix)
        if form in _AR_INTRODUCERS:
            return _Word(OTHER, introduces=True)
        if form in _AR_OTHER_WORDS:
            return _Word(OTHER)
        if form in _AR_PEOPLE:
            return _Word(OTHER, bare_noun=not prefix and not form.startswith("ال"))
        if prefix in _AR_ARTICLE_PREFIXES or form.startswith("ال"):
            continue  # definite: "الأهلي" is a name, never "my family"
        for suffix in _AR_SUFFIXES:
            if form.endswith(suffix) and len(form) - len(suffix) >= 2 \
                    and _is_person_stem(form[: -len(suffix)]):
                return _Word(OTHER)
    return _Word(None)


def _english_word(word: str) -> _Word:
    head = word.replace("’", "'").split("'", 1)[0]  # "i'm" -> "i", "sister's" -> "sister"
    if head in _EN_SPEAKER:
        return _Word(SPEAKER)
    if head in _EN_OBJECTS:
        return _Word(None)
    if head == "her":
        return _Word(OTHER, her=True)
    if head in _EN_PRONOUNS or head in _EN_POSSESSIVES:
        return _Word(OTHER)
    if head in _EN_PEOPLE or (head.endswith("s") and head[:-1] in _EN_PEOPLE):
        return _Word(OTHER, bare_noun=True)
    return _Word(None)


@lru_cache(maxsize=4096)
def _classify(word: str) -> _Word:
    from kazma_core.documents.arabic import fold_for_search

    folded = fold_for_search(word)
    if not folded:
        return _Word(None)
    if any("؀" <= c <= "ۿ" for c in folded):
        written = word[1:] if len(word) > 3 and word[0] in "وف" else word
        return _arabic_word(folded, written)
    return _english_word(folded)


def _is_english_word(word: str) -> bool:
    return bool(word) and word[0].isascii() and word[0].isalpha()


def subjects(words: Sequence[str]) -> list[str | None]:
    """For each word, who the nearest person-naming word before it names:
    ``SPEAKER``, ``OTHER``, or None when no word before it (in its sentence,
    or by name before it) names anyone. Punctuation marks are words of their
    own."""
    out: list[str | None] = []
    last: str | None = None
    introduced = False  # *last* is someone introduced by name
    clause_start = True
    previous = ""
    for i, word in enumerate(words):
        out.append(last)
        if word in _SENTENCE_END:
            if not introduced:
                last = None
            clause_start, previous = True, word
            continue
        w = _classify(word)
        names = w.person
        if w.copula and not clause_start:
            names = None  # the copula
        if w.bare_noun and last == SPEAKER and (previous.lower() in _EN_ARTICLES or not _is_english_word(word)):
            names = None  # describes the speaker: "أنا مدير", "I'm a teacher"
        if w.her:
            following = words[i + 1].lower() if i + 1 < len(words) else ""
            if not _is_english_word(following) or following in _EN_AFTER_OBJECT:
                names = None  # "I told her."
        if names:
            # Someone introduced by name stays introduced while the words
            # after go on naming someone else ("اسمها نورة وهي ...").
            introduced = w.introduces or (introduced and names == OTHER)
            last = names
        clause_start = word in _PUNCTUATION
        previous = word
    return out


# ── does the user say this, with no one else named? ──────────────────────

#: A word, a punctuation mark of its own, or a line break.
_TOKEN_RE = re.compile(r"[^\s.,!?،؛؟:;()\"«»“”]++|[.,!?،؛؟:;()]|\n")
#: Letters written onto a word before a value ("بدبي", "والرياض", "وبالكويت").
_PREPOSITION_LETTERS = "وفبل"
#: Arabic vowel marks a word may carry after a letter written onto it.
_MARKS = "".join(chr(c) for c in range(0x064B, 0x0660)) + "ٰ"


def _fold(text: str) -> str:
    from kazma_core.documents.arabic import fold_for_search

    return fold_for_search(text)


def _token_holds(token: str, word: str) -> tuple[int, bool] | None:
    """Whether the folded *token* is *word*: ``(cut, same)`` -- how many
    letters written onto it to drop ("بدبي" is "دبي" after one), and whether
    what is left is the word letter for letter (else it differs only by the
    article "ال"). None when it is not the word."""
    bare = word[2:] if word.startswith("ال") and len(word) > 3 else word
    for cut in (0, 1, 2):
        if cut and (len(token) - cut < 2 or any(c not in _PREPOSITION_LETTERS for c in token[:cut])):
            continue
        rest = token[cut:]
        if rest == word:
            return cut, True
        if rest in (bare, "ال" + bare) or (token[:cut].endswith("ل") and "ال" + rest == word):
            return cut, False
    return None


def _third_person_pronoun(token: str) -> bool:
    folded = _fold(token)
    bare = folded[1:] if len(folded) > 2 and folded[0] in "وف" else folded
    return folded.replace("’", "'").split("'", 1)[0] in _EN_PRONOUNS or bare in _AR_PRONOUNS


def names_no_one_else(message: str | None, value: str | None) -> str | None:
    """The user's own spelling of *value* when it stands in the user's own
    words of *message* (``own_words``: never a quote, an attached document
    or a text to translate) with no one but the speaker named before it in
    its sentence (:func:`subjects`) and no third-person pronoun after it
    ("I think Rotterdam is where she lives"), in a sentence that states it,
    else None: "Moved to Rotterdam last month" (no one named) and "We moved
    to Rotterdam" answer, "My sister moved to Rotterdam" does not. The
    model's reading of the user's fact is put to the user only when this
    answers (``memory/changes.py``); it never gives the user's trust by
    itself (the second security review of F1, 2026-11-18)."""
    return _said(message, value, (SPEAKER, None))


def _negates(token: str, following: str) -> bool:
    """Whether *token* denies what follows it: a negation word, or one
    written with "و"/"ف" onto it ("وما"). "No"/"لا" before a mark or at the
    end answer instead ("No, Lisbon.")."""
    word = _fold(token).lower().replace("’", "").replace("'", "")
    if word not in _NEGATIONS and not (len(word) > 2 and word[0] in "وف" and word[1:] in _NEGATIONS):
        return False
    if word in ("no", "لا") and (not following or following in _PUNCTUATION or following == "\n"):
        return False
    return True


def _asserted(raw: list[str]) -> list[bool]:
    """For each word of *raw*: whether its sentence states something -- no
    negation before it in its sentence, the sentence neither a question
    (``?``/``؟`` at its end, or a word that asks at its start) nor a request
    to act on a text. One pass each way."""
    n = len(raw)
    asks_at_end = [False] * (n + 1)
    for j in range(n - 1, -1, -1):
        asks_at_end[j] = raw[j] in _QUESTION_END or (raw[j] not in _SENTENCE_END and asks_at_end[j + 1])
    out = [True] * n
    start, denied = True, False
    asking = False
    for j, token in enumerate(raw):
        if start and token not in _SENTENCE_END:
            asking = _fold(token).lower() in _ASKING_STARTS
            start, denied = False, False
        out[j] = not (denied or asking or asks_at_end[j])
        if token in _SENTENCE_END:
            start, denied, asking = True, False, False
        elif _negates(token, raw[j + 1] if j + 1 < n else ""):
            denied = True
    return out


def _said(message: str | None, value: str | None, accept: tuple[str | None, ...]) -> str | None:
    """The user's own spelling of *value* where the nearest person-naming
    word before it is one of *accept*, in a sentence that states it
    (:func:`_asserted`: never denied before it, never a question or a
    request -- "I never lived in Paris", "Should I move to Rotterdam?" and
    "Summarize this page about Dubai" say nothing of the user), else None."""
    from kazma_core.memory.own_words import own_words

    pieces = str(value or "").replace("_", " ").split()
    words = [_fold(p) for p in pieces]
    if not words or not all(words) or not message:
        return None
    raw = _TOKEN_RE.findall(own_words(message))
    folded = [_fold(t) for t in raw]
    named = subjects(raw)
    stated = _asserted(raw)
    # Where each word's sentence ends, and whether a third-person pronoun
    # comes after it in that sentence: one pass from the end.
    pronoun_after = [False] * (len(raw) + 1)
    for j in range(len(raw) - 1, -1, -1):
        pronoun_after[j] = False if raw[j] in _SENTENCE_END else (
            pronoun_after[j + 1] or _third_person_pronoun(raw[j]))
    k = len(words)
    for i in range(len(raw) - k + 1):
        if named[i] not in accept or not stated[i]:
            continue
        held = [_token_holds(folded[i + j], words[j]) for j in range(k)]
        if any(h is None for h in held):
            continue
        if pronoun_after[i + k]:
            continue
        # The user's spelling, without a preposition written onto it; where
        # only the article differs, the model's word (its own letters).
        return " ".join(raw[i + j][cut:].lstrip(_MARKS) if same else pieces[j]
                        for j, (cut, same) in enumerate(h for h in held if h is not None))
    return None
