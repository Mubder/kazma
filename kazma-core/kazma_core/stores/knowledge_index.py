"""Knowledge Index — per-library vector + lexical retrieval with RRF.

This module is the **retrieval engine** for Knowledge Libraries.  It is
deliberately decoupled from chat memory:

- Each library gets its own ChromaDB collection ``kazma_kb_<library_id>``,
  completely separate from the ``agent_memory`` collection the chat memory
  system uses (``swarm/memory/adapter.py``).  Knowledge never leaks into
  chat recall and vice-versa.
- We reuse the :class:`~kazma_core.swarm.memory.vector.VectorStore` *class*
  and the shared :func:`~kazma_core.swarm.memory.embedder.get_embedder`
  singleton, so embeddings stay consistent with the rest of the system and
  the model is never loaded twice — but we do **not** route through the
  shared ``UnifiedMemoryAdapter`` (its L1 is the ``agent_memory``
  collection, its L3 FTS5 layer doesn't reliably filter by metadata, and
  every layer keys on a bare ``sha256(text)[:16]`` which collides on
  identical sections across pages).

Retrieval is a two-layer blend:

    query  ─► ChromaDB cosine  ─┐
           ─► FTS5 BM25 (lib)  ─┤
                                 ├─► RRF (k=60) ─► join SQLite ─► KnowledgeHit[]

The :class:`~kazma_core.stores.knowledge.KnowledgeStore` (SQLite) is the
source of truth for full content; ChromaDB holds the vector + a ≤2000-char
preview (``VectorStore.index`` truncates at ``vector.py:150``), and FTS5
holds a tokenized copy.  At retrieval time the RRF blender produces a
ranked list of chunk IDs, then we join back to SQLite for the full content
and provenance.  This means retrieval still works (via FTS5) even if
ChromaDB is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from kazma_core.paths import vector_memory_path
from kazma_core.stores.knowledge import KnowledgeStore, get_knowledge_store
from kazma_core.memory.vector_store_global import VectorStore

__all__ = [
    "KnowledgeHit",
    "KnowledgeIndex",
    "get_knowledge_auto_inject_block",
    "get_knowledge_index",
    "kb_auto_inject_enabled",
    "kb_smart_search_enabled",
    "reset_knowledge_index",
]

logger = logging.getLogger(__name__)

# Reciprocal Rank Fusion smoothing constant.  Matches ``adapter.py`` so the
# fusion math is identical to the proven 4-layer memory blender.
_RRF_K = 60


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


class KnowledgeIndex:
    """Per-library vector + lexical retrieval with RRF blending.

    Use :func:`get_knowledge_index` for the process-wide singleton.
    """

    def __init__(self, store: KnowledgeStore | None = None) -> None:
        self._store = store or get_knowledge_store()
        # Lazy per-library VectorStore cache.  Created on first access for a
        # given library_id and reused thereafter.
        self._vector_stores: dict[str, VectorStore] = {}
        self._persist_dir = vector_memory_path()

    # ------------------------------------------------------------------
    # Vector store management
    # ------------------------------------------------------------------

    def _vector_store_for(self, library_id: str) -> VectorStore:
        """Return the per-library ChromaDB VectorStore (created lazily).

        Each library is a separate ChromaDB collection named
        ``kazma_kb_<library_id>``, persisted under the shared vector memory
        path.  This guarantees hard isolation from chat memory
        (``agent_memory``) and makes library deletion a single
        ``drop_collection``.
        """
        if library_id in self._vector_stores:
            return self._vector_stores[library_id]
        # Sanitise the library id for use as a Chroma collection name
        # (alphanumerics + underscore + hyphen only).
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in library_id)
        vs = VectorStore(
            collection_name=f"kazma_kb_{safe}",
            persist_dir=self._persist_dir,
        )
        self._vector_stores[library_id] = vs
        return vs

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def purge_source(self, library_id: str, source_url: str) -> int:
        """Remove all SQLite/FTS + Chroma rows for one URL before re-ingest.

        Prevents orphan chunk indices when a page shrinks on refresh.
        """
        if not source_url:
            return 0
        old_ids = self._store.list_chunk_ids_for_source(library_id, source_url)
        n = self._store.delete_chunks_for_source(library_id, source_url)
        vs = self._vector_store_for(library_id)
        if vs.available and old_ids:
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
        """Index a batch of chunks for one library (smart re-index).

        Each chunk dict carries: ``id``, ``content``, ``content_hash``,
        ``source_url``, ``document_title``, ``section_header``,
        ``chunk_index``, ``has_code`` (see ``chunk_to_dict``).

        Per ``source_url``:

        * If the ordered list of ``content_hash`` values matches what is
          already stored → **skip** (no purge, no embed). Cheap refresh.
        * If changed (or ``force=True``) → purge that URL first (orphan-safe
          shrink), then upsert + Chroma only for written rows.

        Returns ``(new_count, skipped_count)`` where skipped includes both
        unchanged full pages and per-chunk dedup.
        """
        if not chunks:
            return (0, 0)

        # Group by source URL for page-level fingerprint compare
        by_url: dict[str, list[dict[str, Any]]] = {}
        for c in chunks:
            url = str(c.get("source_url") or "").strip()
            if not url:
                continue
            by_url.setdefault(url, []).append(c)

        vs = self._vector_store_for(library_id)
        new_count = 0
        skipped = 0
        wrote_chunks: list[dict[str, Any]] = []
        pages_unchanged = 0
        pages_updated = 0

        for url, url_chunks in by_url.items():
            url_chunks = sorted(
                url_chunks, key=lambda x: int(x.get("chunk_index") or 0)
            )
            new_hashes = [str(c.get("content_hash") or "") for c in url_chunks]
            if not force:
                try:
                    old_hashes = self._store.list_source_content_hashes(
                        library_id, url
                    )
                except Exception:
                    old_hashes = []
                if old_hashes and old_hashes == new_hashes:
                    skipped += len(url_chunks)
                    pages_unchanged += 1
                    logger.debug(
                        "[KnowledgeIndex] unchanged page skip library=%s url=%s (%d chunks)",
                        library_id,
                        url[:80],
                        len(url_chunks),
                    )
                    continue

            # Changed page (or first ingest): purge orphans then write
            try:
                self.purge_source(library_id, url)
            except Exception as exc:
                logger.warning(
                    "[KnowledgeIndex] purge failed library=%s url=%s: %s",
                    library_id,
                    url[:80],
                    exc,
                )
            pages_updated += 1

            for chunk in url_chunks:
                try:
                    wrote = self._store.upsert_chunk(chunk)
                except Exception as exc:
                    logger.warning(
                        "[KnowledgeIndex] upsert failed library=%s url=%s idx=%s: %s",
                        library_id,
                        url[:80],
                        chunk.get("chunk_index"),
                        exc,
                    )
                    skipped += 1
                    continue
                if wrote:
                    new_count += 1
                    wrote_chunks.append(chunk)
                else:
                    skipped += 1

        # ChromaDB: only embed chunks that actually changed
        if vs.available:
            for chunk in wrote_chunks:
                if not chunk.get("content"):
                    continue
                try:
                    vs.index(
                        doc_id=chunk["id"],
                        text=chunk["content"],
                        metadata={
                            "library_id": library_id,
                            "source_url": chunk.get("source_url", ""),
                            "section_header": chunk.get("section_header", ""),
                            "document_title": chunk.get("document_title", ""),
                            "chunk_index": int(chunk.get("chunk_index", 0)),
                            "content_hash": chunk.get("content_hash", ""),
                        },
                    )
                except Exception as exc:
                    logger.warning(
                        "[KnowledgeIndex] ChromaDB index failed for %s: %s",
                        chunk.get("id"),
                        exc,
                    )
        elif wrote_chunks:
            logger.info(
                "[KnowledgeIndex] ChromaDB unavailable — chunks stored in SQLite+FTS5 only"
            )

        total = self._store.count_chunks(library_id)
        self._store.set_chunk_count(library_id, total)
        logger.info(
            "[KnowledgeIndex] Indexed library=%s: %d new, %d skipped "
            "(%d pages updated, %d pages unchanged), %d total",
            library_id,
            new_count,
            skipped,
            pages_updated,
            pages_unchanged,
            total,
        )
        return (new_count, skipped)

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
        """Drop the ChromaDB collection + all SQLite/FTS5 rows for a library."""
        vs = self._vector_stores.pop(library_id, None)
        # Best-effort collection drop.  ChromaDB exposes ``delete_collection``
        # on the client; VectorStore keeps it private so we reach in via the
        # underlying client.  Safe to ignore if unavailable.
        if vs is not None:
            try:
                client = getattr(vs, "_client", None)
                if client is not None and vs._ensure_client():
                    col_name = getattr(vs, "_collection_name", "")
                    if col_name:
                        try:
                            client.delete_collection(col_name)
                            logger.info("[KnowledgeIndex] Dropped collection %s", col_name)
                        except Exception as exc:
                            logger.debug(
                                "[KnowledgeIndex] delete_collection failed: %s", exc
                            )
            except Exception as exc:
                logger.debug("[KnowledgeIndex] vector teardown failed: %s", exc)
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
    ) -> list[KnowledgeHit]:
        """Semantic + lexical search scoped to one library, RRF-blended.

        Args:
            query:      Natural-language query.
            library_id: Library to search (hard scoping).
            top_k:      Maximum hits to return.

        Returns:
            Ranked :class:`KnowledgeHit` list, best first.
        """
        if not query or not query.strip():
            return []
        limit = max(1, int(top_k or 5))

        semantic, lexical = self._raw_layers(library_id, query, limit)
        if not semantic and not lexical:
            return []

        # ── RRF blend ───────────────────────────────────────────────────
        blended = self._rrf_blend(
            {"semantic": semantic, "lexical": lexical}, top_n=limit
        )
        return self._hydrate(blended)

    def _raw_layers(
        self, library_id: str, query: str, limit: int
    ) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        """Collect the raw per-layer (semantic, lexical) results for one library.

        Factored out so :meth:`search_all` can pool raw layers across many
        libraries into a single RRF pass (fusing already-blended per-library
        results would double-count the RRF contribution).
        """
        semantic: list[tuple[str, float]] = []
        vs = self._vector_store_for(library_id)
        if vs.available:
            try:
                raw = vs.query(
                    query,
                    limit=limit * 4,
                    where={"library_id": library_id},
                )
                semantic = [(doc_id, score) for doc_id, score in raw]
            except Exception as exc:
                logger.warning("[KnowledgeIndex] semantic query failed: %s", exc)
        lexical = self._store.fts_search(query, library_id, limit=limit * 4)
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
        """Cross-library search with a single fused RRF pass (async façade)."""
        return self.search_all_sync(query, library_ids, top_k=top_k)

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
        limit = max(1, int(top_k or 5))
        pooled: dict[str, list[tuple[str, float]]] = {}
        for lib_id in library_ids:
            semantic, lexical = self._raw_layers(lib_id, query, limit)
            if semantic:
                # Per-library layer names keep contributions from colliding
                # in the fusion map (chunk_ids are already globally unique
                # via ``"{library_id}:{hash}"``, so dedup is automatic).
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

        Identical to the math in ``adapter.py:_rrf_blend`` but without the
        BM25 sign-flip (we already sort FTS5 ascending at the store layer).
        """
        fused: dict[str, float] = {}
        for _layer_name, scored in layers.items():
            # Defensive: ensure best-first.  Semantic cosine → desc;
            # lexical BM25 → we trust the store's ascending sort.
            for rank, (chunk_id, _raw) in enumerate(scored, start=1):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (_RRF_K + rank)
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_n]

    # ------------------------------------------------------------------
    # Health / introspection
    # ------------------------------------------------------------------

    def health(self, library_id: str) -> dict[str, Any]:
        """Report availability + counts for a library's retrieval backends."""
        vs = self._vector_store_for(library_id)
        sqlite_count = self._store.count_chunks(library_id)
        return {
            "library_id": library_id,
            "vector_available": bool(vs.available),
            "sqlite_chunks": sqlite_count,
            "persist_dir": self._persist_dir,
        }


