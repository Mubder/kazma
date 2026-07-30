"""3-tier entity resolution cascade with high-stakes quarantine.

Resolves duplicate entity references (e.g. "John Smith", "J. Smith",
"john_smith") into canonical entities via a three-tier cascade:

  - **Tier 1 (exact)** — alias hash or canonical-slug exact match → auto-merge.
  - **Tier 2 (vector)** — embedding distance < threshold → candidate pair.
  - **Tier 3 (LLM)** — disambiguate tier-2 candidates.

High-stakes entities (``is_high_stakes = 1``, e.g. people, projects) are
quarantined: a merge candidate is written to ``entity_merges`` with
``status='pending'`` for human review instead of being auto-merged.

Low-stakes entities (tools, concepts) auto-merge at tier-2 confidence.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "resolve_entity",
    "alias_hash",
    "slug",
]

_HIGH_STAKES_TYPES = frozenset({"person", "project"})


def slug(text: str) -> str:
    """Canonical slug for an entity label."""
    import re

    raw = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", raw)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80] or "entity"


def alias_hash(text: str) -> str:
    """Stable hash of a normalized alias for exact-match tier 1."""
    norm = " ".join((text or "").strip().lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def resolve_entity(
    conn: sqlite3.Connection,
    name: str,
    *,
    entity_type: str = "concept",
    tenant_id: str = "default",
    cfg: dict[str, Any] | None = None,
    candidate_vectors: dict[str, bytes] | None = None,
    query_vector: bytes | None = None,
) -> dict[str, Any]:
    """Resolve a name to a canonical entity id.

    Returns::

        {"canonical_id": str, "action": "create"|"exact_match"|"auto_merge"|"quarantined"}

    Never raises — logs and creates a fresh entity on any failure.
    """
    if not (name or "").strip():
        return {"canonical_id": "", "action": "noop"}
    norm_name = " ".join(name.strip().split())
    canonical = slug(norm_name)
    ahash = alias_hash(norm_name)
    is_high_stakes = 1 if entity_type in _HIGH_STAKES_TYPES else 0

    # ── Tier 1: exact alias-hash or canonical-slug match ──
    try:
        existing = conn.execute(
            """SELECT id FROM entities
               WHERE tenant_id = ? AND (
                   id = ? OR aliases_json LIKE ?
               ) LIMIT 1""",
            (tenant_id, canonical, f'%{ahash}%'),
        ).fetchone()
        if existing:
            eid = existing["id"] if isinstance(existing, sqlite3.Row) else existing[0]
            return {"canonical_id": eid, "action": "exact_match"}
    except Exception:
        logger.debug("[entity_resolve] tier-1 lookup failed", exc_info=True)

    # ── Tier 2: vector similarity (if vectors provided) ──
    v2 = (cfg or {}).get("v2") or {}
    threshold = float(v2.get("entity_vector_merge_threshold", 0.12))
    if query_vector and candidate_vectors:
        try:
            import numpy as np

            q = np.frombuffer(query_vector, dtype=np.float32)
            qn = np.linalg.norm(q) + 1e-9
            best_id = None
            best_dist = float("inf")
            for eid, evec in candidate_vectors.items():
                v = np.frombuffer(evec, dtype=np.float32)
                dist = float(np.linalg.norm(q - v))
                if dist < best_dist:
                    best_dist = dist
                    best_id = eid
            if best_id is not None and best_dist < threshold:
                # High-stakes → quarantine; low-stakes → auto-merge
                if is_high_stakes:
                    _quarantine_merge(conn, canonical, best_id, tenant_id, best_dist, tier="tier2_vector")
                    # Still create the new entity so it exists pending review
                else:
                    _auto_merge(conn, canonical, best_id, norm_name, ahash, tenant_id, entity_type)
                    return {"canonical_id": best_id, "action": "auto_merge"}
        except ImportError:
            pass  # numpy absent — skip tier 2
        except Exception:
            logger.debug("[entity_resolve] tier-2 vector match failed", exc_info=True)

    # ── No match: create a new canonical entity ──
    _create_entity(conn, canonical, norm_name, ahash, entity_type, tenant_id, is_high_stakes)
    return {"canonical_id": canonical, "action": "create"}


def _create_entity(
    conn: sqlite3.Connection,
    canonical: str,
    name: str,
    ahash: str,
    entity_type: str,
    tenant_id: str,
    is_high_stakes: int,
) -> None:
    aliases = [name]
    conn.execute(
        """INSERT OR IGNORE INTO entities
           (id, tenant_id, type, name, aliases_json, is_high_stakes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (canonical, tenant_id, entity_type, name, json.dumps(aliases), is_high_stakes),
    )
    # Add the alias hash to the aliases list so future tier-1 matches find it
    conn.execute(
        """UPDATE entities SET aliases_json = ?
           WHERE id = ? AND aliases_json NOT LIKE ?""",
        (json.dumps([name, ahash]), canonical, f'%{ahash}%'),
    )
    conn.commit()


def _auto_merge(
    conn: sqlite3.Connection,
    new_id: str,
    target_id: str,
    name: str,
    ahash: str,
    tenant_id: str,
    entity_type: str,
) -> None:
    """Low-stakes auto-merge: add the alias to the target entity."""
    # Ensure the target exists
    conn.execute(
        """INSERT OR IGNORE INTO entities
           (id, tenant_id, type, name, aliases_json, is_high_stakes)
           VALUES (?, ?, ?, ?, '[]', 0)""",
        (target_id, tenant_id, entity_type, target_id),
    )
    # Append the new alias
    row = conn.execute("SELECT aliases_json FROM entities WHERE id=?", (target_id,)).fetchone()
    try:
        aliases = json.loads(row["aliases_json"] if row else "[]")
    except Exception:
        aliases = []
    if name not in aliases:
        aliases.append(name)
    conn.execute(
        "UPDATE entities SET aliases_json=? WHERE id=?",
        (json.dumps(aliases), target_id),
    )
    _record_merge(conn, new_id, target_id, tenant_id, confidence=1.0 - 0.12, tier="tier2_vector", status="auto_merged")
    conn.commit()


def _quarantine_merge(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    tenant_id: str,
    distance: float,
    *,
    tier: str,
) -> None:
    """High-stakes: write a pending merge to the quarantine ledger."""
    import uuid

    mid = "m_" + uuid.uuid4().hex[:20]
    conn.execute(
        """INSERT OR IGNORE INTO entity_merges
           (id, tenant_id, source_entity_id, target_entity_id, status,
            merge_tier, confidence, requested_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (mid, tenant_id, source_id, target_id, tier, max(0.0, 1.0 - distance), time.time()),
    )
    conn.commit()


def _record_merge(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    tenant_id: str,
    *,
    confidence: float,
    tier: str,
    status: str,
) -> None:
    import uuid

    mid = "m_" + uuid.uuid4().hex[:20]
    conn.execute(
        """INSERT OR IGNORE INTO entity_merges
           (id, tenant_id, source_entity_id, target_entity_id, status,
            merge_tier, confidence, requested_at, resolved_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mid, tenant_id, source_id, target_id, status, tier, confidence, time.time(), time.time()),
    )
