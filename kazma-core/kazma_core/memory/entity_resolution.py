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
    "entity_vocabulary",
    "slug",
    "list_pending_merges",
    "decide_entity_merge",
    "preserve_merge_ledger",
]

_HIGH_STAKES_TYPES = frozenset({"person", "project"})


#: Unicode general categories an id keeps: letters, marks (an Arabic hamza
#: seat, a Devanagari vowel sign) and digits, of every script.
_ID_CATEGORIES = ("L", "M", "N")
_ID_MAX = 80


def slug(text: str) -> str:
    """The id of an entity label, in any script -- the one id rule (Memory
    audit M02, 2026-10-13).

    NFKC, casefolded; the letters, marks and digits of every script kept,
    every other run one ``_``, at most 80 characters. ASCII labels get the
    id they always had. The rule kept ``a-z0-9`` only, with ``"entity"`` for
    whatever was left empty: every all-Arabic, Persian or Chinese name became
    ``entity``, so "شركة الخليج located_in الكويت" was superseded by "مطعم
    البحر located_in دبي", and "أحمد Smith" became ``smith``. Spellings that
    differ only by vowel marks or letter forms are matched through
    ``documents.arabic.fold_for_search`` (the alias hash), never folded into
    the id. A label with no letter or digit (an emoji) gets an id from its own
    text. An empty label has no id: :class:`ValueError`.
    """
    import re
    import unicodedata

    norm = unicodedata.normalize("NFKC", text or "").casefold().strip()
    if not norm:
        raise ValueError("an entity label is required")
    kept = "".join(
        ch if ch == "_" or unicodedata.category(ch)[0] in _ID_CATEGORIES else "_" for ch in norm
    )
    s = re.sub(r"_+", "_", kept).strip("_")[:_ID_MAX].strip("_")
    if not s:
        return "x_" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    return s


