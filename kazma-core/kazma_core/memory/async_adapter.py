"""Async adapter wrapping VectorMemory for the CompactionEngine.

The ``CompactionEngine.retrieve_memories`` method calls::

    await self.memory_store.search(query, limit=limit)

``VectorMemory.search`` is **synchronous** and uses ``n_results=`` instead of
``limit=``.  This adapter bridges that impedance mismatch so the compaction
engine can transparently use whichever memory backend is active (ChromaDB
``VectorMemory`` or the ``FTS5Memory`` fallback).

It also adds lightweight "auto-store" heuristics used by the compaction engine
to persist conversation facts before the context window is summarized away.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

__all__ = ["AsyncMemoryAdapter", "wrap_vector_memory"]

logger = logging.getLogger(__name__)


class _SearchableMemory(Protocol):
    """Structural type any memory backend must satisfy."""

    def search(self, query: str, n_results: int = 5) -> list[dict[str, Any]]: ...

    def add(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        doc_id: str | None = None,
    ) -> str: ...


class AsyncMemoryAdapter:
    """Async wrapper so ``CompactionEngine`` can ``await`` the sync VectorMemory.

    Args:
        backend: A ``VectorMemory`` (or any object with synchronous ``search``
            and ``add`` methods).
    """

    def __init__(self, backend: _SearchableMemory | Any) -> None:
        self._backend = backend

    # ── Async search (CompactionEngine calls this) ──────────────────────

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Async search translating ``limit=`` → ``n_results=``.

        Runs the sync ``backend.search`` in a thread so the event loop is
        never blocked (ChromaDB / FTS5 are blocking I/O).
        """
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, lambda: self._backend.search(query, n_results=limit)
            )
        except Exception:
            logger.exception("AsyncMemoryAdapter.search failed")
            return []

    # ── Async store (for auto-store during compaction) ──────────────────

    async def store(self, text: str, metadata: dict[str, Any] | None = None) -> str:
        """Async store delegating to ``backend.add``."""
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, lambda: self._backend.add(text, metadata=metadata)
            )
        except Exception:
            logger.exception("AsyncMemoryAdapter.store failed")
            return ""


def wrap_vector_memory(vm: Any) -> AsyncMemoryAdapter:
    """Convenience factory: wrap a VectorMemory/FTS5Memory in the adapter."""
    return AsyncMemoryAdapter(vm)
