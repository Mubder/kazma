"""V2 Recall engine — unified hybrid retrieval over beliefs + episodes.

This is the single read-path entry point for the V2 cognitive memory
stack. It replaces the legacy 4-layer RRF adapter when
``memory.v2.use_new_stack`` is True.

Pipeline:
  1. **Belief lookup** — exact/FTS match of (subject, predicate) for
     entities surfaced by the query. Highest priority; injected as
     "Known Facts". Only ``valid_until IS NULL`` (currently-believed).
  2. **Episode hybrid search** — FTS5 (sparse) + VectorEngine (dense)
     over recall-tier episodes, fused via Reciprocal Rank Fusion (RRF).
  3. **PPR boost** — Local Ego-Graph Personalized PageRank over the
     belief graph, seeded by the top-K hybrid hits, adds multi-hop
     associative weight.
  4. **Deterministic gate** — dedup by content hash + cosine threshold
     (drops near-identical hits that RRF didn't merge).
  5. **Format** — beliefs first, then ranked episodes, wrapped in the
     untrusted prompt fence (``format_untrusted_block(source=...)``).

The engine is **read-only** — it never mutates the schema. Access-count
bumping happens in the consolidation worker (Phase 3), not here, to keep
the read path lock-free.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["RecallHit", "RecallResult", "recall", "format_recall_block"]

_RRF_K = 60  # RRF smoothing constant (matches legacy adapter)


@dataclass(slots=True)
class RecallHit:
    """A single ranked recall result."""

    id: str
    content: str
    score: float
    kind: str = "episode"  # "belief" | "episode"
    source: str = ""       # "fts5" | "dense" | "ppr" | "belief_fts"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RecallResult:
    """Structured recall output."""

    beliefs: list[RecallHit]
    episodes: list[RecallHit]

    @property
    def empty(self) -> bool:
        return not self.beliefs and not self.episodes


# ── Public entry point ────────────────────────────────────────────────────


def recall(
    query: str,
    *,
    conn: sqlite3.Connection | None = None,
    vector_engine: Any | None = None,
    tenant_id: str = "default",
    limit: int = 5,
    session_id: str | None = None,
) -> RecallResult:
    """Unified V2 recall — beliefs first, then ranked episodes.

    Args:
        query: Natural-language user query.
        conn: Open connection to ``memory_state.db``. If None, a
            transient connection is opened (and closed) per call —
            pass one for repeated calls to avoid reconnect overhead.
        vector_engine: Optional pre-built :class:`VectorEngine`. If
            None, one is built from ``conn`` (or a fresh connection).
        tenant_id: Tenant isolation filter.
        limit: Max episodes to return (beliefs are returned separately).
        session_id: Optional — bias toward the current session's episodes.

    Returns:
        :class:`RecallResult` with ``beliefs`` and ``episodes`` lists.
        Empty lists (not exceptions) on any failure — recall is
        best-effort so a broken path degrades silently.
    """
    own_conn = conn is None
    if conn is None:
        try:
            from kazma_core.paths import primary_memory_db

            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("[recall] could not open primary DB", exc_info=True)
            return RecallResult([], [])

    try:
        # Two-phase: episodes first (hybrid FTS5+dense+PPR), then beliefs
        # bridged by the entities the episodes surface. This is how
        # "where do I live" → episode "I just moved to Paris" → belief
        # "user lives_in Paris" resolves without the query containing "Paris".
        episodes = _recall_episodes(conn, query, vector_engine, tenant_id, limit)
        beliefs = _recall_beliefs(
            conn, query, tenant_id, limit, seed_episodes=episodes
        )
        return RecallResult(beliefs=beliefs, episodes=episodes)
    except Exception:
        logger.debug("[recall] failed — returning empty", exc_info=True)
        return RecallResult([], [])
    finally:
        if own_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ── Belief lookup ─────────────────────────────────────────────────────────


def _recall_beliefs(
    conn: sqlite3.Connection,
    query: str,
    tenant_id: str,
    limit: int,
    *,
    seed_episodes: list[RecallHit] | None = None,
) -> list[RecallHit]:
    """Find currently-valid beliefs relevant to the query.

    Two-stage matching (a real query like "where do I live" rarely
    contains the literal answer "Paris", so naive token-LIKE fails):

    1. **Direct match** — query tokens against object/predicate/subject.
    2. **Episode-bridged match** — when retrieved episodes mention an
       entity that appears as a belief subject/object, surface that
       belief too. This is how "I just moved to Paris" (episode) pulls
       in ``user lives_in Paris`` (belief) even though the query had no
       "Paris" token.

    Only ``valid_until IS NULL`` beliefs are returned. For functional
    predicates, the highest-scoring active belief per (subject,
    predicate) wins — so a superseded "London" never displaces "Paris".
    """
    q = (query or "").strip()
    if not q and not seed_episodes:
        return []
    terms = [t for t in q.lower().split() if len(t) >= 3]
    # Entities surfaced by the retrieved episodes (bridge)
    bridge_entities: set[str] = set()
    if seed_episodes:
        for ep in seed_episodes:
            for tok in (ep.content or "").lower().split():
                cleaned = "".join(c for c in tok if c.isalnum())
                if len(cleaned) >= 3:
                    bridge_entities.add(cleaned)

    try:
        if terms:
            clauses = " OR ".join(
                "(LOWER(b.object) LIKE ? OR LOWER(b.predicate) LIKE ? OR LOWER(b.subject) LIKE ?)"
                for _ in terms
            )
            term_params: list[Any] = []
            for t in terms:
                term_params.extend([f"%{t}%", f"%{t}%", f"%{t}%"])
            sql = f"""
                SELECT b.id, b.subject, b.predicate, b.object, b.predicate_type,
                       b.confidence, b.structural_importance, b.valid_from,
                       b.source_trust_weight
                FROM beliefs b
                WHERE b.valid_until IS NULL AND b.invalidated_at IS NULL
                  AND b.tenant_id = ?
                  AND ({clauses})
            """
            params: list[Any] = [tenant_id] + term_params
            # Add episode-bridged entities as an OR
            if bridge_entities:
                ent_clauses = " OR ".join(
                    "(LOWER(b.object) LIKE ? OR LOWER(b.subject) LIKE ?)"
                    for _ in bridge_entities
                )
                ent_params: list[Any] = []
                for e in bridge_entities:
                    ent_params.extend([f"%{e}%", f"%{e}%"])
                sql += f" UNION SELECT b.id, b.subject, b.predicate, b.object, b.predicate_type, b.confidence, b.structural_importance, b.valid_from, b.source_trust_weight FROM beliefs b WHERE b.valid_until IS NULL AND b.invalidated_at IS NULL AND b.tenant_id = ? AND (" + ent_clauses + ")"
                params.extend([tenant_id] + ent_params)
            sql += " ORDER BY (b.structural_importance * b.confidence * b.source_trust_weight) DESC LIMIT ?"
            params.append(limit * 3)
            rows = conn.execute(sql, params).fetchall()
        elif bridge_entities:
            ent_clauses = " OR ".join(
                "(LOWER(b.object) LIKE ? OR LOWER(b.subject) LIKE ?)"
                for _ in bridge_entities
            )
            ent_params: list[Any] = []
            for e in bridge_entities:
                ent_params.extend([f"%{e}%", f"%{e}%"])
            rows = conn.execute(
                f"""
                SELECT b.id, b.subject, b.predicate, b.object, b.predicate_type,
                       b.confidence, b.structural_importance, b.valid_from,
                       b.source_trust_weight
                FROM beliefs b
                WHERE b.valid_until IS NULL AND b.invalidated_at IS NULL
                  AND b.tenant_id = ?
                  AND ({ent_clauses})
                ORDER BY (b.structural_importance * b.confidence * b.source_trust_weight) DESC
                LIMIT ?
                """,
                [tenant_id] + ent_params + [limit * 3],
            ).fetchall()
        else:
            return []
    except Exception:
        logger.debug("[recall] belief query failed", exc_info=True)
        return []

    hits: list[RecallHit] = []
    seen_subjects: dict[str, RecallHit] = {}
    for r in rows:
        # Dedup: for FUNCTIONAL predicates (single-valued), keep only the
        # highest-scoring belief per (subject, predicate). For SET and STATE
        # predicates (multi-valued), each belief is unique — use the belief
        # id as the key so they ALL survive dedup. This prevents 8 different
        # 'noted' beliefs from collapsing to 1 in recall results.
        ptype = r["predicate_type"] if "predicate_type" in r.keys() else "set"
        if ptype == "functional":
            key = f"{r['subject']}|{r['predicate']}"
        else:
            key = r["id"]
        content = _format_belief_text(r)
        score = float(r["structural_importance"]) * float(r["confidence"]) * float(
            r["source_trust_weight"]
        )
        hit = RecallHit(
            id=r["id"],
            content=content,
            score=score,
            kind="belief",
            source="belief_match",
            metadata={
                "subject": r["subject"],
                "predicate": r["predicate"],
                "object": r["object"],
                "predicate_type": r["predicate_type"],
                "confidence": r["confidence"],
                "importance": r["structural_importance"],
                "valid_from": r["valid_from"],
            },
        )
        prev = seen_subjects.get(key)
        if prev is None or hit.score > prev.score:
            seen_subjects[key] = hit

    hits = list(seen_subjects.values())
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def _format_belief_text(row: sqlite3.Row) -> str:
    """Render a belief as a human-readable fact sentence."""
    pred = row["predicate"].replace("_", " ")
    return f"{row['subject']} {pred} {row['object']}".strip()


# ── Episode hybrid search (FTS5 + dense + PPR via RRF) ────────────────────


def _recall_episodes(
    conn: sqlite3.Connection,
    query: str,
    vector_engine: Any | None,
    tenant_id: str,
    limit: int,
) -> list[RecallHit]:
    """Hybrid episode search: FTS5 + dense + PPR, fused via RRF."""
    q = (query or "").strip()
    if not q:
        return []

    # ── Sparse: FTS5-style LIKE over user/assistant text ──
    sparse = _episode_fts(conn, q, tenant_id, limit * 3)

    # ── Dense: VectorEngine cosine over recall-tier embeddings ──
    dense = _episode_dense(conn, q, vector_engine, tenant_id, limit * 3)

    # ── PPR boost over the belief graph seeded by top hybrid hits ──
    ppr_seeds = [h.id for h in (sparse + dense)[:10]]
    ppr_scores = _episode_ppr(conn, ppr_seeds, tenant_id)

    # ── RRF fusion ──
    fused = _rrf_fuse(sparse, dense, ppr_scores, limit * 2)

    # ── Deterministic dedup gate ──
    deduped = _dedup_gate(fused)

    # Hydrate episode text for the survivors
    out: list[RecallHit] = []
    for hit in deduped[:limit]:
        text = _episode_text(conn, hit.id)
        if text:
            out.append(
                RecallHit(
                    id=hit.id,
                    content=text,
                    score=hit.score,
                    kind="episode",
                    source=hit.source,
                )
            )
    return out


def _episode_fts(
    conn: sqlite3.Connection,
    query: str,
    tenant_id: str,
    limit: int,
) -> list[RecallHit]:
    """Lexical search over episode user/assistant text (LIKE-based)."""
    terms = [t for t in query.lower().split() if len(t) >= 2]
    if not terms:
        return []
    clauses = " OR ".join(
        "(LOWER(COALESCE(e.user_text,'')) LIKE ? OR LOWER(COALESCE(e.assistant_text,'')) LIKE ?)"
        for _ in terms
    )
    params: list[Any] = []
    for t in terms:
        params.extend([f"%{t}%", f"%{t}%"])
    params.extend([tenant_id, limit])
    try:
        rows = conn.execute(
            f"""
            SELECT e.id, e.tier, e.user_text, e.assistant_text
            FROM episodes e
            WHERE e.tenant_id = ?
              AND e.tier IN ('recall', 'episodic')
              AND ({clauses})
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            [tenant_id] + params,
        ).fetchall()
    except Exception:
        return []
    # Assign descending pseudo-scores (rank-based for RRF)
    hits: list[RecallHit] = []
    for i, r in enumerate(rows):
        text = (r["user_text"] or r["assistant_text"] or "")[:300]
        hits.append(
            RecallHit(
                id=r["id"],
                content=text,
                score=1.0 / (i + 1),  # rank-based
                source="fts5",
                metadata={"tier": r["tier"]},
            )
        )
    return hits


