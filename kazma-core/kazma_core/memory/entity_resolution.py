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
    "list_pending_merges",
    "decide_entity_merge",
]

_HIGH_STAKES_TYPES = frozenset({"person", "project"})


def slug(text: str) -> str:
    """Canonical slug for an entity label."""
    import re

    raw = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", raw)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80] or "entity"


def canonical_entity_id(conn, eid: str, *, _max_chain: int = 8) -> str:
    """Follow ``metadata_json.merged_into`` to the terminal canonical id.

    Merges soft-retire the source entity by writing ``merged_into`` into its
    metadata. Nothing used to READ this, so extraction kept minting beliefs
    under the retired id (the root cause of the mubder→user re-orphaning).
    This helper resolves any id to its canonical target — chain-following so
    a→b→c collapses to c — and is the single chokepoint for the redirect.

    Returns the id unchanged if it has no ``merged_into`` (the common case),
    if the row is missing, or if the chain is longer than _max_chain (cycle
    guard). Best-effort: never raises.
    """
    if not eid:
        return eid
    cur = eid
    seen: set[str] = set()
    for _ in range(_max_chain):
        if cur in seen:
            break  # defensive — cycle in the merge graph
        seen.add(cur)
        try:
            row = conn.execute(
                "SELECT metadata_json FROM entities WHERE id=?", (cur,)
            ).fetchone()
        except Exception:
            break
        if not row:
            break
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except Exception:
            break
        nxt = (meta or {}).get("merged_into")
        if not nxt or nxt == cur:
            break
        cur = nxt
    return cur


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
                # High-stakes → tier-3 LLM when enabled, else quarantine
                if is_high_stakes:
                    llm_decision = _tier3_llm_disambiguate(
                        norm_name,
                        best_id,
                        entity_type=entity_type,
                        distance=best_dist,
                        cfg=cfg,
                    )
                    if llm_decision == "merge":
                        _auto_merge(
                            conn, canonical, best_id, norm_name, ahash, tenant_id, entity_type
                        )
                        return {
                            "canonical_id": best_id,
                            "action": "llm_merge",
                        }
                    if llm_decision == "distinct":
                        # Create as separate entity; no quarantine
                        pass
                    else:
                        # unknown / llm off → quarantine for human review
                        _quarantine_merge(
                            conn,
                            canonical,
                            best_id,
                            tenant_id,
                            best_dist,
                            tier="tier2_vector",
                        )
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


