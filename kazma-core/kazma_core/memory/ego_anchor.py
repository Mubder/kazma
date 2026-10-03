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

- A freshly written belief whose subject is not already reachable from
  ``user`` gets an explicit anchor ``user → related_to → <subject>`` with
  ``extraction_method='system_tool'`` — system-asserted context, never a
  user fact, so §20's source-trust gate and conservative auto-store rules
  stay intact. "Already linked to *some* entity" is NOT enough: a pair
  like ``identity_digital_rdap → is_authoritative_for → ai_domain_availability``
  is still a floating cluster.
- Payload objects (``fully_clean``, ``4/4``, paths) must **not** be minted
  as concept entities. That mint made :func:`is_payload_object` return
  false at write time and skipped the hub edge — the remaining orphan
  factory after M-03.
- :func:`anchor_orphan_leaf_concepts` backfills any subject that does not
  yet reach the hub. Idempotent: once ``user → related_to → subject``
  exists, the subject stops qualifying.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ANCHOR_PREDICATE",
    "is_payload_object",
    "object_should_mint_entity",
    "subject_should_mint_entity",
    "subject_reaches_hub",
    "subject_has_entity_link",
    "anchor_leaf_subject",
    "anchor_orphan_leaf_concepts",
]

# Predicates whose OBJECT is another graph node (not a scalar payload).
# Unknown predicates default to payload (fail-safe: do not mint value-nodes).
_RELATIONAL_OBJECT_PREDICATES = frozenset(
    {
        "related_to",
        "has_part",
        "part_of",
        "includes",
        "included_in",
        "competes_with",
        "applies_to",
        "regulates_activity",
        "is_authoritative_for",
        "engages_in",
        "works_at",
        "lives_in",
        "located_in",
        "uses_tool",
        "uses",
        "knows",
        "member_of",
        "owned_by",
        "owns",
        "parent_of",
        "child_of",
        "covers",
        "covered_brand",
        "mentions",
        "mentioned_in",
        "is_a",
        "instance_of",
        "type_of",
        "same_as",
        "alias_of",
        "depends_on",
        "used_by",
    }
)

ANCHOR_PREDICATE = "related_to"


def _looks_like_path_or_url(text: str) -> bool:
    s = text.lower()
    if "://" in s or "\\\\" in text or text.startswith("/") or ":\\" in text:
        return True
    if any(s.endswith(ext) for ext in (".md", ".json", ".txt", ".py", ".yml", ".yaml")):
        return True
    return False


