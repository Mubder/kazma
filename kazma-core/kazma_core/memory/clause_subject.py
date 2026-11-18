"""Who a clause is about: the speaker, someone else, or no one named.

A fact goes under the user only when the user said it about themself. "My
sister moved to Rotterdam" holds a place in the user's own message, but the
clause's subject is the sister. This module answers one question for each
word of a message -- who did the words before it last name? -- in Arabic,
English and the two mixed:

* walking back from the word, the nearest word that names a person decides;
* the speaker is named by a first-person pronoun ("أنا", "احنا", "نحن", "I",
  "we") or by "اسمي" (my name);
* someone else is named by a relative or a companion, in any form ("أختي",
  "أخوي", "صديقه", "زوجتي", "my manager", "sisters"), a third-person pronoun
  ("هي", "هم", "she", "they"), a name given as someone else's ("اسمها"), or a
  verb reporting what someone else said ("قالت", "يقول");
* an Arabic "هو"/"هي" inside a clause, after a word and not after
  punctuation, is the copula ("اسمي هو أحمد", "فيلمي المفضل هو الرسالة"),
  which names no one: a pronoun names someone only where its clause begins;
* with no such word, no one is named, and each caller decides: the Arabic
  quick pass reads a participle or a past verb with no subject of its own as
  the speaker's ("ساكن في دبي").

The rule leans one way. A turn that names someone else and then speaks of the
user with no pronoun ("زوجتي تحب القهوة. ساكن في الكويت") is read as about
someone else: keeping nothing loses a fact the deep pass can still read, while
filing another person's fact under the user would replace what the user said.
So a word is someone else's on a wide list, and the speaker's on a narrow one.

``subjects`` takes the message as words, each punctuation mark a word of its
own, and answers for every word in one pass.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

__all__ = ["OTHER", "SPEAKER", "speaks_of_self", "subjects"]

SPEAKER = "speaker"
OTHER = "other"

_PUNCTUATION = set(".,!?،؛؟:;()")

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
#: Someone else wherever they stand: "his/her name", reporting verbs.
_AR_OTHER_WORDS = {"اسمه", "اسمها", "اسمهم", "اسمهن", "قال", "قالت", "قالوا", "قالو", "يقول",
                   "تقول", "يقولون", "يقولو", "جدتي", "جدته", "جدتها"}
#: People named in any form: bare, or with a possessive ("أختي", "أخته").
_AR_PEOPLE = {"اخت", "اخو", "اخوان", "اخوات", "خوات", "ابن", "بنت", "زوج", "زوجه", "والد", "والده",
              "صديق", "صديقه", "صاحب", "صاحبه", "رفيق", "رفيقه", "زميل", "زميله", "مدير", "مديره",
              "اصدقاء", "اصدقا", "عيال", "اطفال", "اولاد", "بنات", "حفيد", "حفيده", "نسيب", "نسيبه",
              "خطيب", "خطيبه", "دكتور", "دكتوره", "استاذ", "استاذه", "خوي", "جيران"}
#: People named only with a possessive. Bare, each is also another word:
#: "ام" in "أم صلال", "اخ" an exclamation, "خال" empty, "جار" ongoing,
#: "ربع" a quarter. Left out: "جد" ("جدة" is also Jeddah), "اب" ("أبها").
_AR_PEOPLE_OWNED = {"اخ", "ام", "ابو", "ولد", "عم", "عمه", "خال", "خاله", "جار", "جاره", "ربع",
                    "اهل", "عايله", "عائله"}
#: A possessive: my, your, his, her, our, their.
_AR_SUFFIXES = ("ي", "ك", "كي", "كم", "ه", "ها", "هم", "هن", "نا")
#: Prepositions and conjunctions written onto a word, longest first.
_AR_PREFIXES = ("وال", "فال", "بال", "لل", "ال", "و", "ف", "ب", "ل")
#: Never someone else: "أبي" is also "I want" in the Gulf.
_AR_NEVER = {"ابي"}

_EN_SPEAKER = {"i", "we", "me", "us", "my", "our", "mine", "ours", "myself", "ourselves"}
_EN_PRONOUNS = {"he", "she", "they"}
_EN_OTHER = {"he", "she", "they", "him", "her", "his", "hers", "them", "their", "theirs",
             "sister", "brother", "sibling", "mother", "mom", "mum", "father", "dad", "parent",
             "wife", "husband", "partner", "son", "daughter", "child", "children", "kid",
             "friend", "boss", "manager", "colleague", "coworker", "co-worker", "neighbour", "neighbor",
             "cousin", "uncle", "aunt", "grandma", "grandpa", "grandmother", "grandfather",
             "boyfriend", "girlfriend", "fiance", "fiancé", "fiancee", "fiancée", "roommate", "flatmate",
             "teacher", "nephew", "niece"}


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


def _arabic_person(word: str, written: str = "") -> tuple[str | None, bool]:
    if _arabic_verb_person(word, written):
        return SPEAKER, False
    forms = [word] + [word[len(p):] for p in _AR_PREFIXES
                      if word.startswith(p) and len(word) - len(p) >= 2]
    for form in forms:
        if form in _AR_NEVER:
            return None, False
        if form in _AR_SPEAKER:
            return SPEAKER, False
        if form in _AR_PRONOUNS:
            # Written with "و"/"ف" ("وهي") it begins a clause of its own.
            return OTHER, form == word
        if form in _AR_OTHER_WORDS or form in _AR_PEOPLE:
            return OTHER, False
        for suffix in _AR_SUFFIXES:
            if form.endswith(suffix) and len(form) - len(suffix) >= 2 \
                    and _is_person_stem(form[: -len(suffix)]):
                return OTHER, False
    return None, False


def _english_person(word: str) -> str | None:
    head = word.replace("’", "'").split("'", 1)[0]  # "i'm" -> "i", "sister's" -> "sister"
    if head in _EN_SPEAKER:
        return SPEAKER
    if head in _EN_OTHER or (head.endswith("s") and head[:-1] in _EN_OTHER):
        return OTHER
    return None


@lru_cache(maxsize=4096)
def _classify(word: str) -> tuple[str | None, bool]:
    """``(person, copula)``: who the word names, and whether it is a bare
    Arabic pronoun that is the copula when it stands inside a clause."""
    from kazma_core.documents.arabic import fold_for_search

    folded = fold_for_search(word)
    if not folded:
        return None, False
    if any("؀" <= c <= "ۿ" for c in folded):
        written = word[1:] if len(word) > 3 and word[0] in "وف" else word
        return _arabic_person(folded, written)
    return _english_person(folded), False


def subjects(words: Sequence[str]) -> list[str | None]:
    """For each word, who the nearest person-naming word before it names:
    ``SPEAKER``, ``OTHER``, or None when no word before it names anyone.
    Punctuation marks are words of their own."""
    out: list[str | None] = []
    last: str | None = None
    clause_start = True
    for word in words:
        out.append(last)
        person, copula = _classify(word)
        if person and not (copula and not clause_start):
            last = person
        clause_start = word in _PUNCTUATION
    return out


# ── the strict reading: does the user say this of themself? ──────────────

#: A word, a punctuation mark of its own, or a line break.
_TOKEN_RE = re.compile(r"[^\s.,!?،؛؟:;()\"«»“”]++|[.,!?،؛؟:;()]|\n")
#: Where a sentence ends: a value's subject is looked for inside its sentence.
_SENTENCE_END = {".", "!", "?", "؟", "\n"}
#: Letters written onto a word before a value ("بدبي", "والرياض", "وبالكويت").
_PREPOSITION_LETTERS = "وفبل"


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


def speaks_of_self(message: str | None, value: str | None) -> str | None:
    """The user's own spelling of *value* when the user says it of
    themself in *message*, else None.

    The value's words must stand in the user's own words (``own_words``:
    never a quote, an attached document or a text to translate), and in
    their sentence the nearest word before them that names a person must
    name the speaker -- "I", "we", "my", "أنا", "اسمي", a first-person verb
    ("أشتغل", "انتقلنا") -- with no third-person pronoun after them in the
    sentence ("I think Rotterdam is where she lives"). Stricter than the
    quick pass, where no one named is the speaker: "Moved to Rotterdam last
    month" names no one and is None. A fact the model read is the user's own
    statement only when this answers.
    """
    from kazma_core.memory.own_words import own_words

    pieces = str(value or "").replace("_", " ").split()
    words = [_fold(p) for p in pieces]
    if not words or not all(words) or not message:
        return None
    raw = _TOKEN_RE.findall(own_words(message))
    folded = [_fold(t) for t in raw]
    k = len(words)
    for i in range(len(raw) - k + 1):
        held = [_token_holds(folded[i + j], words[j]) for j in range(k)]
        if any(h is None for h in held):
            continue
        start = i
        while start > 0 and raw[start - 1] not in _SENTENCE_END:
            start -= 1
        end = i + k
        while end < len(raw) and raw[end] not in _SENTENCE_END:
            end += 1
        if subjects(raw[start:i + 1])[-1] != SPEAKER:
            continue
        if any(_third_person_pronoun(t) for t in raw[i + k:end]):
            continue
        # The user's spelling, without a preposition written onto it; where
        # only the article differs, the model's word (its own letters).
        return " ".join(raw[i + j][cut:].lstrip(_MARKS) if same else pieces[j]
                        for j, (cut, same) in enumerate(h for h in held if h is not None))
    return None


#: Arabic vowel marks a word may carry after a letter written onto it.
_MARKS = "".join(chr(c) for c in range(0x064B, 0x0660)) + "ٰ"
