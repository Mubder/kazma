"""Which of the facts Kazma holds the deep pass is shown with a turn.

The deep pass may say that a new fact replaces one Kazma holds
(``belief_extractor._take_replaces``), but only one it was SHOWN: a fact left
out is never replaced, and the old value and the new one both stay current.
So the list is the facts the turn is about, chosen here (the memory plan's W6,
step 1, 2026-11-25):

* the facts of another subject the turn **names** (each of its words is one
  the turn holds, as a set: two lists of words were compared with ``<=``,
  which is alphabetical order, so "What should I cook tonight?" was shown
  Acme's facts and "My sister Noura is a surgeon now" was not shown Noura's);
* the speaker's facts whose **value the user's words say**, in either
  language (what Arabic writes onto a word is taken off);
* the speaker's facts Kazma keeps **one value** of (home, employer, name);
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
facts (``limits.DEEP_PASS_HELD_FACTS``) and in tokens
(``limits.DEEP_PASS_HELD_TOKENS``).
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
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
_SHARES = {"named": 7, "said": 5, "one": 3, "near": 4, "alphabet": 2, "newest": 9}

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
#: included.
_NEAR_READ = 60

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


class _Said:
    """The words of a text, and which of a fact's words are among them."""

    def __init__(self, text: str) -> None:
        from kazma_core.memory.query_terms import content_terms

        self._text = text
        self._raw = set(_RAW_WORD.findall(text))
        self._terms = set(content_terms(text))
        self._terms.update(part for term in tuple(self._terms) for part in term.split("-") if len(part) > 1)

    def names(self, subject: str) -> bool:
        """Whether the text holds every word of *subject* ("berlin_office",
        "نورة", "user_sister" as "sister"). An English word as a word of its
        own, never as a stem ("max" is not in "maxed"); a subject whose name is
        also a small word ("Will", "علي") as it is written."""
        from kazma_core.memory.event_text import words_said
        from kazma_core.memory.query_terms import content_terms

        spoken = subject.replace("_", " ")
        words = [w for w in content_terms(spoken) if w != "user"]
        if not words:
            return self._names_small_words([w for w in _RAW_WORD.findall(spoken) if w not in ("user", "my", "s")])
        ascii_words = {w for w in words if w.isascii()}
        return ascii_words <= self._terms and words_said(words, self._text) >= set(words) - ascii_words

    def _names_small_words(self, words: Sequence[str]) -> bool:
        forms = [{w.capitalize()} if w.isascii() else {w, *(f"{letter}{w}" for letter in "ولبف")} for w in words]
        return bool(forms) and all(form & self._raw for form in forms)


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


def _within_tokens(chosen: Sequence[dict[str, Any]], tokens: int) -> list[dict[str, Any]]:
    """The *chosen* facts whose lines fit in *tokens*, the first ones kept. A
    long value does not keep out a shorter one after it."""
    from kazma_core.summarizer import estimate_text_tokens

    out, used = [], 0
    for fact in chosen:
        cost = estimate_text_tokens(held_line({**fact, "ref": "f00"})) + 1
        if used + cost <= tokens:
            out.append(fact)
            used += cost
    return out


def held_facts(conn: Any, *, tenant_id: str, text: str, answer: str = "", said_by: str | None = None,
               vector: Sequence[float] | None = None) -> list[dict[str, Any]]:
    """The current facts the deep pass is shown with a turn, numbered ``f1``,
    ``f2``...: see the module's docstring. *text* is what the user said and
    *answer* the reply (it names people and things too); each as far as the
    model reads it. *vector* is the turn's own stored vector.

    The speaker's first (``user``; their entry in a shared space, shown as
    ``user``), then each other subject's, newest first. Another member's
    entry is never shown. A forgotten fact holds no value and is left out, and
    so is the link from the user to a subject's hub (``ego_anchor``): nobody
    told it. An event carries its ``day`` ("on 2026-05-30"; the second review
    of W4: without it the model could not tell one visit from another, nor a
    wrong day from one)."""
    from kazma_core.memory.belief_mutation import kazma_classifies
    from kazma_core.memory.ego_anchor import ANCHOR_PREDICATE
    from kazma_core.memory.fact_days import metadata_of
    from kazma_core.memory.limits import (
        DEEP_PASS_ANSWER,
        DEEP_PASS_HELD_FACTS,
        DEEP_PASS_HELD_TOKENS,
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
            f"WHERE tenant_id = ? AND {_CURRENT} AND predicate != ? "  # nosec B608 - fixed clause, values bound
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
    by_user, by_turn = _Said(told), _Said(f"{told}\n{reply}")
    named: list[list[dict[str, Any]]] = []
    for asks in (by_user, by_turn):  # who the user names, then who only the reply does
        for subject in sorted(others):
            if asks.names(subject) and (asks is by_user or not by_user.names(subject)):
                facts = others[subject]
                first = _by_words(facts, told)
                named.append([*first, *(f for f in facts if f not in first)][:_A_SUBJECT])
    may_show = {f["id"]: f for f in chain(mine, *others.values())}
    near = _nearest(conn, tenant_id, vector, may_show)
    ways = {
        "named": _in_turn(named),
        "said": _by_words(mine, told),
        "one": [f for f in mine if f["predicate_type"] != "set" and kazma_classifies(f["predicate"])],
        "near": [f for f, sim in near if f["subject"] == own or sim >= _NEAR_ANOTHER],
        "alphabet": [f for f, _sim in near if f["subject"] != own
                     and _in_another_alphabet(f["subject"], told)][:_OTHER_ALPHABET],
        "newest": mine,
    }
    chosen = _within_tokens(_choose(ways, DEEP_PASS_HELD_FACTS), DEEP_PASS_HELD_TOKENS)
    shown = {f["id"] for f in chosen}
    listed = [f for f in chain(mine, *(others[s] for s in sorted(others))) if f["id"] in shown]
    return [{"ref": f"f{n}", **fact} for n, fact in enumerate(listed, 1)]