def _episode_dense(
    conn: sqlite3.Connection,
    query: str,
    vector_engine: Any | None,
    tenant_id: str,
    limit: int,
) -> list[RecallHit]:
    """Dense vector search via the VectorEngine."""
    if vector_engine is None:
        try:
            from kazma_core.memory.vector_engine import VectorEngine

            vector_engine = VectorEngine(conn)
        except Exception:
            return []
    if not getattr(vector_engine, "available", False):
        return []
    # Encode the query
    try:
        from kazma_core.swarm.memory.embedder import get_embedder

        embedder = get_embedder()
        if embedder is None:
            return []
        qvec = embedder.encode(query)
        if qvec is None:
            return []
    except Exception:
        return []
    results = vector_engine.search(qvec, tenant_id=tenant_id, tier="recall", limit=limit)
    return [
        RecallHit(id=eid, content="", score=float(sim), source="dense")
        for eid, sim in results
    ]


def _episode_ppr(
    conn: sqlite3.Connection,
    seed_episode_ids: list[str],
    tenant_id: str,
) -> dict[str, float]:
    """PPR boost: treat episodes as nodes, shared sessions as edges.

    Builds a transient graph where episodes in the same session are
    connected, runs PPR seeded by the hybrid hits, and returns the
    stationary distribution. Episodes strongly connected to the seeds
    get a boost.
    """
    if not seed_episode_ids:
        return {}
    try:
        from kazma_core.memory.ppr import compute_local_ppr
    except Exception:
        return {}
    # Build edges: episodes sharing a session_id are linked.
    try:
        rows = conn.execute(
            "SELECT id, session_id FROM episodes WHERE tenant_id = ? AND tier IN ('recall','episodic')",
            (tenant_id,),
        ).fetchall()
    except Exception:
        return {}
    by_session: dict[str, list[str]] = {}
    for r in rows:
        by_session.setdefault(r["session_id"], []).append(r["id"])
    edges: list[tuple[str, str, float]] = []
    for session_eps in by_session.values():
        # Connect each episode to its session-mates (undirected cliques)
        for i, a in enumerate(session_eps):
            for b in session_eps[i + 1 :]:
                edges.append((a, b, 1.0))
    if not edges:
        return {}
    try:
        return compute_local_ppr(seed_episode_ids, edges, alpha=0.15, max_iter=10, max_nodes=200)
    except Exception:
        return {}