def object_should_mint_entity(
    conn: sqlite3.Connection,
    obj: str,
    *,
    predicate: str = "",
) -> bool:
    """True when the belief *object* should become a concept entity.

    Payload literals (status strings, paths, junk) must not be minted —
    minting them made write-time :func:`is_payload_object` return False
    and skipped the hub edge (the remaining orphan factory).
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
    if _looks_like_path_or_url(s) or " " in s:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM entities WHERE id = ? OR name = ? LIMIT 1", (s, s)
        ).fetchone()
        if row is not None:
            return True
    except Exception:
        logger.debug("[ego_anchor] entity lookup failed", exc_info=True)
    pred = (predicate or "").strip().lower().replace(" ", "_")
    return pred in _RELATIONAL_OBJECT_PREDICATES


# Subject shapes that name a step of work or a record, not a thing the user
# talks about: "phase_25_unified_notification_alert_system", "JIRA-142",
# "v0.10.2", a commit hash. The fact is kept as text on the belief; only the
# entity is not minted, so the entity list holds things, not work items.
_WORK_ITEM_RE = re.compile(
    r"^(phase|step|stage|sprint|wave|round|milestone|ticket|issue|pr|task|run)[_\s-]*\d",
    re.I,
)
_TICKET_RE = re.compile(r"^[a-z]{2,10}-\d+$", re.I)
_VERSION_RE = re.compile(r"^v?\d+([._]\d+)+([._-]?[a-z0-9]+)*$", re.I)
_HASH_RE = re.compile(r"^(?=[0-9a-f]*\d)[0-9a-f]{7,40}$", re.I)
_MAX_SUBJECT_WORDS = 6


def subject_should_mint_entity(subject: str) -> bool:
    """True when a belief *subject* should become a concept entity.

    False for junk tokens, paths and URLs, work-item names (phases, tickets,
    versions, hashes), mostly-digit strings and sentence-length slugs. The
    belief itself is still written and anchored -- the graph draws its
    subject either way; the entity row is what made these clutter the
    entity list as "isolated" concepts.
    """
    from kazma_core.memory.hygiene import is_junk_entity_token

    s = (subject or "").strip()
    if not s or s == "user" or is_junk_entity_token(s):
        return False
    if _looks_like_path_or_url(s):
        return False
    if _WORK_ITEM_RE.match(s) or _TICKET_RE.match(s) or _VERSION_RE.match(s) or _HASH_RE.match(s):
        return False
    letters = sum(ch.isalpha() for ch in s)
    digits = sum(ch.isdigit() for ch in s)
    if digits > letters:
        return False
    words = [w for w in re.split(r"[_\s\-]+", s) if w]
    return len(words) <= _MAX_SUBJECT_WORDS


def is_payload_object(conn: sqlite3.Connection, obj: str, *, predicate: str = "") -> bool:
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
    return not object_should_mint_entity(conn, s, predicate=predicate)


def subject_reaches_hub(
    conn: sqlite3.Connection,
    subject: str,
    *,
    tenant_id: str = "default",
) -> bool:
    """True when *subject* already has a live belief touching ``user``.

    Any-entity linkage is NOT enough — a floating A→B cluster is still an
    orphan from the ego-graph's point of view.
    """
    if not subject or subject == "user":
        return True
    try:
        row = conn.execute(
            """
            SELECT 1 FROM beliefs
            WHERE invalidated_at IS NULL AND valid_until IS NULL
              AND tenant_id = ?
              AND (
                (subject = 'user' AND object = ?)
                OR (object = 'user' AND subject = ?)
              )
            LIMIT 1
            """,
            (tenant_id, subject, subject),
        ).fetchone()
        return row is not None
    except Exception:
        logger.debug("[ego_anchor] hub-reach check failed", exc_info=True)
        return True  # fail-closed: never fabricate anchors on uncertainty


def subject_has_entity_link(conn: sqlite3.Connection, subject: str) -> bool:
    """Deprecated alias of :func:`subject_reaches_hub` (hub, not any-entity)."""
    return subject_reaches_hub(conn, subject)


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
    if subject_reaches_hub(conn, subject, tenant_id=tenant_id):
        return {"action": "noop", "reason": "already_linked"}

    from kazma_core.memory.belief_mutation import mutate_belief

    result = mutate_belief(
        conn,
        "user",
        ANCHOR_PREDICATE,
        subject,
        # 'set' (append-only) is REQUIRED here: a functional predicate would
        # let later anchors supersede each other, and pairing system_tool
        # trust with functional semantics would grant overwrite rights over
        # LLM-inferred facts (§20 posture). related_to must stay append-only.
        predicate_type="set",
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

    A qualifying subject has ≥1 live belief and no live belief that
    touches ``user``. Covers both payload-only leaves and floating
    entity clusters. Idempotent: once hub-anchored, the subject stops
    qualifying.
    """
    stats = {"scanned": 0, "anchored": 0, "skipped_linked": 0}
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT b.subject
            FROM beliefs b
            WHERE b.invalidated_at IS NULL AND b.valid_until IS NULL
              AND b.tenant_id = ?
              AND b.subject != 'user'
              AND NOT EXISTS (
                SELECT 1 FROM beliefs h
                WHERE h.invalidated_at IS NULL AND h.valid_until IS NULL
                  AND h.tenant_id = b.tenant_id
                  AND (
                    (h.subject = 'user' AND h.object = b.subject)
                    OR (h.object = 'user' AND h.subject = b.subject)
                  )
              )
            LIMIT ?
            """,
            (tenant_id, max(1, int(limit))),
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
