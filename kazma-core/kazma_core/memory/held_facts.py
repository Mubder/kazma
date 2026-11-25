"""Which of the facts Kazma holds the deep pass is shown with a turn.

The deep pass may say that a new fact replaces one Kazma holds
(``belief_extractor._take_replaces``), but only one it was SHOWN: a fact left
out is never replaced, and the old value and the new one both stay current.
So the list is the facts the turn is about, chosen here (the memory plan's W6,
step 1, 2026-11-25):

* the facts of another subject the turn **names** (each of its words is one
  the turn holds, as a set: two lists of words were compared with ``<=``,
  which is alphabetical order, so "What should I cook tonight?" was shown
  Acme's facts and "My sister Noura is a surgeon now" was not shown Noura's),
  who it names most first, and after them who the turn before named ("She
  moved too");
* the speaker's facts whose **value the user's words say**, in either
  language (what Arabic writes onto a word is taken off);
* the speaker's facts Kazma keeps **one value** of: home, employer, name and
  role, then favourites (never a service's next reset, which changes weekly);
* the facts **nearest in meaning** to the turn, by the vectors memory already
  holds for the turn and for each fact: "I stopped swimming" is near "swims
  every friday", and an Arabic turn is near a fact held in English. Nothing
  is embedded here;
* the nearest facts of a subject whose name is in **another alphabet** than
  the turn ("عمر" and ``omar``), which no word of it can name;
* the speaker's **newest** facts: what was just said is what is most often
  put right. They used to take all the places.

Each has a share of the places (:data:`_SHARES`), so none can take them all,
and what is left over goes to them in the same order. The list is bounded in
facts (``limits.DEEP_PASS_HELD_FACTS``) and in characters
(``limits.DEEP_PASS_HELD_CHARS``: as many facts in Arabic as in English, whose
letters cost fewer tokens), the newest three kept first.
"""

from __future__ import annotations

import functools
import logging
import math
import re
import sqlite3
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from itertools import chain, zip_longest
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["held_facts", "held_line"]

#: The places each way of choosing has first, in the order they are filled
#: (:func:`_choose`); they add up to ``limits.DEEP_PASS_HELD_FACTS``. Measured
#: on the 4,318 replacements two models made in the pilots' cached replies
#: (2026-11-25; ``docs/agents/15h-facts.md``): most replaced one of the
#: speaker's newest facts.
_SHARES = {"named": 7, "said": 5, "one": 3, "near": 4, "alphabet": 2, "newest": 9, "before": 0}

#: The most facts of one named subject.
_A_SUBJECT = 10

#: How near in meaning another subject's fact is when the turn does not name
#: it ("my sister got promoted" and Noura's job). A fact of the speaker needs
#: no floor: it takes a place a newer one of theirs would have had.
_NEAR_ANOTHER = 0.55

#: The most facts of subjects named in another alphabet, the nearest whatever
#: their distance. One the turn is not about does no harm: a fact only ever
#: replaces one of its own subject (``belief_extractor._take_replaces``).
_OTHER_ALPHABET = 2

#: How many of the nearest facts are read, the ones that may not be shown
#: included: 60 were the speaker's own in a store of 120 facts about cities,
#: and a friend's home was not among them (the review of step 1). 1,000 of
#: 10,000 cost 10 ms more.
_NEAR_READ = 1000

#: The newest facts that are kept whatever the bound drops.
_NEWEST_KEPT = 3

_CURRENT = "valid_until IS NULL AND invalidated_at IS NULL AND COALESCE(object, '') != ''"


def held_line(fact: Mapping[str, Any]) -> str:
    """The line a held fact is shown as: one line, its value cut."""
    from kazma_core.memory.limits import DEEP_PASS_HELD_VALUE

    value = " ".join(str(fact["object"]).split())[:DEEP_PASS_HELD_VALUE]
    return (f"{fact['ref']}. {fact['shown']} {fact['predicate']} {value}"
            + (f" ({fact['day']})" if fact.get("day") else ""))


def _held_day(meta: dict[str, Any]) -> str:
    """An event's day as its held fact shows it."""
    from kazma_core.memory.fact_days import event_day

    day = event_day(meta)
    return f"{'on' if meta.get('happened') else 'planned for'} {day}" if day else ""


# ── What the turn says ───────────────────────────────────────────────────


_RAW_WORD = re.compile(r"[^\W_]+")
_TOKEN = re.compile(r"[^\W_]+(?:-[^\W_]+)*")

