"""Kazma's names for the facts a person tells about themself, and the other
names a model gives them.

The deep pass names its facts freely. Under another name, the model's
reading of the user's home stood beside the user's own: ``home_city Dubai``,
``located_in Dubai`` and ``resides_in Dubai`` were each a fact of their own
next to ``lives_in Kuwait``, and no rule compared them (the memory framework
audit's F4; the second security review of F1, 2026-11-18). This is the
first layer of F4's table: for the user's own single-valued facts, each name
a model gives them is read as Kazma's (``_canonical_for_user``), so the trust
gate and the pending change compare them as one fact.

Matched as ``predicates`` matches names -- order, filler words and a plural
"s" aside -- but a name that puts the thing in the plural ("homes", "names")
is a list, never one of these (``belief_mutation._names_a_list``).

Only facts about the user: "Acme located_in Lisbon" is a company's place,
not a home. Only the names the model returns (the deep pass); the quick
pass writes Kazma's own names. The table grows to F4's forty names after the
owner's review (decision 16); English names only, as the model names its
facts in English whatever the language of the turn.
"""

from __future__ import annotations

import re
from functools import lru_cache

__all__ = ["ASKED_AS_MINE", "ASKED_WITH", "KINDS", "NOT_ASKED_BY_WORDS", "asked_as_mine", "asked_words",
           "kinds_asked", "kinds_said_of_the_user", "names_one_value", "plural_of_a_kind", "users_names",
           "users_predicate"]

#: Kazma's name -> the names a model gives the same fact about a person.
KINDS: dict[str, tuple[str, ...]] = {
    "lives_in": ("located_in", "resides_in", "residence", "home_city", "current_city", "home", "based_in",
                 "living_in", "lives_at", "home_location", "city_of_residence", "resident_of", "residence_city",
                 # The third review of F1, item 5.
                 "hometown", "home_town", "location", "city", "current_location", "address", "moved_to"),
    "name_is": ("full_name", "real_name", "legal_name", "is_called", "called", "named", "first_name", "user_name"),
    "preferred_name": ("nickname", "goes_by", "prefers_to_be_called", "preferred_nickname"),
    "works_at": ("employer", "works_for", "employed_at", "employed_by", "company", "workplace",
                 "current_employer", "employer_name"),
    "current_role": ("job_title", "occupation", "profession", "job_role", "position", "works_as", "job"),
}

#: The table's names that also tell something the user did: "called" my mom,
#: "moved_to" Porto, "named" the cat. With the day it happened the fact is an
#: event of its own, never the user's name or home (the review of W4,
#: 2026-11-24: "I called my mom" became ``user name_is "my mom"``).
_EVENT_NAMES = frozenset({"called", "named", "moved_to"})


#: The words a person asks each of these facts with, English and Arabic
#: (Gulf and MSA), as written; read folded, as search reads a question
#: (``query_terms.content_terms``). A fact's words are Kazma's English name
#: for it ("user lives in بورتو"): an English question matched the name
#: ("live" in lives_in, worth recall's whole fact floor) and an Arabic one no
#: word of it, so the Arabic question had to clear the floor on meaning alone
#: -- "وين أسكن الحين؟" found the user's home once in four runs of the
#: real-use suite (the memory plan's finding 10, 2026-11-22). The same sense
#: only, in both languages -- living, working, a name, an allergy, a
#: favourite -- never a word of other common senses ("home", "بيت", "شغّل"):
#: recall's word coverage reads them beside the fact's own words
#: (``recall._belief_candidate``) and asks its keyword index for the kinds a
#: question names (``recall._belief_search_words``). The fact's vector is
#: left as it is (W3 measures what this table gives).
ASKED_WITH: dict[str, tuple[str, ...]] = {
    "lives_in": ("live", "lives", "lived", "reside", "resides", "residing",
                 "أسكن", "نسكن", "تسكن", "يسكن", "ساكن", "ساكنة", "ساكنين", "سكني", "سكنت", "سكنا",
                 "أعيش", "نعيش", "تعيش", "يعيش", "عايش", "عايشة", "عايشين",
                 "مقيم", "مقيمة", "مقيمين", "أقيم", "نقيم", "تقيم", "يقيم"),
    "works_at": ("work", "works", "working", "worked", "employer", "employed",
                 "أشتغل", "نشتغل", "تشتغل", "يشتغل", "شغال", "شغالة", "أعمل", "نعمل", "تعمل", "يعمل",
                 "أداوم", "نداوم", "تداوم", "يداوم", "دوامي", "شغلي", "موظف", "موظفة"),
    "current_role": ("role", "job", "work", "works", "working", "profession", "occupation",
                     "وظيفة", "وظيفتي", "وظيفتك", "وظيفته", "وظيفتها", "مهنة", "مهنتي", "مهنتك", "مهنته",
                     "مهنتها", "شغلي", "شغلك", "شغله", "شغلها", "أشتغل", "تشتغل", "يشتغل", "منصب", "منصبي"),
    "name_is": ("name", "named", "called", "اسم", "اسمي", "اسمك", "اسمه", "اسمها"),
    "preferred_name": ("name", "call", "called", "nickname", "اسم", "اسمي", "ناديني", "نادني", "أناديك",
                       "لقب", "لقبي"),
    "allergic_to": ("allergic", "allergy", "allergies", "حساسية", "حساسيتي"),
    #: Every favourite_<kind>: the kind's own words come with it
    #: (:func:`_favourite_kind_words`).
    "favorite_": ("favorite", "favourite", "مفضل", "مفضلة", "المفضل", "المفضلة"),
}
#: The facts a quick pass writes that no word table asks: meaning decides.
NOT_ASKED_BY_WORDS: dict[str, str] = {
    "prefers": "like, love, use, أحب, أستخدم are said of anything; a table would cover every question",
}