# ── RRF fusion ────────────────────────────────────────────────────────────


def _rrf_fuse(
    sparse: list[RecallHit],
    dense: list[RecallHit],
    ppr: dict[str, float],
    top_n: int,
) -> list[RecallHit]:
    """Reciprocal Rank Fusion across sparse + dense + PPR."""
    # Sort each source by its own score descending
    sparse.sort(key=lambda h: h.score, reverse=True)
    dense.sort(key=lambda h: h.score, reverse=True)
    ppr_sorted = sorted(ppr.items(), key=lambda x: x[1], reverse=True)

    rrf: dict[str, float] = {}
    meta: dict[str, RecallHit] = {}

    for rank, h in enumerate(sparse, start=1):
        rrf[h.id] = rrf.get(h.id, 0.0) + 1.0 / (_RRF_K + rank)
        meta.setdefault(h.id, h)
    for rank, h in enumerate(dense, start=1):
        rrf[h.id] = rrf.get(h.id, 0.0) + 1.0 / (_RRF_K + rank)
        meta.setdefault(h.id, h)
    for rank, (eid, _score) in enumerate(ppr_sorted, start=1):
        rrf[eid] = rrf.get(eid, 0.0) + 1.0 / (_RRF_K + rank)
        meta.setdefault(eid, RecallHit(id=eid, content="", score=0.0, source="ppr"))

    ranked = sorted(rrf.items(), key=lambda x: x[1], reverse=True)[:top_n]
    out: list[RecallHit] = []
    for eid, score in ranked:
        h = meta[eid]
        h.score = score
        out.append(h)
    return out