#: What Arabic writes onto a word's end for whose it is. Never a taa marbuta or
#: a bare taa: they are the word's own ("كريمة" is not "كريم", "سارت" not "سارة").
_WHOSE = re.compile(r"(?:هما|كما|نا|كم|هم|ها|ي|ك|ه)$")

#: The first word of a two-word name, often written onto the second one.
_FATHER = frozenset({"ابو", "ابا", "ابي", "بو"})

#: Words of a fact's name that only a person's facts have (a fact's name is in
#: English whatever the turn's language).
_A_PERSONS = frozenset(
    "age allergic aunt birthday born boyfriend brother colleague cousin daughter dislikes employer engaged enjoys "
    "father fiance fiancee friend girlfriend hates husband job likes lives loves manager married mother nephew "
    "neighbor neighbour niece nickname occupation prefers profession resides role sister son speaks studies teaches "
    "uncle wife works".split())


#: What a person is called by what they are, not by a name: these are said with
#: the article and with whose they are ("مديري", "الدكتور"), as "my manager" and
#: "the doctor" are. The feminine ("مديرة") goes with each.
_BY_WHAT_THEY_ARE = (
    "مدير جار دكتور طبيب سائق سواق دريول مدرس معلم أستاذ صديق رفيق زميل شريك زوج أخ أخت أم أب ابن بنت ولد والد عم خال "
    "جد خطيب رئيس مسؤول موظف محامي مهندس مدرب حارس بواب خادم شغال عامل مربي طباخ مندوب محاسب ممرض صيدلي قريب نسيب "
    "كفيل مؤجر مستأجر عميل زبون مشرف مرشد طالب تلميذ ضيف شيخ إمام حلاق خياط نجار سباك كهربائي ميكانيكي مقاول سكرتير "
    "مساعد مترجم كاتب طفل حفيد توأم")


@functools.lru_cache(maxsize=1)
def _roles() -> frozenset[str]:
    from kazma_core.documents.arabic import fold_for_search

    words = fold_for_search(_BY_WHAT_THEY_ARE).split()
    return frozenset(words) | frozenset(f"{w}ه" for w in words)


def _a_person(facts: Sequence[Mapping[str, Any]]) -> bool:
    """Whether the *facts* held about a subject are ones a person has."""
    return any(not _A_PERSONS.isdisjoint(str(f["predicate"]).lower().split("_")) for f in facts)


def _whole_names(words: Sequence[str]) -> list[str]:
    """*words* (folded) with a two-word name as the one word it is also
    written as: "عبد الله" is "عبدالله", "بو فهد" and "بوفهد" are "ابوفهد"."""
    out: list[str] = []
    at = 0
    while at < len(words):
        word, after = words[at], words[at + 1] if at + 1 < len(words) else ""
        if after and not after.isascii() and (word == "عبد" or word in _FATHER):
            out.append(("عبد" if word == "عبد" else "ابو") + after)
            at += 2
            continue
        out.append("ابو" + word[2:] if word.startswith("بو") and len(word) >= 5 else word)
        at += 1
    return out


