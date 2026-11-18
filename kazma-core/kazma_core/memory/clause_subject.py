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

from collections.abc import Sequence
from functools import lru_cache

__all__ = ["OTHER", "SPEAKER", "subjects"]

SPEAKER = "speaker"
OTHER = "other"

_PUNCTUATION = set(".,!?،؛؟:;()")

# Arabic, written folded (``documents.arabic.fold_for_search``).
_AR_SPEAKER = {"انا", "احنا", "نحن", "اسمي"}
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

_EN_SPEAKER = {"i", "we"}
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


def _arabic_person(word: str) -> tuple[str | None, bool]:
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
        return _arabic_person(folded)
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
