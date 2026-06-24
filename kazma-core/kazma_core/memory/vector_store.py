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
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class VectorMemory:
    """ChromaDB-backed vector memory for conversation fragments.

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
        logger.info(
            "[VectorMemory] Initialized at %s (collection=%s, model=%s)",
            db_path,
            collection_name,
            model_name,
        )

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
        doc_id = doc_id or str(uuid.uuid4())
        meta = metadata or {"source": "agent"}
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
        results = self._collection.query(
            query_texts=[query],
            n_results=min(n_results, self._collection.count() or 1),
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
        return self._collection.count()