# ══════════════════════════════════════════════════════════════════════════
# Process-wide singleton
# ══════════════════════════════════════════════════════════════════════════

_knowledge_index: KnowledgeIndex | None = None


def get_knowledge_index() -> KnowledgeIndex:
    """Return the shared :class:`KnowledgeIndex` singleton."""
    global _knowledge_index
    if _knowledge_index is None:
        _knowledge_index = KnowledgeIndex()
    return _knowledge_index


def reset_knowledge_index() -> None:
    """Drop the singleton reference (used by test teardown)."""
    global _knowledge_index
    _knowledge_index = None


# ══════════════════════════════════════════════════════════════════════════
# Auto-inject (Phase 2)
# ══════════════════════════════════════════════════════════════════════════
#
# When a library has ``auto_inject = 1``, its top-k chunks for the user's
# latest message are folded into the system prompt at the 3 injection sites
# (agent_runner init, sse_chat per-turn, gateway graph.py per-turn).  This
# is what makes the agent "just know" the corpus without the user having to
# invoke ``knowledge_search``.
#
# Two invariants (mirror the self-improvement Soul, AGENTS.md §11):
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
#      ``safety.prompt_fence.format_untrusted_block``; this getter returns
#      the *raw* markdown and leaves fencing to the caller so the fence
#      parameters (source label) stay at the injection sites — exactly the
#      same split as ``get_agent_evolution_block``.


