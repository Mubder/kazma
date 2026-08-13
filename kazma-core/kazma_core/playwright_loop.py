"""Run Playwright async coroutines on a dedicated Windows ProactorEventLoop.

Kazma's server runs a ``SelectorEventLoop`` (:mod:`kazma_core.eventloop` —
forced by psycopg async, which refuses Proactor). On Windows, the selector
loop does NOT implement subprocess transports, so Playwright's Node-driver
spawn (``asyncio.create_subprocess_exec`` inside its transport) raises
``NotImplementedError`` — the connection task dies in the background with
"Task exception was never retrieved" and every browser-based fetch silently
returns nothing.

This helper routes Playwright coroutines onto a dedicated
``ProactorEventLoop`` living in a daemon thread:

- subprocess spawns work there (Proactor supports them);
- the server's selector loop never blocks — the result is awaited through
  ``asyncio.to_thread``;
- the loop is created lazily and shared process-wide, so Playwright objects
  (and module-level shared browser state) stay bound to one loop, exactly as
  the async API requires.

On non-Windows platforms this is a thin pass-through (the running loop
already supports subprocesses).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Any, Coroutine

logger = logging.getLogger(__name__)

__all__ = ["run_in_browser_loop", "close_browser_loop"]

_IS_WINDOWS = sys.platform.startswith("win")

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _new_loop() -> asyncio.AbstractEventLoop:
    if _IS_WINDOWS:
        from asyncio.windows_events import ProactorEventLoop

        return ProactorEventLoop()
    return asyncio.new_event_loop()


def _get_loop() -> asyncio.AbstractEventLoop | None:
    """Return the shared browser loop (Windows), or None (run inline)."""
    global _loop, _thread
    if not _IS_WINDOWS:
        return None
    with _lock:
        if _loop is None or _loop.is_closed():
            _loop = _new_loop()
            _thread = threading.Thread(
                target=_loop.run_forever, name="kazma-playwright-loop", daemon=True
            )
            _thread.start()
        return _loop


async def run_in_browser_loop(
    coro: Coroutine[Any, Any, Any], timeout: float = 180.0
) -> Any:
    """Run *coro* on the browser loop (Windows) or inline on this loop.

    Raises whatever *coro* raises (after cancelling the cross-loop future).
    """
    if not _IS_WINDOWS:
        return await coro
    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)  # type: ignore[union-attr]
    try:
        return await asyncio.to_thread(future.result, timeout)
    except Exception:
        future.cancel()
        raise


def close_browser_loop() -> None:
    """Stop the dedicated loop (tests / graceful shutdown)."""
    global _loop, _thread
    with _lock:
        loop, _loop = _loop, None
        _thread = None
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(loop.stop)
