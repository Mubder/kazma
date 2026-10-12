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
- the server's selector loop never blocks — thread-safe completion futures
  carry results and cleanup acknowledgements;
- the loop is created lazily and shared process-wide, so Playwright objects
  (and module-level shared browser state) stay bound to one loop, exactly as
  the async API requires.

On non-Windows platforms owned work runs on the caller's loop, with the same
deadline and cancellation join (that loop already supports subprocesses).
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
import threading
from concurrent.futures import Future
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
    coro: Coroutine[Any, Any, Any], timeout: float = 180.0, *, loop: asyncio.AbstractEventLoop | None = None
) -> Any:
    """Run owned work and join its finalizers before reporting cancellation.

    Cancelling run_coroutine_threadsafe's future acknowledges a cancellation
    REQUEST, not completion of the coroutine's asynchronous cleanup. Track the
    actual task, cancel it once, and wait for its done callback. Repeated caller
    cancellation cannot interrupt that join. Timeout covers execution; cleanup
    still has to finish (hard process-tree termination is a separate boundary).
    """
    if not math.isfinite(timeout) or timeout <= 0:
        coro.close()
        raise ValueError("Browser deadline must be finite and positive")
    loop = loop or (_get_loop() if _IS_WINDOWS else asyncio.get_running_loop())
    call = _BrowserCall(loop, coro)
    result = asyncio.wrap_future(call.result)
    result.add_done_callback(_consume_result)
    try:
        async with asyncio.timeout(timeout):
            return await asyncio.shield(result)
    except BaseException:
        call.cancel()
        await finish_owned(asyncio.wrap_future(call.finished))
        raise


def _consume_result(future):
    if not future.cancelled():
        future.exception()  # also observe a late exception after caller timeout


async def finish_owned(awaitable):
    """Defer repeated caller cancellation until owned cleanup completes."""
    task = asyncio.ensure_future(awaitable)
    interrupted = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError
    return result


class _BrowserCall:
    """Actual browser task ownership; completion futures are thread-safe."""

    def __init__(self, loop, coro):
        self.loop, self.coro = loop, coro
        self.result, self.finished = Future(), Future()
        self.task = None
        self.cancel_requested = threading.Event()
        self.cancel_delivered = False
        try:
            loop.call_soon_threadsafe(self._launch)
        except RuntimeError:
            coro.close()
            raise

    def _launch(self):
        self.task = self.loop.create_task(self.coro)
        self.task.add_done_callback(self._done)
        if self.cancel_requested.is_set():
            self._cancel_task()

    def _done(self, task):
        if task.cancelled():
            self.result.cancel()
        elif (error := task.exception()) is not None:
            self.result.set_exception(error)
        else:
            self.result.set_result(task.result())
        self.finished.set_result(None)

    def _cancel_task(self):
        if self.task is not None and not self.cancel_delivered:
            self.cancel_delivered = True
            self.task.cancel()

    def cancel(self):
        self.cancel_requested.set()
        self.loop.call_soon_threadsafe(self._cancel_task)


def close_browser_loop() -> None:
    """Stop the dedicated loop (tests / graceful shutdown)."""
    global _loop, _thread
    with _lock:
        loop, _loop = _loop, None
        _thread = None
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(loop.stop)
