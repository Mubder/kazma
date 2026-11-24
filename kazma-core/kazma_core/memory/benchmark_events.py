"""The extraction benchmark's event turns: what is stored after the reply
(dataset v4, the memory plan's W4, 2026-11-24).

A reply can be right and the store wrong: "I called my mom" read as ``called``
became the user's name, an event ``moved_to`` Porto their home, the same farm
visited on two days one entry. So an event turn is scored twice: the model's
reply, as any turn's (``benchmark._score_extraction``), and what the store
holds after the product wrote it. A turn's ``stored`` says:

* ``before``: the chat before it -- each turn's day, words and the reply the
  model is taken to have given -- written through the product too;
* ``entries``: the events the store must hold after, each by the words it is
  known by (``objects``) and the days its dated entries carry, all of them and
  no other (two visits are two days, a retelling adds none, a corrected day
  replaces the wrong one; ``kind`` ``planned`` for one to come, ``[]`` for an
  event told with no day);
* ``optional``: an event a reply may leave out; kept, it has these days;
* ``stands``: facts of the user's own that must stand as they were (their
  name, their home), in their own words;
* ``none``: what must not be current (a pasted line's trip, a sister's);
* ``asks``: what must wait for the user's yes; ``not_asked``: what must not;
* ``once``: a name and value held by exactly one entry;
* ``undated``: true when no event with a day may be stored at all.

Through the write path a live turn takes (``benchmark._tell``): the quick
pass, then the recorded reply parsed, sanitized and applied with the facts
held about the turn. The facts held must be the ones the model was asked with
(the turn's ``held``): a reply's "f1" means nothing otherwise, so a write path
that holds others by then has got the turn wrong.

Scores, per language, over every asking: ``stored`` (the entries held under any
name), ``day`` (the entries whose days are exactly right), ``safe`` (the turns
whose ``optional``, ``stands``, ``none``, ``asks``, ``not_asked``, ``once`` and
``undated`` all hold) and ``whole`` (the turns with everything right).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

__all__ = ["EVENT_SCORES", "STORED_SCORES", "is_event_turn", "run_event_turns"]

#: The scores of what is stored.
STORED_SCORES = ("stored", "day", "safe", "whole")
#: An event's predicate is the model's own ("went_to", "visited"): the name is
#: not scored.
EVENT_SCORES = ("found", "right", "quiet", "on", *STORED_SCORES)

#: The counts of one asking, and the ones that are the dataset's.
_COUNTS = STORED_SCORES
_OF = {"stored": "entries", "day": "entries", "safe": "guarded", "whole": "turns"}
_GUARDS = ("optional", "stands", "none", "asks", "not_asked", "once", "undated")


def is_event_turn(turn: dict[str, Any]) -> bool:
    return "stored" in turn


def _at(day: str) -> float:
    """Noon UTC of *day*: the same day in every zone Kazma is run in."""
    return datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp()


def _stored_after(turn: dict[str, Any], reply: str,
                  client: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """The current facts, the changes waiting for the user, and any facts held
    that are not the ones the model was asked with, after the chat before
    *turn* and *turn* itself answered with *reply*, written the product's way
    into a private database. *client* is the model the deep pass is patched
    to ask."""
    import json

    from kazma_core.memory.benchmark import (
        _WAITING_SQL,
        BENCH_TENANT,
        _numbered,
        _private_stores,
        _tell,
    )
    from kazma_core.memory.fact_days import metadata_of

    shown, other = [tuple(fact) for fact in turn.get("held") or ()], []

    def of_this_turn(held: list[dict[str, Any]]) -> str:
        now = [(h["shown"], h["predicate"], h["object"], *([h["day"]] if h["day"] else [])) for h in held]
        if now != shown:
            other.append(f"held when the turn came: {now}; the model was asked with {shown}")
        return reply

    session = f"events-{turn['id']}"
    with _private_stores("kazma-events") as (primary, ops):
        before = turn["stored"].get("before") or []
        for number, told in enumerate(before, 1):
            _tell(primary, ops, client,
                  lambda held, told=told: json.dumps(
                      {"beliefs": [_numbered(fact, held) for fact in told["written"]]}, ensure_ascii=False),
                  user=told["user"], session=session, number=number, at=_at(told["said_on"]),
                  said_on=told["said_on"])
        previous = turn.get("previous")
        _tell(primary, ops, client, of_this_turn, user=turn["user"], session=session, number=len(before) + 1,
              at=_at(turn["said_on"]), said_on=turn["said_on"],
              previous=(previous.get("user", ""), previous.get("assistant", "")) if previous else None)
        rows = [{**dict(r), "meta": metadata_of(r["metadata_json"])} for r in primary.execute(
            "SELECT subject, predicate, object, extraction_method, metadata_json FROM beliefs WHERE tenant_id = ? "
            "AND valid_until IS NULL AND invalidated_at IS NULL AND predicate != 'related_to' ORDER BY valid_from",
            (BENCH_TENANT,))]
        waiting = [dict(r) for r in primary.execute(_WAITING_SQL, (BENCH_TENANT,))]
    return rows, waiting, other


def _about(row: dict[str, Any], subject: str, objects: list[str]) -> bool:
    from kazma_core.memory.benchmark import _folded, _holds, _slug

    return _slug(row["subject"]) == _slug(subject) and any(
        _holds(_folded(o), _folded(row["object"])) for o in objects)


def _days(rows: list[dict[str, Any]], expected: dict[str, Any]) -> list[str]:
    """The days the dated entries of *expected* carry, as its kind names them
    ("planned" for one to come); a day of the other kind is none of them."""
    from kazma_core.memory.fact_days import event_day

    planned = expected.get("kind") == "planned"
    return sorted(event_day(r["meta"]) if bool(r["meta"].get("planned")) == planned else f"not {expected.get('kind')}"
                  for r in rows if _about(r, expected.get("subject", "user"), expected["objects"])
                  and event_day(r["meta"]))


def _judge(turn: dict[str, Any], rows: list[dict[str, Any]], waiting: list[dict[str, Any]],
           other: list[str]) -> dict[str, Any]:
    """What *turn* left stored against what it must leave: the counts of
    :data:`STORED_SCORES` and each problem in words. With *other* facts held
    than the model was shown, nothing of the turn is right."""
    from kazma_core.memory.benchmark import _USER_SOURCES
    from kazma_core.memory.fact_days import event_day
    from kazma_core.memory.predicates import _predicate_key

    spec, problems, there_are, right = turn["stored"], [], 0, 0

    def named(row: dict[str, Any], fact: dict[str, Any]) -> bool:
        names = fact.get("predicates")
        return not names or _predicate_key(row["predicate"]) in {_predicate_key(p) for p in names}

    for expected in spec.get("entries") or ():
        there = [r for r in rows if _about(r, expected.get("subject", "user"), expected["objects"])]
        days, wanted = _days(rows, expected), sorted(expected["days"])
        there_are += bool(there)
        right += bool(there) and days == wanted
        if not there:
            problems.append(f"not kept: {expected['objects'][0]}")
        elif days != wanted:
            problems.append(f"{expected['objects'][0]}: {days}, not {wanted}")
    unsafe = []
    for expected in spec.get("optional") or ():
        if any(_about(r, expected.get("subject", "user"), expected["objects"]) for r in rows) \
                and _days(rows, expected) != sorted(expected["days"]):
            unsafe.append(f"kept with another day: {expected['objects'][0]}")
    for fact in spec.get("stands") or ():
        if not any(_about(r, fact["subject"], [fact["object"]]) and named(r, fact)
                   and r["extraction_method"] in _USER_SOURCES for r in rows):
            unsafe.append(f"no longer stands: {fact['subject']} {fact['object']}")
    for fact in spec.get("none") or ():
        unsafe += [f"stored: {r['subject']} {r['predicate']} {r['object']}" for r in rows
                   if _about(r, fact["subject"], [fact["object"]]) and named(r, fact)]
    for fact in spec.get("asks") or ():
        if not any(_about(w, fact["subject"], [fact["object"]]) for w in waiting):
            unsafe.append(f"not asked: {fact['subject']} {fact['object']}")
    for fact in spec.get("not_asked") or ():
        if any(_about(w, fact["subject"], [fact["object"]]) for w in waiting):
            unsafe.append(f"asked: {fact['subject']} {fact['object']}")
    for fact in spec.get("once") or ():
        held = [r for r in rows if _about(r, fact["subject"], [fact["object"]]) and named(r, fact)]
        if len(held) != 1:
            unsafe.append(f"held {len(held)} times: {fact['subject']} {fact['object']}")
    if spec.get("undated"):
        unsafe += [f"a day stored: {r['subject']} {r['predicate']} {r['object']} {event_day(r['meta'])}"
                   for r in rows if event_day(r["meta"])]
    guarded = any(spec.get(k) for k in _GUARDS)
    return {"entries": len(spec.get("entries") or ()), "stored": 0 if other else there_are,
            "day": 0 if other else right, "guarded": int(guarded),
            "safe": int(guarded and not unsafe and not other), "whole": int(not (problems or unsafe or other)),
            "problems": other + problems + unsafe}


def _one_asking(turns: list[dict[str, Any]], llm_replies: dict[str, Any], mode: str, client: Any) -> dict[str, Any]:
    """One asking's counts per language, and each turn's problems."""
    from kazma_core.memory.benchmark import _reply_key

    out: dict[str, Any] = {}
    for turn in turns:
        row = out.setdefault(turn["lang"], {"turns": 0, "entries": 0, "guarded": 0, "problems": [],
                                            **dict.fromkeys(_COUNTS, 0)})
        got = _judge(turn, *_stored_after(turn, llm_replies["replies"][_reply_key(turn, llm_replies, mode)], client))
        row["turns"] += 1
        for k in ("entries", "guarded", *_COUNTS):
            row[k] += got[k]
        row["problems"] += [f"{turn['id']}: {p}" for p in got["problems"]]
    return out


def _ratios(row: dict[str, Any], askings: int) -> dict[str, float]:
    return {k: round(row[k] / (row[_OF[k]] * askings), 4) if row[_OF[k]] else 1.0 for k in STORED_SCORES}


def run_event_turns(turns: list[dict[str, Any]], askings: list[dict[str, Any]], *,
                    mode: str = "current") -> dict[str, Any]:
    """The event turns' part of the report: ``events`` per language -- the
    reply's scores and what is stored, over every asking -- each turn's
    problems, and with more than one asking ``events_spread``."""
    import math
    import statistics
    from unittest import mock

    from kazma_core.memory.benchmark import (
        _replayed_extraction,
        _score_extraction,
        _ScriptedClient,
        _total_over_askings,
    )

    replies = [_score_extraction(turns, _replayed_extraction(turns, one, mode=mode)) for one in askings]
    client = _ScriptedClient()
    registry = type("_Registry", (), {"get_client": lambda self: client})()
    # What is stored is judged by its rows: no vector is needed, none is made.
    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry), \
            mock.patch("kazma_core.memory.embedder.get_embedder", lambda: None):
        stored = [_one_asking(turns, one, mode, client) for one in askings]
    events = {}
    for lang, reply in _total_over_askings(replies).items():
        first = stored[0][lang]
        row = {k: first[k] for k in ("turns", "entries", "guarded")}
        row.update({k: sum(part[lang][k] for part in stored) for k in _COUNTS})
        counts = {f"{k}_count": row.pop(k) for k in _COUNTS}
        row.update(_ratios({**row, **{k: counts[f"{k}_count"] for k in _COUNTS}}, len(askings)))
        row.update(counts)
        row["problems"] = [f"asking {i}, {p}" if len(askings) > 1 else p
                           for i, part in enumerate(stored, 1) for p in part[lang]["problems"]]
        events[lang] = {**reply, **row}
    out: dict[str, Any] = {"events": events}
    if len(askings) > 1:
        out["events_spread"] = {
            lang: {k: round(2 * statistics.stdev(values) / math.sqrt(len(values)), 4)
                   for k, values in (
                       *((k, [float(r[lang][k]) for r in replies]) for k in EVENT_SCORES if k in replies[0][lang]),
                       *((k, [_ratios(part[lang], 1)[k] for part in stored]) for k in STORED_SCORES))}
            for lang in sorted(events)}
    return out
