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

__all__ = ["KINDS", "plural_of_a_kind", "users_names", "users_predicate"]

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


def plural_of_a_kind(predicate: str) -> bool:
    """Whether *predicate* puts one of the table's names in the plural
    ("homes", "employers"): a list."""
    from kazma_core.memory.belief_mutation import _plural_of
    from kazma_core.memory.predicates import _predicate_key

    named = _normalised(predicate)
    key = _predicate_key(named)
    found = _aliases().get(key) if key else None
    return found is not None and _plural_of(named, found[1])
