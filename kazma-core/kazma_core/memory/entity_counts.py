"""Materialized belief_count / graph_degree maintenance for entities.

Phase 3 performance fix. The operator ``/memory`` page used to compute these
per-row via correlated subqueries (``_belief_count_sql`` / ``_entity_degree_sql``
in ``kazma_ui/memory_api.py``), which is O(entities × beliefs) on every page
load. This module maintains the same values as columns on ``entities`` so the
read path is a plain column read.

Design invariants
-----------------
* The columns are **derived data**. If a write site is missed, the read path
  self-heals: a sentinel value of ``-1`` means "not computed yet", and the
  reader falls back to the live subquery for that row only (then enqueues a
  background recompute). So a missed write site can make a row temporarily
  slow, never wrong.
* The count semantics EXACTLY mirror ``_belief_count_sql`` /
  ``_entity_degree_sql`` (active beliefs where ``subject = e.id OR object =
  e.name OR object = e.id OR subject = e.name``, scoped by tenant_id). Drift
  between the column and the live query would be worse than no column.
* Callers must pass every identity-affected entity id: for an insert that's
  ``[subject, object]``; for a merge/identity-rewrite it's the union of old
  AND new subject+object (a flip changes two entities' counts).

Safe-to-skip sites: write paths that only touch embedding / access_count /
last_accessed (``_bump_access``, ``_reembed_missing``) do NOT change the
active belief set and must NOT call this — doing so is wasted work, not a
correctness bug.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["recompute_entity_counts", "BELIEF_COUNT_STALE"]


# Sentinel stored in entities.belief_count / graph_degree meaning "not yet
# computed". The read path treats -1 as stale and falls back to the live
# subquery for that row.
BELIEF_COUNT_STALE = -1


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
        tenant_id:   Tenant scope for the count queries (must match the
                     tenant_id on the entity rows).

    Returns:
        The number of entity rows updated (0 if none matched / on no-op).

    The per-entity SQL mirrors ``_belief_count_sql`` / ``_entity_degree_sql``
    in ``kazma_ui/memory_api.py`` exactly, so the column stays consistent with
    the live subquery the read path falls back to. Never raises — logs on
    failure so a bad entity id can't break the calling write.
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

    # Per-entity recompute. The count/degree SQL mirrors _belief_count_sql /
    # _entity_degree_sql in kazma_ui/memory_api.py exactly so the materialized
    # column stays consistent with the live subquery the read path falls back
    # to when a row is stale.
    updated = 0
    for eid in ids:
        try:
            # Look up the entity's display name + tenant so the degree query
            # can be self-contained (no correlated outer-scope reference to e).
            erow = conn.execute(
                "SELECT name, tenant_id FROM entities WHERE id = ?", (eid,)
            ).fetchone()
            if not erow:
                continue
            ename = erow[0] if erow[0] is not None else ""
            etid = erow[1] if erow[1] is not None else tenant_id

            cnt_row = conn.execute(
                "SELECT COUNT(*) FROM beliefs b "
                "WHERE b.tenant_id = ? "
                "AND b.valid_until IS NULL AND b.invalidated_at IS NULL "
                "AND (b.subject = ? OR b.object = ? OR b.object = ? OR b.subject = ?)",
                (etid, eid, ename, eid, ename),
            ).fetchone()
            cnt = int(cnt_row[0]) if cnt_row else 0

            # Distinct OTHER entities co-occurring in active beliefs. The CASE
            # picks the opposite endpoint; the outer filter excludes self and
            # non-entity literals. Mirrors _entity_degree_sql exactly.
            deg_row = conn.execute(
                """
                SELECT COUNT(DISTINCT other_id) FROM (
                  SELECT CASE
                    WHEN b.subject = ? THEN b.object
                    WHEN b.object = ? THEN b.subject
                    WHEN b.subject = ? THEN b.object
                    WHEN b.object = ? THEN b.subject
                    ELSE NULL
                  END AS other_id
                  FROM beliefs b
                  WHERE b.tenant_id = ?
                    AND b.valid_until IS NULL AND b.invalidated_at IS NULL
                    AND (b.subject = ? OR b.object = ?
                         OR b.subject = ? OR b.object = ?)
                )
                WHERE other_id IS NOT NULL
                  AND other_id != ?
                  AND other_id != ?
                  AND other_id NOT IN ('', 'true', 'false', 'null')
                """,
                (eid, eid, ename, ename, etid, eid, eid, ename, ename, eid, ename),
            ).fetchone()
            deg = int(deg_row[0]) if deg_row else 0

            cur = conn.execute(
                "UPDATE entities SET belief_count = ?, graph_degree = ? "
                "WHERE id = ?",
                (cnt, deg, eid),
            )
            updated += int(cur.rowcount or 0)
        except Exception:
            logger.debug("[entity_counts] recompute failed for %r", eid, exc_info=True)
    return updated