class _Said:
    """The words of a text, and where it says a fact's subject."""

    def __init__(self, text: str) -> None:
        from kazma_core.documents.arabic import fold_for_search
        from kazma_core.memory.query_terms import normalised_word

        #: A word as it was written -> where; then folded, with only what can be
        #: written before a name taken off; then with the article and whose-it-is too.
        self._raw: dict[str, list[int]] = {}
        self._name: dict[str, list[int]] = {}
        self._thing: dict[str, list[int]] = {}
        for at, m in enumerate(_RAW_WORD.finditer(text)):
            self._raw.setdefault(m.group(), []).append(at)
        plain = unicodedata.normalize("NFKC", text)
        tokens = [(t, fold_for_search(t)) for t in _TOKEN.findall("".join(c for c in plain if not _unwritten(c)))]
        for at, (written, folded) in enumerate(tokens):
            if folded.isascii():
                word = normalised_word(folded)
                for part in {word, *(p for p in word.split("-") if len(p) > 1 or p.isdigit())}:
                    self._name.setdefault(part, []).append(at)
                    self._thing.setdefault(part, []).append(at)
                continue
            names = self._before_a_name(folded)
            after = tokens[at + 1][1] if at + 1 < len(tokens) else ""
            if after and not after.isascii():
                names |= {whole for form in tuple(names) if form == "عبد" or form in _FATHER
                          for whole in _whole_names([form, after])}
            names |= {whole for form in tuple(names) for whole in _whole_names([form])}
            things = {normalised_word(form) for form in names} | names
            if not written.endswith("ة"):
                for form in tuple(things):
                    bare = _WHOSE.sub("", form)
                    if bare in ("ابو", "اخو"):  # "أبوي", "أخوها": "أب" and "أخ" before whose they are
                        bare = bare[:-1]
                    if bare != form and (len(bare) >= 3 or bare in _roles()):
                        # "سيارتها" is "سياره" once whose it is comes off.
                        things |= {bare, bare[:-1] + "ه"} if bare.endswith("ت") else {bare}
            for form in names:
                self._name.setdefault(form, []).append(at)
            for form in things:
                self._thing.setdefault(form, []).append(at)

    @staticmethod
    def _before_a_name(word: str) -> set[str]:
        """*word* and what it is without the letters written before a name
        ("لنورة", "وبالكويت", "للعازمي")."""
        out, rest = {word}, word
        for _ in range(2):
            if rest[0] not in "وفبلك" or (len(rest) < 4 and rest[1:] not in _FATHER):
                break
            if rest.startswith("لل") and len(rest) > 4:
                out.add("ال" + rest[2:])
            rest = rest[1:]
            out.add(rest)
        return out

    def names(self, subject: str, *, person: bool = False) -> tuple[int, int] | None:
        """How often and where (first) the text holds every word of *subject*
        ("berlin_office", "نورة", "user_sister" as "sister"); None when it does
        not. An English word as a word of its own, never as a stem ("max" is
        not in "maxed"); an Arabic one with what Arabic writes before it, and
        unless the subject is a *person* by a name with its article and whose
        it is ("السيارة", "سيارتي", "مديري"; "الهند" is not Hind). A subject whose name is
        also a small word ("Will", "علي") as it is written."""
        from kazma_core.documents.arabic import fold_for_search
        from kazma_core.memory.query_terms import content_terms

        spoken = subject.replace("_", " ")
        tokens = [t for t in _TOKEN.findall(fold_for_search(spoken))]
        told = [(t, terms[0]) for t in tokens if (terms := content_terms(t, digits=True)) and t != "user"]
        if not told:
            return self._small_words([w for w in _RAW_WORD.findall(spoken) if w not in ("user", "my", "s")])
        whole = _whole_names(tokens)
        as_told = None if len(whole) < len(tokens) else self._all(told, person)
        return as_told or (self._all([(w, w) for w in whole], person) if whole != tokens else None)

    def _all(self, words: Sequence[tuple[str, str]], person: bool) -> tuple[int, int] | None:
        # The first name of a person's two settles who it is: the second may
        # be said without its article ("أحمد عازمي"). One called by what they
        # are has no name that a word could be taken for ("مديري", "الدكتور").
        strict = person and len(words) == 1 and words[0][1] not in _roles()
        found = [self._name.get(word) if strict and not word.isascii()
                 else self._name.get(word) or self._thing.get(plain) for word, plain in words]
        return (min(len(f) for f in found if f), min(f[0] for f in found if f)) if found and all(found) else None

    def _small_words(self, words: Sequence[str]) -> tuple[int, int] | None:
        forms = [{w.capitalize()} if w.isascii() else {w, *(f"{letter}{w}" for letter in "ولبف")} for w in words]
        found = [sorted(at for form in wanted for at in self._raw.get(form, ())) for wanted in forms]
        return (min(map(len, found)), min(f[0] for f in found)) if found and all(found) else None


def _unwritten(char: str) -> bool:
    """A mark or a stretch: folded away, and it would hide a word's last letter."""
    return char == "ـ" or unicodedata.category(char) == "Mn"


