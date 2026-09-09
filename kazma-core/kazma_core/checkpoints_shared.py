"""Process-wide shared AsyncSqliteSaver for the default checkpoints DB.

Audit M-G5: two independent factories used to open their OWN
``AsyncSqliteSaver`` on ``kazma-data/checkpoints.db`` — the gateway's
``CheckpointManager`` (server graphs) and ``KazmaAgent._ensure_graph``
(``agent.run()``, the public API). WAL + busy_timeout prevent corruption,
but two unwrapped writers can interleave checkpoint sequences for the same
thread when ``agent.run()`` is invoked in the server process.

Routing both through this accessor guarantees exactly ONE saver per DB
path per event loop while holders are alive.

Lifecycle rules (they exist because an unclosed aiosqlite connection's
worker thread BLOCKS interpreter exit — proven live — and LangGraph's
saver holds a loop-bound asyncio.Lock, so a saver must never cross loops):

* The cache holds WEAK references to savers. Holders (the gateway manager,
  a KazmaAgent) keep the saver alive; when the last holder is garbage
  collected without closing, the connection dies with it — the exact
  pre-refactor semantics abandoned agents already had.
* Lifetime holders (the gateway's CheckpointManager) ``retain`` once and
  never release; transient holders (KazmaAgent) release on close. The
  connection is closed deterministically when the retention count reaches
  zero.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from pathlib import Path
from typing import Any

import aiosqlite

from kazma_core.config_store import apply_sqlite_pragmas_async

logger = logging.getLogger(__name__)

__all__ = [
    "close_shared_checkpoints",
    "get_shared_sqlite_saver",
    "release_shared_checkpoints",
    "retain_shared_checkpoints",
]

# loop -> {resolved path -> weakref(saver)}. LangGraph's AsyncSqliteSaver
# holds an asyncio.Lock bound to its creating loop, so savers are per-loop.
_by_loop: "weakref.WeakKeyDictionary[Any, dict[str, weakref.ref]]" = (
    weakref.WeakKeyDictionary()
)
_loop_locks: "weakref.WeakKeyDictionary[Any, asyncio.Lock]" = weakref.WeakKeyDictionary()

# Retention counts per resolved path (process-wide). EVERY holder retains
# once; release decrements; zero closes.
_refcounts: dict[str, int] = {}


def _lock_for(loop: Any) -> asyncio.Lock:
    lock = _loop_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _loop_locks[loop] = lock
    return lock


def _key(db_path: str) -> str:
    return str(Path(db_path).expanduser().resolve())


def retain_shared_checkpoints(db_path: str = "kazma-data/checkpoints.db") -> None:
    """Add one retention on the shared saver for *db_path* (sync, cheap)."""
    _refcounts[_key(db_path)] = _refcounts.get(_key(db_path), 0) + 1


async def get_shared_sqlite_saver(
    db_path: str = "kazma-data/checkpoints.db",
    *,
    serde: Any = None,
) -> Any:
    """Return the ONE live AsyncSqliteSaver for *db_path* on this loop.

    Reuses a still-alive saver from a previous acquisition; constructs a
    fresh one (WAL pragmas + ``setup()``) otherwise. The caller OWNS a
    reference and must either :func:`retain_shared_checkpoints` (lifetime
    holders) or keep the object alive for as long as it uses it.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    loop = asyncio.get_running_loop()
    cache = _by_loop.get(loop)
    if cache is None:
        cache = {}
        _by_loop[loop] = cache
    key = _key(db_path)
    async with _lock_for(loop):
        ref = cache.get(key)
        if ref is not None:
            saver = ref()
            if saver is not None:
                return saver
        path = Path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(key)
        await apply_sqlite_pragmas_async(conn)
        saver = AsyncSqliteSaver(conn, serde=serde) if serde is not None else AsyncSqliteSaver(conn)
        await saver.setup()
        cache[key] = weakref.ref(saver)
        logger.info("[checkpoints_shared] shared AsyncSqliteSaver for %s", key)
        return saver


async def release_shared_checkpoints(db_path: str = "kazma-data/checkpoints.db") -> None:
    """Release one retention on the shared saver; close at zero."""
    loop = asyncio.get_running_loop()
    cache = _by_loop.get(loop)
    key = _key(db_path)
    async with _lock_for(loop):
        count = _refcounts.get(key, 1) - 1
        if count <= 0:
            _refcounts.pop(key, None)
            saver = None
            if cache is not None:
                ref = cache.pop(key, None)
                saver = ref() if ref is not None else None
            if saver is not None:
                try:
                    await saver.conn.close()
                except Exception:
                    logger.debug("[checkpoints_shared] close failed for %s", key, exc_info=True)
        else:
            _refcounts[key] = count


async def close_shared_checkpoints() -> None:
    """Close every live shared checkpoint connection (tests / shutdown)."""
    for loop, cache in list(_by_loop.items()):
        for key in list(cache.keys()):
            ref = cache.pop(key, None)
            saver = ref() if ref is not None else None
            if saver is None:
                continue
            try:
                if loop is asyncio.get_running_loop():
                    await saver.conn.close()
                else:
                    # Foreign/dead loop: best-effort thread-side close.
                    import threading

                    done = threading.Event()

                    def _close(s: Any = saver) -> None:
                        try:
                            s.conn._stop_running()  # noqa: SLF001
                        except Exception:
                            pass
                        done.set()

                    threading.Thread(target=_close, daemon=True).start()
            except Exception:
                logger.debug("[checkpoints_shared] close failed for %s", key, exc_info=True)
    _refcounts.clear()