#: Words that ask a fact only when they are said of the user: "my city",
#: "home for me", "مدينتي", "وين بيتي" ask where the user lives, while "which
#: city is the capital", "the home page", "مدينة الملاهي" and "بيت الشعر"
#: ask nothing of the user (the main session's decision, 22 Nov, after the
#: real-use suite's Arabic "أي مدينة صارت بيتي هالفترة؟" found no fact). Per
#: kind: the words, as written, and how each language says they are the
#: user's -- English in the phrase (its "my" is no content word), Arabic in
#: the word itself (the possessive suffix) -- and the kind's own word they
#: are read as (one of :data:`ASKED_WITH`'s).
#:
#: The word must be the thing asked, in both languages: never a word that
#: describes another ("my home Wi-Fi", "my home address", "my home town";
#: "شبكة بيتي", "عنوان بيتي"), never a place something is in ("in my home",
#: "في بيتي"), never another kind of city ("my favourite city", "مدينتي
#: المفضلة", "مدينتي الأصلية"). The small store's abstain question q805, "What
#: is my home Wi-Fi network called?", showed the home before this rule.
ASKED_AS_MINE: dict[str, dict[str, object]] = {
    "lives_in": {
        "words": ("city", "home", "town", "مدينة", "بيت", "ديرة"),
        "en": re.compile(
            r"(?<!\bin )(?<!\bat )(?<!\bof )(?<!\bfrom )(?<!\bto )(?<!\bnear )(?<!\binside )"
            r"\bmy (?:city|town|home)(?='s\b|\s*(?:$|[?.!,;:])|\s+(?:is|was|now|these|this|at|in|right"
            r"|currently|nowadays|today|then|lately)\b)"
            r"|\b(?:city|town|home) (?:for|to) (?:me|us)\b"
            r"|\b(?:i|we) call home\b"),
        "ar": ("مدينتي", "بيتي", "ديرتي", "مدينتنا", "بيتنا", "ديرتنا"),
        # The word an Arabic first-person word may follow (folded): a question
        # word, "it is", a verb of becoming -- else it is another noun's
        # ("عنوان بيتي") or a place something is in ("في بيتي").
        "ar_after": ("وين", "فين", "شنو", "وش", "ايش", "شو", "اي", "هي", "هو", "ماهي", "وينها",
                     "صار", "صارت", "يصير", "تصير", "كان", "كانت"),
        # Words after it that make it another city ("المفضلة", "الأصلية").
        "ar_not_before": ("المفضله", "المفضل", "الاصليه", "الاصلي"),
        "as": "live",
    },
}


@lru_cache(maxsize=1)
def _mine_sets() -> dict[str, tuple[frozenset[str], frozenset[str], str]]:
    """Per kind of :data:`ASKED_AS_MINE`: its words and its Arabic
    first-person words, folded (``query_terms.content_terms``), and the
    kind's word, folded."""
    from kazma_core.memory.query_terms import content_terms

    out = {}
    for key, rule in ASKED_AS_MINE.items():
        mine = frozenset(content_terms(" ".join(rule["ar"])))  # type: ignore[arg-type]
        words = frozenset(content_terms(" ".join(rule["words"]))) | mine  # type: ignore[arg-type]
        out[key] = (words, mine, content_terms(str(rule["as"]))[0])
    return out