def _by_words(facts: Sequence[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """The *facts* whose value the text says a word of, best first: the share
    of the value's words it says, a word few of the facts hold counting for
    more ("swimming" than "friday"), then the newest."""
    from kazma_core.memory.event_text import value_words, words_said

    words = [(fact, value_words(str(fact["object"]))) for fact in facts]
    said = {fact["id"]: words_said(of_it, text) for fact, of_it in words}
    told = [(fact, of_it) for fact, of_it in words if said[fact["id"]]]
    holding: dict[str, int] = {}
    for _fact, of_it in words:
        for word in set(of_it):
            holding[word] = holding.get(word, 0) + 1

    def weight(word: str) -> float:
        return math.log((len(facts) + 1) / (holding.get(word, 0) + 1)) + 1.0

    def share(fact: dict[str, Any], of_it: Sequence[str]) -> float:
        return sum(weight(w) for w in said[fact["id"]]) / sum(weight(w) for w in set(of_it))

    ranked = sorted(((share(f, w), i, f) for i, (f, w) in enumerate(told)), key=lambda r: (-r[0], r[1]))
    return [fact for _share, _at, fact in ranked]


def _nearest(conn: Any, tenant_id: str, vector: Sequence[float] | None,
             may_show: Mapping[str, dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
    """The facts nearest in meaning to the turn's stored *vector*, nearest
    first. None of them: the turn has no vector yet, or no vector search here."""
    if not vector:
        return []
    from kazma_core.memory.vector_engine import VectorEngine

    try:
        found = VectorEngine(conn).search_beliefs(list(vector), tenant_id=tenant_id, limit=_NEAR_READ)
    except (sqlite3.Error, ValueError):  # the list is still chosen by words and by age
        logger.warning("[held_facts] the facts nearest to a turn could not be read", exc_info=True)
        return []
    return [(may_show[i], sim) for i, sim in found if i in may_show]


#: Latin letters, accents included, end here; Arabic ones come after.
_LAST_LATIN = "ɏ"


def _in_another_alphabet(subject: str, text: str) -> bool:
    """Whether *text* has words in an alphabet *subject* is not written in."""
    latin = [c <= _LAST_LATIN for c in text if c.isalpha()]
    return (not all(latin)) if all(c <= _LAST_LATIN for c in subject if c.isalpha()) else any(latin)


# ── The choice ───────────────────────────────────────────────────────────


#: Kazma's names for what a person has one of, the ones a turn changes most first.
_ONE_OF = ("lives_in", "located_in", "works_at", "name_is", "preferred_name", "current_role", "active_project")


def _one_value(facts: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The *facts* Kazma keeps one value of: home, employer, name and role,
    then the others, each newest first. Not a service's next reset
    (``current_facts``): those are always the newest."""
    from kazma_core.memory.belief_mutation import kazma_classifies, one_value_name
    from kazma_core.memory.current_facts import is_functional_current_predicate

    def rank(fact: dict[str, Any]) -> int:
        name = one_value_name(fact["predicate"])
        return _ONE_OF.index(name) if name in _ONE_OF else len(_ONE_OF)

    return sorted((f for f in facts if f["predicate_type"] != "set" and kazma_classifies(f["predicate"])
                   and not is_functional_current_predicate(f["predicate"])), key=rank)


def _in_turn(lists: Iterable[Sequence[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One of each list, then the next of each."""
    return [fact for row in zip_longest(*lists) for fact in row if fact is not None]


def _choose(ways: Mapping[str, Sequence[dict[str, Any]]], places: int) -> list[dict[str, Any]]:
    """At most *places* of the facts of *ways*, the ones to keep first: each
    way's share, then what is left of each in the same order."""
    chosen: dict[str, dict[str, Any]] = {}
    for name, facts in ways.items():
        room = _SHARES[name]
        for fact in facts:
            if room <= 0 or len(chosen) >= places:
                break
            if fact["id"] not in chosen:
                chosen[fact["id"]] = fact
                room -= 1
    for fact in chain.from_iterable(ways.values()):
        if len(chosen) >= places:
            break
        chosen.setdefault(fact["id"], fact)
    return list(chosen.values())


def _within(chosen: Sequence[dict[str, Any]], newest: Sequence[dict[str, Any]], chars: int) -> set[str]:
    """The ids of the *chosen* facts whose lines fit in *chars*: the *newest*
    few first, then in the order they were chosen. A long value does not keep
    out a shorter one after it."""
    kept: set[str] = set()
    used = 0
    of_them = {f["id"] for f in chosen}
    for fact in chain((f for f in newest if f["id"] in of_them), chosen):
        cost = len(held_line({**fact, "ref": "f00"})) + 1
        if fact["id"] not in kept and used + cost <= chars:
            kept.add(fact["id"])
            used += cost
    return kept


def _named(said: Sequence[_Said], others: Mapping[str, list[dict[str, Any]]],
           told: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The facts of the subjects the texts *said* name, one of each subject in
    turn: those of the subjects the turn names -- by the user first, the most
    often, the earliest --, and those only the last text (the turn before)
    names. Of a subject, the facts whose value *told* says come first."""
    told_by: list[list[tuple[int, int, str]]] = [[] for _ in said]
    for subject, facts in others.items():
        person = _a_person(facts)
        for at, text in enumerate(said):
            where = text.names(subject, person=person)
            if where:
                told_by[at].append((-where[0], where[1], subject))
                break

    def facts_of(subjects: Iterable[tuple[int, int, str]]) -> list[dict[str, Any]]:
        lists = []
        for _often, _where, subject in subjects:
            first = _by_words(others[subject], told)
            ids = {f["id"] for f in first}
            lists.append([*first, *(f for f in others[subject] if f["id"] not in ids)][:_A_SUBJECT])
        return _in_turn(lists)

    return (facts_of(chain.from_iterable(sorted(t) for t in told_by[:-1])), facts_of(sorted(told_by[-1])))


def held_facts(conn: Any, *, tenant_id: str, text: str, answer: str = "", said_by: str | None = None,
               vector: Sequence[float] | None = None, previous: tuple[str, str] | None = None) -> list[dict[str, Any]]:
    """The current facts the deep pass is shown with a turn, numbered ``f1``,
    ``f2``...: see the module's docstring. *text* is what the user said and
    *answer* the reply (it names people and things too); each as far as the
    model reads it. *vector* is the turn's own stored vector, *previous* the
    question and the answer of the turn before, which a pronoun speaks of.

    The speaker's first (``user``; their entry in a shared space, shown as
    ``user``), then each other subject's, newest first. Another member's
    entry is never shown. A forgotten fact holds no value and is left out, and
    so is the link Kazma writes from the user to a subject's hub
    (``ego_anchor``): nobody told it; one the user told is a fact. An event carries its ``day`` ("on 2026-05-30"; the second review
    of W4: without it the model could not tell one visit from another, nor a
    wrong day from one)."""
    from kazma_core.memory.ego_anchor import ANCHOR_PREDICATE
    from kazma_core.memory.fact_days import metadata_of
    from kazma_core.memory.limits import (
        DEEP_PASS_ANSWER,
        DEEP_PASS_HELD_CHARS,
        DEEP_PASS_HELD_FACTS,
        DEEP_PASS_PREVIOUS_ANSWER,
        DEEP_PASS_PREVIOUS_QUESTION,
        DEEP_PASS_QUESTION,
    )
    from kazma_core.memory.speakers import USER, is_member_entry
    from kazma_core.memory.speakers import room as speakers_room

    people = speakers_room(tenant_id)
    own = people.entry_for(said_by) if people.shared else USER
    mine: list[dict[str, Any]] = []
    others: dict[str, list[dict[str, Any]]] = {}
    for r in conn.execute(
            "SELECT id, subject, predicate, predicate_type, object, extraction_method, metadata_json FROM beliefs "
            f"WHERE tenant_id = ? AND {_CURRENT} AND NOT (predicate = ? AND COALESCE(extraction_method, '') = 'system_tool') "  # nosec B608 - fixed clause, values bound
            "ORDER BY valid_from DESC, id", (tenant_id, ANCHOR_PREDICATE)).fetchall():
        subject = r[1]
        if not subject or (subject != own and (subject == USER or is_member_entry(subject))):
            continue
        kind = r[3] or "set"
        fact = {"id": r[0], "subject": subject, "shown": USER if subject == own else subject, "predicate": r[2],
                "predicate_type": kind, "object": r[4], "method": r[5],
                "day": _held_day(metadata_of(r[6])) if kind == "set" else ""}
        (mine if subject == own else others.setdefault(subject, [])).append(fact)

    told, reply = (text or "")[:DEEP_PASS_QUESTION], (answer or "")[:DEEP_PASS_ANSWER]
    before = (f"{previous[0][:DEEP_PASS_PREVIOUS_QUESTION]}\n{previous[1][:DEEP_PASS_PREVIOUS_ANSWER]}"
              if previous else "")
    named, named_before = _named([_Said(told), _Said(f"{told}\n{reply}"), _Said(before)], others, told)
    may_show = {f["id"]: f for f in chain(mine, *others.values())}
    near = _nearest(conn, tenant_id, vector, may_show)
    ways = {
        "named": [*named, *named_before[:max(0, _SHARES["named"] - len(named))]],
        "said": _by_words(mine, told),
        "one": _one_value(mine),
        "near": [f for f, sim in near if f["subject"] == own or sim >= _NEAR_ANOTHER],
        "alphabet": [f for f, _sim in near if f["subject"] != own
                     and _in_another_alphabet(f["subject"], told)][:_OTHER_ALPHABET],
        "newest": mine,
        "before": named_before,
    }
    shown = _within(_choose(ways, DEEP_PASS_HELD_FACTS), mine[:_NEWEST_KEPT], DEEP_PASS_HELD_CHARS)
    listed = [f for f in chain(mine, *(others[s] for s in sorted(others))) if f["id"] in shown]
    return [{"ref": f"f{n}", **fact} for n, fact in enumerate(listed, 1)]
