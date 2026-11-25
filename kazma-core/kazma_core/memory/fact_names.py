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
pass writes Kazma's own names. English names only, as the model names its
facts in English whatever the language of the turn.

**The second part of the table** (the memory plan's W6, step 2, 2026-11-25;
decision 16 named forty names, measured about ten kinds are needed). Near
doubles are rare: of 154 pairs of facts in two names that look alike in the
pilots' stores, read one by one, about 20 were one statement, so nothing here
joins facts by meaning, and a list is never merged:

* a person's role or employer goes under the name they ALREADY have a single
  value of it under, and the user's role and age under Kazma's when they have
  none (:func:`name_in_use`, asked with the store at hand: a list of four
  occupations stays four);
* a list is read by its kind (:data:`LISTS`): "plans_to", "planning" and
  "intends_to" are one list of plans when all are asked for, each under its
  own name.
"""

from __future__ import annotations

import re
import sqlite3
from functools import lru_cache
from typing import Any

__all__ = ["ASKED_ALONE", "ASKED_AS_MINE", "ASKED_WITH", "KINDS", "LISTS", "NOT_ASKED_BY_WORDS", "ONE_VALUE_NAMES",
           "asked_as_mine", "asked_words", "fact_kind", "indexed_words", "kinds_asked", "kinds_said_of_the_user", "list_kind",
           "name_in_use", "names_one_value", "normalised_name", "plural_of_a_kind", "users_names", "users_predicate"]

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

#: Names read as Kazma's only where the fact is one value (a functional or
#: state label) and no list is held under them (:func:`name_in_use`): "has_occupation career coach, influencer, teacher, business
#: owner" is a list of true jobs, "has_role Éponine" a part in a play. ``age_is``
#: is not in :data:`KINDS`: a first reading of one of those is put on the
#: Memory page. ``has_role`` is in neither: it says a part as often as a job.
ONE_VALUE_NAMES: dict[str, tuple[str, ...]] = {
    "current_role": ("role", "has_occupation", "has_profession", "has_job", "has_job_title", "current_job",
                     "current_occupation", "current_profession", "employed_as"),
    "age_is": ("has_age", "current_age"),
}

#: The names another person's role and employer go by. Never a place, and
#: never a name a thing has too ("position" of a shelf, "company" of a phone).
_ANOTHERS: dict[str, tuple[str, ...]] = {
    "current_role": ("current_role", "job_title", "occupation", "profession", "job_role", "works_as", "job",
                     *ONE_VALUE_NAMES["current_role"]),
    "works_at": ("works_at", "employer", "works_for", "employed_at", "employed_by", "workplace",
                 "current_employer", "employer_name"),
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
    # The kinds below are read by a name's words (:data:`LISTS`, :data:`_FEW`).
    # Arabic says whose it is in the word ("خططي"): a word that says nobody's
    # ("خطة المبنى", "كم هدف سجل؟") asks none of them, and an event's words are
    # its "I did" forms, as English names it in the past: "شنو أشتري هدية؟"
    # (what shall I buy) asks no purchase. Only kinds an Arabic or an English
    # question was measured to miss (``benchmark_kinds``).
    "plans": ("plan", "plans", "planned", "planning", "intend", "خطتي", "خطتنا", "خططي", "خططنا", "أخطط", "نخطط",
              "ناوي", "ناوية", "ناوين", "أنوي", "ننوي"),
    "goals": ("goal", "goals", "هدفي", "أهدافي", "أهدافنا", "طموحي", "طموحاتي"),
    "interests": ("interest", "interests", "interested", "hobby", "hobbies", "اهتماماتي", "مهتم", "مهتمة", "هوايتي",
                  "هواياتي"),
    "bought": ("bought", "purchased", "purchases", "شريت", "شريته", "شريتها", "شرينا", "اشتريت", "اشتريته",
               "اشتريتها", "اشترينا", "مشترياتي"),
    "received": ("received", "استلمت", "استلمته", "استلمتها", "استلمنا", "تلقيت"),
    "met": ("met", "قابلت", "قابلته", "قابلتها", "قابلتهم", "قابلنا", "التقيت", "التقينا"),
    "attended": ("attended", "حضرت", "حضرته", "حضرتها", "حضرنا"),
    "finished": ("finished", "completed", "خلصت", "خلصته", "خلصتها", "كملت", "أكملت", "أنهيت",
                 "أنهيته", "أنهيتها"),
    "habits": ("habit", "habits", "routine", "عادتي", "عاداتي", "عوايدي", "روتيني"),
    "diet": ("diet", "dietary", "حميتي", "دايت"),
    "doctor": ("doctor", "physician", "دكتوري", "دكتورتي", "طبيبي", "طبيبتي"),
    "age_is": ("age", "عمري"),
    "birthday": ("birthday", "birthdate", "ميلادي", "مولدي"),
    "study": ("study", "studies", "studying", "أدرس", "ندرس", "دراستي", "جامعتي"),
    #: Every favourite_<kind>: the kind's own words come with it
    #: (:func:`_favourite_kind_words`).
    "favorite_": ("favorite", "favourite", "مفضل", "مفضلة", "المفضل", "المفضلة"),
}
#: The facts a quick pass writes that no word table asks: meaning decides.
NOT_ASKED_BY_WORDS: dict[str, str] = {
    "prefers": "like, love, use, أحب, أستخدم are said of anything; a table would cover every question",
}

#: Kinds asked only by a question of nothing else ("ما هي سيارتي؟"): beside
#: another word the question is of that word ("وش رقم سيارتي؟", its number).
ASKED_ALONE: dict[str, tuple[str, ...]] = {
    "car": ("car", "سيارتي", "سيارتنا"),
    "daughters": ("daughter", "daughters", "بنتي", "ابنتي", "بناتي"),
    "sons": ("son", "sons", "ولدي", "ابني"),
}

#: Words a fact's name may begin with that say nothing of its kind.
_LEADING = frozenset("is are was has have had currently current now active the a my user new".split())


def _one_of(*words: str, unless: str = "") -> Any:
    """A name whose first word is one of *words*, with none of *unless* in it."""
    first, out = frozenset(words), frozenset(unless.split())
    return lambda name: bool(name) and name[0] in first and not out.intersection(name)


def _holding(*words: str, unless: str = "") -> Any:
    """A name that holds one of *words*, and none of *unless*."""
    held, out = frozenset(words), frozenset(unless.split())
    return lambda name: bool(held.intersection(name)) and not out.intersection(name)


def _named(*names: str) -> Any:
    """One of *names*, whole."""
    every = {tuple(n.split("_")) for n in names}
    return lambda name: tuple(name) in every


#: The kinds of LISTS a model names in many ways -- "plans_to", "planning",
#: "intends_to_try", "has_plan" -- read from a name's words, the first that
#: fits. "Nothing is renamed": a question for all of them shows them as one
#: list (``recall._list_siblings``), each under the name it was told by. Only
#: where the names say ONE list: "uses_service" and "uses_tool", "owns" and
#: "has_item", "went_to" a shop and to bed were read as one and put eight tools
#: into "which food delivery services have I used?" (read on the pilots' blocks).
LISTS: dict[str, Any] = {
    "plans": _one_of("plan", "plans", "planned", "planning", "intends", "intend", "intending", "intention", "intentions",
                     unless="type price name cost tier provider renews expires status"),
    "goals": _holding("goal", "goals", "aim", "aims", "aspires", "aspiration", "ambition", "ambitions", "objective",
                      "objectives", unless="scored keeper team"),
    "interests": _one_of("interested", "interest", "interests", "hobby", "hobbies", unless="rate rates"),
    "bought": _one_of("bought", "purchased"),
    "attended": _one_of("attended", "participated"),
    "habits": _one_of("habit", "habits", "routine", "routines"),
}

#: The other kinds whose words :data:`ASKED_WITH` or :data:`ASKED_ALONE` holds:
#: one value, or a list under its own name.
_FEW: dict[str, Any] = {
    "received": _one_of("received"), "met": _one_of("met"), "finished": _one_of("finished", "completed"),
    "diet": _holding("diet", "dietary", "vegetarian", "vegan", unless="considering"),
    "doctor": _named("doctor", "doctor_is", "doctor_name", "physician", "primary_care_doctor", "family_doctor"),
    "age_is": _named("age", "age_is"),
    "birthday": _holding("birthday", "birthdate", unless="party gift"),
    "study": _one_of("studies", "studying", "enrolled", "university", "college", "major", "majors"),
    "car": _named("drives", "car", "car_is", "car_model", "vehicle", "owns_car", "drives_car"),
    "daughters": _named("daughter", "daughters", "daughter_is", "daughter_name"),
    "sons": _named("son", "sons", "son_is", "son_name"),
}

#: Names of the user's home that are never a company's or a thing's.
_HOME_NAMES = frozenset({"resides_in", "residence", "home_city", "hometown", "city_of_residence", "residence_city"})


def _words_of(predicate: str) -> list[str]:
    words = [w for w in normalised_name(predicate).split("_") if w]
    while len(words) > 1 and words[0] in _LEADING:
        words = words[1:]
    return words


@lru_cache(maxsize=4096)
def list_kind(predicate: str) -> str | None:
    """The kind of list *predicate* names (:data:`LISTS`: "planning_to_visit"
    -> "plans"), or None."""
    words = _words_of(predicate)
    return next((kind for kind, fits in LISTS.items() if fits(words)), None)


@lru_cache(maxsize=4096)
def fact_kind(predicate: str) -> str | None:
    """The kind of fact *predicate* names, by which it is asked
    (:data:`ASKED_WITH`'s keys), in whatever words the model named it: Kazma's
    own name, another name of a role, an employer or a home, a list's or one
    of the few below; None when no kind fits."""
    from kazma_core.memory.predicates import _predicate_key

    name = str(predicate or "")
    if name in ASKED_WITH:
        return name
    if name.startswith("favorite_") and len(name) > len("favorite_"):
        return "favorite_"
    named = normalised_name(name)
    anothers = _anothers().get(_predicate_key(named))
    if anothers:
        return anothers
    if named in _HOME_NAMES:
        return "lives_in"
    found = list_kind(name)
    if found:
        return found
    words = _words_of(name)
    return next((kind for kind, fits in _FEW.items() if fits(words)), None)


#: What the keyword index is asked for each kind, when its own name is not a
#: word of the facts' names (prefixes: "plan" is in "plans_to", "planning").
_INDEXED: dict[str, tuple[str, ...]] = {
    "current_role": ("current_role", "occupation", "profession", "job"), "works_at": ("works_at", "employer"),
    "plans": ("plan", "intend"), "goals": ("goal",), "interests": ("interest", "hobb"), "habits": ("habit", "routine"),
    "diet": ("diet", "vegetarian", "vegan"), "age_is": ("age",), "study": ("studies", "enrolled", "university"),
    "car": ("drives", "car"), "daughters": ("daughter",), "sons": ("son",), "favorite_": ("favorite",),
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
    "current_role": {
        # "living" alone is a room, a cost; only with what the user does.
        "words": ("living",),
        "en": re.compile(r"\b(?:do|am) (?:i|we) (?:do(?:ing)?|doing) for (?:a |my )?(?:living|work|job)\b"
                         r"|\bwhat do (?:i|we) work as\b|\bmy line of work\b"),
        "ar": ("شغلتي", "شغلتنا", "مهنتي", "وظيفتي"),
        "ar_after": ("شنو", "وش", "ايش", "شو", "ما", "ماهي", "هي", "هو"),
        "ar_not_before": ("المفضله", "السابقه", "القديمه", "الاولي"),
        "as": "job",
    },
    "diet": {
        "words": ("eat", "آكل", "آكله"),
        "en": re.compile(r"\b(?:can(?:'t| ?not)|should(?:n't| not)|must(?:n't| not)) (?:i|we) (?:not )?eat\b"
                         r"|\b(?:can|should|must) (?:i|we) not eat\b|\bwhat (?:can't|cannot|shouldn't|mustn't) (?:i|we) eat\b"
                         r"|\bwhat (?:foods? )?(?:do|should) (?:i|we) avoid(?: eating)?\b(?=\s*(?:$|[?.!]))"),
        "ar": (),
        "ar_after": (), "ar_not_before": (),
        "ar_said": re.compile(r"(?:^|\s)(?:ما|لا) (?:اقدر|اكدر|استطيع|يمكنني|يصير|لازم) (?:ان )?اكله?(?=\s|$|[؟?.,])"
                              r"|(?:^|\s)ممنوع (?:علي )?(?:من )?(?:اكل|اكله)(?=\s|$|[؟?.,])"),
        "as": "diet",
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


def _said_in_arabic(key: str, question: str) -> bool:
    """Whether *question* asks kind *key* by a whole Arabic phrase
    (``ar_said``), folded."""
    from kazma_core.documents.arabic import fold_for_search

    pattern = ASKED_AS_MINE[key].get("ar_said")
    return pattern is not None and bool(pattern.search(fold_for_search(question or "")))  # type: ignore[attr-defined]


#: What a question of nothing else says of the user, per kind: the first-person
#: words of :data:`ASKED_WITH` in Arabic (the word says whose it is), and the
#: English phrase beside which the kind's words stand (its "my" and "I" are no
#: content words). "شنو شريت؟", "What did I buy?", "شنو خططي؟", "What are my
#: plans?" ask that list whatever meaning makes of them: bge-m3 put "user bought
#: a blue bicycle" below an unrelated question's background for "شنو شريت؟".
#: "What can I buy here?", "شنو أشتري هدية؟", "the plans of the house" ask nothing.
_ALL_MINE_AR = """
خطتي خطتنا خططي خططنا ناوي ناوية ناوين هدفي أهدافي أهدافنا طموحي طموحاتي اهتماماتي هوايتي هواياتي شريت شريته شريتها شرينا
اشتريت اشتريته اشتريتها اشترينا مشترياتي استلمت استلمته استلمتها استلمنا تلقيت قابلت قابلته قابلتها قابلتهم قابلنا التقيت
التقينا حضرت حضرته حضرتها حضرنا خلصت خلصته خلصتها كملت أكملت أنهيت أنهيته أنهيتها عادتي عاداتي عوايدي روتيني حميتي دكتوري دكتورتي طبيبي
طبيبتي عمري ميلادي مولدي دراستي جامعتي أدرس ندرس سيارتي سيارتنا بنتي ابنتي بناتي ولدي ابني
"""
_ALL_MINE_EN: dict[str, str] = {
    "bought": r"(?:did|have) (?:i|we) (?:buy|bought|purchased?)", "received": r"(?:did|have) (?:i|we) received?",
    "study": r"do (?:i|we) study|am i studying", "met": r"(?:did|have) (?:i|we) me(?:e)?t",
    "attended": r"(?:did|have) (?:i|we) attend(?:ed)?", "finished": r"(?:did|have) (?:i|we) (?:finish|complete)(?:e?d)?",
}


#: Words that only ask, or say nothing of the thing: "منو قابلت؟", "شنو ناوي
#: أسوي؟" (an English "do" is no content word), "the people I met".
_ONLY_ASKING = ("أسوي نسوي أفعل نفعل منو مين منهو ماهي ماهو الذي التي هناك اسم عيد يوم عطني أعطني اذكر عدد الأشياء أشياء الناس "
                "name called list things people stuff day")


@lru_cache(maxsize=1)
def _all_mine() -> tuple[dict[str, str], dict[str, re.Pattern[str]]]:
    """``(a first-person Arabic word -> its kind, kind -> the English phrase)``."""
    from kazma_core.memory.query_terms import content_terms

    mine = frozenset(content_terms(_ALL_MINE_AR))
    arabic = {word: kind for kind, words in _alone_sets().items() for word in words & mine}
    english = {kind: re.compile(rf"\b(?:{phrase})\b") for kind, phrase in _ALL_MINE_EN.items()}
    for kind, words in (*ASKED_WITH.items(), *ASKED_ALONE.items()):
        own = [re.escape(w) for w in words if w.isascii()]
        if (kind in LISTS or kind in _FEW) and kind not in english and own:
            english[kind] = re.compile(rf"\bmy (?:{'|'.join(own)})\b")
    return arabic, english


#: An Arabic message that asks: a question mark, or a word of asking (folded).
#: Told, the same words ask nothing: "خلصت" is "I'm done" as often as a question.
_ASKS_AR = re.compile(r"[؟?]|(?:^|\s)(?:شنو|وش|ايش|شو|ما|ماذا|منو|مين|من|وين|فين|اين|كم|شكثر|هل|متي|اي|عطني|اعطني"
                      r"|اذكر\w{0,4}|ذكرني|ذكريني|قايمه|لسته)(?=\s|$)")


def _kinds_it_is_all_of(question: str, time_words: frozenset[str]) -> tuple[str, ...]:
    """The kind *question* asks of the user when it holds no other content
    word, *time_words* aside: "شنو شريت؟", "What are my plans now?"."""
    from kazma_core.documents.arabic import fold_for_search
    from kazma_core.memory.query_terms import content_terms

    arabic, english = _all_mine()
    terms = set(content_terms(question)) - time_words - set(content_terms(_ONLY_ASKING))
    lowered = _plain_english(question)
    out = [kind for kind, pattern in english.items()
           if (said := pattern.search(lowered)) and terms <= set(content_terms(said.group())) | _alone_sets()[kind]]
    if _ASKS_AR.search(fold_for_search(question or "")):
        out += [kind for kind in sorted({arabic[t] for t in terms if t in arabic}) if terms <= _alone_sets()[kind]]
    return tuple(dict.fromkeys(out))


def _plain_english(question: str) -> str:
    return " ".join(str(question or "").lower().replace("’", "'").split())


def _said_as_mine(question: str) -> tuple[str, ...]:
    """The kinds of :data:`ASKED_AS_MINE` *question* says the words of, of the
    user: "What is my city?", "وين بيتي؟" -> ``lives_in``; "What do I do for a
    living?", "شنو شغلتي؟" -> ``current_role``."""
    lowered = _plain_english(question)
    neighbours = _arabic_neighbours()
    return tuple(key for key, (_words, mine, _word) in _mine_sets().items()
                 if ASKED_AS_MINE[key]["en"].search(lowered)  # type: ignore[attr-defined]
                 or _said_of_the_user_ar(question, mine, *neighbours[key])
                 or _said_in_arabic(key, question))


def kinds_said_of_the_user(question: str, time_words: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """The kinds *question* asks of the user by its wording: those it says
    the words of (:func:`_said_as_mine`) and the one it is all of
    (:func:`_kinds_it_is_all_of`). Such a question asks that fact whatever
    meaning makes of it: bge-m3 puts "What is my city?" below an unrelated
    question's background for "user lives in Porto", so recall shows the
    asker's own fact of that kind (``recall._rank_by_evidence``'s ``named``)."""
    return tuple(dict.fromkeys((*_said_as_mine(question), *_kinds_it_is_all_of(question, time_words))))


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
    for key in _said_as_mine(question):
        words, _mine, word = sets[key]
        out = [word if term in words else term for term in out]
    return list(dict.fromkeys(out))


def _favourite_kind_words(kind: str) -> tuple[str, ...]:
    """The words of a favourite's kind ("color"), English and Arabic: the
    Arabic quick pass's own table (``arabic_facts._KINDS``), with "my"
    ("لوني", "رياضتي")."""
    from kazma_core.memory.arabic_facts import favourite_kind_words

    return (kind, *({"color": ("colour",)}.get(kind, ())), *favourite_kind_words(kind))


@lru_cache(maxsize=256)
def asked_words(predicate: str) -> str:
    """The words *predicate*'s fact is asked with, folded, one text ("" when
    the table names none): recall reads them beside the fact's own words."""
    from kazma_core.memory.query_terms import content_terms

    key = fact_kind(predicate)
    if key is None or key not in ASKED_WITH:
        return ""
    words = list(ASKED_WITH[key])
    if key == "current_role" and predicate != key:
        # Under Kazma's name a role holds "work" too, as the employer does;
        # "where do I work?" asks no "has_occupation".
        words = [w for w in words if w not in ASKED_WITH["works_at"]]
    if key == "favorite_":
        words += _favourite_kind_words(predicate[len("favorite_"):])
    return " ".join(content_terms(" ".join(words)))


@lru_cache(maxsize=1)
def _asked_sets() -> dict[str, frozenset[str]]:
    from kazma_core.memory.query_terms import content_terms

    return {key: frozenset(content_terms(" ".join(words))) for key, words in ASKED_WITH.items()}


@lru_cache(maxsize=1)
def _alone_sets() -> dict[str, frozenset[str]]:
    from kazma_core.memory.query_terms import content_terms

    return {**_asked_sets(), **{key: frozenset(content_terms(" ".join(words))) for key, words in ASKED_ALONE.items()}}


def indexed_words(kinds: tuple[str, ...]) -> tuple[str, ...]:
    """What the keyword index is asked for *kinds* (:data:`_INDEXED`, else the
    kind's own name)."""
    return tuple(dict.fromkeys(word for kind in kinds for word in _INDEXED.get(kind, (kind,))))


def kinds_asked(terms: list[str] | frozenset[str]) -> tuple[str, ...]:
    """The facts a question's content words (``query_terms.content_terms``,
    its time words out) ask by the table: what the keyword index is asked for
    them (:data:`_INDEXED`, else the kind's name)."""
    held = set(terms)
    return indexed_words(tuple(key for key, words in _asked_sets().items() if held & words))


@lru_cache(maxsize=1)
def _aliases() -> dict[frozenset[str], tuple[str, str]]:
    from kazma_core.memory.predicates import _predicate_key

    table: dict[frozenset[str], tuple[str, str]] = {}
    for kazma, others in KINDS.items():
        for name in (kazma, *others):
            table[_predicate_key(name)] = (kazma, name)
    return table


@lru_cache(maxsize=1)
def _one_value_aliases() -> dict[frozenset[str], tuple[str, str]]:
    from kazma_core.memory.predicates import _predicate_key

    return {_predicate_key(name): (kazma, name) for kazma, others in ONE_VALUE_NAMES.items()
            for name in (kazma, *others)}


@lru_cache(maxsize=1)
def _anothers() -> dict[frozenset[str], str]:
    from kazma_core.memory.predicates import _predicate_key

    return {_predicate_key(name): kazma for kazma, names in _ANOTHERS.items() for name in names}


#: The labels of a fact that holds one value.
_ONE_VALUE = ("functional", "state")


def name_in_use(conn: sqlite3.Connection, *, tenant_id: str, subject: str, predicate: str, predicate_type: str,
                value: str, extraction_method: str) -> str:
    """The name a person's role or employer is written under: the one
    *subject* ALREADY has a current value of that kind under ("Noura occupation
    dentist", then "Noura job_title surgeon" -> ``occupation``); for the user,
    with none held, Kazma's ("has_occupation" -> ``current_role``); else
    *predicate*. One value of each kind, whatever the model names it: under two
    names both stayed current. Most of the user's names are Kazma's before this
    (:func:`users_names`): here are the role's and the age's that a list may
    stand under too (:data:`ONE_VALUE_NAMES`).

    Nothing is lost by it: both must be single values, and a name a list is
    held under stays a list's. Of another person, the value must be one this
    source may overrule (a refused write would drop the new one; under its own
    name it is kept) and say no less of the same ("neuroscientist", held
    "neuroscientist specializing in ..."). The user's is refused and put to
    them, as any change of a fact they stated."""
    from kazma_core.memory.predicates import _predicate_key
    from kazma_core.memory.provenance import may_overrule
    from kazma_core.memory.query_terms import content_terms
    from kazma_core.memory.speakers import is_member_entry
    from kazma_core.spaces.scope import space_named_or_bound

    theirs = _is_the_user(subject) or is_member_entry(subject)

    def kind_of(name: str) -> str | None:
        key = _predicate_key(normalised_name(name))
        if theirs:
            return _one_value_aliases().get(key, (None, ""))[0]
        return _anothers().get(key)

    kind = kind_of(predicate)
    if kind is None or predicate_type not in _ONE_VALUE:
        return predicate
    held: dict[str, tuple[str, str]] = {}
    for name, its, method, ptype in conn.execute(
            "SELECT predicate, object, extraction_method, predicate_type FROM beliefs WHERE tenant_id = ? "
            "AND subject = ? AND valid_until IS NULL AND invalidated_at IS NULL AND COALESCE(object, '') != '' "
            "ORDER BY valid_from DESC, id", (space_named_or_bound(tenant_id), subject)).fetchall():
        if name == predicate:
            return predicate  # one more of it, whatever it is: a list stays a list
        if ptype in _ONE_VALUE and kind_of(name) == kind:
            held.setdefault(str(name), (str(its), str(method or "")))
    if not held:
        return kind if theirs else predicate
    name, (its, method) = next(iter(held.items()))
    if theirs:
        return name
    new = set(content_terms(str(value).replace("_", " ")))
    says_less = bool(new) and new < set(content_terms(its.replace("_", " ")))
    return name if may_overrule(method, extraction_method) and not says_less else predicate


def _canonical_for_user(predicate: str) -> str:
    """Kazma's name for *predicate* when it names one of the user's facts in
    the table ("home_city", "city_home", "resides_in" -> "lives_in"), else
    *predicate* as it is. A plural ("homes", "employers") stays as it is: a
    list."""
    from kazma_core.memory.belief_mutation import _plural_of
    from kazma_core.memory.predicates import _predicate_key

    named = normalised_name(predicate)
    key = _predicate_key(named)
    found = _aliases().get(key) if key else None
    if found is None:
        return predicate
    kazma, name = found
    return predicate if _plural_of(named, name) else kazma


#: A word's start inside a camelCase name ("livesIn", "currentLocation").
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def normalised_name(predicate: str) -> str:
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
    found = _aliases().get(_predicate_key(normalised_name(predicate)))
    return found is not None and found[1] not in _EVENT_NAMES


def plural_of_a_kind(predicate: str) -> bool:
    """Whether *predicate* puts one of the table's names in the plural
    ("homes", "employers"): a list."""
    from kazma_core.memory.belief_mutation import _plural_of
    from kazma_core.memory.predicates import _predicate_key

    named = normalised_name(predicate)
    key = _predicate_key(named)
    found = _aliases().get(key) if key else None
    return found is not None and _plural_of(named, found[1])
