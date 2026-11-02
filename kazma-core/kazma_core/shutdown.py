"""Global shutdown signal for graceful server termination.

All infinite loops (SSE streams, WebSocket handlers, background tasks)
must check ``is_shutting_down()`` and exit cleanly when it returns True.

Usage:
    from kazma_core.shutdown import is_shutting_down, signal_shutdown

    while not is_shutting_down():
        yield data
        await asyncio.sleep(1)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from collections.abc import Callable

__all__ = [
    "SHUTDOWN_WAKE",
    "add_shutdown_waker",
    "add_stop_listener",
    "stop_time_left",
    "is_shutting_down",
    "reset_shutdown",
    "signal_shutdown",
    "sleep_or_shutdown",
    "step_bound",
    "uvicorn_graceful_s",
    "wait_for_shutdown",
    "install_shutdown_signal_hooks",
    "uninstall_shutdown_signal_hooks",
]

logger = logging.getLogger(__name__)

_shutdown_event: asyncio.Event | None = None
#: When the shutdown began (``time.monotonic``), for :func:`_shutdown_time_left`.
_shutdown_started: float | None = None

# ── One deadline for the whole stop (FA-261) ───────────────────────────
# A reload with work running took 58-60 s of the guard's 60 s grace, and one
# drill ended in a kill and a page: the stop was a sum of fixed per-step
# bounds (uvicorn 15 s, background drain 10 s, documents up to 45 s, memory up
# to 30 s, a 120 s ceiling) with no total that fits the guard. The guard hands
# the server its budget (``KAZMA_SHUTDOWN_BUDGET_S``: its grace less a margin);
# every stop step takes ``step_bound(its own bound)``, never more than is left.

#: Without a guard (a server run by hand): the guard's default grace (60 s)
#: less the margin it keeps.
_DEFAULT_BUDGET_S = 50.0


def _shutdown_budget_s() -> float:
    """Seconds the whole stop may take (``KAZMA_SHUTDOWN_BUDGET_S``)."""
    raw = (os.environ.get("KAZMA_SHUTDOWN_BUDGET_S") or "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_BUDGET_S
    except ValueError:
        value = _DEFAULT_BUDGET_S
    return min(600.0, max(5.0, value))


def _shutdown_time_left() -> float:
    """Seconds of the stop's budget not yet spent (the whole budget before
    the shutdown began)."""
    if _shutdown_started is None:
        return _shutdown_budget_s()
    return max(0.0, _shutdown_budget_s() - (time.monotonic() - _shutdown_started))


def stop_time_left() -> float:
    """Seconds of the stop's budget not yet spent (the whole budget before
    the stop began)."""
    return _shutdown_time_left()


def step_bound(own: float) -> float:
    """A stop step's bound: its own, or what is left of the stop's, whichever
    is smaller."""
    return max(0.0, min(float(own), _shutdown_time_left()))


def uvicorn_graceful_s() -> int:
    """uvicorn's graceful wait for open connections: a fifth of the stop's
    budget, 3 to 10 s. It was 15 s on its own, spent before the app's own
    stop began (FA-261); streams close on the shutdown flag at once now, so
    this is a cap, not a wait."""
    return max(3, int(min(10.0, _shutdown_budget_s() / 5)))


#: Put in a waiting stream's queue when the shutdown begins, so it wakes and
#: closes now instead of at its next 10 s keepalive (uvicorn waited its whole
#: graceful window on such streams, FA-261).
SHUTDOWN_WAKE = object()

_wakers: list[Callable[[], None]] = []


def add_shutdown_waker(waker: Callable[[], None]) -> Callable[[], None]:
    """Run *waker* (on the event loop) when the shutdown begins; returns the
    call that removes it."""
    _wakers.append(waker)

    def remove() -> None:
        if waker in _wakers:
            _wakers.remove(waker)

    return remove


#: Called in the thread that begins the stop -- a signal handler, often --
#: the moment it begins, never through the event loop (a stuck loop is what
#: a listener may be there for). Each must be quick and thread-safe.
_stop_listeners: list[Callable[[], None]] = []


def add_stop_listener(listener: Callable[[], None]) -> None:
    """Call *listener* (synchronously, once) when the stop begins."""
    if listener not in _stop_listeners:
        _stop_listeners.append(listener)


def _run_stop_listeners() -> None:
    for listener in list(_stop_listeners):
        try:
            listener()
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
            # One listener's failure must not stop the stop (a thread that
            # cannot start is a RuntimeError).
            logger.warning("[shutdown] a stop listener failed", exc_info=True)


def _run_wakers() -> None:
    for waker in list(_wakers):
        try:
            waker()
        except Exception:  # one waker's failure must not keep another stream open
            logger.debug("[shutdown] a waker failed", exc_info=True)


def _get_event() -> asyncio.Event:
    global _shutdown_event
    if _shutdown_event is None:
        _shutdown_event = asyncio.Event()
    return _shutdown_event


def is_shutting_down() -> bool:
    """Check if the server is shutting down.

    Returns True after signal_shutdown() has been called.
    All infinite loops should check this and exit when True.
    """
    return _get_event().is_set()


def signal_shutdown() -> None:
    """Signal all loops to stop, start the stop's clock, and wake the streams
    waiting on a queue. Idempotent."""
    global _shutdown_started
    event = _get_event()
    if not event.is_set():
        logger.info("Shutdown signal sent — terminating all streams")
        _shutdown_started = time.monotonic()
        event.set()
        _run_stop_listeners()
        # The wakers touch asyncio queues: on the loop, never inside a signal
        # handler that interrupted it mid-statement.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.call_soon_threadsafe(_run_wakers)
        else:
            _run_wakers()


def reset_shutdown() -> None:
    """Reset the shutdown signal (for testing or restart)."""
    global _shutdown_started
    _get_event().clear()
    _shutdown_started = None


async def sleep_or_shutdown(seconds: float) -> bool:
    """Sleep *seconds*, or less if the server begins to stop; True when it did.

    A background loop's wait between runs (2026-11, FA-265): with plain
    ``asyncio.sleep`` a loop asleep for hours was only cancelled at the end
    of the stop, after the steps that wait for work to finish. The wake is a
    shutdown waker that sets an event of this call's own loop, so it works
    on whichever loop the caller runs.
    """
    if is_shutting_down():
        return True
    loop = asyncio.get_running_loop()
    woken = asyncio.Event()
    remove = add_shutdown_waker(lambda: loop.call_soon_threadsafe(woken.set))
    sleeper = asyncio.ensure_future(asyncio.sleep(max(0.0, float(seconds))))
    waiter = asyncio.ensure_future(woken.wait())
    try:
        done, _ = await asyncio.wait({sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        remove()
        for task in (sleeper, waiter):
            if not task.done():
                task.cancel()
    if waiter in done or is_shutting_down():
        return True
    sleeper.result()  # a cancelled sleep is the caller's cancellation
    return False


async def wait_for_shutdown(timeout: float | None = None) -> None:
    """Await the shutdown signal (for background tasks)."""
    try:
        await asyncio.wait_for(_get_event().wait(), timeout=timeout)
    except TimeoutError:
        pass


# ── Early shutdown signal hooks ────────────────────────────────────────
# uvicorn cancels still-open streaming connections (SSE/WS) during its
# graceful-shutdown wait — BEFORE the ASGI lifespan shutdown runs. So a
# signal_shutdown() call placed in lifespan shutdown fires too late, and any
# stream still open gets hard-cancelled, logging a noisy
# ``CancelledError: Task cancelled, timeout graceful shutdown exceeded``
# traceback via Starlette's middleware. By flipping the shutdown flag at
# signal time, long-lived streams that check is_shutting_down() self-close
# within the graceful window and uvicorn never has to cancel them.
_shutdown_hooks_installed: dict[int, tuple] = {}


def install_shutdown_signal_hooks() -> None:
    """Install SIGINT/SIGTERM (and SIGBREAK on Windows) hooks that fire
    ``signal_shutdown()`` immediately when the operator stops the server,
    chaining to whatever handler the server (uvicorn) already installed.

    Must run on the main thread; idempotent. Captured previous handlers are
    restored by :func:`uninstall_shutdown_signal_hooks` (and, as a backstop, by
    uvicorn's own ``capture_signals`` teardown).
    """
    global _shutdown_hooks_installed
    if _shutdown_hooks_installed:
        return
    sigs = [signal.SIGINT, signal.SIGTERM]
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        sigs.append(sigbreak)
    for sig in sigs:
        try:
            prev = signal.getsignal(sig)
        except (ValueError, OSError):
            continue  # not the main thread / unsupported

        def _hook(signum, frame, _prev=prev):  # noqa: ANN001
            try:
                signal_shutdown()
            except Exception:  # noqa: BLE001
                logger.debug("[shutdown] signal_shutdown in hook failed", exc_info=True)
            if callable(_prev) and _prev not in (signal.SIG_DFL, signal.SIG_IGN):
                try:
                    _prev(signum, frame)
                except Exception:  # noqa: BLE001
                    logger.debug("[shutdown] chained handler failed", exc_info=True)

        try:
            signal.signal(sig, _hook)
            _shutdown_hooks_installed[sig] = (prev, _hook)
        except (ValueError, OSError):
            pass  # main-thread-only; ignore silently
    if _shutdown_hooks_installed:
        logger.debug(
            "[shutdown] early shutdown hooks installed for signals: %s",
            sorted(_shutdown_hooks_installed),
        )


def uninstall_shutdown_signal_hooks() -> None:
    """Restore the signal handlers captured by :func:`install_shutdown_signal_hooks`."""
    global _shutdown_hooks_installed
    for sig, (prev, hook) in list(_shutdown_hooks_installed.items()):
        try:
            if signal.getsignal(sig) is hook:
                signal.signal(sig, prev)
        except (ValueError, OSError):
            pass
    _shutdown_hooks_installed.clear()