def kb_auto_inject_enabled() -> bool:
    """Global kill switch: ``KAZMA_KB_AUTO_INJECT=0|false|off`` disables auto-inject.

    Mirrors ``self_improvement.self_improvement_enabled()``: read live on
    every call (per-turn), default ON.  Per-library opt-in is checked
    separately inside :func:`get_knowledge_auto_inject_block`.
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
# ≤4000 chars each ≈ ≤3k tokens worst case.  Tunable via env.
def _auto_inject_top_k() -> int:
    raw = (os.environ.get("KAZMA_KB_AUTO_INJECT_TOP_K") or "3").strip()
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 3


async def get_knowledge_auto_inject_block(user_message: str) -> str:
    """Return the raw markdown to fence+inject for the latest user message.

    Uses the **same** hybrid RRF retrieval + library resolution as federated
    search (``kb_mode=inject``), so chat inject and tool/federated RAG stay
    industry-aligned (one stack, tenant/archive filters, fence at caller).

    Returns ``""`` when kill switch is off, no inject libraries, empty
    message, or no hits.

    The caller MUST wrap the returned text in
    :func:`kazma_core.safety.prompt_fence.format_untrusted_block`.
    """
    if not kb_auto_inject_enabled():
        return ""
    msg = (user_message or "").strip()
    if not msg:
        return ""

    try:
        from kazma_core.memory.federated_search import (
            federated_search,
            format_kb_hits_for_prompt,
        )

        fed = federated_search(
            msg,
            limit_memory=0,
            limit_kb=_auto_inject_top_k(),
            include_memory=False,
            include_knowledge=True,
            kb_mode="inject",
        )
        hits = fed.get("hits") or []
        if not hits:
            return ""
        block = format_kb_hits_for_prompt(hits, max_hits=_auto_inject_top_k())
        if not block:
            return ""
        # Citation footer for chat answers (product convention)
        cited_libs = sorted(
            {
                str((h.get("provenance") or {}).get("library_id") or "")
                for h in hits
                if (h.get("provenance") or {}).get("library_id")
            }
        )
        if len(cited_libs) == 1:
            lib_footer = f'📚 This data is from Knowledge "{cited_libs[0]}".'
        elif cited_libs:
            lib_footer = (
                "📚 This data is from Knowledge libraries: "
                + ", ".join(f'"{l}"' for l in cited_libs)
                + "."
            )
        else:
            lib_footer = "📚 This data is from the Knowledge Library."
        return (
            block
            + "\n"
            + lib_footer
            + "\nWhen you use this material in your answer, append the library footer verbatim."
        )
    except Exception:
        logger.debug("[kb_auto_inject] retrieval failed", exc_info=True)
        return ""


def get_knowledge_auto_inject_block_sync(user_message: str) -> str:
    """Synchronous wrapper for call sites that can't await (e.g. init-time
    injection in ``agent_runner.py`` where there is no user message yet).

    At init time there is no user message, so this always returns ``""``;
    auto-inject is fundamentally a per-turn behaviour.  Kept for symmetry
    with the init-time call sites and to make the kill-switch check cheap
    and synchronous at boot.
    """
    if not kb_auto_inject_enabled():
        return ""
    return ""
