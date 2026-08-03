"""V2 Recall engine — unified hybrid retrieval over beliefs + episodes.

This is the single read-path entry point for the V2 cognitive memory
stack when ``memory.v2.use_new_stack`` is True.

Pipeline:
  1. **Episode hybrid search** — FTS5 MATCH+bm25 (LIKE fallback) + dense
     vector over episodic+recall tiers, session-clique PPR, RRF fusion,
     optional same-session bias.
  2. **Belief lookup** — FTS5/LIKE + episode-bridge + dense cosine (capped)
     + belief-graph PPR multi-hop. Only currently-valid beliefs.
  3. **Access bump** — on non-empty hits, increment access_count /
     last_accessed (Phase A; toggle ``access_bump_enabled``).
  4. **Format** — beliefs first, then episodes, prompt-fenced.

Optional ``explain=True`` (or ``memory.v2.explain_recall``) tags each hit
with source channels: fts5 / dense / ppr / session_boost / belief_match.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["RecallHit", "RecallResult", "recall", "search", "format_recall_block"]

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
    explain: bool | None = None,
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
        explain: When True, each hit's ``metadata["sources"]`` lists the
            channels that contributed (fts5/dense/ppr/session_boost).
            ``None`` reads ``memory.v2.explain_recall`` (default False).

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

    do_explain = explain
    if do_explain is None:
        try:
            from kazma_core.memory.config import read_memory_cfg

            do_explain = bool(
                ((read_memory_cfg() or {}).get("v2") or {}).get("explain_recall", False)
            )
        except Exception:
            do_explain = False

    try:
        # Two-phase: episodes first (hybrid FTS5+dense+PPR), then beliefs
        # bridged by the entities the episodes surface. This is how
        # "where do I live" → episode "I just moved to Paris" → belief
        # "user lives_in Paris" resolves without the query containing "Paris".
        episodes = _recall_episodes(
            conn, query, vector_engine, tenant_id, limit,
            session_id=session_id,
            explain=bool(do_explain),
        )
        beliefs = _recall_beliefs(
            conn, query, tenant_id, limit, seed_episodes=episodes,
            vector_engine=vector_engine,
            explain=bool(do_explain),
        )
        result = RecallResult(beliefs=beliefs, episodes=episodes)
        # Phase A: bump access so macro_sleep promotion/retention is real.
        if not result.empty:
            _bump_access(conn, beliefs, episodes)
        return result
    except Exception:
        logger.warning("[recall] failed — returning empty", exc_info=True)
        return RecallResult([], [])
    finally:
        if own_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ── Dict-shape compat shim ────────────────────────────────────────────────


def search(
    query: str,
    limit: int = 5,
    *,
    session_id: str | None = None,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """V2-native search returning ``list[dict]`` — the shape callers of the
    legacy ``adapter.search()`` consume.

    This is the single read contract for swarm/compaction/self-improvement
    callers after the V1→V2 migration. Returns dicts with keys
    ``id, content, text, score, source_layer, metadata`` (the same keys
    ``UnifiedMemoryAdapter.search`` documented and
    ``compaction._build_compacted_system`` consumes).

    Args:
        query: Natural-language query.
        limit: Max results to return (beliefs + episodes combined).
        session_id: Optional — bias toward the current session's episodes
            (thread_id). Activates the same session-bias the supervisor
            per-turn RAG path uses.
        kind: Optional — restrict to one hit kind (``"belief"`` or
            ``"episode"``). ``None`` returns both, beliefs first.

    Best-effort: never raises; returns ``[]`` on any failure.
    """
    try:
        result = recall(query, limit=limit, session_id=session_id)
        out: list[dict[str, Any]] = []
        hits = list(result.beliefs) + list(result.episodes)
        if kind:
            hits = [h for h in hits if h.kind == kind]
        for h in hits[:limit]:
            source_layer = f"v2:{h.kind}:{h.source}" if h.source else f"v2:{h.kind}"
            out.append({
                "id": h.id,
                "content": h.content,
                "text": h.content,  # alias for retrieve_memories fallback
                "score": h.score,
                "source_layer": source_layer,
                "metadata": dict(h.metadata),
            })
        return out
    except Exception:
        logger.debug("[recall.search] failed — returning []", exc_info=True)
        return []


# ── Belief lookup ─────────────────────────────────────────────────────────


def _recall_beliefs(
    conn: sqlite3.Connection,
    query: str,
    tenant_id: str,
    limit: int,
    *,
    seed_episodes: list[RecallHit] | None = None,
    vector_engine: Any = None,
    explain: bool = False,
) -> list[RecallHit]:
    """Find currently-valid beliefs relevant to the query.

    Matching stages (a real query like "where do I live" rarely
    contains the literal answer "Paris", so naive token match fails):

    1. **FTS5 / LIKE** — query tokens against subject/predicate/object.
    2. **Episode-bridged match** — entities in retrieved episodes surface
       matching beliefs (e.g. "moved to Paris" → ``user lives_in Paris``).
    3. **Belief-graph PPR** — multi-hop over subject–object edges.
    4. **Dense cosine** — capped candidate scan when sparse results are thin.

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

    source_by_id: dict[str, list[str]] = {}
    rows: list[Any] = []

    # ── Stage 1: FTS5 MATCH (preferred) or LIKE fallback ──
    fts_rows = _belief_fts(conn, q, tenant_id, limit * 3) if q else []
    if fts_rows:
        rows = list(fts_rows)
        for r in fts_rows:
            source_by_id.setdefault(r["id"], []).append("belief_fts")
    else:
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
                if bridge_entities:
                    ent_clauses = " OR ".join(
                        "(LOWER(b.object) LIKE ? OR LOWER(b.subject) LIKE ?)"
                        for _ in bridge_entities
                    )
                    ent_params: list[Any] = []
                    for e in bridge_entities:
                        ent_params.extend([f"%{e}%", f"%{e}%"])
                    sql = (
                        f"SELECT * FROM ({sql} UNION SELECT b.id, b.subject, b.predicate, "
                        f"b.object, b.predicate_type, b.confidence, b.structural_importance, "
                        f"b.valid_from, b.source_trust_weight FROM beliefs b WHERE "
                        f"b.valid_until IS NULL AND b.invalidated_at IS NULL AND "
                        f"b.tenant_id = ? AND ({ent_clauses}))"
                    )
                    params.extend([tenant_id] + ent_params)
                sql += (
                    " ORDER BY (structural_importance * confidence * source_trust_weight) "
                    "DESC LIMIT ?"
                )
                params.append(limit * 3)
                rows = list(conn.execute(sql, params).fetchall())
                for r in rows:
                    source_by_id.setdefault(r["id"], []).append("belief_like")
            elif bridge_entities:
                ent_clauses = " OR ".join(
                    "(LOWER(b.object) LIKE ? OR LOWER(b.subject) LIKE ?)"
                    for _ in bridge_entities
                )
                ent_params = []
                for e in bridge_entities:
                    ent_params.extend([f"%{e}%", f"%{e}%"])
                rows = list(
                    conn.execute(
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
                )
                for r in rows:
                    source_by_id.setdefault(r["id"], []).append("belief_bridge")
        except Exception:
            logger.debug("[recall] belief query failed", exc_info=True)
            rows = []

    # Bridge entities even when FTS already returned rows
    if bridge_entities and terms:
        try:
            existing_ids = {r["id"] for r in rows}
            ent_clauses = " OR ".join(
                "(LOWER(b.object) LIKE ? OR LOWER(b.subject) LIKE ?)"
                for _ in bridge_entities
            )
            ent_params = []
            for e in bridge_entities:
                ent_params.extend([f"%{e}%", f"%{e}%"])
            bridged = conn.execute(
                f"""
                SELECT b.id, b.subject, b.predicate, b.object, b.predicate_type,
                       b.confidence, b.structural_importance, b.valid_from,
                       b.source_trust_weight
                FROM beliefs b
                WHERE b.valid_until IS NULL AND b.invalidated_at IS NULL
                  AND b.tenant_id = ?
                  AND ({ent_clauses})
                LIMIT ?
                """,
                [tenant_id] + ent_params + [limit * 3],
            ).fetchall()
            for r in bridged:
                if r["id"] not in existing_ids:
                    rows.append(r)
                    existing_ids.add(r["id"])
                source_by_id.setdefault(r["id"], []).append("belief_bridge")
        except Exception:
            logger.debug("[recall] belief bridge failed", exc_info=True)

    # ── Stage 3: belief-graph PPR multi-hop ──
    ppr_scores = _belief_graph_ppr(
        conn, q, tenant_id, seed_episodes=seed_episodes or []
    )
    if ppr_scores:
        try:
            existing_ids = {r["id"] for r in rows}
            top_ppr = sorted(ppr_scores.items(), key=lambda x: x[1], reverse=True)[
                : limit * 2
            ]
            missing = [bid for bid, _ in top_ppr if bid not in existing_ids]
            if missing:
                placeholders = ",".join("?" for _ in missing)
                ppr_rows = conn.execute(
                    f"""
                    SELECT b.id, b.subject, b.predicate, b.object, b.predicate_type,
                           b.confidence, b.structural_importance, b.valid_from,
                           b.source_trust_weight
                    FROM beliefs b
                    WHERE b.valid_until IS NULL AND b.invalidated_at IS NULL
                      AND b.tenant_id = ?
                      AND b.id IN ({placeholders})
                    """,
                    [tenant_id] + missing,
                ).fetchall()
                for r in ppr_rows:
                    rows.append(r)
            for bid in ppr_scores:
                source_by_id.setdefault(bid, []).append("belief_ppr")
        except Exception:
            logger.debug("[recall] belief PPR hydrate failed", exc_info=True)

    # ── Stage 4: dense (capped) when sparse is thin ──
    if q and len(rows) < limit:
        try:
            dense_rows = _belief_dense(conn, q, vector_engine, tenant_id, limit)
            existing_ids = {r["id"] for r in rows}
            for dr in dense_rows:
                if dr["id"] not in existing_ids:
                    rows.append(dr)
                source_by_id.setdefault(dr["id"], []).append("dense")
        except Exception:
            logger.debug("[recall] belief dense search failed", exc_info=True)

    if not rows:
        return []

    hits: list[RecallHit] = []
    seen_subjects: dict[str, RecallHit] = {}
    for r in rows:
        ptype = r["predicate_type"] if "predicate_type" in r.keys() else "set"
        if ptype == "functional":
            key = f"{r['subject']}|{r['predicate']}"
        else:
            key = r["id"]
        content = _format_belief_text(r)
        score = float(r["structural_importance"]) * float(r["confidence"]) * float(
            r["source_trust_weight"]
        )
        # PPR multi-hop boost (additive on structural score)
        if r["id"] in ppr_scores:
            score = score + float(ppr_scores[r["id"]]) * 2.0
        srcs = source_by_id.get(r["id"]) or ["belief_match"]
        meta: dict[str, Any] = {
            "subject": r["subject"],
            "predicate": r["predicate"],
            "object": r["object"],
            "predicate_type": r["predicate_type"],
            "confidence": r["confidence"],
            "importance": r["structural_importance"],
            "valid_from": r["valid_from"],
        }
        if explain:
            meta["sources"] = list(dict.fromkeys(srcs))
        hit = RecallHit(
            id=r["id"],
            content=content,
            score=score,
            kind="belief",
            source=srcs[0],
            metadata=meta,
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
    *,
    session_id: str | None = None,
    explain: bool = False,
) -> list[RecallHit]:
    """Hybrid episode search: FTS5 + dense + PPR, fused via RRF."""
    q = (query or "").strip()
    if not q:
        return []

    # Track contributing channels per episode id for explain mode
    sources: dict[str, list[str]] = {}

    # ── Sparse: real FTS5 MATCH+bm25 (LIKE fallback) ──
    sparse = _episode_fts(conn, q, tenant_id, limit * 3)
    for h in sparse:
        sources.setdefault(h.id, []).append(h.source or "fts5")

    # ── Dense: cosine over recall + episodic (fresh turns) ──
    dense = _episode_dense(conn, q, vector_engine, tenant_id, limit * 3)
    for h in dense:
        sources.setdefault(h.id, []).append("dense")

    # ── PPR boost over session cliques seeded by top hybrid hits ──
    ppr_seeds = [h.id for h in (sparse + dense)[:10]]
    ppr_scores = _episode_ppr(conn, ppr_seeds, tenant_id)
    for eid in ppr_scores:
        sources.setdefault(eid, []).append("ppr")

    # ── RRF fusion ──
    fused = _rrf_fuse(sparse, dense, ppr_scores, limit * 2)

    # ── Session bias: boost same-thread episodes (Phase A) ──
    if session_id:
        boost = 0.35
        try:
            from kazma_core.memory.config import read_memory_cfg

            boost = float(
                ((read_memory_cfg() or {}).get("v2") or {}).get("session_boost", 0.35)
            )
        except Exception:
            pass
        fused = _apply_session_bias(conn, fused, session_id, boost=boost)
        for h in fused:
            if (h.metadata or {}).get("session_boost"):
                sources.setdefault(h.id, []).append("session_boost")

    # ── Deterministic dedup gate ──
    deduped = _dedup_gate(fused)

    # Hydrate episode text for the survivors
    out: list[RecallHit] = []
    for hit in deduped[:limit]:
        text = _episode_text(conn, hit.id)
        if text:
            meta = dict(hit.metadata or {})
            if explain:
                meta["sources"] = list(dict.fromkeys(sources.get(hit.id) or [hit.source or ""]))
            out.append(
                RecallHit(
                    id=hit.id,
                    content=text,
                    score=hit.score,
                    kind="episode",
                    source=hit.source,
                    metadata=meta,
                )
            )
    return out


def _fts_match_query(query: str) -> str:
    """Build a safe FTS5 MATCH expression from free text.

    Tokens are alphanumeric-only (punctuation stripped). Joined with OR so
    any term can hit. Empty when no usable tokens remain.
    """
    raw = (query or "").lower()
    tokens: list[str] = []
    for part in raw.replace("-", " ").split():
        cleaned = "".join(c for c in part if c.isalnum())
        if len(cleaned) >= 2:
            tokens.append(cleaned)
    # De-dupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    if not uniq:
        return ""
    return " OR ".join(uniq)


def _episode_fts(
    conn: sqlite3.Connection,
    query: str,
    tenant_id: str,
    limit: int,
) -> list[RecallHit]:
    """Lexical search over episodes — FTS5 MATCH+bm25, LIKE fallback."""
    match_q = _fts_match_query(query)
    if match_q:
        try:
            rows = conn.execute(
                """
                SELECT e.id, e.tier, e.user_text, e.assistant_text,
                       bm25(episodes_fts) AS rank
                FROM episodes_fts
                JOIN episodes e ON e.rowid = episodes_fts.rowid
                WHERE episodes_fts MATCH ?
                  AND e.tenant_id = ?
                  AND e.tier IN ('recall', 'episodic')
                ORDER BY rank
                LIMIT ?
                """,
                (match_q, tenant_id, limit),
            ).fetchall()
            hits: list[RecallHit] = []
            for r in rows:
                text = (r["user_text"] or r["assistant_text"] or "")[:300]
                # bm25: more negative = better match → invert for higher-is-better
                bm = float(r["rank"] if r["rank"] is not None else 0.0)
                score = max(0.0, -bm) if bm < 0 else 1.0 / (1.0 + abs(bm))
                hits.append(
                    RecallHit(
                        id=r["id"],
                        content=text,
                        score=score if score > 0 else 0.01,
                        source="fts5",
                        metadata={"tier": r["tier"], "bm25": bm},
                    )
                )
            if hits:
                return hits
        except Exception:
            logger.debug("[recall] episodes_fts MATCH failed — LIKE fallback", exc_info=True)

    # ── LIKE fallback (FTS missing / empty / error) ──
    terms = [t for t in query.lower().split() if len(t) >= 2]
    if not terms:
        return []
    clauses = " OR ".join(
        "(LOWER(COALESCE(e.user_text,'')) LIKE ? OR LOWER(COALESCE(e.assistant_text,'')) LIKE ?)"
        for _ in terms
    )
    params: list[Any] = []
    for t in terms:
        cleaned = "".join(c for c in t if c.isalnum()) or t
        params.extend([f"%{cleaned}%", f"%{cleaned}%"])
    params.extend([limit])
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
    except Exception as e:
        logger.error("[recall] episode LIKE fallback failed: %s", e, exc_info=True)
        return []
    hits = []
    for i, r in enumerate(rows):
        text = (r["user_text"] or r["assistant_text"] or "")[:300]
        hits.append(
            RecallHit(
                id=r["id"],
                content=text,
                score=1.0 / (i + 1),
                source="fts_like",
                metadata={"tier": r["tier"]},
            )
        )
    return hits


def _belief_fts(
    conn: sqlite3.Connection,
    query: str,
    tenant_id: str,
    limit: int,
) -> list[Any]:
    """FTS5 MATCH over beliefs; empty list if FTS unavailable."""
    match_q = _fts_match_query(query)
    if not match_q:
        return []
    try:
        return list(
            conn.execute(
                """
                SELECT b.id, b.subject, b.predicate, b.object, b.predicate_type,
                       b.confidence, b.structural_importance, b.valid_from,
                       b.source_trust_weight, bm25(beliefs_fts) AS rank
                FROM beliefs_fts
                JOIN beliefs b ON b.rowid = beliefs_fts.rowid
                WHERE beliefs_fts MATCH ?
                  AND b.tenant_id = ?
                  AND b.valid_until IS NULL AND b.invalidated_at IS NULL
                ORDER BY rank
                LIMIT ?
                """,
                (match_q, tenant_id, limit),
            ).fetchall()
        )
    except Exception:
        logger.debug("[recall] beliefs_fts MATCH failed", exc_info=True)
        return []


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
        from kazma_core.memory.embedder import get_embedder

        embedder = get_embedder()
        if embedder is None:
            return []
        qvec = embedder.encode(query)
        if qvec is None:
            return []
    except Exception:
        return []
    # Search both recall (promoted) and episodic (fresh post-turn writes).
    # Searching only tier=recall was the main "semantic amnesia" footgun:
    # dual_write defaults to tier=episodic, so new memories never dense-hit.
    results = vector_engine.search(
        qvec,
        tenant_id=tenant_id,
        tier=["recall", "episodic"],
        limit=limit,
    )
    return [
        RecallHit(id=eid, content="", score=float(sim), source="dense")
        for eid, sim in results
    ]


def _belief_dense(
    conn: sqlite3.Connection,
    query: str,
    vector_engine: Any | None,
    tenant_id: str,
    limit: int,
) -> list[sqlite3.Row]:
    """Dense (semantic) belief match via cosine over a **capped** candidate set.

    Phase B: never full-scan every belief embedding. Prefer high-importance
    rows up to ``memory.v2.dense_belief_candidate_cap`` (default 400).
    Best-effort: returns [] if no embedder or no belief embeddings exist.
    """
    del vector_engine  # reserved for future sqlite-vec belief path
    try:
        from kazma_core.memory.embedder import get_embedder

        embedder = get_embedder()
        if embedder is None:
            return []
        qvec = embedder.encode(query)
        if not qvec:
            return []
    except Exception:
        return []
    try:
        import numpy as np

        qarr = np.asarray(qvec, dtype=np.float32)
        qnorm = float(np.linalg.norm(qarr)) or 1.0
        qarr = qarr / qnorm
    except Exception:
        return []  # numpy required for cosine; degrade silently

    cap = 400
    try:
        from kazma_core.memory.config import read_memory_cfg

        cap = int(
            ((read_memory_cfg() or {}).get("v2") or {}).get(
                "dense_belief_candidate_cap", 400
            )
        )
        cap = max(50, min(cap, 5000))
    except Exception:
        pass

    # Cap + importance prefilter (Phase B scale guard)
    rows = conn.execute(
        """SELECT id, subject, predicate, object, predicate_type,
                  confidence, structural_importance, valid_from,
                  source_trust_weight, embedding
           FROM beliefs
           WHERE valid_until IS NULL AND invalidated_at IS NULL
             AND tenant_id = ? AND embedding IS NOT NULL
           ORDER BY structural_importance DESC, confidence DESC
           LIMIT ?""",
        (tenant_id, cap),
    ).fetchall()
    if not rows:
        return []

    scored: list[tuple[float, sqlite3.Row]] = []
    for r in rows:
        try:
            varr = np.frombuffer(r["embedding"], dtype=np.float32)
            vnorm = float(np.linalg.norm(varr)) or 1.0
            sim = float(np.dot(qarr, varr / vnorm))
        except Exception:
            continue
        scored.append((sim, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _sim, r in scored[:limit]]


def _belief_graph_ppr(
    conn: sqlite3.Connection,
    query: str,
    tenant_id: str,
    *,
    seed_episodes: list[RecallHit] | None = None,
) -> dict[str, float]:
    """Multi-hop PPR over the belief triple graph (subject ↔ object edges).

    Seeds: query tokens that match entity names + entities from seed
    episodes. Returns ``{belief_id: ppr_mass}`` for beliefs incident to
    high-PPR entities — so ``user works_at Acme`` + ``Acme located_in
    Paris`` can surface Paris from a user-centric seed.
    """
    try:
        from kazma_core.memory.ppr import compute_local_ppr
    except Exception:
        return {}
    alpha, max_iter, max_nodes = 0.15, 15, 200
    try:
        from kazma_core.memory.config import read_memory_cfg

        v2 = (read_memory_cfg() or {}).get("v2") or {}
        alpha = float(v2.get("ppr_alpha", 0.15))
        max_iter = int(v2.get("ppr_max_iter", 15))
        max_nodes = int(v2.get("ppr_max_nodes", 200))
    except Exception:
        pass

    # Load active beliefs as graph edges (capped)
    try:
        rows = conn.execute(
            """
            SELECT id, subject, predicate, object
            FROM beliefs
            WHERE valid_until IS NULL AND invalidated_at IS NULL
              AND tenant_id = ?
            ORDER BY structural_importance DESC, confidence DESC
            LIMIT ?
            """,
            (tenant_id, max(max_nodes * 3, 300)),
        ).fetchall()
    except Exception:
        return {}
    if not rows:
        return {}

    edges: list[tuple[str, str, float]] = []
    entity_to_beliefs: dict[str, list[str]] = {}
    node_set: set[str] = set()

    def _norm(s: str) -> str:
        return " ".join((s or "").strip().lower().split())

    for r in rows:
        sub = _norm(r["subject"])
        obj = _norm(r["object"])
        if not sub or not obj:
            continue
        bid = r["id"]
        # Directed subject→object (strong) + reverse (weaker) for undirected walk
        edges.append((sub, obj, 1.0))
        edges.append((obj, sub, 0.5))
        node_set.add(sub)
        node_set.add(obj)
        entity_to_beliefs.setdefault(sub, []).append(bid)
        entity_to_beliefs.setdefault(obj, []).append(bid)

    if not edges:
        return {}

    # Seeds: query tokens + multi-word entity names that appear in the graph
    seeds: list[str] = []
    q_low = (query or "").lower()
    tokens = [
        "".join(c for c in t if c.isalnum())
        for t in q_low.replace("-", " ").split()
    ]
    tokens = [t for t in tokens if len(t) >= 3]
    for t in tokens:
        if t in node_set:
            seeds.append(t)
        # Also seed entities that contain the token (e.g. "acmecorp")
        for node in node_set:
            if t in node.split() or t == node:
                seeds.append(node)
    # Full-string entity match when query mentions the entity name
    for node in node_set:
        if len(node) >= 3 and node in q_low:
            seeds.append(node)
    if seed_episodes:
        for ep in seed_episodes:
            text = (ep.content or "").lower()
            for node in node_set:
                if len(node) >= 3 and node in text:
                    seeds.append(node)

    # De-dupe seeds
    seen_s: set[str] = set()
    uniq_seeds: list[str] = []
    for s in seeds:
        if s not in seen_s:
            seen_s.add(s)
            uniq_seeds.append(s)
    if not uniq_seeds:
        return {}

    try:
        entity_scores = compute_local_ppr(
            uniq_seeds,
            edges,
            alpha=alpha,
            max_iter=max_iter,
            max_nodes=max_nodes,
        )
    except Exception:
        return {}

    belief_scores: dict[str, float] = {}
    for entity, mass in entity_scores.items():
        for bid in entity_to_beliefs.get(entity, []):
            prev = belief_scores.get(bid, 0.0)
            if mass > prev:
                belief_scores[bid] = float(mass)
    return belief_scores


def _episode_ppr(
    conn: sqlite3.Connection,
    seed_episode_ids: list[str],
    tenant_id: str,
) -> dict[str, float]:
    """PPR boost: treat episodes as nodes, shared sessions as edges.

    Secondary to belief-graph PPR — keeps same-session episode cliques
    in the hybrid RRF fusion. Bounded by ``ppr_max_nodes``.
    """
    if not seed_episode_ids:
        return {}
    try:
        from kazma_core.memory.config import read_memory_cfg
        from kazma_core.memory.ppr import compute_local_ppr

        v2 = (read_memory_cfg() or {}).get("v2") or {}
        alpha = float(v2.get("ppr_alpha", 0.15))
        max_iter = int(v2.get("ppr_max_iter", 10))
        max_nodes = int(v2.get("ppr_max_nodes", 200))
    except Exception:
        return {}
    # Cap seed set
    seed_k = 10
    try:
        from kazma_core.memory.config import read_memory_cfg as _rmc

        seed_k = int(((_rmc() or {}).get("v2") or {}).get("ppr_seed_k", 10))
    except Exception:
        pass
    seeds = seed_episode_ids[:seed_k]

    # Build edges: only sessions that touch seeds (avoid full-tenant clique load)
    try:
        placeholders = ",".join("?" for _ in seeds)
        seed_sessions = conn.execute(
            f"SELECT DISTINCT session_id FROM episodes WHERE id IN ({placeholders})",
            seeds,
        ).fetchall()
        session_ids = [r[0] for r in seed_sessions if r[0]]
        if not session_ids:
            return {}
        sph = ",".join("?" for _ in session_ids)
        rows = conn.execute(
            f"""
            SELECT id, session_id FROM episodes
            WHERE tenant_id = ?
              AND tier IN ('recall','episodic')
              AND session_id IN ({sph})
            LIMIT ?
            """,
            [tenant_id] + session_ids + [max_nodes * 2],
        ).fetchall()
    except Exception:
        return {}
    by_session: dict[str, list[str]] = {}
    for r in rows:
        by_session.setdefault(r["session_id"], []).append(r["id"])
    edges: list[tuple[str, str, float]] = []
    for session_eps in by_session.values():
        for i, a in enumerate(session_eps):
            for b in session_eps[i + 1 :]:
                edges.append((a, b, 1.0))
    if not edges:
        return {}
    try:
        return compute_local_ppr(
            seeds, edges, alpha=alpha, max_iter=max_iter, max_nodes=max_nodes
        )
    except Exception:
        return {}


# ── Access accounting + session bias (Phase A) ────────────────────────────


def _bump_access(
    conn: sqlite3.Connection,
    beliefs: list[RecallHit],
    episodes: list[RecallHit],
) -> None:
    """Increment access_count / last_accessed for recalled rows.

    Without this, macro_sleep promotion (access >= 2) never fires and
    retention scoring stays stale. Best-effort — never raises to caller.
    """
    try:
        from kazma_core.memory.config import read_memory_cfg

        v2 = (read_memory_cfg() or {}).get("v2") or {}
        if v2.get("access_bump_enabled", True) is False:
            return
    except Exception:
        pass
    now = __import__("time").time()
    try:
        for h in beliefs:
            if not h.id:
                continue
            conn.execute(
                """UPDATE beliefs
                   SET access_count = COALESCE(access_count, 0) + 1,
                       last_accessed = ?
                   WHERE id = ?""",
                (now, h.id),
            )
        for h in episodes:
            if not h.id:
                continue
            conn.execute(
                """UPDATE episodes
                   SET access_count = COALESCE(access_count, 0) + 1,
                       last_accessed = ?
                   WHERE id = ?""",
                (now, h.id),
            )
        conn.commit()
    except Exception:
        logger.debug("[recall] access bump failed", exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass


def _apply_session_bias(
    conn: sqlite3.Connection,
    hits: list[RecallHit],
    session_id: str,
    *,
    boost: float = 0.35,
) -> list[RecallHit]:
    """Boost scores for episodes belonging to the active thread/session.

    Does not hard-filter: global memories still appear, but same-session
    episodes rank higher for identical content. ``boost`` is added to the
    fused RRF score (typical RRF scores are small, so 0.35 is material).
    """
    if not hits or not session_id:
        return hits
    try:
        ids = [h.id for h in hits if h.id]
        if not ids:
            return hits
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, session_id FROM episodes WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        same = {r["id"] for r in rows if (r["session_id"] or "") == session_id}
        if not same:
            return hits
        boosted: list[RecallHit] = []
        for h in hits:
            if h.id in same:
                meta = dict(h.metadata or {})
                meta["session_boost"] = True
                boosted.append(
                    RecallHit(
                        id=h.id,
                        content=h.content,
                        score=float(h.score) + boost,
                        kind=h.kind,
                        source=h.source,
                        metadata=meta,
                    )
                )
            else:
                boosted.append(h)
        boosted.sort(key=lambda x: x.score, reverse=True)
        return boosted
    except Exception:
        logger.debug("[recall] session bias failed", exc_info=True)
        return hits


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
