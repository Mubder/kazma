"""Ego-graph anchoring for leaf belief subjects (memory graph integrity).

Problem this solves (live-data verified 2026-08-24): the belief extractor
mints a ``concept`` entity for every non-user subject, but when the belief's
object is a literal payload ("fully_clean", "4/4", "instagram", a file
path), nothing ever links that concept back into the ego/hub component.
The v2 graph painter renders subject + virtual-object nodes correctly, yet
the pair floats as a disconnected component away from ``user`` — the
"orphan nodes" operators see. The entities list compounded the confusion by
counting scalar strings as co-occurrence "links".

Fix semantics (industry-standard ego-graph invariant — every concept in a
personal memory graph is reachable from the ego node):

- A freshly written belief whose object is a **payload** (not an entity)
  and whose subject has no other entity-side linkage gets an explicit
  anchor belief ``user → related_to → <subject>`` with
  ``extraction_method='system_tool'`` — system-asserted context, never a
  user fact, so §20's source-trust gate and conservative auto-store rules
  stay intact.
- :func:`anchor_orphan_leaf_concepts` backfills existing leaf subjects
  idempotently: once anchored, the subject has an entity-side link
  (``user`` is an entity) and never qualifies again.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ANCHOR_PREDICATE",
    "is_payload_object",
    "subject_has_entity_link",
    "anchor_leaf_subject",
    "anchor_orphan_leaf_concepts",
]

ANCHOR_PREDICATE = "related_to"


def is_payload_object(conn: sqlite3.Connection, obj: str) -> bool:
    """True when *obj* is a literal value rather than an entity node.

    Empty/junk tokens are NOT payloads — they are rejected upstream entirely
    and must not trigger anchoring.
    """
    s = (obj or "").strip()
    if not s or s == "user":
        return False
    try:
        from kazma_core.memory.hygiene import is_junk_entity_token

        if is_junk_entity_token(s):
            return False
    except Exception:
        logger.debug("[ego_anchor] hygiene import failed", exc_info=True)
    try:
        row = conn.execute("SELECT 1 FROM entities WHERE id = ?", (s,)).fetchone()
        return row is None
    except Exception:
        logger.debug("[ego_anchor] entity lookup failed", exc_info=True)
        return False


def subject_has_entity_link(conn: sqlite3.Connection, subject: str) -> bool:
    """True when *subject* already touches any ENTITY via a live belief —
    as a subject with an entity object, or as an object of another entity.
    Payload-only self-links (its own scalar beliefs) don't count."""
    try:
        row = conn.execute(
            """
            SELECT 1 FROM beliefs b
            WHERE b.invalidated_at IS NULL AND b.valid_until IS NULL
              AND (
                (b.subject = ? AND EXISTS (
                    SELECT 1 FROM entities e WHERE e.id = b.object))
                OR
                (b.object = ? AND EXISTS (
                    SELECT 1 FROM entities e WHERE e.id = b.subject))
              )
            LIMIT 1
            """,
            (subject, subject),
        ).fetchone()
        return row is not None
    except Exception:
        logger.debug("[ego_anchor] entity-link check failed", exc_info=True)
        return True  # fail-closed: never fabricate anchors on uncertainty


def anchor_leaf_subject(
    conn: sqlite3.Connection,
    subject: str,
    *,
    tenant_id: str = "default",
    cfg: dict[str, Any] | None = None,
    source_session: str | None = None,
) -> dict[str, Any]:
    """Anchor one leaf subject to the ego node. Returns mutate_belief result.

    No-op (without writing) when the subject already has an entity-side
    link — idempotent by construction.
    """
    if not subject or subject == "user":
        return {"action": "noop", "reason": "not_anchorable"}
    if subject_has_entity_link(conn, subject):
        return {"action": "noop", "reason": "already_linked"}

    from kazma_core.memory.belief_mutation import mutate_belief

    result = mutate_belief(
        conn,
        "user",
        ANCHOR_PREDICATE,
        subject,
        predicate_type="semantic",
        confidence=0.9,
        importance=1,
        extraction_method="system_tool",
        tenant_id=tenant_id,
        source_session=source_session,
        cfg=cfg,
    )
    if result.get("action") not in ("noop", None):
        logger.info(
            "[ego_anchor] anchored leaf subject '%s' (%s)",
            subject[:48], result.get("action"),
        )
    return result


def anchor_orphan_leaf_concepts(
    conn: sqlite3.Connection,
    *,
    tenant_id: str = "default",
    limit: int = 200,
    cfg: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Backfill pass: anchor every existing leaf subject (bounded per sweep).

    A leaf subject = has ≥1 live belief and ALL of them carry payload
    objects. Idempotent: anchored subjects gain an entity-side link and stop
    qualifying on the next sweep.
    """
    stats = {"scanned": 0, "anchored": 0, "skipped_linked": 0}
    try:
        rows = conn.execute(
            """
            SELECT b.subject, COUNT(*) AS n,
                   SUM(CASE WHEN oe.id IS NULL THEN 1 ELSE 0 END) AS payload_objs
            FROM beliefs b
            LEFT JOIN entities oe ON oe.id = b.object
            WHERE b.invalidated_at IS NULL AND b.valid_until IS NULL
              AND b.subject != 'user'
            GROUP BY b.subject
            HAVING payload_objs = n
            ORDER BY n DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        for r in rows:
            subject = r["subject"]
            stats["scanned"] += 1
            result = anchor_leaf_subject(conn, subject, tenant_id=tenant_id, cfg=cfg)
            if result.get("action") not in ("noop", None):
                stats["anchored"] += 1
            else:
                stats["skipped_linked"] += 1
        conn.commit()
    except Exception:
        logger.warning("[ego_anchor] backfill sweep failed", exc_info=True)
        stats["error"] = 1
    return stats
