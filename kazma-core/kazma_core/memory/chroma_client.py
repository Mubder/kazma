"""Shared ChromaDB client factory (P5 — dual-client cleanup).

``VectorMemory`` (tool path) and ``VectorStore`` (adapter L1) historically
each opened their own ``PersistentClient`` on the same on-disk path. That
works but double-loads the SQLite handle and can confuse Chroma under
concurrent writes.

Both call :func:`get_chroma_client` so one process shares one client per
persist path. In-memory clients (no path) are keyed separately.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

__all__ = ["get_chroma_client", "reset_chroma_clients"]

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_clients: dict[str, Any] = {}


def _key(path: str | None) -> str:
    if not path:
        return ":memory:"
    return str(Path(path).expanduser().resolve())


def get_chroma_client(path: str | None = None) -> Any:
    """Return a process-wide shared Chroma client for *path*.

    Args:
        path: Persist directory. ``None`` / empty → ephemeral in-memory client.
    """
    k = _key(path)
    with _lock:
        existing = _clients.get(k)
        if existing is not None:
            return existing
        import chromadb

        if path:
            p = Path(path).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(p))
            logger.debug("[chroma] PersistentClient shared path=%s", p)
        else:
            client = chromadb.Client(
                chromadb.config.Settings(anonymized_telemetry=False)
            )
            logger.debug("[chroma] in-memory Client shared")
        _clients[k] = client
        return client


def reset_chroma_clients() -> None:
    """Drop cached clients (tests / process teardown)."""
    with _lock:
        _clients.clear()
    try:
        import chromadb

        clear = getattr(
            getattr(chromadb.api.client, "SharedSystemClient", None),
            "clear_system_cache",
            None,
        )
        if callable(clear):
            clear()
    except Exception:
        logger.debug("[chroma] clear_system_cache skipped", exc_info=True)
