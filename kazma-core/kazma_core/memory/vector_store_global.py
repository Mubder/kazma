"""ChromaDB global vector store (shared, non-chat-memory).

Backs the knowledge-base index, semantic cache, and semantic router — NOT the
chat memory (which is V2 since the V1→V2 cutover). Relocated here from
``swarm/memory/vector.py`` so the V1 ``swarm/memory`` package could be deleted
without breaking the subsystems that depend on this class.

Encoder pattern: ``get_encoder()`` delegates to the pluggable
``embedder.get_embedder()`` factory so the system can use either local
sentence-transformers or a remote OpenAI-compatible endpoint (NVIDIA NIM).
All vector backends MUST use ``get_encoder()`` / ``get_embedder()`` so the
model is never loaded twice.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from pathlib import Path
from typing import Any

from kazma_core.memory.embedder import get_embedder

__all__ = ["VectorStore", "get_encoder"]

logger = logging.getLogger(__name__)

_DEFAULT_COLLECTION = "kazma_global"

#: One Chroma client per persist directory, for the whole process: every
#: collection on it (one per Knowledge Library) shares it. Opening a
#: second PersistentClient on the same directory double-loads its SQLite
#: handle. This lived in ``memory/chroma_client.py`` until 2026-07-31, when
#: that module was deleted with V1 while this import of it stayed -- inside a
#: ``try`` that reported "chromadb not installed", so the Knowledge Library
#: had no meaning search from then until 2026-09-26.
_CLIENTS: dict[str, Any] = {}
_CLIENTS_LOCK = threading.Lock()
#: After a failed start, how long before a store tries again (and at most how
#: often it says so): a store is asked on every search.
_RETRY_AFTER_S = 300.0

#: Collections compare by angle. Chroma's default is squared L2, which every
#: collection got until 2026-09-26 (no ``hnsw:space`` was set) while
#: :meth:`VectorStore.query` reported ``1 - distance`` as a cosine. Vectors
#: are made unit length before they are stored or searched, so an older L2
#: collection ranks exactly as a cosine one, and :func:`_similarity` turns
#: either distance into the cosine it stands for.
_SPACE = "cosine"
#: Ids fetched per call when listing a collection.
_ID_PAGE = 5000
#: The most text one vector is made from. The model reads up to 8,192
#: tokens and a batch is padded to its longest text: one 60,000-character
#: chunk on the live install would have made a 32-text batch of 8,192 tokens
#: each. Keyword search still reads the whole chunk.
_EMBED_MAX_CHARS = 4000


def _unit(vec: Any) -> list[float] | None:
    """*vec* scaled to length 1; None for an empty, zero or non-finite vector."""
    if vec is None:
        return None
    values = [float(x) for x in vec]
    norm = math.sqrt(sum(x * x for x in values))
    if not values or not math.isfinite(norm) or norm == 0.0:
        return None
    return [x / norm for x in values]


def _similarity(distance: float, space: str) -> float:
    """The cosine similarity a Chroma distance between unit vectors stands for."""
    if space == "l2":  # squared euclidean: |a - b|^2 = 2 - 2 cos
        return 1.0 - distance / 2.0
    return 1.0 - distance  # cosine: 1 - cos; ip: 1 - a.b


def _collection_space(collection: Any) -> str:
    """The distance a collection was created with (Chroma's default is l2)."""
    config = getattr(collection, "configuration_json", None)
    hnsw = config.get("hnsw") if isinstance(config, dict) else None
    space = hnsw.get("space") if isinstance(hnsw, dict) else None
    if not space:
        metadata = getattr(collection, "metadata", None)
        space = metadata.get("hnsw:space") if isinstance(metadata, dict) else None
    return str(space or "l2")


def _shared_client(persist_dir: str | None) -> Any:
    """The process's Chroma client for *persist_dir* (in-memory when None).

    Telemetry off: Chroma's default sends usage events to PostHog.
    """
    import chromadb
    from chromadb.config import Settings

    key = str(Path(persist_dir).expanduser().resolve()) if persist_dir else ":memory:"
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(key)
        if client is None:
            settings = Settings(anonymized_telemetry=False)
            if persist_dir:
                Path(key).mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=key, settings=settings)
            else:
                client = chromadb.Client(settings)
            _CLIENTS[key] = client
        return client


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
        self._failed_at: float | None = None
        self._space: str = _SPACE

    # ── Initialisation ─────────────────────────────────────────────────

    def _ensure_client(self) -> bool:
        """Lazy-init the ChromaDB client and collection.  Returns True on success.

        A failure is retried after :data:`_RETRY_AFTER_S` and reported once
        per attempt with its real cause: "chromadb not installed" only when
        ``chromadb`` itself is missing.
        """
        if self._ready:
            return True
        if self._failed_at is not None and time.monotonic() - self._failed_at < _RETRY_AFTER_S:
            return False
        try:
            self._client = _shared_client(self._persist_dir)
            self._model = get_encoder()
            # Every write and search passes its own vectors, so the collection
            # needs no embedding function (Chroma would otherwise attach its
            # default model, and it is deprecating custom wrappers without a
            # config). An existing collection keeps its metadata and space.
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"description": "Kazma global semantic memory", "hnsw:space": _SPACE},
                embedding_function=None,
            )
            self._space = _collection_space(self._collection)
            self._ready = True
            self._failed_at = None
            logger.info("[VectorStore] ChromaDB collection ready: %s", self._collection_name)
            return True
        except ImportError as exc:
            self._failed_at = time.monotonic()
            if (exc.name or "").split(".")[0] == "chromadb":
                logger.warning("[VectorStore] chromadb not installed — meaning search off for %s",
                               self._collection_name)
            else:
                logger.warning("[VectorStore] %s: meaning search off (%s)",
                               self._collection_name, exc)
            return False
        except Exception as exc:
            self._failed_at = time.monotonic()
            logger.warning("[VectorStore] ChromaDB init failed for %s: %s",
                           self._collection_name, exc)
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
            return self._model.encode(text[:_EMBED_MAX_CHARS])
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
        # encode() returns [] on failure (not None) — treat empty as miss.
        embedding = _unit(self._encode(text))
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
        tenant_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """Semantic search via cosine similarity.

        Returns list of (doc_id, similarity_score) tuples.

        Args:
            tenant_id: If provided, results are filtered to this tenant.
        """
        if not self.available:
            return []
        embedding = _unit(self._encode(text))
        if not embedding:
            logger.warning("[VectorStore] Query skipped — empty embedding")
            return []
        # Build combined where filter
        filters: dict[str, Any] = {}
        if where:
            filters.update(where)
        if tenant_id:
            filters["tenant_id"] = tenant_id
        try:
            count = self._collection.count() if hasattr(self._collection, "count") else limit
            if count <= 0:
                return []
            results = self._collection.query(
                query_embeddings=[embedding],
                n_results=min(limit, count),
                where=filters or None,
            )
            if not results or not results.get("ids") or not results["ids"][0]:
                return []
            scored: list[tuple[str, float]] = []
            ids_list = results["ids"][0]
            distances = results.get("distances", [[0.0] * len(ids_list)])[0]
            for i, doc_id in enumerate(ids_list):
                dist = distances[i] if i < len(distances) else 0.0
                scored.append((doc_id, _similarity(float(dist), self._space)))
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

    def ids(self) -> set[str]:
        """Every document id the collection holds (empty when unavailable).

        A failing collection raises: the caller decides what an incomplete
        answer means (the backfill must not take "none" for "all missing").
        """
        if not self._ensure_client():
            return set()
        found: set[str] = set()
        offset = 0
        while True:
            page = self._collection.get(include=[], limit=_ID_PAGE, offset=offset).get("ids") or []
            found.update(str(i) for i in page)
            if len(page) < _ID_PAGE:
                return found
            offset += len(page)

    def delete_many(self, doc_ids: list[str]) -> int:
        """Remove *doc_ids* from the collection; returns how many were asked for.

        A failing collection raises to the caller.
        """
        doc_ids = [str(i) for i in doc_ids if i]
        if not doc_ids or not self._ensure_client():
            return 0
        for start in range(0, len(doc_ids), 500):
            self._collection.delete(ids=doc_ids[start:start + 500])
        return len(doc_ids)

    def drop(self) -> bool:
        """Delete the whole collection (its Knowledge Library is being deleted).

        False when the store is unavailable; a failing client raises.
        """
        if not self._ensure_client():
            return False
        self._client.delete_collection(self._collection_name)
        self._collection = None
        self._ready = False
        return True

    def index_many(self, docs: list[tuple[str, str, dict[str, Any]]]) -> int:
        """Embed and store ``(doc_id, text, metadata)`` in one batch; returns how many.

        One model call for the batch: the Knowledge Library backfill embeds
        thousands of chunks, and ``index`` encodes one at a time. A text the
        embedder returns nothing for is left out; a failing model or
        collection raises to the caller.
        """
        docs = [(i, t, m) for i, t, m in docs if i and t]
        if not docs or not self.available:
            return 0
        texts = [t[:_EMBED_MAX_CHARS] for _i, t, _m in docs]
        encode_batch = getattr(self._model, "encode_batch", None)
        vectors = encode_batch(texts) if callable(encode_batch) else [self._model.encode(t) for t in texts]
        keep = [(d, u) for d, u in ((d, _unit(v)) for d, v in zip(docs, vectors)) if u]
        if not keep:
            return 0
        self._collection.upsert(
            ids=[d[0] for d, _v in keep],
            embeddings=[v for _d, v in keep],
            documents=[d[1][:2000] for d, _v in keep],
            metadatas=[dict(d[2]) or {"source": "memory"} for d, _v in keep],
        )
        return len(keep)

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
