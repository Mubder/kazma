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
        path: str = "~/.kazma/vector_memory",
        collection_name: str = "agent_memory",
        model_name: str = "all-MiniLM-L6-v2",
    ) -> None:
        from kazma_core.config_store import get_config_store
        from kazma_core.observability import AlertDispatcher
        from kazma_core.memory.fts5 import FTS5Memory

        self.degraded = False
        self._fallback: FTS5Memory | None = None

        try:
            import chromadb
            from chromadb.utils import embedding_functions

            db_path = str(Path(path).expanduser().resolve())
            Path(db_path).mkdir(parents=True, exist_ok=True)

            self._client = chromadb.PersistentClient(path=db_path)
            self._ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=model_name,
            )
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
                embedding_function=self._ef,
            )
            
            # Update status in ConfigStore to ACTIVE
            store = get_config_store()
            store.set("system.memory.status", "ACTIVE", category="system")
            
            logger.info(
                "[VectorMemory] Initialized at %s (collection=%s, model=%s)",
                db_path,
                collection_name,
                model_name,
            )
        except ImportError:
            self.degraded = True
            logger.warning("[VectorMemory] Missing sentence-transformers or chromadb. Gracefully degrading to FTS5Memory.")
            
            # Update status in ConfigStore to DEGRADED
            store = get_config_store()
            store.set("system.memory.status", "DEGRADED", category="system")
            
            self._fallback = FTS5Memory()
            
            # Broadcast the alert to active platform adapters
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(AlertDispatcher.broadcast_alert(
                    title="Permission Required: Memory Subsystem",
                    subsystem="VectorMemory",
                    status="DEGRADED",
                    reason="Missing sentence-transformers or chromadb library",
                    callback_id="sentence-transformers",
                    button_text="Install ML Dependencies"
                ))
            except RuntimeError:
                pass

    def add(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        doc_id: str | None = None,
    ) -> str:
        """Store a text fragment with metadata.

        Args:
            text:     The text to store.
            metadata: Optional metadata dict (topic, user, timestamp, etc.).
            doc_id:   Optional ID. Generated if not provided.

        Returns:
            The document ID used for storage.
        """
        if self.degraded and self._fallback is not None:
            return self._fallback.add(text, metadata, doc_id)

        from kazma_core.tenant_context import get_current_tenant_id

        doc_id = doc_id or str(uuid.uuid4())
        meta = metadata.copy() if metadata is not None else {"source": "agent"}
        
        tenant_id = get_current_tenant_id()
        if tenant_id:
            meta["tenant_id"] = tenant_id

        self._collection.add(
            documents=[text],
            metadatas=[meta],
            ids=[doc_id],
        )
        logger.debug("[VectorMemory] Stored doc %s: %.80s", doc_id, text)
        return doc_id

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
            return self._fallback.count()
        return self._collection.count()