def _tier3_llm_disambiguate(
    name: str,
    candidate_id: str,
    *,
    entity_type: str,
    distance: float,
    cfg: dict[str, Any] | None,
) -> str:
    """Tier-3 LLM: return ``merge`` | ``distinct`` | ``skip``.

    Gated by ``memory.v2.entity_llm_disambiguate`` (default False) so we
    never spend LLM tokens without an explicit opt-in.
    """
    v2 = (cfg or {}).get("v2") or {}
    if not bool(v2.get("entity_llm_disambiguate", False)):
        return "skip"
    try:
        # Prefer sync config read if cfg was partial
        if "entity_llm_disambiguate" not in v2:
            from kazma_core.memory.config import read_memory_cfg

            v2 = (read_memory_cfg() or {}).get("v2") or v2
            if not bool(v2.get("entity_llm_disambiguate", False)):
                return "skip"
    except Exception:
        pass

    prompt = (
        "You are an entity-resolution assistant. Reply with exactly one word: "
        "MERGE or DISTINCT.\n"
        f"New mention: {name!r} (type={entity_type})\n"
        f"Candidate canonical id: {candidate_id!r}\n"
        f"Vector distance: {distance:.4f}\n"
        "MERGE if they refer to the same real-world entity; DISTINCT otherwise."
    )
    try:
        # Use active model — one-shot, no tools
        try:
            from kazma_core.model_registry import get_model_registry

            client = get_model_registry().get_client()
        except Exception:
            return "skip"
        if client is None:
            return "skip"
        # Support both .chat and simple call patterns
        if hasattr(client, "chat"):
            resp = client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=8,
            )
            text = ""
            if isinstance(resp, dict):
                text = (
                    ((resp.get("choices") or [{}])[0].get("message") or {}).get("content")
                    or resp.get("content")
                    or ""
                )
            else:
                text = str(resp or "")
        else:
            return "skip"
        low = (text or "").strip().lower()
        if "merge" in low and "distinct" not in low:
            return "merge"
        if "distinct" in low:
            return "distinct"
        return "skip"
    except Exception:
        logger.debug("[entity_resolve] tier-3 LLM failed", exc_info=True)
        return "skip"


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
    cur = conn.execute(
        """INSERT OR IGNORE INTO entities
           (id, tenant_id, type, name, aliases_json, is_high_stakes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (canonical, tenant_id, entity_type, name, json.dumps(aliases), is_high_stakes),
    )
    if cur.rowcount == 1:
        # Fresh row — seed aliases with name + alias hash so tier-1 matches
        # find it.
        conn.execute(
            "UPDATE entities SET aliases_json=? WHERE id=?",
            (json.dumps([name, ahash]), canonical),
        )
    else:
        # Entity already existed — APPEND the alias hash if absent instead of
        # replacing the whole list. The old `aliases_json NOT LIKE ?` UPDATE
        # clobbered any prior aliases with [name, ahash] whenever ahash was
        # absent (audit finding).
        row = conn.execute(
            "SELECT aliases_json FROM entities WHERE id=?", (canonical,)
        ).fetchone()
        try:
            existing = json.loads((row["aliases_json"] if row else None) or "[]")
        except Exception:
            existing = []
        if not isinstance(existing, list):
            existing = []
        if ahash not in existing:
            existing.append(ahash)
            conn.execute(
                "UPDATE entities SET aliases_json=? WHERE id=?",
                (json.dumps(existing), canonical),
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
    # Wake the durable worker to resolve this merge under the auto-policy
    # (tier1_exact always; tier2_vector >= 0.85). Without this enqueue the
    # pending row sat forever — the "entity_merge" handler had no producer.
    try:
        from kazma_core.memory.task_queue import enqueue_task

        enqueue_task("entity_merge", {"merge_id": mid, "tenant_id": tenant_id})
    except Exception:
        logger.debug("[entity_resolution] entity_merge enqueue failed", exc_info=True)


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


def list_pending_merges(
    conn: sqlite3.Connection,
    *,
    tenant_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List pending entity merges from the quarantine ledger.

    ``offset`` is clamped to >= 0 for pager support (Phase 1.1). Default 0
    preserves the pre-pagination behavior for existing callers.
    """
    off = max(0, int(offset or 0))
    lim = max(1, min(limit, 200))
    try:
        if tenant_id:
            rows = conn.execute(
                """
                SELECT id, tenant_id, source_entity_id, target_entity_id,
                       status, merge_tier, confidence, requested_at, metadata_json
                FROM entity_merges
                WHERE status = 'pending' AND tenant_id = ?
                ORDER BY requested_at DESC
                LIMIT ? OFFSET ?
                """,
                (tenant_id, lim, off),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, tenant_id, source_entity_id, target_entity_id,
                       status, merge_tier, confidence, requested_at, metadata_json
                FROM entity_merges
                WHERE status = 'pending'
                ORDER BY requested_at DESC
                LIMIT ? OFFSET ?
                """,
                (lim, off),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.debug("[entity_resolve] list_pending failed", exc_info=True)
        return []


def count_pending_merges(
    conn: sqlite3.Connection,
    *,
    tenant_id: str | None = None,
) -> int:
    """Total pending merge count (for the pager's 'of N')."""
    try:
        if tenant_id:
            row = conn.execute(
                "SELECT COUNT(*) FROM entity_merges WHERE status = 'pending' AND tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM entity_merges WHERE status = 'pending'"
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        logger.debug("[entity_resolve] count_pending failed", exc_info=True)
        return 0


def decide_entity_merge(
    conn: sqlite3.Connection,
    merge_id: str,
    *,
    approve: bool,
) -> dict[str, Any]:
    """Approve or reject a pending entity merge. Returns status dict."""
    try:
        row = conn.execute(
            "SELECT * FROM entity_merges WHERE id=? AND status='pending'",
            (merge_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "not_found_or_resolved"}
        now = time.time()
        if approve:
            source_id = row["source_entity_id"]
            target_id = row["target_entity_id"]
            # Merge aliases + redirect beliefs (same as worker path)
            src = conn.execute(
                "SELECT aliases_json, name FROM entities WHERE id=?", (source_id,)
            ).fetchone()
            tgt = conn.execute(
                "SELECT aliases_json FROM entities WHERE id=?", (target_id,)
            ).fetchone()
            if src and tgt:
                try:
                    src_aliases = json.loads(src["aliases_json"] or "[]")
                except Exception:
                    src_aliases = []
                try:
                    tgt_aliases = json.loads(tgt["aliases_json"] or "[]")
                except Exception:
                    tgt_aliases = []
                for a in src_aliases:
                    if a not in tgt_aliases:
                        tgt_aliases.append(a)
                if src["name"] and src["name"] not in tgt_aliases:
                    tgt_aliases.append(src["name"])
                conn.execute(
                    "UPDATE entities SET aliases_json=? WHERE id=?",
                    (json.dumps(tgt_aliases), target_id),
                )
                # Scope redirects to THIS merge's tenant. ``entities.id`` is a
                # GLOBAL primary key (AGENTS.md §16), so an unscoped
                # ``UPDATE beliefs SET subject=? WHERE subject=?`` rewrites EVERY
                # tenant's beliefs pointing at this entity — irreversible
                # cross-tenant graph corruption (audit finding).
                _merge_tenant = row["tenant_id"]
                conn.execute(
                    "UPDATE beliefs SET subject=? WHERE subject=? AND tenant_id=?",
                    (target_id, source_id, _merge_tenant),
                )
                conn.execute(
                    "UPDATE beliefs SET object=? WHERE object=? AND tenant_id=?",
                    (target_id, source_id, _merge_tenant),
                )
                # Soft-retire source (keep row — entity_merges FKs still reference it)
                conn.execute(
                    """UPDATE entities
                       SET metadata_json = json_set(
                         COALESCE(NULLIF(metadata_json,''), '{}'),
                         '$.merged_into', ?
                       )
                       WHERE id = ?""",
                    (target_id, source_id),
                )
                # M-06: the rewire moved N beliefs between source and target —
                # refresh both entities' materialized counts (they are sticky
                # until the next touch otherwise).
                try:
                    from kazma_core.memory.entity_counts import recompute_entity_counts

                    recompute_entity_counts(
                        conn, [source_id, target_id], tenant_id=_merge_tenant
                    )
                except Exception:
                    logger.debug("[entity_resolve] merge count recompute skipped", exc_info=True)
            conn.execute(
                "UPDATE entity_merges SET status='approved', resolved_at=? WHERE id=?",
                (now, merge_id),
            )
            conn.commit()
            return {"ok": True, "status": "approved", "merge_id": merge_id}
        conn.execute(
            "UPDATE entity_merges SET status='rejected', resolved_at=? WHERE id=?",
            (now, merge_id),
        )
        conn.commit()
        return {"ok": True, "status": "rejected", "merge_id": merge_id}
    except Exception as exc:
        logger.debug("[entity_resolve] decide failed", exc_info=True)
        return {"ok": False, "error": str(exc)[:200]}
