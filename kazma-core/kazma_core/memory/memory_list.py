"""Every memory about a subject, a name or a time, a page at a time (part F).

Recall shows the few memories that answer a question best: five facts and
five conversations, or ``limits.LIST_LIMIT`` of each for a question that asks
for every memory of a kind. "How many times did I...", "all my
subscriptions" need every one (framework audit F9). :func:`list_memories` is
the agent's ``memory_list``: the facts and the conversations of the caller's
space that hold every content word of *about* (none: all of them), from the
period *when* names, newest first, :data:`limits.LIST_PAGE` a page, each with
its id (``memory_read`` reads a conversation whole). Read-only: nothing is
bumped, nothing written.

The caller's tenant only (``tenant_view.narrowed_to``, as every memory read
by id); the tiers recall searches (a forgotten memory is not listed); the
current facts, and their earlier values when *when* names a period they held
in. In a shared space every line is shown under its owner, as recall shows it
(``speakers.Room``).
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kazma_core.memory import word_search
from kazma_core.memory.limits import LIST_PAGE, LIST_SHOW_ANSWER, LIST_SHOW_ONE, LIST_SHOW_QUESTION

__all__ = ["MemoryListing", "list_memories"]


@dataclass(slots=True)
class MemoryListing:
    """One page of a listing: its lines (``kind``, ``id``, ``text``), how
    many memories match in all, and the offset of the next page (None at the
    end)."""

    lines: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    offset: int = 0
    next_offset: int | None = None
    about: str = ""
    when: str = ""
    period: tuple[float, float] | None = None


_ISO_RANGE = re.compile(r"^\s*(\d{4}-\d{2}(?:-\d{2})?)\s*(?:\.\.|to|/)\s*(\d{4}-\d{2}(?:-\d{2})?)\s*$")
_ISO_ONE = re.compile(r"^\s*(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?\s*$")


def _iso_span(text: str, zone: Any) -> tuple[float, float] | None:
    """A day, a month or a year written as ISO ("2026-08", "2026-08-14")."""
    m = _ISO_ONE.match(text)
    if not m:
        return None
    year, month, day = int(m.group(1)), m.group(2), m.group(3)
    if day:
        start = datetime(year, int(month), int(day), tzinfo=zone)
        return start.timestamp(), start.timestamp() + 86400.0
    if month:
        m_ = int(month)
        start = datetime(year, m_, 1, tzinfo=zone)
        end = datetime(year + (m_ == 12), m_ % 12 + 1, 1, tzinfo=zone)
        return start.timestamp(), end.timestamp()
    return datetime(year, 1, 1, tzinfo=zone).timestamp(), datetime(year + 1, 1, 1, tzinfo=zone).timestamp()


def _read_when(when: str, *, now: float, zone: Any) -> tuple[float, float] | None:
    """The period *when* names: ISO ("2026-08", "2026-08-01..2026-08-31")
    or words ("last week", "in August", "الشهر الماضي", read as the past by
    ``time_words.read_time``). None when it names none."""
    text = " ".join(str(when or "").split())
    if not text:
        return None
    rng = _ISO_RANGE.match(text)
    if rng:
        first, last = _iso_span(rng.group(1), zone), _iso_span(rng.group(2), zone)
        if first and last:
            return first[0], last[1]
    one = _iso_span(text, zone)
    if one:
        return one
    from kazma_core.memory.time_words import read_time

    ask = read_time(text, now=now, zone=zone, past=True)
    if ask is None or not ask.period:
        return None
    return float(ask.start), float(ask.end)


def _every_word(columns: tuple[str, ...], asked: word_search.Asked) -> tuple[str, list[str]]:
    """Every word asked for is in the row's *columns*, both sides folded
    (``word_search``). A word written in ASCII letters is looked for with
    ``LIKE`` first, which is cheap and, folded or not, the same letters; an
    Arabic word or a number never is: it was, and the folded word was not in
    the text as written (W6, 2026-11-25: 0 of 8 Arabic words found)."""
    held = " || ' ' || ".join(f"COALESCE({c},'')" for c in columns)
    plain = [t for t in asked.terms if t.isascii() and any(ch.isalpha() for ch in t)]
    clause = "".join(f"LOWER({held}) LIKE ? AND " for _ in plain) + word_search.holds("every", *columns)
    # A digit may stand in another script in the text ("iphone١٥", "٣G").
    return clause, ["%" + "".join("_" if ch.isdigit() else ch for ch in t) + "%" for t in plain]


def _day(ts: Any, zone: Any) -> str:
    from kazma_core.memory.recall import _said_on

    return _said_on(ts, zone)


def list_memories(
    *,
    tenant_id: str,
    about: str = "",
    when: str = "",
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
    now: float | None = None,
    zone: Any = None,
    asker: str | None = None,
) -> MemoryListing:
    """One page of every memory of *tenant_id* about *about*, from *when*.

    Facts first (their own page share), then conversations, newest first in
    each; ``offset`` counts lines across both. Raises ``sqlite3.Error`` when
    the memory database cannot be read: the caller says so, never "nothing".
    """
    from kazma_core.memory import speakers
    from kazma_core.memory.episode_text import display_parts
    from kazma_core.memory.tenant_view import narrowed_to
    from kazma_core.memory.vector_engine import RECALLABLE_TIERS

    if zone is None:
        from kazma_core.memory.recall import _memory_zone

        zone = _memory_zone()
    now = time.time() if now is None else float(now)
    period = _read_when(when, now=now, zone=zone)
    if when.strip() and period is None:
        # A time the reader cannot place is never read as "any time".
        return MemoryListing(about=about, when=when, offset=offset)
    only = narrowed_to(tenant_id)
    people = speakers.room(tenant_id)
    own = conn is None
    if own:
        from kazma_core.paths import primary_memory_db

        conn = sqlite3.connect(primary_memory_db(), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        tiers = ",".join("?" for _ in RECALLABLE_TIERS)
        texts = ("user_text", "assistant_text", "summary_text")
        asked = word_search.asked_in(conn, about, [
            ("FROM beliefs WHERE (? IS NULL OR tenant_id = ?) AND valid_until IS NULL AND invalidated_at IS NULL",
             (only, only), _FACT_WORDS),
            (f"FROM episodes WHERE tier IN ({tiers}) AND (? IS NULL OR tenant_id = ?)",
             (*RECALLABLE_TIERS, only, only), texts)], words=8)
        facts = _facts(conn, only, asked, period, zone)
        where, params = [f"tier IN ({tiers})", "(? IS NULL OR tenant_id = ?)"], [*RECALLABLE_TIERS, only, only]
        if period is not None:
            where.append("created_at >= ? AND created_at < ?")
            params.extend(period)
        if asked.terms:
            clause, likes = _every_word(texts, asked)
            where.append(clause)
            params.extend(likes)
        turns = conn.execute(
            "SELECT id, user_text, assistant_text, summary_text, created_at, "
            "json_extract(metadata_json, '$.said_by') AS said_by FROM episodes "
            f"WHERE {' AND '.join(where)} ORDER BY created_at DESC",  # nosec B608 - fixed clauses, '?' placeholders
            params,
        ).fetchall()
    finally:
        if own:
            conn.close()
    from kazma_core.memory.fact_days import today as day_now
    from kazma_core.memory.recall import _event_label

    today = day_now(now, zone).isoformat()
    lines: list[dict[str, Any]] = []
    for f in facts:
        sub = people.shown_subject(str(f["subject"] or ""), asker) if people.shared else str(f["subject"] or "")
        text = f"{sub.replace('_', ' ')} {str(f['predicate'] or '').replace('_', ' ')} {f['object']}".strip()
        held = _event_label(_event_of(f), today) or _day(f["valid_from"], zone)
        if f["valid_until"] is not None:
            held = f"{held} to {_day(f['valid_until'], zone)}"
        lines.append({"kind": "fact", "id": str(f["id"]), "date": held, "text": " ".join(text.split())})
    for r in turns:
        shown, _more = display_parts(r["user_text"], r["assistant_text"], r["summary_text"],
                                     question_max=LIST_SHOW_QUESTION, answer_max=LIST_SHOW_ANSWER,
                                     one_max=LIST_SHOW_ONE)
        name = people.shown_author(r["said_by"], asker) if people.shared else None
        if name and shown.startswith("User: "):
            shown = f"{name}: {shown[len('User: '):]}"
        lines.append({"kind": "conversation", "id": str(r["id"]), "date": _day(r["created_at"], zone),
                      "text": shown})
    start = max(0, int(offset or 0))
    page = lines[start:start + LIST_PAGE]
    end = start + len(page)
    return MemoryListing(lines=page, total=len(lines), offset=start,
                         next_offset=end if end < len(lines) else None, about=about, when=when, period=period)


def _event_of(row: sqlite3.Row) -> dict[str, Any]:
    """A fact's metadata when it may be an event's: a list entry's."""
    from kazma_core.memory.fact_days import metadata_of

    return metadata_of(row["metadata_json"]) if str(row["predicate_type"] or "set") == "set" else {}