def canonical_entity_id(conn, eid: str, *, tenant_id: str = "default", _max_chain: int = 8) -> str:
    """Follow ``metadata_json.merged_into`` to the terminal canonical id, among
    *tenant_id*'s entities (keyed by ``(tenant_id, id)``, Memory audit M21).

    Merges soft-retire the source entity by writing ``merged_into`` into its
    metadata. Nothing used to READ this, so extraction kept minting beliefs
    under the retired id (the root cause of a person merged into the hub,
    ``user``, getting new facts under the old id).
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
                "SELECT metadata_json FROM entities WHERE id=? AND tenant_id=?", (cur, tenant_id or "default")
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


def entity_vocabulary(conn, *, tenant_id: str, limit: int = 60) -> list[str]:
    """The tenant's most used entity ids, for the extractor to reuse as
    subjects instead of minting a second slug for the same thing."""
    try:
        rows = conn.execute(
            "SELECT id FROM entities WHERE tenant_id = ? AND belief_count > 0 "
            "AND COALESCE(json_extract(CASE WHEN json_valid(metadata_json) "
            "THEN metadata_json END, '$.merged_into'), '') = '' "
            "ORDER BY belief_count DESC, id LIMIT ?",
            (tenant_id or "default", int(limit)),
        ).fetchall()
    except sqlite3.Error:
        logger.warning("[entity_resolve] entity vocabulary unavailable", exc_info=True)
        return []
    return [str(r[0]) for r in rows if r[0] and str(r[0]) != "user"]


def alias_hash(text: str) -> str:
    """Stable hash of a normalized alias for exact-match tier 1.

    Normalized with the search fold (``documents.arabic.fold_for_search``):
    "أحمد" and "أحمدُ", or "إسلام" and "اسلام", are one name. For ASCII it is
    the lower-cased, space-collapsed text it always was, so stored hashes
    still match."""
    from kazma_core.documents.arabic import fold_for_search

    norm = fold_for_search(text or "")
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
            _store_name_vector(conn, eid, tenant_id, query_vector)
            return {"canonical_id": eid, "action": "exact_match"}
    except Exception:
        logger.debug("[entity_resolve] tier-1 lookup failed", exc_info=True)

    # ── Tier 2: vector similarity ──
    # The nearest stored name vector (Memory audit M18): every extraction
    # embedded every entity name of the tenant to compare against.
    v2 = (cfg or {}).get("v2") or {}
    threshold = float(v2.get("entity_vector_merge_threshold", 0.12))
    if query_vector:
        try:
            best_id, best_dist = _nearest_entity(conn, tenant_id, query_vector, candidate_vectors)
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
                        # unknown / llm off → quarantine for human review.
                        # The new entity exists first: the ledger row names
                        # it, and entity_merges enforces that foreign key.
                        _create_entity(
                            conn, canonical, norm_name, ahash, entity_type, tenant_id,
                            is_high_stakes, vector=query_vector,
                        )
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
            # WARNING: at DEBUG this hid every auto-merge failing on the
            # merge ledger's foreign key, each leaving a duplicate entity.
            logger.warning("[entity_resolve] tier-2 vector match failed", exc_info=True)

    # ── No match: create a new canonical entity ──
    _create_entity(conn, canonical, norm_name, ahash, entity_type, tenant_id, is_high_stakes,
                   vector=query_vector)
    return {"canonical_id": canonical, "action": "create"}


def _nearest_entity(
    conn: sqlite3.Connection, tenant_id: str, query_vector: bytes,
    candidate_vectors: dict[str, bytes] | None,
) -> tuple[str | None, float]:
    """``(id, distance)`` of the nearest entity name, the L2 distance of unit
    vectors (the threshold's unit). *candidate_vectors*, when a caller has
    them, are scanned; otherwise the stored name vectors are queried."""
    import math
    import struct

    if candidate_vectors:
        import numpy as np

        q = np.frombuffer(query_vector, dtype=np.float32)
        best: tuple[str | None, float] = (None, float("inf"))
        for eid, evec in candidate_vectors.items():
            dist = float(np.linalg.norm(q - np.frombuffer(evec, dtype=np.float32)))
            if dist < best[1]:
                best = (eid, dist)
        return best
    from kazma_core.memory.vector_engine import VectorEngine

    q = list(struct.unpack(f"{len(query_vector) // 4}f", query_vector))
    hits = VectorEngine(conn).nearest_entities(q, tenant_id=tenant_id, limit=1)
    if not hits:
        return None, float("inf")
    eid, sim = hits[0]
    return eid, math.sqrt(max(0.0, 2.0 - 2.0 * float(sim)))


def _store_name_vector(conn: sqlite3.Connection, eid: str, tenant_id: str, vector: bytes | None) -> None:
    """Store an entity's name vector once (no vector yet, the caller's
    tenant's row); commits only a transaction it began."""
    if not vector or not eid:
        return
    from kazma_core.memory.embedder import get_embedding_model_name

    began = not conn.in_transaction
    conn.execute(
        "UPDATE entities SET embedding = ?, embedding_model_version = ? "
        "WHERE id = ? AND tenant_id = ? AND embedding IS NULL",
        (vector, get_embedding_model_name() or "", eid, tenant_id or "default"),
    )
    if began and conn.in_transaction:
        conn.commit()


def repair_entity_name_vectors(conn: sqlite3.Connection, *, limit: int = 200) -> int:
    """Store the name vector of up to *limit* entities that have none (made
    before vectors were stored, or while the embedder was down), newest
    first. Returns how many were stored; part of the vector repair pass."""
    from kazma_core.memory.embedder import encode_text_to_blob

    rows = conn.execute(
        "SELECT id, tenant_id, name FROM entities WHERE embedding IS NULL AND COALESCE(name, '') != '' "
        "ORDER BY rowid DESC LIMIT ?", (int(limit),)).fetchall()
    stored = 0
    for eid, tenant, name in rows:
        vec = encode_text_to_blob(str(name).replace("_", " "))
        if vec is None:
            break  # the embedder is down: the next pass tries again
        _store_name_vector(conn, str(eid), str(tenant or "default"), vec)
        stored += 1
    return stored


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
    *,
    vector: bytes | None = None,
) -> None:
    aliases = [name]
    tenant = tenant_id or "default"
    cur = conn.execute(
        """INSERT OR IGNORE INTO entities
           (id, tenant_id, type, name, aliases_json, is_high_stakes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (canonical, tenant, entity_type, name, json.dumps(aliases), is_high_stakes),
    )
    if cur.rowcount == 1:
        # Fresh row — seed aliases with name + alias hash so tier-1 matches
        # find it.
        conn.execute(
            "UPDATE entities SET aliases_json=? WHERE id=? AND tenant_id=?",
            (json.dumps([name, ahash]), canonical, tenant),
        )
    else:
        # Entity already existed — APPEND the alias hash if absent instead of
        # replacing the whole list. The old `aliases_json NOT LIKE ?` UPDATE
        # clobbered any prior aliases with [name, ahash] whenever ahash was
        # absent (audit finding).
        row = conn.execute(
            "SELECT aliases_json FROM entities WHERE id=? AND tenant_id=?", (canonical, tenant)
        ).fetchone()
        if row is None:
            # A store still keyed by id alone (memory/entity_key.py), where
            # another tenant holds this id: nothing of this tenant's is written
            # into its row (Memory audit M21 -- the alias hash used to be
            # appended to it). The tenant's facts keep the id; the migration
            # lets the next fact that names it create the tenant's own row.
            conn.commit()
            return
        try:
            existing = json.loads(row["aliases_json"] or "[]")
        except Exception:
            existing = []
        if not isinstance(existing, list):
            existing = []
        if ahash not in existing:
            existing.append(ahash)
            conn.execute(
                "UPDATE entities SET aliases_json=? WHERE id=? AND tenant_id=?",
                (json.dumps(existing), canonical, tenant),
            )
    if vector:
        from kazma_core.memory.embedder import get_embedding_model_name

        conn.execute(
            "UPDATE entities SET embedding = ?, embedding_model_version = ? "
            "WHERE id = ? AND tenant_id = ? AND embedding IS NULL",
            (vector, get_embedding_model_name() or "", canonical, tenant),
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
    """Low-stakes auto-merge: fold *name* into the target entity.

    The target gains the name and its alias hash (so tier 1 finds it next
    time), and *new_id* is written as a retired shell pointing at the target
    (``metadata.merged_into``) -- the shape every merge leaves, which
    ``canonical_entity_id`` follows. Without the shell the ledger row below
    broke ``entity_merges``' foreign key on every connection that enforces
    it (``ensure_primary_schema`` turns it on): the merge raised, the caller
    created a second entity for the same thing, and the target kept a
    half-written alias.
    """
    # Ensure the target exists
    conn.execute(
        """INSERT OR IGNORE INTO entities
           (id, tenant_id, type, name, aliases_json, is_high_stakes)
           VALUES (?, ?, ?, ?, '[]', 0)""",
        (target_id, tenant_id, entity_type, target_id),
    )
    # Append the new alias
    row = conn.execute(
        "SELECT aliases_json FROM entities WHERE id=? AND tenant_id=?", (target_id, tenant_id)
    ).fetchone()
    try:
        aliases = json.loads(row["aliases_json"] if row else "[]")
    except Exception:
        aliases = []
    for alias in (name, ahash):
        if alias and alias not in aliases:
            aliases.append(alias)
    conn.execute(
        "UPDATE entities SET aliases_json=? WHERE id=? AND tenant_id=?",
        (json.dumps(aliases), target_id, tenant_id),
    )
    if new_id and new_id != target_id:
        conn.execute(
            """INSERT OR IGNORE INTO entities
               (id, tenant_id, type, name, aliases_json, is_high_stakes, metadata_json)
               VALUES (?, ?, ?, ?, '[]', 0, ?)""",
            (new_id, tenant_id, entity_type, name, json.dumps({"merged_into": target_id})),
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
        # ``tenant_id`` None: every tenant's (the install's own view).
        rows = conn.execute(
            """
            SELECT id, tenant_id, source_entity_id, target_entity_id,
                   status, merge_tier, confidence, requested_at, metadata_json
            FROM entity_merges
            WHERE status = 'pending' AND (? IS NULL OR tenant_id = ?)
            ORDER BY requested_at DESC
            LIMIT ? OFFSET ?
            """,
            (tenant_id or None, tenant_id or None, lim, off),
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
        row = conn.execute(
            "SELECT COUNT(*) FROM entity_merges WHERE status = 'pending' AND (? IS NULL OR tenant_id = ?)",
            (tenant_id or None, tenant_id or None),
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
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Approve or reject a pending entity merge. Returns status dict.

    The merge row names its tenant, and every entity it touches is that
    tenant's (entities are keyed by ``(tenant_id, id)``, Memory audit M21).
    *tenant_id*, when given, is the caller's: another tenant's row reads as
    not found. ``None``: the install's own view (the memory worker)."""
    try:
        row = conn.execute(
            "SELECT * FROM entity_merges WHERE id=? AND status='pending' AND (? IS NULL OR tenant_id = ?)",
            (merge_id, tenant_id or None, tenant_id or None),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "not_found_or_resolved"}
        merge_tenant = str(row["tenant_id"] or "default")
        # M-13: explicit self-merge guard (API/tool layers had one; this
        # decision path did not).
        if str(row["source_entity_id"] or "") == str(row["target_entity_id"] or ""):
            conn.execute(
                "UPDATE entity_merges SET status='rejected', resolved_at=? WHERE id=? AND tenant_id=?",
                (time.time(), merge_id, merge_tenant),
            )
            conn.commit()
            return {"ok": False, "error": "self_merge_refused", "merge_id": merge_id}
        now = time.time()
        if approve:
            source_id = row["source_entity_id"]
            target_id = row["target_entity_id"]
            # Merge aliases + redirect beliefs (same as worker path)
            src = conn.execute(
                "SELECT aliases_json, name FROM entities WHERE id=? AND tenant_id=?", (source_id, merge_tenant)
            ).fetchone()
            tgt = conn.execute(
                "SELECT aliases_json FROM entities WHERE id=? AND tenant_id=?", (target_id, merge_tenant)
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
                    "UPDATE entities SET aliases_json=? WHERE id=? AND tenant_id=?",
                    (json.dumps(tgt_aliases), target_id, merge_tenant),
                )
                # Every redirect is THIS merge's tenant's: an unscoped
                # ``UPDATE beliefs SET subject=? WHERE subject=?`` rewrote
                # every tenant's beliefs naming the id (audit finding).
                conn.execute(
                    "UPDATE beliefs SET subject=? WHERE subject=? AND tenant_id=?",
                    (target_id, source_id, merge_tenant),
                )
                conn.execute(
                    "UPDATE beliefs SET object=? WHERE object=? AND tenant_id=?",
                    (target_id, source_id, merge_tenant),
                )
                # Soft-retire source (keep row — entity_merges FKs still reference it)
                conn.execute(
                    """UPDATE entities
                       SET metadata_json = json_set(
                         COALESCE(NULLIF(metadata_json,''), '{}'),
                         '$.merged_into', ?
                       )
                       WHERE id = ? AND tenant_id = ?""",
                    (target_id, source_id, merge_tenant),
                )
                # M-06: the rewire moved N beliefs between source and target —
                # refresh both entities' materialized counts (they are sticky
                # until the next touch otherwise).
                try:
                    from kazma_core.memory.entity_counts import recompute_entity_counts

                    recompute_entity_counts(
                        conn, [source_id, target_id], tenant_id=merge_tenant, neighbours=True
                    )
                except Exception:
                    logger.debug("[entity_resolve] merge count recompute skipped", exc_info=True)
            conn.execute(
                "UPDATE entity_merges SET status='approved', resolved_at=? WHERE id=? AND tenant_id=?",
                (now, merge_id, merge_tenant),
            )
            conn.commit()
            return {"ok": True, "status": "approved", "merge_id": merge_id}
        conn.execute(
            "UPDATE entity_merges SET status='rejected', resolved_at=? WHERE id=? AND tenant_id=?",
            (now, merge_id, merge_tenant),
        )
        conn.commit()
        return {"ok": True, "status": "rejected", "merge_id": merge_id}
    except Exception as exc:
        logger.debug("[entity_resolve] decide failed", exc_info=True)
        return {"ok": False, "error": str(exc)[:200]}


def preserve_merge_ledger(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    tenant_id: str = "default",
    reason: str = "entity_delete",
) -> int:
    """Copy *tenant_id*'s ``entity_merges`` rows for ``entity_id`` into the
    archive table.

    Entity DELETE has to drop live ledger rows (SQLite FK to the entity's
    key). Archiving first keeps the quarantine audit trail (M-14). Returns the
    number of rows copied. Caller still DELETEs from ``entity_merges``.
    """
    eid = (entity_id or "").strip()
    if not eid:
        return 0
    now = time.time()
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO entity_merges_archive
              (id, tenant_id, source_entity_id, target_entity_id, status,
               merge_tier, confidence, requested_at, resolved_at, metadata_json,
               archived_at, archive_reason)
            SELECT id, tenant_id, source_entity_id, target_entity_id, status,
                   merge_tier, confidence, requested_at, resolved_at, metadata_json,
                   ?, ?
            FROM entity_merges
            WHERE tenant_id=? AND (source_entity_id=? OR target_entity_id=?)
            """,
            (now, reason, tenant_id or "default", eid, eid),
        )
        return int(cur.rowcount or 0)
    except Exception:
        logger.debug("[entity_resolve] merge-ledger archive failed", exc_info=True)
        return 0
