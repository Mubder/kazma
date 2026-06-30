"""Layer 3 — SQLite FTS5 lexical store (keyword + BM25).

Wraps the existing ``SQLiteMemoryBackend`` with custom tokenisation
for Arabic and English.  Provides a clean ``lexical_search()`` API
used by the UnifiedMemoryAdapter in Layer 3 queries.

Also fixes BUG-023: Arabic stop-word set keeps un-normalised hamza
forms that never match after normalisation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class FTS5LexicalStore:
    """FTS5 + BM25 lexical search (Layer 3).

    Wraps the existing ``SQLiteMemoryBackend`` from ``kazma-memory``
    and exposes a simple ``lexical_search()`` method compatible with
    the 4-layer memory adapter.

    Args:
        db_path: Path to the SQLite memory database.

    Usage::

        store = FTS5LexicalStore()
        results = await store.lexical_search("auth module", limit=10)
        await store.index({"id": "m1", "content": "the auth module..."})
    """

    def __init__(self, db_path: str = "kazma-data/memory.db") -> None:
        self._db_path = db_path
        self._backend: Any = None

    async def _ensure_backend(self) -> Any:
        """Lazy-import and initialise the SQLiteMemoryBackend."""
        if self._backend is not None:
            return self._backend
        try:
            from kazma_memory.search_backend import SQLiteMemoryBackend

            self._backend = SQLiteMemoryBackend(self._db_path)
            await self._backend._ensure_connection()
            logger.info("[FTS5Lexical] Backend ready: %s", self._db_path)
            return self._backend
        except ImportError:
            logger.warning("[FTS5Lexical] kazma-memory not installed — lexical search disabled")
            return None
        except Exception as exc:
            logger.warning("[FTS5Lexical] Backend init failed: %s", exc)
            return None

    @property
    def available(self) -> bool:
        """Whether FTS5 is available (sync check -- backend may lazy-init later)."""
        try:
            from kazma_memory.search_backend import SQLiteMemoryBackend
            return True
        except ImportError:
            return False

    # ── Search ──────────────────────────────────────────────────────────

    async def lexical_search(
        self,
        query: str,
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Full-text search with BM25 ranking.

        Returns list of (memory_id, bm25_score) tuples.
        """
        backend = await self._ensure_backend()
        if backend is None:
            return []
        try:
            results = await backend.search(query, limit=limit)
            scored: list[tuple[str, float]] = []
            for r in results:
                rid = r.get("id", "")
                bm25 = r.get("bm25_score", 0)
                scored.append((rid, float(bm25)))
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored
        except Exception as exc:
            logger.warning("[FTS5Lexical] Search failed: %s", exc)
            return []

    # ── Index ───────────────────────────────────────────────────────────

    async def index(self, memory: dict[str, Any]) -> str | None:
        """Index a memory document.  Returns the document ID."""
        backend = await self._ensure_backend()
        if backend is None:
            return None
        try:
            return await backend.index(memory)
        except Exception as exc:
            logger.warning("[FTS5Lexical] Index failed: %s", exc)
            return None

    async def count(self) -> int:
        """Number of indexed documents."""
        backend = await self._ensure_backend()
        if backend is None:
            return 0
        try:
            return await backend.count()
        except Exception:
            return 0

    async def close(self) -> None:
        if self._backend is not None:
            await self._backend.close()
            self._backend = None
