"""Materialized belief_count / graph_degree maintenance for entities.

Phase 3: ``entities.belief_count`` / ``entities.graph_degree`` are
recomputed per-row instead of per-request correlated subqueries (the read
path in ``kazma_ui/memory_api.py`` prefers the materialized columns and
falls back to the live SQL only when a row is stale, i.e. sentinel -1).

Single source of truth: the canonical count/degree SQL lives HERE as
:func:`belief_count_sql` / :func:`entity_degree_sql` (aliased to the outer
``entities e`` row), and ``memory_api`` imports these exact strings. There
must never be a second handwritten copy — the 2026-08-24 orphan-node fix
originally patched only the memory_api copy and silently left this
maintainer with pre-fix semantics (scalars counted as neighbors).

The columns are a cache, and a cache a writer forgets drifts: a merge moves
the source's facts onto the target, which changes the distinct-neighbour
count of every entity that linked to both, and only source and target were
recomputed (11 of 377 live entities were wrong on 2026-10-03, two since an
August merge). Merges recompute the neighbours now (``neighbours=True``),
and :func:`repair_entity_counts` -- a maintenance sweep -- recomputes any
row whose stored counts differ from the live ones, whichever writer missed
it.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "BELIEF_COUNT_STALE",
    "belief_count_sql",
    "entity_degree_sql",
    "recompute_entity_counts",
    "repair_entity_counts",
]

# Sentinel stored in entities.belief_count / graph_degree meaning "not yet
# computed" — memory_api treats any -1 as stale and falls back to live SQL.
BELIEF_COUNT_STALE = -1


def belief_count_sql() -> str:
    """Correlated subquery: active-belief count for the outer ``entities e`` row."""
    return """
        (
          SELECT COUNT(*) FROM beliefs b
          WHERE b.tenant_id = e.tenant_id
            AND b.valid_until IS NULL AND b.invalidated_at IS NULL
            AND (b.subject = e.id OR b.object = e.name
                 OR b.object = e.id OR b.subject = e.name)
        )
    """


def entity_degree_sql() -> str:
    """Correlated subquery: distinct ENTITY nodes co-occurring with the
    outer ``entities e`` row in active beliefs.

    Only entity-to-entity co-occurrence is graph degree. Literal payload
    objects ("fully_clean", "4/4", a file path) are belief text — the v2
    painter shows them as virtual fact nodes, but they are not neighbors.
    The ``EXISTS entities`` filter encodes that; keep this the ONLY copy.
    """
    return """
        (
          SELECT COUNT(DISTINCT other_id) FROM (
            SELECT CASE
              WHEN b.subject = e.id THEN b.object
              WHEN b.object = e.id THEN b.subject
              WHEN b.subject = e.name THEN b.object
              WHEN b.object = e.name THEN b.subject
              ELSE NULL
            END AS other_id
            FROM beliefs b
            WHERE b.tenant_id = e.tenant_id
              AND b.valid_until IS NULL AND b.invalidated_at IS NULL
              AND (b.subject = e.id OR b.object = e.id
                   OR b.subject = e.name OR b.object = e.name)
          )
          WHERE other_id IS NOT NULL
            AND other_id != e.id
            AND other_id != e.name
            AND other_id NOT IN ('', 'true', 'false', 'null')
            AND EXISTS (SELECT 1 FROM entities oe WHERE oe.id = other_id)
        )
    """


def recompute_entity_counts(
    conn: Any,
    entity_ids: list[str],
    *,
    tenant_id: str = "default",
    neighbours: bool = False,
) -> int:
    """Recompute and persist belief_count + graph_degree for the given entities.

    Args:
        conn:        An open sqlite3.Connection on the primary memory DB. The
                     caller owns the transaction (commit) — this function only
                     executes UPDATEs, matching the convention of the other
                     write helpers in this package.
        entity_ids:  Entity ids whose counts may have changed. De-duplicated
                     internally; empties/None are skipped.
        tenant_id:   Tenant scope fallback when the entity row lacks one.
        neighbours:  Also recompute every entity sharing a live fact with
                     these (a merge: the source's neighbours now link to the
                     target, and one that linked to both lost a neighbour).

    Returns:
        The number of entity rows updated (0 if none matched / on no-op).

    Uses exactly :func:`belief_count_sql` / :func:`entity_degree_sql` so the
    materialized columns can never drift from the live subqueries the read
    path falls back to. Never raises — logs on failure so a bad entity id
    can't break the calling write.
    """
    # De-dup + drop empties, preserving order.
    seen: set[str] = set()
    ids: list[str] = []
    for eid in entity_ids or []:
        if not eid:
            continue
        s = str(eid)
        if s not in seen:
            seen.add(s)
            ids.append(s)
    if not ids:
        return 0
    if neighbours:
        for other in _neighbours(conn, ids):
            if other not in seen:
                seen.add(other)
                ids.append(other)

    sql = (
        f"SELECT {belief_count_sql()} AS cnt, {entity_degree_sql()} AS deg "
        "FROM entities e WHERE e.id = ?"
    )

    updated = 0
    for eid in ids:
        try:
            row = conn.execute(sql, (eid,)).fetchone()
            if row is None:
                continue
            cnt = int(row[0] or 0)
            deg = int(row[1] or 0)
            cur = conn.execute(
                "UPDATE entities SET belief_count = ?, graph_degree = ? "
                "WHERE id = ?",
                (cnt, deg, eid),
            )
            updated += int(cur.rowcount or 0)
        except Exception:
            logger.debug("[entity_counts] recompute failed for %r", eid, exc_info=True)
    return updated


def _neighbours(conn: Any, entity_ids: list[str]) -> list[str]:
    """Entity ids that share a live fact with any of *entity_ids*."""
    marks = ",".join("?" for _ in entity_ids)
    try:
        rows = conn.execute(
            "SELECT DISTINCT CASE WHEN b.subject IN (" + marks + ") THEN b.object "
            "ELSE b.subject END FROM beliefs b "
            "WHERE b.valid_until IS NULL AND b.invalidated_at IS NULL "
            "AND (b.subject IN (" + marks + ") OR b.object IN (" + marks + "))",
            (*entity_ids, *entity_ids, *entity_ids),
        ).fetchall()
    except sqlite3.Error:
        logger.warning("[entity_counts] neighbours of %d entities unreadable", len(entity_ids), exc_info=True)
        return []
    return [str(r[0]) for r in rows if r[0]]


def repair_entity_counts(conn: Any, *, limit: int = 500) -> list[str]:
    """Recompute every entity (up to *limit*) whose stored counts differ from
    the live ones; returns their ids. The caller owns the commit.

    One pass reads every entity's live counts: 261 ms for 377 entities and
    2,249 facts on the live install.
    """
    rows = conn.execute(
        f"SELECT e.id FROM entities e WHERE e.belief_count != {belief_count_sql()} "
        f"OR e.graph_degree != {entity_degree_sql()} ORDER BY e.id LIMIT ?",
        (int(limit),),
    ).fetchall()
    ids = [str(r[0]) for r in rows if r[0]]
    if ids:
        recompute_entity_counts(conn, ids)
        logger.info("[entity_counts] repaired the counts of %d entit(ies): %s", len(ids), ids[:10])
    return ids
