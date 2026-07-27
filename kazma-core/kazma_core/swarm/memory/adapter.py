"""Unified 4-layer memory adapter with Reciprocal Rank Fusion (RRF).

Fans out queries to all four backends in parallel, blends results
using RRF, de-duplicates by content, and returns a single ranked
list of MemoryHit objects.

Architecture:
    query("fix auth bug")
    → asyncio.gather(L1.query(), L2.query(), L3.query(), L4.query())
    → RRF blending (k=60)
    → drop empty-content hits
    → top-N results

``store()`` / ``index()`` are **fail-closed**: they only return a document
id when at least one durable layer (L1 Chroma, L3 FTS5, or L4 sqlite-vec)
confirmed a write. L2 graph alone is structural and does not count as
durable recall storage.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

__all__ = ["MemoryHit", "UnifiedMemoryAdapter", "get_adapter", "set_adapter"]

logger = logging.getLogger(__name__)

_RRF_K = 60  # smoothing constant
_CHUNK_SIZE = 2000
_CHUNK_OVERLAP = 200


@dataclass(slots=True)
class MemoryHit:
    """A single blended memory result from the 4-layer adapter."""

    id: str
    content: str = ""
    score: float = 0.0
    source_layer: str = ""       # "L1:chromadb" | "L2:graph" | "L3:fts5" | "L4:sqlite_vec"
    metadata: dict[str, Any] = field(default_factory=dict)


class UnifiedMemoryAdapter:
    """4-layer co-processing memory adapter with RRF blending.

    Holds references to all four backends.  ``query()`` fans out
    to all layers, blends with RRF, and returns MemoryHits.

    Args:
        vector_store:    Layer 1 — ChromaDB global semantic.
        graph:           Layer 2 — NetworkX knowledge graph.
        fts5_store:      Layer 3 — FTS5 lexical.
        sqlite_vec:      Layer 4 — sqlite-vec local embeddings.
    """

    def __init__(
        self,
        vector_store: Any | None = None,
        graph: Any | None = None,
        fts5_store: Any | None = None,
        sqlite_vec: Any | None = None,
    ) -> None:
        self._l1 = vector_store
        self._l2 = graph
        self._l3 = fts5_store
        self._l4 = sqlite_vec

    # ── Health ──────────────────────────────────────────────────────────

    def health(self) -> dict[str, bool]:
        """Report per-layer availability."""
        return {
            "chromadb": self._l1 is not None and getattr(self._l1, "available", False),
            "graph": self._l2 is not None and getattr(self._l2, "available", False),
            "fts5": self._l3 is not None and getattr(self._l3, "available", False),
            "sqlite_vec": self._l4 is not None and getattr(self._l4, "available", False),
        }

    # ── Query ───────────────────────────────────────────────────────────

    async def query(
        self,
        text: str,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[MemoryHit]:
        """Fan-out query to all 4 layers, blend with RRF, return top-N.

        Args:
            text:  Natural language query.
            tags:  Optional expertise tags for filtering (e.g. ["code", "security"]).
            limit: Maximum results to return.

        Returns:
            Sorted list of MemoryHit objects (highest RRF score first).
        """
        tasks = []

        # Layer 1 — ChromaDB
        if self._l1 and getattr(self._l1, "available", False):
            tasks.append(self._query_l1(text, limit * 2))

        # Layer 2 — Knowledge Graph
        if self._l2 and getattr(self._l2, "available", False):
            tasks.append(self._query_l2(text, tags, limit * 2))

        # Layer 3 — FTS5
        if self._l3:
            tasks.append(self._query_l3(text, limit * 2))

        # Layer 4 — sqlite-vec
        if self._l4 and getattr(self._l4, "available", False):
            tasks.append(self._query_l4(text, limit * 2))

        if not tasks:
            return []

        # Run all layers in parallel
        layer_results: list[list[tuple[str, float, str, str, dict]]] = await asyncio.gather(
            *tasks, return_exceptions=True,
        )

        # Collect all scored results with source layer info
        all_results: list[tuple[str, float, str, str | None, dict]] = []
        for result in layer_results:
            if isinstance(result, Exception):
                continue
            all_results.extend(result)

        if not all_results:
            return []

        # RRF blending — over-fetch so empty-content drops still fill top-N
        blended = self._rrf_blend(all_results, limit * 3)

        # Convert to MemoryHit objects; drop empty content (L2 noise, failed
        # get_documents, etc.) so chat injection never shows blank rows.
        hits: list[MemoryHit] = []
        for uid, score, content, source, metadata in blended:
            body = (content or "").strip()
            if not body:
                # L2 structural hits may only have metadata.content
                if isinstance(metadata, dict):
                    body = str(metadata.get("content") or "").strip()
            if not body:
                continue
            hits.append(MemoryHit(
                id=uid,
                content=body,
                score=score,
                source_layer=source or "unknown",
                metadata=metadata or {},
            ))
            if len(hits) >= limit:
                break
        return hits

    # ── Per-layer query helpers ─────────────────────────────────────────

    async def _query_l1(self, text: str, limit: int) -> list[tuple[str, float, str, str, dict]]:
        """ChromaDB semantic query — fetches document content by ID after scoring."""
        try:
            tenant_id = self._get_tenant_id()
            results = self._l1.query(text, limit=limit, tenant_id=tenant_id)
            if not results:
                return []
            # Fetch document text for the scored IDs
            ids = [r[0] for r in results]
            docs = self._l1.get_documents(ids) if hasattr(self._l1, "get_documents") else {}
            return [(r[0], r[1], docs.get(r[0], ""), "L1:chromadb", {}) for r in results]
        except Exception as exc:
            logger.warning("[Adapter] L1 query failed: %s", exc)
            return []

    async def _query_l2(self, text: str, tags: list[str] | None, limit: int) -> list[tuple[str, float, str, str, dict]]:
        """Property-graph query: FTS + multi-hop neighbors + type/tag lookup."""
        try:
            tenant_id = self._get_tenant_id()
            results: list[tuple[str, float, str, str, dict]] = []
            seen: set[str] = set()

            def _accept(ent: dict, base_score: float) -> None:
                eid = str(ent.get("id") or "")
                if not eid or eid in seen:
                    return
                props = ent.get("properties") if isinstance(ent.get("properties"), dict) else {}
                if tenant_id and props.get("tenant_id") not in (None, tenant_id):
                    if ent.get("tenant_id") not in (None, tenant_id):
                        return
                content = str(
                    ent.get("content")
                    or props.get("content")
                    or ent.get("label")
                    or ""
                ).strip()
                if not content:
                    return
                seen.add(eid)
                meta = dict(props)
                meta.setdefault("entity_type", ent.get("type") or "")
                if ent.get("relation"):
                    meta["relation"] = ent["relation"]
                results.append((eid, base_score, content, "L2:graph", meta))

            # Primary: FTS over the SQLite property graph
            if hasattr(self._l2, "search"):
                for ent in self._l2.search(text, limit=limit * 2, tenant_id=tenant_id) or []:
                    score = float(ent.get("score") or 0.9)
                    _accept(ent, max(score, 0.5))

            # Multi-hop expansion from FTS seeds / tags
            seeds = list(seen)[:5]
            for tag in list(tags or []):
                seeds.append(str(tag))
            for seed in seeds:
                for r in self._l2.query_related(seed, depth=2) or []:
                    depth = max(int(r.get("depth", 1)), 1)
                    _accept(r, 0.75 / depth)

            # Type lookup for explicit tags
            for tag in tags or []:
                for e in self._l2.query_by_type(str(tag)) or []:
                    _accept(e, 0.85)

            results.sort(key=lambda x: x[1], reverse=True)
            return results[:limit]
        except Exception as exc:
            logger.warning("[Adapter] L2 query failed: %s", exc)
            return []

    async def _query_l3(self, text: str, limit: int) -> list[tuple[str, float, str, str, dict]]:
        """FTS5 lexical query — fetches document content by ID after scoring.

        Hard tenant isolation (P6): when ``tenant_id`` is set in context it is
        pushed into the backend search so only that tenant's rows return.
        """
        try:
            tenant_id = self._get_tenant_id()
            # Prefer signature that accepts tenant_id; fall back for stubs.
            try:
                results = await self._l3.lexical_search(
                    text, limit=limit * 2, tenant_id=tenant_id
                )
            except TypeError:
                results = await self._l3.lexical_search(text, limit=limit * 2)
            if not results:
                return []
            ids = [r[0] for r in results]
            texts = await self._l3.get_texts(ids) if hasattr(self._l3, "get_texts") else {}
            out: list[tuple[str, float, str, str, dict]] = []
            for r in results:
                uid = r[0]
                meta: dict[str, Any] = {}
                if tenant_id:
                    meta["tenant_id"] = tenant_id
                out.append((uid, float(r[1]), texts.get(uid, ""), "L3:fts5", meta))
                if len(out) >= limit:
                    break
            return out
        except Exception as exc:
            logger.warning("[Adapter] L3 query failed: %s", exc)
            return []

    async def _query_l4(self, text: str, limit: int) -> list[tuple[str, float, str, str, dict]]:
        """sqlite-vec local query.  Queries all known worker tables."""
        try:
            all_results: list[tuple[str, float, str, str, dict]] = []
            # Query default + enumerate registered workers
            workers = ["default"]
            try:
                from kazma_core.swarm.registry import get_worker_registry
                workers.extend([w.name for w in get_worker_registry().list_all()])
            except Exception as exc:
                logger.debug("Worker registry lookup failed: %s", exc)
            for worker in workers:
                results = self._l4.query(worker, text, limit=limit)
                if not results:
                    continue
                texts: dict[str, str] = {}
                if hasattr(self._l4, "get_texts"):
                    try:
                        texts = self._l4.get_texts(worker, [r[0] for r in results])
                    except Exception:
                        texts = {}
                all_results.extend(
                    (r[0], r[1], texts.get(r[0], ""), "L4:sqlite_vec", {"worker": worker})
                    for r in results
                )
            return all_results[:limit]
        except Exception as exc:
            logger.warning("[Adapter] L4 query failed: %s", exc)
            return []

    # ── RRF Blending ────────────────────────────────────────────────────

    def _rrf_blend(
        self,
        results: list[tuple[str, float, str, str | None, dict]],
        top_n: int = 10,
    ) -> list[tuple[str, float, str, str | None, dict]]:
        """Blend results from multiple layers using Reciprocal Rank Fusion.

        Each result is a tuple of (uid, score, content, source_layer, metadata).
        The original score is used for ranking within each layer, then RRF
        combines across layers.
        """
        # Group by source layer
        layers: dict[str, list[tuple[str, float, str, str | None, dict]]] = {}
        for r in results:
            source = r[3] or "unknown"
            layers.setdefault(source, []).append(r)

        # Sort within each layer by original score.
        # FTS5 (BM25) scores are negative (more negative = more relevant),
        # so sort ascending for that layer. Other layers use descending
        # (higher score = more relevant).
        for source in layers:
            if source == "L3:fts5":
                layers[source].sort(key=lambda x: x[1])  # ascending for BM25
            else:
                layers[source].sort(key=lambda x: x[1], reverse=True)  # descending

        # Compute RRF scores across all layers
        rrf_scores: dict[str, tuple[float, str, str | None, dict]] = {}
        for source, layer_items in layers.items():
            for rank, item in enumerate(layer_items, start=1):
                uid = item[0]
                content = item[2]
                metadata = item[4]
                rrf = 1.0 / (_RRF_K + rank)
                if uid in rrf_scores:
                    prev_score, prev_content, _, prev_meta = rrf_scores[uid]
                    rrf_scores[uid] = (prev_score + rrf, prev_content or content, source, prev_meta or metadata)
                else:
                    rrf_scores[uid] = (rrf, content, source, metadata)

        # Sort by RRF score descending, take top N
        sorted_items = sorted(rrf_scores.items(), key=lambda x: x[1][0], reverse=True)
        return [(uid, score, content, source, metadata) for uid, (score, content, source, metadata) in sorted_items[:top_n]]

    # ── Index ───────────────────────────────────────────────────────────

    @staticmethod
    def _get_tenant_id() -> str | None:
        """Return the active tenant_id from context (or None)."""
        try:
            from kazma_core.tenant_context import get_current_tenant_id
            return get_current_tenant_id()
        except Exception:
            return None

    @staticmethod
    def _doc_id(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
        """Split long text into overlapping chunks (matches VectorMemory policy)."""
        if len(text) <= chunk_size:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            if end < len(text):
                boundary = text.rfind("\n", start, end)
                if boundary > start + chunk_size // 2:
                    end = boundary
            piece = text[start:end].strip()
            if piece:
                chunks.append(piece)
            start = end - overlap
            if start < 0:
                start = 0
            if end >= len(text):
                break
        return chunks or [text[:chunk_size]]

    async def index(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Index content across all available layers (async parallel).

        Returns a result dict::

            {"id": str, "durable_ok": bool, "layers": {"l1": bool, ...}}

        ``durable_ok`` is True only when L1, L3, or L4 confirmed a write.
        """
        body = (text or "").strip()
        uid = self._doc_id(body) if body else ""
        result: dict[str, Any] = {
            "id": uid,
            "durable_ok": False,
            "layers": {"l1": False, "l2": False, "l3": False, "l4": False},
        }
        if not body:
            return result

        meta = dict(metadata or {})
        tenant_id = self._get_tenant_id()
        if tenant_id:
            meta["tenant_id"] = tenant_id

        tasks: list[tuple[str, Any]] = []

        if self._l1 and getattr(self._l1, "available", False):
            tasks.append(("l1", self._index_l1(body, meta, uid)))
        if self._l2 and getattr(self._l2, "available", False):
            tasks.append(("l2", self._index_l2(body, meta, tags, uid)))
        if self._l3:
            tasks.append(("l3", self._index_l3(body, meta, uid)))
        if self._l4 and getattr(self._l4, "available", False):
            tasks.append(("l4", self._index_l4(body, meta, uid)))

        if not tasks:
            logger.warning("[Adapter] index skipped — no layers available")
            return result

        outcomes = await asyncio.gather(
            *(t[1] for t in tasks), return_exceptions=True
        )
        for (layer, _), outcome in zip(tasks, outcomes):
            ok = False
            if isinstance(outcome, Exception):
                logger.warning("[Adapter] %s index failed: %s", layer, outcome)
            else:
                ok = bool(outcome)
            result["layers"][layer] = ok

        durable = bool(
            result["layers"]["l1"]
            or result["layers"]["l3"]
            or result["layers"]["l4"]
        )
        result["durable_ok"] = durable
        if not durable:
            logger.warning(
                "[Adapter] index wrote no durable layer (l1/l3/l4) for id=%s layers=%s",
                uid,
                result["layers"],
            )
        return result

    async def _index_l1(self, text: str, meta: dict, uid: str) -> bool:
        """Index into Chroma with chunking for long documents."""
        try:
            chunks = self._chunk_text(text)
            any_ok = False
            for i, chunk in enumerate(chunks):
                if len(chunks) == 1:
                    cid = uid
                    chunk_meta = dict(meta)
                else:
                    cid = hashlib.sha256(f"{uid}:{i}".encode()).hexdigest()[:16]
                    chunk_meta = {
                        **meta,
                        "chunk_index": i,
                        "chunk_total": len(chunks),
                        "parent_id": uid,
                    }
                if self._l1.index(cid, chunk, chunk_meta):
                    any_ok = True
            return any_ok
        except Exception as exc:
            logger.warning("[Adapter] L1 index failed: %s", exc)
            return False

    async def _index_l2(self, text: str, meta: dict, tags: list[str] | None, uid: str) -> bool:
        """Structural index: memory chunk + user hub + tags + heuristic SPO."""
        try:
            # Only JSON-safe scalar props on the graph (avoid nested blobs)
            safe_meta: dict[str, Any] = {}
            for k, v in (meta or {}).items():
                if k in ("content", "label"):
                    continue
                if isinstance(v, (str, int, float, bool)) or v is None:
                    safe_meta[str(k)] = v
                else:
                    safe_meta[str(k)] = str(v)[:200]

            self._l2.add_entity(
                uid,
                "memory_chunk",
                {
                    "content": text[:2000],
                    "label": (text[:80] if text else uid),
                    **safe_meta,
                },
            )
            # Link chunk to user hub
            if hasattr(self._l2, "add_relation"):
                try:
                    self._l2.add_entity(
                        "user", "person", {"label": "user", "content": "chat user"}
                    )
                    self._l2.add_relation(
                        "user",
                        uid,
                        "has_memory",
                        {"source": str(meta.get("source", "memory") or "memory")},
                    )
                except Exception:
                    logger.debug("[Adapter] L2 user hub link failed", exc_info=True)

            for tag in tags or []:
                t = str(tag).strip()
                if not t:
                    continue
                self._l2.add_entity(t, "tag", {"label": t, "content": t})
                self._l2.add_relation(uid, t, "tagged")

            # Heuristic SPO so L2 is not empty when consolidator LLM is skipped
            try:
                self._index_l2_heuristic_triples(text)
            except Exception:
                logger.debug("[Adapter] L2 heuristic triples failed", exc_info=True)

            return True
        except Exception as exc:
            logger.warning("[Adapter] L2 index failed: %s", exc)
            return False

    def _index_l2_heuristic_triples(self, text: str) -> None:
        """Promote durable cues into graph triples on every store (no LLM)."""
        if not self._l2 or not hasattr(self._l2, "upsert_triple"):
            return
        body = (text or "").strip()
        if len(body) < 8:
            return
        try:
            from kazma_core.memory.consolidator import extract_heuristic

            extracted = extract_heuristic(body, "")
        except Exception:
            return
        for t in extracted.get("triples") or []:
            if not isinstance(t, dict):
                continue
            s = str(t.get("subject") or "").strip()
            p = str(t.get("predicate") or "").strip()
            o = str(t.get("object") or "").strip()
            if not (s and p and o):
                continue
            fact = f"{s} {p} {o}"
            try:
                self._l2.upsert_triple(
                    s,
                    p,
                    o,
                    fact=fact[:240],
                    extra={"source": "adapter_heuristic"},
                )
            except Exception:
                logger.debug("[Adapter] upsert_triple failed for %s-%s-%s", s, p, o)

    async def _index_l3(self, text: str, meta: dict, uid: str) -> bool:
        """Lexical + optional embedding BLOB into memory.db (real timestamps)."""
        try:
            from kazma_core.swarm.memory.embedder import (
                encode_text_to_blob,
                resolve_unix_timestamp,
            )

            ts = resolve_unix_timestamp(meta)
            # Stamp metadata so consumers that only read JSON still see time
            meta_out = dict(meta or {})
            meta_out.setdefault("timestamp", ts)
            # Encode off the event loop (local MiniLM can be multi-second first load)
            emb_blob = await asyncio.to_thread(encode_text_to_blob, text)

            doc: dict[str, Any] = {
                "id": uid,
                "content": text,
                "metadata": meta_out,
                "timestamp": ts,
                "source": str(meta_out.get("source", "memory") or "memory"),
                "embedding": emb_blob,
            }
            doc_id = await self._l3.index(doc)
            return bool(doc_id)
        except Exception as exc:
            logger.warning("[Adapter] L3 index failed: %s", exc)
            return False

    async def _index_l4(self, text: str, meta: dict, uid: str) -> bool:
        try:
            worker = meta.get("worker", "default")
            return bool(self._l4.index(worker, uid, text))
        except Exception as exc:
            logger.warning("[Adapter] L4 index failed: %s", exc)
            return False


    # ── Soul Evolution logging ─────────────────────────────────────────

    async def log_evolution(
        self,
        task_id: str,
        worker_name: str,
        timestamp: str = "",
        original_prompt: str = "",
        delta: str = "",
        summary: str = "",
    ) -> None:
        """Persist a Soul Evolution log entry for semantic retrieval."""
        if not timestamp:
            from datetime import datetime
            timestamp = datetime.now(UTC).isoformat()
        text = f"[SoulEvolution] worker={worker_name} task={task_id} summary={summary[:200]} delta={delta[:200]}"
        meta = {
            "worker": worker_name,
            "task_id": task_id,
            "timestamp": timestamp,
            "original_prompt": original_prompt[:500],
            "delta": delta[:500],
            "summary": summary[:300],
        }
        await self.index(text, metadata=meta, tags=["soul_evolution", worker_name])

    async def search(self, query_text: str, limit: int = 5) -> list[dict[str, Any]]:
        """Semantic search returning list[dict] (compatibility with retrieve_memories).

        Returns dicts with ``content``, ``score``, ``id``, ``source_layer``,
        ``metadata`` keys — the shape that ``retrieve_memories`` and
        ``_format_retrieved_memories`` expect.
        """
        hits = await self.query(query_text, limit=limit)
        return [
            {
                "id": h.id,
                "content": h.content,
                "text": h.content,  # alias for _format_retrieved_memories fallback
                "score": h.score,
                "source_layer": h.source_layer,
                "metadata": h.metadata,
            }
            for h in hits
        ]

    async def search_dict(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search returning list[dict] for chat compaction compatibility.

        Args:
            query: Search query string.
            limit: Maximum results to return.

        Returns:
            List of dicts with keys: id, content, score, source_layer, metadata.
        """
        hits = await self.query(query, limit=limit)
        return [
            {
                "id": h.id,
                "content": h.content,
                "score": h.score,
                "source_layer": h.source_layer,
                "metadata": h.metadata,
            }
            for h in hits
        ]

    async def store(self, text: str, metadata: dict[str, Any] | None = None) -> str:
        """Store content across durable layers. Fail-closed.

        Returns the document id only when L1, L3, or L4 confirmed a write.
        Returns ``""`` if nothing durable was persisted (so tools never lie
        about a successful store).
        """
        try:
            tags = None
            if isinstance(metadata, dict):
                raw_tags = metadata.get("tags")
                if isinstance(raw_tags, list):
                    tags = [str(t) for t in raw_tags]
            result = await self.index(text, metadata=metadata, tags=tags)
            if result.get("durable_ok"):
                return str(result.get("id") or "")
            return ""
        except Exception:
            logger.exception("UnifiedMemoryAdapter.store failed")
            return ""

    # ── Self-improvement retrieval ────────────────────────────────────

    async def get_evolution_history(
        self,
        worker_name: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Retrieve Soul Evolution log entries for a specific worker."""
        hits = await self.query(
            f"SoulEvolution {worker_name} improvement pattern",
            tags=["soul_evolution", worker_name],
            limit=limit,
        )
        return [
            {
                "score": h.score,
                "summary": h.metadata.get("summary", ""),
                "delta": h.metadata.get("delta", ""),
                "task_id": h.metadata.get("task_id", ""),
                "timestamp": h.metadata.get("timestamp", ""),
            }
            for h in hits
        ]


# ── Module-level singleton ──────────────────────────────────────────────

_adapter: UnifiedMemoryAdapter | None = None


def get_adapter() -> UnifiedMemoryAdapter | None:
    """Return the shared adapter, initialized lazily with available backends.

    The L1 (ChromaDB) VectorStore is pointed at the SAME persistent path +
    collection (``agent_memory``) that the ``memory_store``/``memory_search``
    tools use, so memories written by the agent are visible to per-turn RAG
    retrieval and compaction. Previously the L1 used an ephemeral in-memory
    ``kazma_global`` collection — separate from the tools' ``agent_memory``,
    causing a silent write/read split.
    """
    global _adapter
    if _adapter is not None:
        return _adapter
    # Initialize L1 — persistent, same collection as the tools.
    try:
        from kazma_core.swarm.memory.vector import VectorStore
        from kazma_core.paths import vector_memory_path
        import os

        _collection = os.environ.get("KAZMA_VECTOR_COLLECTION", "agent_memory")
        chroma = VectorStore(
            collection_name=_collection,
            persist_dir=str(vector_memory_path()),
        )
    except Exception:
        chroma = None
    try:
        from kazma_core.swarm.memory.graph import get_knowledge_graph
        graph = get_knowledge_graph()
    except Exception:
        graph = None
    try:
        from kazma_core.swarm.memory.fts5 import FTS5LexicalStore
        fts5 = FTS5LexicalStore()
    except Exception:
        fts5 = None
    try:
        from kazma_core.swarm.memory.sqlite_vec import SQLiteVectorStore
        sv = SQLiteVectorStore()
    except Exception:
        sv = None
    _adapter = UnifiedMemoryAdapter(
        vector_store=chroma,
        graph=graph,
        fts5_store=fts5,
        sqlite_vec=sv,
    )
    return _adapter


def set_adapter(adapter: UnifiedMemoryAdapter) -> None:
    """Replace the shared adapter (for testing)."""
    global _adapter
    _adapter = adapter
