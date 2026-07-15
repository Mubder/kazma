"""Vector memory — stores and retrieves conversation fragments.

Each fragment is a (text, metadata) pair stored in ChromaDB.
Retrieval uses cosine similarity via sentence-transformers embeddings.
All embeddings run locally — no external API calls.

Usage:
    memory = VectorMemory()
    memory.add("User prefers dark mode", {"topic": "preferences"})
    results = memory.search("what theme does the user like?")
"""

from __future__ import annotations

import logging
import uuid
import asyncio
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Pending degradation alerts scheduled outside an event loop.
# app.py should call ``await flush_pending_alerts()`` after startup.
_pending_alerts: list = []


async def flush_pending_alerts() -> None:
    """Flush any degradation alerts that were scheduled outside an event loop.

    Call this from app.py's startup handler (inside the running loop) after
    VectorMemory construction.
    """
    if not _pending_alerts:
        return
    for coro_fn in _pending_alerts:
        try:
            await coro_fn()
        except Exception:
            logger.debug("[VectorMemory] Pending alert failed", exc_info=True)
    _pending_alerts.clear()


class VectorMemory:
    """ChromaDB-backed vector memory for conversation fragments.

    Gracefully falls back to FTS5Memory if chromadb or sentence-transformers
    are not installed, triggering system alerts.

    Args:
        path: Directory for ChromaDB persistent storage.
              Defaults to ~/.kazma/vector_memory.
        collection_name: Name of the ChromaDB collection.
        model_name: Sentence-transformers model for embeddings.
    """

    def __init__(
        self,
        path: str | None = None,
        collection_name: str = "agent_memory",
        model_name: str = "all-MiniLM-L6-v2",
    ) -> None:
        from kazma_core.config_store import get_config_store
        from kazma_core.memory.fts5 import FTS5Memory
        from kazma_core.paths import vector_memory_path

        self.degraded = False
        self._fallback: FTS5Memory | None = None
        self._path = path or vector_memory_path()
        self._path = str(Path(self._path).expanduser().resolve())
        self._collection_name = collection_name
        self._model_name = model_name

        try:
            import chromadb
            from chromadb.utils import embedding_functions

            Path(self._path).mkdir(parents=True, exist_ok=True)

            self._client = chromadb.PersistentClient(path=self._path)
            # Use the pluggable embedder via the shared factory. For local
            # sentence-transformers this delegates to ChromaDB's native
            # SentenceTransformerEmbeddingFunction (no double-load); for a
            # remote provider (NIM etc.) it wraps the Embedder in a
            # ChromaDB-compatible EmbeddingFunction.
            from kazma_core.swarm.memory.embedder import (
                get_embedder,
                make_chroma_embedding_function,
            )

            get_embedder()  # eagerly warm the singleton
            self._ef = make_chroma_embedding_function(get_embedder())
            try:
                self._collection = self._client.get_or_create_collection(
                    name=collection_name,
                    embedding_function=self._ef,
                )
            except Exception as ef_conflict:
                # Embedding-function conflict: the collection was created with
                # a different EF (e.g. user switched from local to NIM, or the
                # wrapper name changed). Drop and recreate — old vectors used
                # a different embedder and can't be queried with the new one.
                if "embedding function" in str(ef_conflict).lower() or "conflict" in str(ef_conflict).lower():
                    logger.warning(
                        "[VectorMemory] Embedding function conflict — recreating collection %s", collection_name,
                    )
                    try:
                        self._client.delete_collection(collection_name)
                    except Exception:
                        pass
                    self._collection = self._client.create_collection(
                        name=collection_name,
                        embedding_function=self._ef,
                    )
                else:
                    raise

            # Update status in ConfigStore to ACTIVE
            store = get_config_store()
            store.set("system.memory.status", "ACTIVE", category="system")

            logger.info(
                "[VectorMemory] Initialized at %s (collection=%s, model=%s)",
                self._path,
                collection_name,
                model_name,
            )
        except Exception as exc:
            # Broad catch: ImportError (libs missing), but also ChromaDB
            # version incompatibility, corrupt DB, disk permission errors, etc.
            is_missing = isinstance(exc, ImportError)
            self.degraded = True
            if is_missing:
                logger.warning(
                    "[VectorMemory] Missing sentence-transformers or chromadb. "
                    "Gracefully degrading to FTS5Memory."
                )
            else:
                logger.warning(
                    "[VectorMemory] ChromaDB init failed (%s). "
                    "Gracefully degrading to FTS5Memory.", exc
                )

            # Update status in ConfigStore to DEGRADED
            try:
                store = get_config_store()
                store.set("system.memory.status", "DEGRADED", category="system")
            except Exception:
                pass

            self._fallback = FTS5Memory()

            # Broadcast the alert to active platform adapters.
            # This used to be a no-op outside a running event loop (the normal
            # app.py startup path) because get_running_loop() raised RuntimeError
            # and the except swallowed it.  Now we defer the alert so it fires
            # once the loop is actually running.
            self._schedule_degradation_alert(is_missing, exc)

    def _schedule_degradation_alert(self, is_missing: bool, exc: Exception) -> None:
        """Schedule the degradation alert robustly (works outside an event loop).

        If a running event loop is available, the alert coroutine is scheduled
        via ``create_task``.  If not (typical during app.py startup, which runs
        in a thread before the loop is live), the coroutine is deferred to
        ``_pending_alerts`` and flushed by ``flush_pending_alerts()`` from the
        lifespan handler.
        """
        try:
            from kazma_core.observability import AlertDispatcher

            async def _fire() -> None:
                await AlertDispatcher.broadcast_alert(
                    title="Permission Required: Memory Subsystem",
                    subsystem="VectorMemory",
                    status="DEGRADED",
                    reason=(
                        "Missing sentence-transformers or chromadb library"
                        if is_missing
                        else f"ChromaDB initialization error: {exc}"
                    ),
                    callback_id="sentence-transformers",
                    button_text="Install ML Dependencies",
                )

            try:
                loop = asyncio.get_running_loop()
                # We're inside a running loop — schedule the task.
                loop.create_task(_fire())
            except RuntimeError:
                # No running loop (typical during startup).  Defer the alert.
                _pending_alerts.append(_fire)
        except Exception:
            logger.debug("[VectorMemory] Could not schedule degradation alert", exc_info=True)

    def add(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        doc_id: str | None = None,
    ) -> str:
        """Store a text fragment with metadata.

        Long texts (>2000 chars) are split into overlapping chunks of 2000
        characters with a 200-character overlap, so retrieval can surface
        the relevant portion rather than the whole document.

        Args:
            text:     The text to store.
            metadata: Optional metadata dict (topic, user, timestamp, etc.).
            doc_id:   Optional ID. Generated if not provided.

        Returns:
            The document ID used for storage (the first chunk's ID; chunk
            IDs are ``{doc_id}_chunk_{i}``).
        """
        if self.degraded and self._fallback is not None:
            return self._fallback.add(text, metadata, doc_id)

        from kazma_core.tenant_context import get_current_tenant_id

        base_id = doc_id or str(uuid.uuid4())
        meta = metadata.copy() if metadata is not None else {"source": "agent"}

        tenant_id = get_current_tenant_id()
        if tenant_id:
            meta["tenant_id"] = tenant_id

        # ── Chunking ──
        chunks = self._chunk_text(text)
        ids = []
        docs = []
        metas = []
        for i, chunk in enumerate(chunks):
            cid = f"{base_id}_chunk_{i}" if len(chunks) > 1 else base_id
            ids.append(cid)
            docs.append(chunk)
            chunk_meta = meta.copy()
            if len(chunks) > 1:
                chunk_meta["chunk_index"] = i
                chunk_meta["chunk_total"] = len(chunks)
                chunk_meta["parent_id"] = base_id
            metas.append(chunk_meta)

        self._collection.add(
            documents=docs,
            metadatas=metas,
            ids=ids,
        )
        logger.debug("[VectorMemory] Stored %d chunk(s) for doc %s", len(chunks), base_id)
        return base_id

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 2000, overlap: int = 200) -> list[str]:
        """Split text into overlapping chunks for better retrieval.

        Args:
            text:       The full text to chunk.
            chunk_size: Maximum characters per chunk (default 2000).
            overlap:    Character overlap between chunks (default 200).

        Returns:
            List of text chunks.  Short texts (≤ chunk_size) return a
            single-element list unchanged.
        """
        if len(text) <= chunk_size:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            # Try to break at a word boundary for readability
            if end < len(text):
                boundary = text.rfind("\n", start, end)
                if boundary > start + chunk_size // 2:
                    end = boundary
            chunks.append(text[start:end].strip())
            start = end - overlap
        return [c for c in chunks if c]

    def search(self, query: str, n_results: int = 5) -> list[dict[str, Any]]:
        """Search for fragments relevant to the query.

        Args:
            query:     Natural language search query.
            n_results: Number of results to return (default 5).

        Returns:
            List of dicts with 'text', 'metadata', and 'distance' keys.
        """
        if self.degraded and self._fallback is not None:
            results = self._fallback.search(query, limit=n_results)
            return [
                {
                    "text": r["text"],
                    "metadata": r["metadata"],
                    "distance": 1.0 / (r["score"] + 1.0) if r["score"] >= 0 else 1.0
                }
                for r in results
            ]

        from kazma_core.tenant_context import get_current_tenant_id

        where_filter = None
        tenant_id = get_current_tenant_id()
        if tenant_id:
            where_filter = {"tenant_id": tenant_id}

        results = self._collection.query(
            query_texts=[query],
            n_results=min(n_results, self._collection.count() or 1),
            where=where_filter,
        )

        if not results["documents"] or not results["documents"][0]:
            return []

        return [
            {"text": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]

    @property
    def count(self) -> int:
        """Number of stored fragments."""
        if self.degraded and self._fallback is not None:
            # FTS5Memory.count is a method, VectorMemory.count is a property —
            # handle both transparently.
            result = self._fallback.count
            return result() if callable(result) else result
        return self._collection.count()

    def delete(self, doc_id: str) -> bool:
        """Delete a document and all its chunks from memory.

        Args:
            doc_id: The base document ID.  Chunk IDs (``{doc_id}_chunk_*``)
                    are also removed.

        Returns:
            True if any documents were deleted.
        """
        if self.degraded and self._fallback is not None:
            return self._fallback.delete(doc_id)

        try:
            # ChromaDB's where clause can match on parent_id for chunked docs.
            # We delete both the exact id and any chunks whose parent_id matches.
            self._collection.delete(ids=[doc_id])
            # Also delete chunks: query first to find their IDs, then delete
            try:
                results = self._collection.get(where={"parent_id": doc_id})
                chunk_ids = results.get("ids", []) if results else []
                if chunk_ids:
                    self._collection.delete(ids=chunk_ids)
            except Exception:
                pass
            logger.debug("[VectorMemory] Deleted doc %s (and chunks)", doc_id)
            return True
        except Exception as exc:
            logger.warning("[VectorMemory] Delete failed for %s: %s", doc_id, exc)
            return False

    def update(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Update an existing document (delete + re-add).

        Args:
            doc_id:   The base document ID to update.
            text:     The new text.
            metadata: Optional new metadata dict.

        Returns:
            The document ID.
        """
        self.delete(doc_id)
        return self.add(text, metadata=metadata, doc_id=doc_id)

    def clear(self) -> int:
        """Delete ALL documents from the collection.

        Returns:
            The number of documents that were in the collection before clearing.
        """
        if self.degraded and self._fallback is not None:
            result = self._fallback.clear() if hasattr(self._fallback, "clear") else 0
            return result

        try:
            count = self._collection.count()
            if count > 0:
                self._collection.delete(ids=self._collection.get()["ids"])
            logger.info("[VectorMemory] Cleared %d documents", count)
            return count
        except Exception as exc:
            logger.warning("[VectorMemory] Clear failed: %s", exc)
            return 0
