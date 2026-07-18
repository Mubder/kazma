"""Layer 1 — ChromaDB global vector store.

Encoder pattern: ``get_encoder()`` delegates to the pluggable
``embedder.get_embedder()`` factory so the system can use either local
sentence-transformers or a remote OpenAI-compatible endpoint (NVIDIA NIM).
All vector backends (L1 + L4) MUST use ``get_encoder()`` / ``get_embedder()``
so the model is never loaded twice.

The ``VectorStore`` manages ChromaDB collections for cross-worker semantic
search.  Falls back gracefully to empty results when ChromaDB or
sentence-transformers is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.swarm.memory.embedder import get_embedder

logger = logging.getLogger(__name__)

_DEFAULT_COLLECTION = "kazma_global"


def get_encoder(model_name: str | None = None) -> Any | None:
    """Return the shared encoder (delegates to the pluggable embedder).

    The ``model_name`` arg is accepted for backward compatibility but
    ignored — the model is chosen by config (``memory.embedding`` in
    ``kazma.yaml`` or ``KAZMA_EMBED_*`` env vars). Returns an object with
    an ``encode(text) -> list[float]`` method (an Embedder).
    """
    return get_embedder()


# ── VectorStore ────────────────────────────────────────────────────────────


class VectorStore:
    """ChromaDB-backed global vector store (Layer 1).

    Manages cross-worker semantic search via ChromaDB collections.
    Embeddings are produced by the shared ``get_encoder()`` singleton.

    Args:
        collection_name: ChromaDB collection name.
        persist_dir:     Optional directory for persistent storage.
    """

    def __init__(
        self,
        collection_name: str = _DEFAULT_COLLECTION,
        persist_dir: str | None = None,
    ) -> None:
        self._collection_name = collection_name
        self._persist_dir = persist_dir
        self._collection: Any = None
        self._client: Any = None
        self._ready: bool = False
        self._model: Any | None = None

    # ── Initialisation ─────────────────────────────────────────────────

    def _ensure_client(self) -> bool:
        """Lazy-init the ChromaDB client and collection.  Returns True on success."""
        if self._ready:
            return True
        try:
            import chromadb

            if self._persist_dir:
                self._client = chromadb.PersistentClient(path=self._persist_dir)
            else:
                self._client = chromadb.Client(
                    chromadb.config.Settings(anonymized_telemetry=False)
                )
            # Get or create the collection
            try:
                self._collection = self._client.get_collection(self._collection_name)
            except Exception:
                self._collection = self._client.create_collection(
                    name=self._collection_name,
                    metadata={"description": "Kazma global semantic memory"},
                )
            self._model = get_encoder()
            self._ready = True
            logger.info("[VectorStore] ChromaDB collection ready: %s", self._collection_name)
            return True
        except ImportError:
            logger.warning("[VectorStore] chromadb not installed — vector search disabled")
            return False
        except Exception as exc:
            logger.warning("[VectorStore] ChromaDB init failed: %s", exc)
            return False

    @property
    def available(self) -> bool:
        """Whether the vector store is ready for queries."""
        return self._ensure_client() and self._model is not None

    # ── CRUD ───────────────────────────────────────────────────────────

    def _encode(self, text: str) -> list[float] | None:
        """Embed a single text string.  Returns None on failure."""
        if self._model is None:
            return None
        try:
            # _model is an Embedder (via get_encoder → get_embedder).
            return self._model.encode(text)
        except Exception as exc:
            logger.warning("[VectorStore] Encode failed: %s", exc)
            return None

    def index(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Index a document into the ChromaDB collection.

        Returns True on success.
        """
        if not self.available:
            return False
        embedding = self._encode(text)
        # encode() returns [] on failure (not None) — treat empty as miss.
        if not embedding:
            logger.warning("[VectorStore] Index skipped — empty embedding for %s", doc_id)
            return False
        try:
            # ChromaDB rejects empty metadata dicts — always pass at least one key.
            meta = dict(metadata or {})
            if not meta:
                meta = {"source": "memory"}
            self._collection.upsert(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[text[:2000]],
                metadatas=[meta],
            )
            return True
        except Exception as exc:
            logger.warning("[VectorStore] Index failed: %s", exc)
            return False

    def query(
        self,
        text: str,
        limit: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        """Semantic search via cosine similarity.

        Returns list of (doc_id, similarity_score) tuples.
        """
        if not self.available:
            return []
        embedding = self._encode(text)
        if not embedding:
            logger.warning("[VectorStore] Query skipped — empty embedding")
            return []
        try:
            count = self._collection.count() if hasattr(self._collection, "count") else limit
            if count <= 0:
                return []
            results = self._collection.query(
                query_embeddings=[embedding],
                n_results=min(limit, count),
                where=where,
            )
            if not results or not results.get("ids") or not results["ids"][0]:
                return []
            scored: list[tuple[str, float]] = []
            ids_list = results["ids"][0]
            distances = results.get("distances", [[0.0] * len(ids_list)])[0]
            for i, doc_id in enumerate(ids_list):
                dist = distances[i] if i < len(distances) else 0.0
                scored.append((doc_id, 1.0 - float(dist)))
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored
        except Exception as exc:
            logger.warning("[VectorStore] Query failed: %s", exc)
            return []

    def delete(self, doc_id: str) -> bool:
        """Remove a document from the collection."""
        if not self._ensure_client():
            return False
        try:
            self._collection.delete(ids=[doc_id])
            return True
        except Exception as exc:
            logger.warning("[VectorStore] Delete failed: %s", exc)
            return False

    def count(self) -> int:
        """Return the number of documents in the collection."""
        if not self._ensure_client():
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0

    def get_documents(self, ids: list[str]) -> dict[str, str]:
        """Fetch document text by IDs. Returns a {id: text} mapping.

        Used by the UnifiedMemoryAdapter to populate the ``content`` field
        after scoring — previously the adapter hard-coded ``""`` for content.
        """
        if not ids or not self._ensure_client():
            return {}
        try:
            result = self._collection.get(ids=ids, include=["documents"])
            docs = result.get("documents", [])
            returned_ids = result.get("ids", [])
            return {rid: doc for rid, doc in zip(returned_ids, docs) if doc}
        except Exception as exc:
            logger.warning("[VectorStore] get_documents failed: %s", exc)
            return {}

    def build_from_registry(self, workers: list[dict[str, Any]]) -> int:
        """Rebuild the collection from a list of worker dicts.

        Each dict should have: name, expertise (list), system_prompt (str).
        Returns the number of workers indexed.
        """
        if not self.available:
            return 0
        count = 0
        for w in workers:
            profile = f"Worker: {w.get('name','')}\nExpertise: {', '.join(w.get('expertise',[]))}\n{w.get('system_prompt','')[:200]}"
            if self.index(w.get("name", ""), profile, {"type": "worker_profile"}):
                count += 1
        logger.info("[VectorStore] Built profiles for %d workers", count)
        return count