@lru_cache(maxsize=1)
def _arabic_neighbours() -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    """Per kind: the words an Arabic first-person word may follow, and the
    words it may not come before, folded as search folds them."""
    from kazma_core.documents.arabic import fold_for_search

    return {key: (frozenset(fold_for_search(w) for w in rule["ar_after"]),  # type: ignore[union-attr]
                  frozenset(fold_for_search(w) for w in rule["ar_not_before"]))  # type: ignore[union-attr]
            for key, rule in ASKED_AS_MINE.items()}


_WORD_RE = re.compile(r"[^\W_]+")


def _said_of_the_user_ar(question: str, mine: frozenset[str], after: frozenset[str],
                         not_before: frozenset[str]) -> bool:
    """Whether an Arabic first-person word of *mine* is the thing asked: the
    question's first word or after one of *after*, and not before one of
    *not_before*."""
    from kazma_core.documents.arabic import fold_for_search

    words = _WORD_RE.findall(fold_for_search(question or ""))
    for i, word in enumerate(words):
        if word not in mine:
            continue
        if (i == 0 or words[i - 1] in after) and (i + 1 == len(words) or words[i + 1] not in not_before):
            return True
    return False


def kinds_said_of_the_user(question: str) -> tuple[str, ...]:
    """The kinds of :data:`ASKED_AS_MINE` *question* asks by saying their
    words of the user ("What is my city?", "وين بيتي؟" -> ``lives_in``). Such
    a question asks that fact by its wording, whatever meaning makes of it:
    bge-m3 puts "What is my city?" below an unrelated question's background
    for "user lives in Porto", so recall shows the asker's own fact of that
    kind (``recall._rank_by_evidence``'s ``named``)."""
    lowered = " ".join(str(question or "").lower().replace("’", "'").split())
    neighbours = _arabic_neighbours()
    return tuple(key for key, (_words, mine, _word) in _mine_sets().items()
                 if ASKED_AS_MINE[key]["en"].search(lowered)  # type: ignore[union-attr]
                 or _said_of_the_user_ar(question, mine, *neighbours[key]))


def asked_as_mine(question: str, terms: list[str]) -> list[str]:
    """*terms* (a question's content words) with the words :data:`ASKED_AS_MINE`
    names read as their fact's own word ("live") when the question says them
    of the user, in either language; else *terms* unchanged. Recall's fact
    lane asks it before it looks up the kinds a question names and before it
    measures a fact's word coverage (``recall._belief_search_words``,
    ``recall._rank_by_evidence``): never the other lanes, where "city" in a
    turn is a word like any other."""
    out = list(terms)
    sets = _mine_sets()
    for key in kinds_said_of_the_user(question):
        words, _mine, word = sets[key]
        out = [word if term in words else term for term in out]
    return list(dict.fromkeys(out))


def _favourite_kind_words(kind: str) -> tuple[str, ...]:
    """The words of a favourite's kind ("color"), English and Arabic: the
    Arabic quick pass's own table (``arabic_facts._KINDS``), with "my"
    ("لوني", "رياضتي")."""
    from kazma_core.memory.arabic_facts import favourite_kind_words

    return (kind, *({"color": ("colour",)}.get(kind, ())), *favourite_kind_words(kind))


def _table_key(predicate: str) -> str | None:
    name = str(predicate or "")
    if name in ASKED_WITH:
        return name
    if name.startswith("favorite_") and len(name) > len("favorite_"):
        return "favorite_"
    return None


@lru_cache(maxsize=256)
def asked_words(predicate: str) -> str:
    """The words *predicate*'s fact is asked with, folded, one text ("" when
    the table names none): recall reads them beside the fact's own words."""
    from kazma_core.memory.query_terms import content_terms

    key = _table_key(predicate)
    if key is None:
        return ""
    words = list(ASKED_WITH[key])
    if key == "favorite_":
        words += _favourite_kind_words(predicate[len("favorite_"):])
    return " ".join(content_terms(" ".join(words)))


@lru_cache(maxsize=1)
def _asked_sets() -> dict[str, frozenset[str]]:
    from kazma_core.memory.query_terms import content_terms

    return {key: frozenset(content_terms(" ".join(words))) for key, words in ASKED_WITH.items()}


