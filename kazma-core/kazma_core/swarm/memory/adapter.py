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
from typing import Any

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
        """ChromaDB semantic query."""
        try:
            results = self._l1.query(text, limit=limit)
            return [(r[0], r[1], "", "L1:chromadb", {}) for r in results]
        except Exception as exc:
            logger.warning("[Adapter] L1 query failed: %s", exc)
            return []

    async def _query_l2(self, text: str, tags: list[str] | None, limit: int) -> list[tuple[str, float, str, str, dict]]:
        """Knowledge Graph structural query."""
        try:
            results: list[tuple[str, float, str, str, dict]] = []
            words = text.lower().split()
            for tag in (tags or []) + words:
                entities = self._l2.query_by_type(tag)
                if entities:
                    results.extend(
                        (e["id"], 0.9, str(e.get("properties", {})), "L2:graph", {})
                        for e in entities[:limit]
                    )
                related = self._l2.query_related(tag, depth=2)
                if related:
                    results.extend(
                        (r["id"], 0.7 / r.get("depth", 1), "", "L2:graph", r)
                        for r in related[:limit]
                    )
            return results[:limit]
        except Exception as exc:
            logger.warning("[Adapter] L2 query failed: %s", exc)
            return []

    async def _query_l3(self, text: str, limit: int) -> list[tuple[str, float, str, str, dict]]:
        """FTS5 lexical query."""
        try:
            results = await self._l3.lexical_search(text, limit=limit)
            return [(r[0], float(r[1]), "", "L3:fts5", {}) for r in results]
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
                from kazma_core.swarm.registry import WorkerRegistry
                workers.extend([w.name for w in WorkerRegistry().list_all()])
            except Exception:
                pass
            for worker in workers:
                results = self._l4.query(worker, text, limit=limit)
                all_results.extend((r[0], r[1], "", "L4:sqlite_vec", {"worker": worker}) for r in results)
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

        # Sort within each layer by original score
        for source in layers:
            layers[source].sort(key=lambda x: x[1], reverse=True)

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

    async def index(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Index content across all available layers (async parallel)."""
        meta = metadata or {}
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
            from datetime import datetime, timezone
            timestamp = datetime.now(timezone.utc).isoformat()
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

    async def search(self, query_text: str, limit: int = 5) -> list[MemoryHit]:
        """Semantic search alias for self-improvement queries."""
        return await self.query(query_text, limit=limit)

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
    """Return the shared adapter, initialized lazily with available backends."""
    global _adapter
    if _adapter is not None:
        return _adapter
    # Initialize with available backends
    try:
        from kazma_core.swarm.memory.vector import GlobalVectorStore
        chroma = GlobalVectorStore()
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
