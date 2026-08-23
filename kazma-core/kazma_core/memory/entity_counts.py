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
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "BELIEF_COUNT_STALE",
    "belief_count_sql",
    "entity_degree_sql",
    "recompute_entity_counts",
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