def kinds_asked(terms: list[str] | frozenset[str]) -> tuple[str, ...]:
    """The facts a question's content words (``query_terms.content_terms``,
    its time words out) ask by the table: Kazma's names, and "favorite" for
    a favourite of any kind. Recall's keyword index is asked for them."""
    held = set(terms)
    out = []
    for key, words in _asked_sets().items():
        if held & words:
            out.append("favorite" if key == "favorite_" else key)
    return tuple(out)


@lru_cache(maxsize=1)
def _aliases() -> dict[frozenset[str], tuple[str, str]]:
    from kazma_core.memory.predicates import _predicate_key

    table: dict[frozenset[str], tuple[str, str]] = {}
    for kazma, others in KINDS.items():
        for name in (kazma, *others):
            table[_predicate_key(name)] = (kazma, name)
    return table


def _canonical_for_user(predicate: str) -> str:
    """Kazma's name for *predicate* when it names one of the user's facts in
    the table ("home_city", "city_home", "resides_in" -> "lives_in"), else
    *predicate* as it is. A plural ("homes", "employers") stays as it is: a
    list."""
    from kazma_core.memory.belief_mutation import _plural_of
    from kazma_core.memory.predicates import _predicate_key

    named = _normalised(predicate)
    key = _predicate_key(named)
    found = _aliases().get(key) if key else None
    if found is None:
        return predicate
    kazma, name = found
    return predicate if _plural_of(named, name) else kazma


#: A word's start inside a camelCase name ("livesIn", "currentLocation").
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _normalised(predicate: str) -> str:
    """*predicate* in snake case whatever its separators: "lives-in",
    "lives in", "lives.in", "livesIn", "currentLocation" (the main
    session's check of the third review, fix 4)."""
    spaced = _CAMEL_RE.sub(" ", str(predicate or "")).lower()
    return "_".join(re.sub(r"[-./\s]+", " ", spaced).split())


def _is_the_user(subject: object) -> bool:
    """Whether *subject* names the user, in any case or spacing ("User",
    " USER ")."""
    return str(subject or "").strip().casefold() == "user"


def _users_subject(subject: object) -> object:
    """``"user"`` for the user in any case, any other subject as it is."""
    return "user" if _is_the_user(subject) else subject


def users_predicate(subject: object, predicate: str) -> str:
    """The name a fact is written under: Kazma's name (:func:`_canonical_for_user`)
    when the subject is the user, in any case ("User"), else *predicate* as
    it is."""
    if not _is_the_user(subject):
        return predicate
    return _canonical_for_user(predicate)


def users_names(subject: object, predicate: str) -> tuple[object, str]:
    """The subject and the name a fact naming one of the user's facts is
    written under: "User" is the user, and the user's facts go by Kazma's
    names (:func:`users_predicate`). The one place for both, which every
    writer of a fact someone else named calls before ``mutate_belief`` --
    the deep pass, the memory tools, the Memory page's link, the V1
    backfill: a memory tool wrote ``home_city`` beside the user's
    ``lives_in`` at the user's trust (the main session's check of the third
    review, fix 1). ``tests/test_user_fact_names_every_writer.py`` holds
    every writer to it."""
    return _users_subject(subject), users_predicate(subject, predicate)


def names_one_value(subject: object, predicate: str) -> bool:
    """Whether *predicate* names a fact a person holds one value of: by
    Kazma's own class (``belief_mutation``), or another name of one of the
    user's such facts ("home_city") -- unless that name also tells something
    the user did ("called", "moved_to": :data:`_EVENT_NAMES`). An event's day
    on such a fact is when it began."""
    from kazma_core.memory.belief_mutation import _classify_predicate
    from kazma_core.memory.predicates import _predicate_key

    if _classify_predicate(predicate, None) != "set":
        return True
    kazma = users_predicate(subject, predicate)
    if kazma == predicate or _classify_predicate(kazma, None) == "set":
        return False
    found = _aliases().get(_predicate_key(_normalised(predicate)))
    return found is not None and found[1] not in _EVENT_NAMES


def plural_of_a_kind(predicate: str) -> bool:
    """Whether *predicate* puts one of the table's names in the plural
    ("homes", "employers"): a list."""
    from kazma_core.memory.belief_mutation import _plural_of
    from kazma_core.memory.predicates import _predicate_key

    named = _normalised(predicate)
    key = _predicate_key(named)
    found = _aliases().get(key) if key else None
    return found is not None and _plural_of(named, found[1])