# ── Deterministic dedup gate ──────────────────────────────────────────────


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").strip().lower().encode("utf-8")).hexdigest()[:16]


def _dedup_gate(hits: list[RecallHit]) -> list[RecallHit]:
    """Drop exact-duplicate content (by hash) keeping the highest-scoring."""
    seen: dict[str, RecallHit] = {}
    for h in hits:
        ch = _content_hash(h.content) if h.content else h.id
        prev = seen.get(ch)
        if prev is None or h.score > prev.score:
            seen[ch] = h
    return list(seen.values())


def _episode_text(conn: sqlite3.Connection, episode_id: str) -> str:
    """Hydrate the display text for an episode."""
    try:
        row = conn.execute(
            "SELECT user_text, assistant_text, summary_text FROM episodes WHERE id = ?",
            (episode_id,),
        ).fetchone()
    except Exception:
        return ""
    if not row:
        return ""
    return (row["summary_text"] or row["user_text"] or row["assistant_text"] or "")[:400]


# ── Formatting ────────────────────────────────────────────────────────────


def format_recall_block(
    result: RecallResult,
    *,
    fence_source: str = "memory_v2_recall",
    max_beliefs: int = 5,
    max_episodes: int = 5,
    max_tokens: int = 1500,
) -> str:
    """Render a RecallResult into a prompt-fenced context block.

    Beliefs are rendered as "Known Facts", episodes as "Relevant
    History". The whole block is wrapped in the untrusted prompt fence
    via :func:`format_untrusted_block` (the ``source`` kwarg is
    REQUIRED per the actual signature — resolution #2).

    A hard ``max_tokens`` budget (default ~1500, ≈4 chars/token) caps
    the total injected context so PPR/RRF can't overrun the prompt.
    Items are added in priority order (beliefs first, then episodes by
    score) until the budget is exhausted.
    """
    from kazma_core.safety.prompt_fence import format_untrusted_block

    # ~4 chars per token is the standard heuristic estimate
    char_budget = max_tokens * 4
    parts: list[str] = []
    used = 0

    if result.beliefs:
        lines: list[str] = []
        for h in result.beliefs[:max_beliefs]:
            line = f"- {h.content}"
            if used + len(line) + 1 > char_budget:
                break
            lines.append(line)
            used += len(line) + 1
        if lines:
            header = "## Known Facts\n"
            parts.append(header + "\n".join(lines))
            used += len(header)

    if result.episodes and used < char_budget:
        lines = []
        for h in result.episodes[:max_episodes]:
            line = f"- {h.content}"
            if used + len(line) + 1 > char_budget:
                break
            lines.append(line)
            used += len(line) + 1
        if lines:
            header = "## Relevant History\n"
            parts.append(header + "\n".join(lines))

    if not parts:
        return ""
    body = "\n\n".join(parts)
    return format_untrusted_block(body, source=fence_source)
