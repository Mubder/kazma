"""Layer 1 — ChromaDB global vector store.

Singleton encoder pattern: ``get_encoder()`` loads ``all-MiniLM-L6-v2``
once and reuses it across all vector backends (L1 + L4).  This prevents
double-loading the ~90MB model into RAM.

The ``VectorStore`` manages ChromaDB collections for cross-worker semantic
search.  Falls back gracefully to empty results when ChromaDB or
sentence-transformers is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "all-MiniLM-L6-v2"
_DEFAULT_COLLECTION = "kazma_global"

# ── Shared encoder singleton ──────────────────────────────────────────────

_encoder: Any = None  # SentenceTransformer instance (lazy)


def get_encoder(model_name: str = _DEFAULT_MODEL) -> Any | None:
    """Return the shared SentenceTransformer encoder.

    Loads the model on first call and caches it.  All vector backends
    MUST use this function so the model is never loaded twice.
    """
    global _encoder
    if _encoder is not None:
        return _encoder
    try:
        from sentence_transformers import SentenceTransformer
        _encoder = SentenceTransformer(model_name)
        logger.info("[VectorStore] Loaded encoder: %s", model_name)
        return _encoder
    except ImportError:
        logger.warning("[VectorStore] sentence-transformers not installed")
        return None
    except Exception as exc:
        logger.warning("[VectorStore] Encoder load failed: %s", exc)
        return None


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
            embedding = self._model.encode(text, convert_to_numpy=False)
            if isinstance(embedding, list):
                return embedding
            return list(embedding)  # type: ignore[arg-type]
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
        if embedding is None:
            return False
        try:
            self._collection.upsert(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[text[:2000]],
                metadatas=[metadata or {}],
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
        if embedding is None:
            return []
        try:
            results = self._collection.query(
                query_embeddings=[embedding],
                n_results=min(limit, self._collection.count() if hasattr(self._collection, "count") else limit),
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
