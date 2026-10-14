"""Knowledge Index — per-library vector + lexical retrieval with RRF.

This module is the **retrieval engine** for Knowledge Libraries.  It is
deliberately decoupled from chat memory:

- Each library gets its own ChromaDB collection ``kazma_kb_<library_id>``,
  separate from anything chat memory uses. Knowledge is injected into a
  turn as fenced, untrusted data; it is never written into memory.
- We reuse the :class:`~kazma_core.memory.vector_store_global.VectorStore`
  class and the shared embedder singleton, so embeddings stay consistent
  with the rest of the system and the model is never loaded twice.

Retrieval is a two-layer blend:

    query  ─► ChromaDB cosine  ─┐
           ─► FTS5 BM25 (lib)  ─┤
                                 ├─► RRF (k=60) ─► join SQLite ─► KnowledgeHit[]

The :class:`~kazma_core.stores.knowledge.KnowledgeStore` (SQLite) is the
source of truth for full content; ChromaDB holds the vector + a ≤2000-char
preview, and FTS5 holds a tokenized copy.  At retrieval time the RRF
blender produces a ranked list of chunk IDs, then we join back to SQLite
for the full content and provenance.  Retrieval still works (via FTS5)
when ChromaDB is unavailable.

Vectors carry the identity of the embedder that MADE them
(:func:`~kazma_core.memory.vector_store_global.embedder_identity`, read off
the embedder instance -- Knowledge audit KB-E03). Searches read only vectors
of the active identity, and the repair sweep re-makes every other one; a
library's coverage is the share of its chunks whose vector carries the
active identity, recorded by the sweep and read by the page without opening
the vector store.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kazma_core.memory.vector_store_global import VectorStore
from kazma_core.paths import vector_memory_path
from kazma_core.stores.knowledge import (
    KnowledgeLibraryGone,
    KnowledgeStore,
    get_knowledge_store,
)

__all__ = [
    "KnowledgeHit",
    "KnowledgeIndex",
    "KnowledgeInject",
    "KnowledgePublishError",
    "build_knowledge_inject",
    "get_knowledge_index",
    "kb_auto_inject_enabled",
    "kb_smart_search_enabled",
    "knowledge_index_for",
    "digest_problems",
    "repair_metric_lines",
    "reset_knowledge_index",
    "run_repair_sweep",
    "vector_readiness",
]

logger = logging.getLogger(__name__)

# Reciprocal Rank Fusion smoothing constant.  Matches ``adapter.py`` so the
# fusion math is identical to the proven 4-layer memory blender.
_RRF_K = 60

#: Results a caller may ask for at once (KB-S13: the route and the agent's
#: tool passed any number through; every layer then fetched four times it).
MAX_TOP_K = 20


def _vector_dir_for(store: KnowledgeStore) -> str:
    """Where a library's meaning vectors live.

    ``KAZMA_VECTOR_PATH`` when the operator set it, else beside the chunks'
    database: for the install's store that is ``data_dir()/vector_memory``,
    as always, and a store opened elsewhere (a test's temporary database)
    keeps its vectors with it instead of writing into the install's.
    """
    if (os.environ.get("KAZMA_VECTOR_PATH") or "").strip():
        return vector_memory_path()
    return str(Path(store.path).resolve().parent / "vector_memory")


class KnowledgePublishError(RuntimeError):
    """One or more pages of an index call failed to publish (KB-E02).

    ``failures`` holds ``(source_url, error)``; ``new`` and ``unchanged``
    count the chunks of the pages that did publish. A failed page keeps its
    previous version: it is never counted as unchanged.
    """

    def __init__(self, failures: list[tuple[str, BaseException]], new: int, unchanged: int) -> None:
        self.failures = failures
        self.new = new
        self.unchanged = unchanged
        first = failures[0][1] if failures else None
        super().__init__(
            f"{len(failures)} page(s) could not be stored"
            + (f": {type(first).__name__}: {first}" if first is not None else "")
        )


@dataclass(slots=True)
class KnowledgeHit:
    """A single ranked retrieval result from a Knowledge Library."""

    chunk_id: str
    content: str
    score: float                  # fused RRF score (higher = better)
    library_id: str
    source_url: str
    document_title: str
    section_header: str
    chunk_index: int
    has_code: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    document_id: str | None = None
    version_id: str | None = None
    citation_label: str = ""


class KnowledgeIndex:
    """Per-library vector + lexical retrieval with RRF blending.

    Use :func:`get_knowledge_index` (or :func:`knowledge_index_for` with a
    store) for the one instance per store; a second instance kept its own
    collection handles and failed on a collection the first had dropped
    (KB-E12).
    """

    def __init__(self, store: KnowledgeStore | None = None) -> None:
        self._store = store or get_knowledge_store()
        # Lazy per-library VectorStore cache.  Created on first access for a
        # given library_id and reused thereafter.
        self._vector_stores: dict[str, VectorStore] = {}
        self._persist_dir = _vector_dir_for(self._store)

    @property
    def store(self) -> KnowledgeStore:
        return self._store

    # ------------------------------------------------------------------
    # Vector store management
    # ------------------------------------------------------------------

    def _vector_store_for(self, library_id: str) -> VectorStore:
        """Return the per-library ChromaDB VectorStore (created lazily).

        Each library is a separate ChromaDB collection named
        ``kazma_kb_<library_id>``, persisted under the shared vector memory
        path, so deleting a library is a single ``drop_collection``.
        """
        if library_id in self._vector_stores:
            return self._vector_stores[library_id]
        vs = self._open_vector_store(library_id)
        self._vector_stores[library_id] = vs
        return vs

    def _open_vector_store(self, library_id: str) -> VectorStore:
        """A (not yet connected) store for the library's collection."""
        # Sanitise the library id for use as a Chroma collection name
        # (alphanumerics + underscore + hyphen only).
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in library_id)
        return VectorStore(collection_name=f"kazma_kb_{safe}", persist_dir=self._persist_dir)

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def purge_source(self, library_id: str, source_url: str) -> int:
        """Remove all SQLite/FTS + Chroma rows for one URL (an explicit purge)."""
        if not source_url:
            return 0
        old_ids = self._store.list_chunk_ids_for_source(library_id, source_url)
        n = self._store.delete_chunks_for_source(library_id, source_url)
        if old_ids:
            vs = self._vector_store_for(library_id)
            if vs.available:
                for oid in old_ids:
                    try:
                        vs.delete(oid)
                    except Exception as exc:
                        logger.debug(
                            "[KnowledgeIndex] chroma delete %s failed: %s", oid, exc
                        )
        if n:
            logger.info(
                "[KnowledgeIndex] Purged %d chunks for library=%s url=%s",
                n,
                library_id,
                source_url[:80],
            )
        return n

    def index(
        self,
        library_id: str,
        chunks: list[dict[str, Any]],
        *,
        force: bool = False,
    ) -> tuple[int, int]:
        """Publish the pages of a batch of chunks, one transaction per page.

        Each chunk dict carries: ``content``, ``content_hash``, ``source_url``,
        ``document_title``, ``section_header``, ``chunk_index``, ``has_code``
        (see ``chunk_to_dict``). Per ``source_url`` the page is published by
        :meth:`KnowledgeStore.publish_page` (KB-E02): unchanged when its
        ``(id, hash)`` sequence equals what is stored, else the old version
        replaced by the new one in one transaction. Vectors are written after
        the page committed, only while the library still exists (a deleted
        library's crawl must not re-create its collection).

        Returns ``(new_count, unchanged_count)`` in chunks. A page that could
        not be stored raises :class:`KnowledgePublishError` after the other
        pages were published -- it is never counted as unchanged; a library
        that no longer exists raises
        :class:`~kazma_core.stores.knowledge.KnowledgeLibraryGone`.
        """
        if not chunks:
            return (0, 0)

        # Group by source URL for page-level publication.
        by_url: dict[str, list[dict[str, Any]]] = {}
        for c in chunks:
            url = str(c.get("source_url") or "").strip()
            if not url:
                continue
            by_url.setdefault(url, []).append(c)

        new_count = 0
        unchanged = 0
        pages_unchanged = 0
        published: list[dict[str, Any]] = []
        failures: list[tuple[str, BaseException]] = []
        for url, url_chunks in by_url.items():
            try:
                result = self._store.publish_page(library_id, url, url_chunks, force=force)
            except KnowledgeLibraryGone:
                raise
            except Exception as exc:
                logger.warning(
                    "[KnowledgeIndex] page not stored library=%s url=%s: %s: %s",
                    library_id, url[:80], type(exc).__name__, exc,
                )
                failures.append((url, exc))
                continue
            if result["outcome"] == "unchanged":
                unchanged += len(url_chunks)
                pages_unchanged += 1
                continue
            new_count += len(result["new_ids"])
            published.append(result)

        if published and self._store.library_exists(library_id):
            self._write_vectors(library_id, published)

        logger.info(
            "[KnowledgeIndex] Indexed library=%s: %d new chunks, %d unchanged "
            "(%d pages published, %d unchanged, %d failed)",
            library_id, new_count, unchanged, len(published), pages_unchanged, len(failures),
        )
        if failures:
            raise KnowledgePublishError(failures, new_count, unchanged)
        return (new_count, unchanged)

    def _write_vectors(self, library_id: str, published: list[dict[str, Any]]) -> None:
        """Retire the replaced pages' vectors and embed the new chunks.

        Best effort: a chunk the vector store did not take is embedded by
        the repair sweep; keyword search reaches it meanwhile.
        """
        vs = self._vector_store_for(library_id)
        if not vs.available:
            logger.info("[KnowledgeIndex] meaning search unavailable — chunks stored for keyword "
                        "search only until the repair sweep embeds them")
            return
        identity = vs.identity
        retired = [i for page in published for i in page["retired_ids"]]
        if retired:
            try:
                vs.delete_many(retired)
            except Exception as exc:
                logger.warning("[KnowledgeIndex] could not remove replaced vectors of %s: %s",
                               library_id, exc)
        docs = [
            (chunk["id"], chunk["content"], self._chunk_vector_metadata(library_id, chunk, identity))
            for page in published for chunk in page.get("chunks", []) if chunk.get("content")
        ]
        if not docs:
            return
        try:
            vs.index_many(docs)
        except Exception as exc:
            logger.warning("[KnowledgeIndex] ChromaDB index failed for library %s: %s; the repair "
                           "sweep embeds them", library_id, exc)

    def publish_document_version(
        self,
        *,
        tenant_id: str,
        library_id: str,
        document_id: str,
        version_id: str,
        source_sha256: str,
        chunks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Publish through the existing SQLite/FTS/vector retrieval stack."""
        result = self._store.publish_document_version(
            tenant_id=tenant_id,
            library_id=library_id,
            document_id=document_id,
            version_id=version_id,
            source_sha256=source_sha256,
            chunks=chunks,
        )
        vector = self._vector_store_for(library_id)
        if vector.available:
            identity = vector.identity
            for identifier in result["retired_ids"]:
                try:
                    vector.delete(identifier)
                except Exception as exc:
                    logger.debug("[KnowledgeIndex] vector retire failed: %s", exc)
            if result["published"]:
                for chunk in chunks:
                    try:
                        vector.index(
                            doc_id=chunk["id"],
                            text=chunk["content"],
                            metadata=self._chunk_vector_metadata(
                                library_id,
                                {**chunk, "document_id": document_id, "version_id": version_id},
                                identity,
                            ),
                        )
                    except Exception as exc:
                        logger.warning(
                            "[KnowledgeIndex] document vector index failed for %s: %s",
                            chunk.get("id"),
                            exc,
                        )
        return result

    def unindex_document(
        self, *, tenant_id: str, library_id: str, document_id: str
    ) -> int:
        """Tombstone one document and remove its physical vector entries."""
        identifiers = self._store.unindex_document(
            tenant_id=tenant_id,
            library_id=library_id,
            document_id=document_id,
        )
        vector = self._vector_store_for(library_id)
        if vector.available:
            for identifier in identifiers:
                try:
                    vector.delete(identifier)
                except Exception as exc:
                    logger.debug("[KnowledgeIndex] vector unindex failed: %s", exc)
        return len(identifiers)

    def prune_sources_not_in(
        self,
        library_id: str,
        keep_urls: set[str] | list[str],
        *,
        candidate_urls: set[str] | list[str] | None = None,
    ) -> int:
        """Delete indexed URLs that are no longer in the keep set.

        Args:
            library_id: Library to prune.
            keep_urls: URLs that must remain (e.g. just discovered).
            candidate_urls: If set, only consider pruning URLs in this set
                (typically: previously indexed URLs that fall under the seed
                scope). When None, any library URL not in keep_urls is removed.

        Returns:
            Number of source URLs purged.
        """
        keep = {str(u).strip() for u in (keep_urls or set()) if str(u).strip()}
        if candidate_urls is None:
            try:
                existing = set(self._store.list_source_urls(library_id))
            except Exception:
                existing = set()
            to_drop = existing - keep
        else:
            cand = {str(u).strip() for u in candidate_urls if str(u).strip()}
            to_drop = cand - keep
        dropped = 0
        for url in sorted(to_drop):
            try:
                n = self.purge_source(library_id, url)
                if n:
                    dropped += 1
            except Exception as exc:
                logger.warning(
                    "[KnowledgeIndex] prune failed library=%s url=%s: %s",
                    library_id,
                    url[:80],
                    exc,
                )
        if dropped:
            logger.info(
                "[KnowledgeIndex] Pruned %d gone URLs from library=%s",
                dropped,
                library_id,
            )
        return dropped

    def delete_library(self, library_id: str) -> bool:
        """Drop the ChromaDB collection + all SQLite/FTS5 rows for a library.

        The collection is opened when this process never used it: dropping
        only a cached one left every library deleted after a restart, before
        anyone searched it, with its vectors on disk for good. Callers that
        answer an operator go through
        :func:`kazma_core.stores.knowledge_lifecycle.delete_library`, which
        also stops at a running crawl and tells the Documents platform.
        """
        vs = self._vector_stores.pop(library_id, None) or self._open_vector_store(library_id)
        try:
            if vs.drop():
                logger.info("[KnowledgeIndex] dropped the meaning vectors of library %s", library_id)
        except Exception as exc:
            # The chunks still go; a collection whose library is gone is
            # never searched, and the vectors go with a re-created library's
            # first write (another instance re-opens by name, KB-E12).
            logger.warning(
                "[KnowledgeIndex] could not drop the meaning vectors of library %s: %s",
                library_id,
                exc,
            )
        return self._store.delete_library(library_id)

    # ------------------------------------------------------------------
    # Search (two-layer RRF)
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        library_id: str,
        *,
        top_k: int = 5,
        tenant_id: str | None = None,
    ) -> list[KnowledgeHit]:
        """Async façade over :meth:`search_sync`, run off the event loop.

        The work is SQLite FTS, a vector query and the query embedding — all
        blocking. These façades used to run it inline on the loop that serves
        every SSE and WebSocket stream (AGENTS.md §26E).
        """
        return await asyncio.to_thread(
            self.search_sync, query, library_id, top_k=top_k, tenant_id=tenant_id
        )

    @staticmethod
    def _limit(top_k: Any) -> int:
        try:
            value = int(top_k or 5)
        except (TypeError, ValueError):
            value = 5
        return max(1, min(MAX_TOP_K, value))

    def search_sync(
        self,
        query: str,
        library_id: str,
        *,
        top_k: int = 5,
        tenant_id: str | None = None,
    ) -> list[KnowledgeHit]:
        """Semantic + lexical search scoped to one library, RRF-blended.

        Args:
            query:      Natural-language query.
            library_id: Library to search (hard scoping).
            top_k:      Maximum hits to return (1 to :data:`MAX_TOP_K`).

        Returns:
            Ranked :class:`KnowledgeHit` list, best first.
        """
        if not query or not query.strip():
            return []
        library = (
            self._store.get_library_for_tenant(library_id, tenant_id)
            if tenant_id is not None
            else self._store.get_library(library_id)
        )
        if library is None:
            return []
        limit = self._limit(top_k)

        semantic, lexical = self._raw_layers(library_id, query, limit)
        if not semantic and not lexical:
            return []

        # ── RRF blend ───────────────────────────────────────────────────
        blended = self._rrf_blend(
            {"semantic": semantic, "lexical": lexical}, top_n=limit
        )
        return self._hydrate(blended)

    async def search_document(
        self,
        query: str,
        *,
        tenant_id: str,
        library_id: str,
        document_id: str,
        top_k: int = 5,
    ) -> list[KnowledgeHit]:
        """Async façade over :meth:`search_document_sync`, run off the event loop."""
        return await asyncio.to_thread(
            self.search_document_sync,
            query,
            tenant_id=tenant_id,
            library_id=library_id,
            document_id=document_id,
            top_k=top_k,
        )

    def search_document_sync(
        self,
        query: str,
        *,
        tenant_id: str,
        library_id: str,
        document_id: str,
        top_k: int = 5,
    ) -> list[KnowledgeHit]:
        """Hybrid-search only the active version of one explicitly scoped document."""
        if self._store.get_library_for_tenant(library_id, tenant_id) is None:
            return []
        limit = self._limit(top_k)
        semantic, lexical = self._raw_layers(
            library_id, query, limit, document_id=document_id
        )
        blended = self._rrf_blend(
            {"semantic": semantic, "lexical": lexical}, top_n=limit
        )
        return [
            hit
            for hit in self._hydrate(blended)
            if hit.document_id == document_id
        ]

    def _raw_layers(
        self,
        library_id: str,
        query: str,
        limit: int,
        *,
        document_id: str | None = None,
    ) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        """Collect the raw per-layer (semantic, lexical) results for one library.

        Factored out so :meth:`search_all` can pool raw layers across many
        libraries into a single RRF pass (fusing already-blended per-library
        results would double-count the RRF contribution). Meaning search
        reads only vectors the active embedder made (KB-E03).
        """
        semantic: list[tuple[str, float]] = []
        vs = self._vector_store_for(library_id)
        if vs.available:
            conditions: list[dict[str, Any]] = [{"library_id": library_id}, {"model": vs.identity}]
            if document_id is not None:
                conditions.append({"document_id": document_id})
            try:
                raw = vs.query(query, limit=limit * 4, where={"$and": conditions})
                semantic = [(doc_id, score) for doc_id, score in raw]
            except Exception as exc:
                logger.warning("[KnowledgeIndex] semantic query failed: %s", exc)
        lexical = self._store.fts_search(
            query, library_id, limit=limit * 4, document_id=document_id
        )
        return semantic, lexical

    def _hydrate(self, blended: list[tuple[str, float]]) -> list[KnowledgeHit]:
        """Join RRF-ranked chunk IDs back to full content + provenance."""
        if not blended:
            return []
        chunk_ids = [chunk_id for chunk_id, _score in blended]
        full = self._store.get_chunks_by_ids(chunk_ids)
        # Preserve RRF order; drop any ID that has no row (shouldn't happen).
        hits: list[KnowledgeHit] = []
        for chunk_id, score in blended:
            row = full.get(chunk_id)
            if not row:
                continue
            hits.append(
                KnowledgeHit(
                    chunk_id=chunk_id,
                    content=row["content"],
                    score=score,
                    library_id=row["library_id"],
                    source_url=row["source_url"],
                    document_title=row["document_title"],
                    section_header=row["section_header"],
                    chunk_index=int(row["chunk_index"]),
                    has_code=bool(row["has_code"]),
                    metadata=dict(row.get("metadata") or {}),
                    document_id=row.get("document_id"),
                    version_id=row.get("version_id"),
                    citation_label=str(
                        (row.get("metadata") or {}).get("citation_label")
                        or row["section_header"]
                    ),
                )
            )
        return hits

    async def search_across(
        self, query: str, library_ids: list[str], *, top_k: int = 5
    ) -> dict[str, list[KnowledgeHit]]:
        """Search multiple libraries, returning per-library hits (no fusion).

        Each library is queried and RRF-blended *independently*; the results
        are returned as a dict keyed by library_id.  Use this when you want
        explicit per-library attribution.  For a single fused ranking across
        libraries, use :meth:`search_all`.
        """
        out: dict[str, list[KnowledgeHit]] = {}
        for lib_id in library_ids:
            out[lib_id] = await self.search(query, lib_id, top_k=top_k)
        return out

    async def search_all(
        self, query: str, library_ids: list[str], *, top_k: int = 5
    ) -> list[KnowledgeHit]:
        """Cross-library search with a single fused RRF pass (async façade, off the loop)."""
        return await asyncio.to_thread(self.search_all_sync, query, library_ids, top_k=top_k)

    def search_all_sync(
        self, query: str, library_ids: list[str], *, top_k: int = 5
    ) -> list[KnowledgeHit]:
        """Cross-library search with a single fused RRF pass.

        Unlike :meth:`search_across`, this pools the raw per-layer (semantic
        + lexical) results from every library into ONE RRF blend, so a hit
        that ranks high across multiple libraries/layers gets the combined
        score.  Fusing already-blended per-library results would double-count
        the RRF contribution, which is why we go back to the raw layers here.

        Sync entry point used by federated search + inject (industry single path).

        Returns a single ranked :class:`KnowledgeHit` list, best first.
        """
        if not query or not query.strip() or not library_ids:
            return []
        limit = self._limit(top_k)
        pooled: dict[str, list[tuple[str, float]]] = {}
        for lib_id in library_ids:
            semantic, lexical = self._raw_layers(lib_id, query, limit)
            if semantic:
                # Per-library layer names keep contributions from colliding
                # in the fusion map (chunk ids are globally unique).
                pooled[f"semantic:{lib_id}"] = semantic
            if lexical:
                pooled[f"lexical:{lib_id}"] = lexical
        if not pooled:
            return []
        blended = self._rrf_blend(pooled, top_n=limit)
        return self._hydrate(blended)

    # ------------------------------------------------------------------
    # RRF
    # ------------------------------------------------------------------

    @staticmethod
    def _rrf_blend(
        layers: dict[str, list[tuple[str, float]]],
        top_n: int,
    ) -> list[tuple[str, float]]:
        """Reciprocal Rank Fusion across an arbitrary number of layers.

        Each layer is a list of ``(chunk_id, raw_score)`` already sorted
        best-first within that layer.  We rank each layer independently,
        sum ``1 / (k + rank)``, and return the top_n by fused score.
        """
        fused: dict[str, float] = {}
        for _layer_name, scored in layers.items():
            for rank, (chunk_id, _raw) in enumerate(scored, start=1):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (_RRF_K + rank)
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_n]

    # ------------------------------------------------------------------
    # Health / introspection
    # ------------------------------------------------------------------

    def health(self, library_id: str) -> dict[str, Any]:
        """A library's meaning-search coverage, as the repair sweep last
        recorded it, without opening the vector store or loading the
        embedder (KB-S15: listing the libraries created collections and
        loaded the model).

        ``vector_chunks`` counts chunks whose vector carries the active
        embedder's identity (KB-E03) -- raw collection counts reported
        "N of N" over vectors a previous model made. ``state`` is
        ``unknown`` (not checked yet), ``ok``, ``rebuilding`` (fewer than
        all), ``unavailable`` or ``failed``.
        """
        chunks = self._store.count_chunks(library_id)
        status = self._store.vector_status(library_id)
        if status is None:
            covered, state, checked = 0, "unknown", None
        else:
            covered = min(int(status["covered"]), chunks)
            state = status["state"]
            if state == "ok" and covered < chunks:
                state = "rebuilding"
            checked = status["checked_at"]
        return {
            "library_id": library_id,
            "vector_available": state not in ("unavailable", "failed", "unknown"),
            "state": state,
            "sqlite_chunks": chunks,
            "vector_chunks": covered if state not in ("unavailable", "failed") else 0,
            "checked_at": checked,
            "keyword_index_ok": self._store.fts_problem is None,
            "persist_dir": self._persist_dir,
        }

    def backfill_vectors(self, *, time_budget_s: float = 300.0, batch: int = 16) -> dict[str, Any]:
        """Make each library's vectors match its chunks; one bounded, resumable pass.

        Embeds every active chunk whose vector is missing or was made by
        another embedder (its ``model`` tag is not the active identity,
        KB-E03), and removes every vector with no active chunk behind it.
        The shortest chunks go first, across all libraries. Each library is
        handled on its own (KB-E11): one library whose collection fails is
        recorded in ``failed`` and the others are still repaired. Each
        library's coverage -- chunks whose vector carries the active
        identity -- is recorded for :meth:`health`.
        """
        deadline = time.monotonic() + max(1.0, float(time_budget_s))
        step = max(1, int(batch))
        report: dict[str, Any] = {"libraries": 0, "embedded": 0, "removed": 0, "missing": 0,
                                  "unavailable": False, "complete": True, "failed": {},
                                  "coverage": {}, "identity": ""}
        stores: dict[str, VectorStore] = {}
        identities: dict[str, str] = {}
        todo: list[tuple[int, str, str]] = []  # (characters, library, chunk id)
        for library_id in self._store.all_library_ids():
            if time.monotonic() >= deadline:
                report["complete"] = False
                break
            try:
                vs = self._vector_store_for(library_id)
                if not vs.available:
                    report["unavailable"] = True
                    report["complete"] = False
                    chunks = self._store.count_chunks(library_id)
                    self._store.set_vector_status(library_id, identity="", chunks=chunks, covered=0,
                                                  state="unavailable")
                    continue
                identity = vs.identity
                report["identity"] = identity
                report["libraries"] += 1
                # Vectors first: a chunk ingested between the two reads is then
                # embedded twice (harmless), never taken for a retired one.
                tags = vs.models()
                sizes = self._store.active_chunk_sizes(library_id)
                report["removed"] += vs.delete_many(sorted(set(tags) - set(sizes)))
                current = {cid for cid, made_by in tags.items() if made_by == identity}
                covered = len(current & set(sizes))
                todo += [(sizes[cid], library_id, cid) for cid in set(sizes) - current]
                stores[library_id] = vs
                identities[library_id] = identity
                report["coverage"][library_id] = [covered, len(sizes)]
            except Exception as exc:
                report["failed"][library_id] = f"{type(exc).__name__}: {exc}"[:300]
                report["complete"] = False
                logger.warning("[knowledge] meaning-vector repair of library %s failed: %s",
                               library_id, exc)
        todo.sort()
        report["missing"] = len(todo)
        for start in range(0, len(todo), step):
            if time.monotonic() >= deadline:
                report["complete"] = False
                break
            by_library: dict[str, list[str]] = {}
            for _size, library_id, chunk_id in todo[start:start + step]:
                if library_id not in report["failed"]:
                    by_library.setdefault(library_id, []).append(chunk_id)
            for library_id, chunk_ids in by_library.items():
                try:
                    rows = self._store.get_chunks_by_ids(chunk_ids)
                    done = stores[library_id].index_many([
                        (chunk_id, row.get("content") or "",
                         self._chunk_vector_metadata(library_id, row, identities[library_id]))
                        for chunk_id, row in rows.items()
                    ])
                except Exception as exc:
                    report["failed"][library_id] = f"{type(exc).__name__}: {exc}"[:300]
                    report["complete"] = False
                    logger.warning("[knowledge] embedding chunks of library %s failed: %s",
                                   library_id, exc)
                    continue
                report["embedded"] += done
                report["missing"] -= done
                report["coverage"][library_id][0] += done
        for library_id, (covered, chunks) in report["coverage"].items():
            failed = report["failed"].get(library_id, "")
            state = "failed" if failed else "ok" if covered >= chunks else "rebuilding"
            try:
                self._store.set_vector_status(library_id, identity=identities.get(library_id, ""),
                                              chunks=chunks, covered=covered, state=state,
                                              error=failed)
            except Exception:
                logger.debug("[knowledge] coverage not recorded for %s", library_id, exc_info=True)
        for library_id, error in report["failed"].items():
            if library_id not in report["coverage"]:
                try:
                    self._store.set_vector_status(library_id, identity="", chunks=0, covered=0,
                                                  state="failed", error=error)
                except Exception:
                    logger.debug("[knowledge] coverage not recorded for %s", library_id, exc_info=True)
        if report["embedded"] or report["removed"]:
            logger.info(
                "[knowledge] meaning vectors: %d chunks embedded, %d retired vectors removed (%s)",
                report["embedded"],
                report["removed"],
                "all libraries done" if report["complete"] else "more next pass",
            )
        # An outage is said once when it starts and once when it ends
        # (KB-E11): it returned "unavailable" with no log at any level.
        with _SWEEP_LOCK:
            was_unavailable = _SWEEP["unavailable"]
            _SWEEP["unavailable"] = bool(report["unavailable"])
        if report["unavailable"] and not was_unavailable:
            logger.warning("[knowledge] meaning search is unavailable: libraries answer by keyword "
                           "only until the vector store is back")
        elif was_unavailable and not report["unavailable"] and report["libraries"]:
            logger.warning("[knowledge] meaning search is available again")
        return report

    @staticmethod
    def _chunk_vector_metadata(library_id: str, chunk: dict[str, Any],
                               identity: str = "") -> dict[str, Any]:
        """What the vector store keeps beside a chunk; Chroma refuses None values.

        Every write goes through here -- ingest, document publish, the repair
        -- so search can filter any vector by library, document and the
        identity of the embedder that made it (``model``, KB-E03).
        """
        meta = {
            "model": identity,
            "library_id": library_id,
            "source_url": chunk.get("source_url"),
            "section_header": chunk.get("section_header"),
            "document_title": chunk.get("document_title"),
            "chunk_index": int(chunk.get("chunk_index") or 0),
            "content_hash": chunk.get("content_hash"),
            "document_id": chunk.get("document_id"),
            "version_id": chunk.get("version_id"),
        }
        extra = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        for key in ("page_start", "page_end"):
            if extra.get(key) is not None:
                meta[key] = int(extra[key])
        if extra.get("citation_label"):
            meta["citation_label"] = str(extra["citation_label"])
        return {k: v for k, v in meta.items() if v is not None and v != ""}


# ══════════════════════════════════════════════════════════════════════════
# One index per store (KB-E12)
# ══════════════════════════════════════════════════════════════════════════

_INDEXES: weakref.WeakKeyDictionary[KnowledgeStore, KnowledgeIndex] = weakref.WeakKeyDictionary()
_INDEXES_LOCK = threading.Lock()


def knowledge_index_for(store: KnowledgeStore) -> KnowledgeIndex:
    """The one :class:`KnowledgeIndex` of *store*, shared by the Knowledge
    Library and the Documents platform (KB-E12: Documents built its own and
    kept dead collection handles after the other dropped them)."""
    with _INDEXES_LOCK:
        index = _INDEXES.get(store)
        if index is None:
            index = KnowledgeIndex(store)
            _INDEXES[store] = index
        return index


def get_knowledge_index() -> KnowledgeIndex:
    """Return the shared :class:`KnowledgeIndex` of the shared store."""
    return knowledge_index_for(get_knowledge_store())


def reset_knowledge_index() -> None:
    """Drop the cached indexes (used by test teardown)."""
    with _INDEXES_LOCK:
        _INDEXES.clear()


# ══════════════════════════════════════════════════════════════════════════
# The repair sweep's report: metrics, warnings, alerts (KB-E11)
# ══════════════════════════════════════════════════════════════════════════

#: Passes of unavailable or failing meaning search before the ops alert.
_ALERT_AFTER_PASSES = 3
_SWEEP: dict[str, Any] = {"report": None, "unavailable": False, "bad_passes": 0, "at": None}
_SWEEP_LOCK = threading.Lock()


def _last_repair_report() -> dict[str, Any] | None:
    """The last repair sweep's report and when it ran (None before the
    first): the metrics route, readiness and the digest read it."""
    with _SWEEP_LOCK:
        if _SWEEP["report"] is None:
            return None
        return {**_SWEEP["report"], "ran_at": _SWEEP["at"], "bad_passes": _SWEEP["bad_passes"]}


def _label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def repair_metric_lines() -> list[str]:
    """Prometheus lines from the last repair sweep (KB-E11); empty before
    the first pass. ``/metrics`` appends them."""
    report = _last_repair_report()
    if report is None:
        return []
    lines = [
        "# HELP kazma_kb_vector_store_unavailable 1 when the last knowledge vector repair "
        "found the vector store unavailable",
        "# TYPE kazma_kb_vector_store_unavailable gauge",
        f"kazma_kb_vector_store_unavailable {int(bool(report['unavailable']))}",
        "# HELP kazma_kb_vector_bad_passes Knowledge vector repair passes in a row that were "
        "unavailable or failed",
        "# TYPE kazma_kb_vector_bad_passes gauge",
        f"kazma_kb_vector_bad_passes {int(report['bad_passes'])}",
        "# HELP kazma_kb_vector_failed_libraries Libraries the last repair pass could not repair",
        "# TYPE kazma_kb_vector_failed_libraries gauge",
        f"kazma_kb_vector_failed_libraries {len(report['failed'])}",
        "# HELP kazma_kb_vector_missing Chunks still waiting for a vector of the active embedder",
        "# TYPE kazma_kb_vector_missing gauge",
        f"kazma_kb_vector_missing {int(report['missing'])}",
        "# HELP kazma_kb_keyword_index_ok 1 when the keyword index matched the chunks at the "
        "last pass",
        "# TYPE kazma_kb_keyword_index_ok gauge",
        f"kazma_kb_keyword_index_ok {int(bool((report.get('fts') or {}).get('ok', True)))}",
        "# HELP kazma_kb_vector_last_pass_timestamp_seconds When the last repair pass ran",
        "# TYPE kazma_kb_vector_last_pass_timestamp_seconds gauge",
        f"kazma_kb_vector_last_pass_timestamp_seconds {float(report['ran_at'] or 0):.0f}",
    ]
    coverage = report.get("coverage") or {}
    if coverage:
        lines += [
            "# HELP kazma_kb_vector_coverage_ratio Share of a library's chunks whose vector the "
            "active embedder made",
            "# TYPE kazma_kb_vector_coverage_ratio gauge",
        ]
        for library_id, (covered, chunks) in sorted(coverage.items()):
            ratio = 1.0 if not chunks else min(1.0, covered / chunks)
            lines.append(f'kazma_kb_vector_coverage_ratio{{library="{_label(library_id)}"}} {ratio:.4f}')
    return lines


def vector_readiness() -> dict[str, Any]:
    """The readiness line for Knowledge meaning search (KB-E11, KB-E07).

    Read from the last repair pass (no store is opened). ``degraded`` while
    the vector store is unavailable, a library failed to repair or the
    keyword index did not match its chunks; ``ok`` otherwise, with the
    chunks still waiting for a vector of the active embedder. The Knowledge
    Library is one install's: ``topology`` says so.
    """
    out: dict[str, Any] = {"component": "knowledge_vectors", "topology": "single_install"}
    report = _last_repair_report()
    if report is None:
        return {**out, "status": "ok", "detail": "not checked yet"}
    fts_ok = bool((report.get("fts") or {}).get("ok", True))
    problems = []
    if report["unavailable"]:
        problems.append("the vector store is unavailable")
    if report["failed"]:
        problems.append("libraries failed to repair: " + ", ".join(sorted(report["failed"])))
    if not fts_ok:
        problems.append("the keyword index did not match the chunks (rebuilt)")
    out.update(missing=int(report["missing"]), bad_passes=int(report["bad_passes"]),
               checked_at=report["ran_at"])
    if problems:
        return {**out, "status": "degraded", "error": "; ".join(problems)}
    detail = (f"{report['missing']} chunk(s) waiting for a vector" if report["missing"]
              else "every chunk has a vector of the active embedder")
    return {**out, "status": "ok", "detail": detail}


def digest_problems() -> dict[str, int]:
    """What the daily digest lists under "Needs attention" for the
    Knowledge Library (KB-E11): nothing while the last pass was clean."""
    report = _last_repair_report()
    if report is None:
        return {}
    out: dict[str, int] = {}
    if report["unavailable"]:
        out["knowledge meaning-search passes without a vector store"] = int(report["bad_passes"])
    if report["failed"]:
        out["knowledge libraries whose vectors could not be repaired"] = len(report["failed"])
    if not bool((report.get("fts") or {}).get("ok", True)):
        out["knowledge keyword index rebuilt after drifting"] = 1
    return out


def run_repair_sweep(*, time_budget_s: float = 300.0) -> dict[str, Any]:
    """One pass of the knowledge vector repair, observed (KB-E11).

    The report used to be discarded: a vector store down for days, or
    chunks no embedder could make, left no log, metric, alert or readiness
    line, and the outage lasted two months once. Now: the keyword index's
    coverage is checked and rebuilt when it drifted; the first unavailable
    pass and the recovery are WARNINGs; three bad passes in a row raise the
    ops alert ``knowledge.meaning_search``; and a library that failed makes
    the pass raise, so the maintenance runner logs it at WARNING.
    """
    index = get_knowledge_index()
    store = index.store
    fts = store.fts_coverage()
    if not fts["ok"]:
        logger.warning(
            "[knowledge] the keyword index does not match the chunks (missing=%d stale=%d%s); "
            "rebuilding it", fts["missing"], fts["stale"],
            f", error: {fts['error']}" if fts["error"] else "",
        )
        store.rebuild_fts(reason="coverage check")
    report = index.backfill_vectors(time_budget_s=time_budget_s)
    report["fts"] = fts
    bad = bool(report["unavailable"] or report["failed"])
    with _SWEEP_LOCK:
        _SWEEP["report"] = report
        _SWEEP["at"] = time.time()
        _SWEEP["bad_passes"] = _SWEEP["bad_passes"] + 1 if bad else 0
        bad_passes = _SWEEP["bad_passes"]
    if bad_passes >= _ALERT_AFTER_PASSES:
        try:
            from kazma_core.observability.ops_alerts import alert

            what = ("the vector store is unavailable" if report["unavailable"]
                    else f"{len(report['failed'])} library(ies) failed to repair")
            alert("knowledge.meaning_search",
                  "Knowledge Library meaning search is degraded",
                  f"{what} for {bad_passes} passes; searches use keyword matching only.",
                  severity="warn", cooldown_s=6 * 3600)
        except Exception:
            logger.debug("[knowledge] meaning-search alert not raised", exc_info=True)
    if report["failed"]:
        raise RuntimeError(
            "knowledge vector repair failed for " + ", ".join(sorted(report["failed"]))
        )
    return report


# ══════════════════════════════════════════════════════════════════════════
# Auto-inject
# ══════════════════════════════════════════════════════════════════════════
#
# When a library has ``auto_inject = 1``, its top-k chunks for the user's
# latest message are folded into the turn's first model call by the
# supervisor -- the ONE injection site (Knowledge audit KB-S08: the
# transports injected the same chunks a second time). Two invariants
# (mirror the self-improvement Soul, AGENTS.md §11):
#
#   1. **Kill switch is checked live, per-turn.**  ``KAZMA_KB_AUTO_INJECT=0``
#      disables the whole subsystem at runtime without a restart.  Default
#      is ON (no env var = enabled) — but per-library ``auto_inject`` must
#      ALSO be true, so the *behaviour* is strictly opt-in: nothing is
#      injected until the user flips the toggle on a library.
#
#   2. **Every injected chunk goes through the prompt fence.**  Doc content
#      is untrusted (a malicious page could carry prompt injection).  The
#      caller wraps the block via
#      ``safety.prompt_fence.format_untrusted_block``.


def kb_auto_inject_enabled() -> bool:
    """Global kill switch: ``KAZMA_KB_AUTO_INJECT=0|false|off`` disables auto-inject.

    Mirrors ``self_improvement.self_improvement_enabled()``: read live on
    every call (per-turn), default ON.  Per-library opt-in is checked
    separately inside :func:`build_knowledge_inject`.
    """
    raw = (os.environ.get("KAZMA_KB_AUTO_INJECT") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "disabled")


def kb_smart_search_enabled() -> bool:
    """When true, inject from **all active libraries with chunks** (not only
    ``auto_inject=1``), if the user message looks technical / doc-related.

    Env: ``KAZMA_KB_SMART_SEARCH=1`` or ConfigStore ``knowledge.smart_search``.
    Still respects the global kill switch and tenant/archive filters.
    """
    raw = (os.environ.get("KAZMA_KB_SMART_SEARCH") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        from kazma_core.config_store import get_config_store

        v = get_config_store().get("knowledge.smart_search")
        if v is not None:
            return bool(v)
    except Exception:
        pass
    return False


_TECHNICAL_RE = re.compile(
    r"\b("
    r"api|sdk|endpoint|webhook|oauth|jwt|http|rest|graphql|"
    r"how\s+do\s+i|how\s+to|documentation|docs|configure|"
    r"error|exception|parameter|payload|schema|tutorial|"
    r"install|authenticate|authorization|rate\s*limit"
    r")\b",
    re.I,
)


def _looks_technical(msg: str) -> bool:
    if not msg or len(msg.strip()) < 8:
        return False
    return bool(_TECHNICAL_RE.search(msg))


# Per-injection top-k.  Keeps the prompt footprint bounded; 3 chunks of
# ≤2000 chars each.  Tunable via env.
def _auto_inject_top_k() -> int:
    raw = (os.environ.get("KAZMA_KB_AUTO_INJECT_TOP_K") or "3").strip()
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 3


@dataclass(slots=True)
class KnowledgeInject:
    """What one turn injects: the raw (unfenced) block and EXACTLY the hits
    it holds, in its order -- the turn's "Memory used" record lists these
    and nothing else (KB-S02)."""

    block: str = ""
    hits: list[dict[str, Any]] = field(default_factory=list)


def build_knowledge_inject(user_message: str) -> KnowledgeInject:
    """The Knowledge block for the latest user message (blocking: callers
    on the loop use ``asyncio.to_thread``).

    One hybrid RRF retrieval over the inject-scoped libraries (auto-inject
    ones, plus every active library under smart search for a technical
    message), ranked on its own scale with its own budget. Empty when the
    kill switch is off, no library opted in, or nothing matched.
    """
    if not kb_auto_inject_enabled():
        return KnowledgeInject()
    msg = (user_message or "").strip()
    if not msg:
        return KnowledgeInject()
    from kazma_core.memory.federated_search import _search_knowledge, format_kb_hits_for_prompt

    top_k = _auto_inject_top_k()
    hits = [h for h in _search_knowledge(msg, limit=top_k, mode="inject", user_message=msg)
            if h.get("store") == "knowledge"][:top_k]
    if not hits:
        return KnowledgeInject()
    block = format_kb_hits_for_prompt(hits, max_hits=top_k)
    if not block:
        return KnowledgeInject()
    cited_libs = sorted({str((h.get("provenance") or {}).get("library_id") or "") for h in hits} - {""})
    if len(cited_libs) == 1:
        lib_footer = f'📚 This data is from Knowledge "{cited_libs[0]}".'
    elif cited_libs:
        lib_footer = ("📚 This data is from Knowledge libraries: "
                      + ", ".join(f'"{library}"' for library in cited_libs) + ".")
    else:
        lib_footer = "📚 This data is from the Knowledge Library."
    return KnowledgeInject(
        block=block + "\n" + lib_footer
        + "\nWhen you use this material in your answer, append the library footer verbatim.",
        hits=hits,
    )
