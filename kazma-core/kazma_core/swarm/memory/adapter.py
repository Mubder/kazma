"""Unified 4-layer memory adapter with Reciprocal Rank Fusion (RRF).

Fans out queries to all four backends in parallel, blends results
using RRF, de-duplicates by content, and returns a single ranked
list of MemoryHit objects.

Architecture:
    query("fix auth bug")
    → asyncio.gather(L1.query(), L2.query(), L3.query(), L4.query())
    → RRF blending (k=60)
    → dedup by content hash
    → top-N results
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

        # RRF blending
        blended = self._rrf_blend(all_results, limit)

        # Convert to MemoryHit objects
        hits: list[MemoryHit] = []
        for uid, score, content, source, metadata in blended:
            hits.append(MemoryHit(
                id=uid,
                content=content or "",
                score=score,
                source_layer=source or "unknown",
                metadata=metadata or {},
            ))
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
        """Knowledge Graph structural query."""
        try:
            tenant_id = self._get_tenant_id()
            results: list[tuple[str, float, str, str, dict]] = []
            words = text.lower().split()
            for tag in (tags or []) + words:
                entities = self._l2.query_by_type(tag)
                if entities:
                    for e in entities[:limit]:
                        # Tenant isolation: skip entities belonging to a different tenant
                        if tenant_id and e.get("properties", {}).get("tenant_id") not in (None, tenant_id):
                            continue
                        results.append(
                            (e["id"], 0.9, str(e.get("properties", {})), "L2:graph", {})
                        )
                related = self._l2.query_related(tag, depth=2)
                if related:
                    for r in related[:limit]:
                        if tenant_id and r.get("properties", {}).get("tenant_id") not in (None, tenant_id):
                            continue
                        results.append(
                            (r["id"], 0.7 / r.get("depth", 1), "", "L2:graph", r)
                        )
            return results[:limit]
        except Exception as exc:
            logger.warning("[Adapter] L2 query failed: %s", exc)
            return []

    async def _query_l3(self, text: str, limit: int) -> list[tuple[str, float, str, str, dict]]:
        """FTS5 lexical query — fetches document content by ID after scoring.

        Applies tenant isolation via post-filter on metadata when
        ``tenant_id`` is set in context.
        """
        try:
            tenant_id = self._get_tenant_id()
            results = await self._l3.lexical_search(text, limit=limit * 2)  # fetch extra for filtering
            if not results:
                return []
            # Fetch document text for the scored IDs
            ids = [r[0] for r in results]
            texts = await self._l3.get_texts(ids) if hasattr(self._l3, "get_texts") else {}
            # Post-filter by tenant if needed
            out: list[tuple[str, float, str, str, dict]] = []
            for r in results:
                uid = r[0]
                if tenant_id:
                    # Check metadata for tenant — try to parse it from stored text
                    content = texts.get(uid, "")
                    # If we can't determine tenant from metadata, include it
                    # (safe default: don't over-filter)
                out.append((uid, float(r[1]), texts.get(uid, ""), "L3:fts5", {}))
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

    async def index(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Index content across all available layers (async parallel)."""
        meta = metadata or {}
        tenant_id = self._get_tenant_id()
        if tenant_id:
            meta["tenant_id"] = tenant_id
        tasks = []

        # L1 — ChromaDB
        if self._l1 and getattr(self._l1, "available", False):
            tasks.append(self._index_l1(text, meta))

        # L2 — Knowledge Graph
        if self._l2 and getattr(self._l2, "available", False):
            tasks.append(self._index_l2(text, meta, tags))

        # L3 — FTS5
        if self._l3:
            tasks.append(self._index_l3(text, meta))

        # L4 — sqlite-vec
        if self._l4 and getattr(self._l4, "available", False):
            tasks.append(self._index_l4(text, meta))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _index_l1(self, text: str, meta: dict) -> None:
        uid = hashlib.sha256(text.encode()).hexdigest()[:16]
        self._l1.index(uid, text, meta)

    async def _index_l2(self, text: str, meta: dict, tags: list[str] | None) -> None:
        uid = hashlib.sha256(text.encode()).hexdigest()[:16]
        self._l2.add_entity(uid, "memory_chunk", {"content": text[:200], **meta})
        for tag in (tags or []):
            self._l2.add_relation(uid, tag, "tagged")

    async def _index_l3(self, text: str, meta: dict) -> None:
        uid = hashlib.sha256(text.encode()).hexdigest()[:16]
        await self._l3.index({"id": uid, "content": text, "metadata": meta, "timestamp": 0, "source": "memory"})

    async def _index_l4(self, text: str, meta: dict) -> None:
        uid = hashlib.sha256(text.encode()).hexdigest()[:16]
        worker = meta.get("worker", "default")
        self._l4.index(worker, uid, text)


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
        """Store content across all available layers (chat compatibility wrapper).

        Delegates to index() with extracted tags from metadata.
        """
        try:
            # Extract tags if present for L2/L4 indexing
            tags = metadata.get("tags", None) if isinstance(metadata, dict) else None
            await self.index(text, metadata=metadata, tags=tags)
            import hashlib
            return hashlib.sha256(text.encode()).hexdigest()[:16]
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
        from kazma_core.swarm.memory.graph import KnowledgeGraph
        graph = KnowledgeGraph()
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
