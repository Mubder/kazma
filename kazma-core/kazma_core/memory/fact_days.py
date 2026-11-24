"""The day a fact carries, where it came from, and whether two days are one
occasion's (the memory plan's W4, dated event lines, 2026-11-23; its review,
2026-11-24).

A fact's metadata holds one day, if any:

* ``started``: when a lasting fact began ("since 2019"), or -- with
  ``happened`` -- the day an event happened ("I went to a pottery exhibition
  last Saturday");
* ``planned``: the day an event is planned for ("the concert on 3 June"): a
  day after the turn that told it, never a start and never a day it happened;
* ``day_source``: the turn and the source that gave the day, when that is not
  the fact's own -- a later turn's ("it was last Saturday"), or the turn of
  the entry this one replaced. ``{"session", "turn", "method", "at"}``;
* ``day_before``: the days it held before, newest first, each with its own
  source: a day a later turn corrected or made more exact.

So a forgotten turn takes back the day it gave and nothing else
(:func:`without_turn`), and a day the model read never passes for the user's
(:func:`days_method`): until 2026-11-24 a day carried no source, and a model's
day on a fact the user stated read as the user's own.

A day is ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``. Two days are *compatible*
when one is the other said less exactly ("2026-05" and "2026-05-30"). Pure
functions over a fact's metadata; no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

__all__ = [
    "DAY_KEYS",
    "any_day",
    "compatible",
    "could_have_happened_by",
    "day_span",
    "days_method",
    "event_day",
    "exact",
    "has_day",
    "metadata_of",
    "same_thing",
    "today",
    "vaguer",
    "with_day",
    "without_turn",
]

#: What a fact's day is made of, in its metadata.
DAY_KEYS = ("started", "happened", "planned", "day_source")


def metadata_of(value: Any) -> dict[str, Any]:
    """A fact's metadata as a dict, whatever the column holds (an unreadable
    one is empty)."""
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def event_day(meta: dict[str, Any]) -> str:
    """The day an event happened or is planned for ("" when it is neither)."""
    if meta.get("happened"):
        return str(meta.get("started") or "")
    return str(meta.get("planned") or "")


def any_day(meta: dict[str, Any]) -> str:
    """The day the fact holds, an event's or a start ("" for none)."""
    return event_day(meta) or str(meta.get("started") or "")


def has_day(meta: dict[str, Any]) -> bool:
    return bool(meta.get("started") or meta.get("planned"))


def exact(day: str | None) -> bool:
    """Whether *day* names one day, not a month or a year."""
    return len(str(day or "")) == 10


def compatible(a: str | None, b: str | None) -> bool:
    """Whether *a* and *b* may be one day: the same, or one said less exactly."""
    a, b = str(a or ""), str(b or "")
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def vaguer(a: str | None, b: str | None) -> bool:
    """Whether *a* says less of the same day than *b* ("2026-05" of
    "2026-05-30"); no day at all says less than any."""
    a, b = str(a or ""), str(b or "")
    return len(a) < len(b) and b.startswith(a)


def day_span(day: str | None, zone: Any) -> tuple[float, float] | None:
    """The time *day* covers in *zone* (epoch seconds, end exclusive): that
    day, month or year."""
    parts = str(day or "").split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 0
        dom = int(parts[2]) if len(parts) > 2 else 0
        first = datetime(year, month or 1, dom or 1, tzinfo=zone)
        if dom:
            last = first + timedelta(days=1)
        elif month:
            last = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=zone)
        else:
            last = datetime(year + 1, 1, 1, tzinfo=zone)
    except (ValueError, IndexError, OverflowError):
        return None
    return first.timestamp(), last.timestamp()


def could_have_happened_by(day: str, told_at: Any) -> bool:
    """Whether something told at *told_at* (epoch) can have happened on
    *day*: not after the day it was told, in the latest zone there is. What
    was told in May did not happen in November, so a retelling that says so
    is another occasion."""
    try:
        latest = datetime.fromtimestamp(float(told_at), UTC) + timedelta(hours=14)
    except (TypeError, ValueError, OverflowError, OSError):
        return True
    told = latest.date().isoformat()
    return str(day)[: len(told)] <= told[: len(str(day))]


def today(now: float, zone: Any) -> date:
    return datetime.fromtimestamp(now, zone).date()


def same_thing(a: str, b: str) -> bool:
    """Whether two values may name one thing: the same value, or sharing a
    content word ("a pottery exhibition" and "the pottery show", never
    "bananas" and "fresh vegetables")."""
    from kazma_core.memory.belief_mutation import _same_value
    from kazma_core.memory.query_terms import content_terms

    if _same_value(a, b):
        return True
    return bool(set(content_terms(a)) & set(content_terms(b)))


def days_method(meta: dict[str, Any], fact_method: str | None) -> str:
    """The source that gave the fact's day (``provenance``'s names): its own
    ``day_source``; else the fact's, except that a day on a fact the user
    stated came from the model's pass (the quick pass reads no days), also on
    the rows written before a day named its source."""
    from kazma_core.memory.provenance import LLM_INFERRED, USER_SOURCES

    source = meta.get("day_source")
    if isinstance(source, dict) and source.get("method"):
        return str(source["method"])
    return LLM_INFERRED if fact_method in USER_SOURCES else str(fact_method or "")


def _layer(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: meta[k] for k in DAY_KEYS if meta.get(k)}


def with_day(meta: dict[str, Any], day: dict[str, Any], *, keep_before: bool = True) -> dict[str, Any]:
    """*meta* with *day* (some of :data:`DAY_KEYS`) as the fact's day; the day
    it held before is kept under ``day_before`` with its own source."""
    out = {k: v for k, v in meta.items() if k not in DAY_KEYS}
    held = _layer(meta)
    if keep_before and has_day(held):
        out["day_before"] = [held, *(meta.get("day_before") or [])][:5]
    out.update({k: day[k] for k in DAY_KEYS if day.get(k)})
    return out


def without_turn(meta: dict[str, Any], given_by: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
    """*meta* without the days whose source *given_by* names (a
    ``day_source``): the newest day left stands. None when it names none of
    them. A day with no ``day_source`` is the fact's own turn's, which a
    forget of that turn empties whole."""
    def theirs(layer: dict[str, Any]) -> bool:
        source = layer.get("day_source")
        return isinstance(source, dict) and given_by(source)

    layers = [_layer(meta), *(x for x in meta.get("day_before") or [] if isinstance(x, dict))]
    kept = [x for x in layers if has_day(x) and not theirs(x)]
    if len(kept) == sum(1 for x in layers if has_day(x)):
        return None
    out = {k: v for k, v in meta.items() if k not in DAY_KEYS and k != "day_before"}
    if kept:
        out.update(kept[0])
        if kept[1:]:
            out["day_before"] = kept[1:]
    return out