#: A fact's metadata when it reads as JSON (an event's day is in it).
_META = "CASE WHEN json_valid(metadata_json) THEN metadata_json END"


#: A fact's words.
_FACT_WORDS = ("subject", "predicate", "object")


def _facts(conn: sqlite3.Connection, only: str | None, asked: word_search.Asked,
           period: tuple[float, float] | None, zone: Any) -> list[sqlite3.Row]:
    """The facts holding every word asked for: the current ones, and with a period
    every value that held during it (an earlier value included) -- an event
    by its own day, whenever it was told. Never a forgotten fact (its value
    is gone)."""
    from kazma_core.memory import fact_days

    where = ["(? IS NULL OR tenant_id = ?)", "COALESCE(object, '') != ''"]
    params: list[Any] = [only, only]
    if period is None:
        where.append("valid_until IS NULL AND invalidated_at IS NULL")
    else:
        # Held during the period: began before its end, ended after its start.
        # An earlier value is a fact another one replaced (``supersedes_id``):
        # a fact the user took back was never true, and is not listed.
        where.append("((valid_from < ? AND (valid_until IS NULL OR valid_until > ?)) "
                     f"OR (valid_until IS NULL AND COALESCE(json_extract({_META}, '$.happened'), "
                     f"json_extract({_META}, '$.planned')) IS NOT NULL))")
        params.extend([period[1], period[0]])
        where.append("(invalidated_at IS NULL OR id IN (SELECT supersedes_id FROM beliefs "
                     "WHERE supersedes_id IS NOT NULL))")
    if asked.terms:
        clause, likes = _every_word(_FACT_WORDS, asked)
        where.append(clause)
        params.extend(likes)
    rows = conn.execute(
        "SELECT id, subject, predicate, predicate_type, object, valid_from, valid_until, metadata_json FROM beliefs "
        f"WHERE {' AND '.join(where)} ORDER BY valid_from DESC",  # nosec B608 - fixed clauses, '?' placeholders
        params,
    ).fetchall()

    def in_period(row: sqlite3.Row) -> bool:
        if period is None:
            return True
        span = fact_days.day_span(fact_days.event_day(_event_of(row)), zone)
        return span is None or (span[0] < period[1] and span[1] > period[0])

    return [r for r in rows if in_period(r)]
