"""End the server process after a fatal error, now.

The 2026-11 functional audit (FA-138): when the server's event loop died
(WinError 10055, no buffer space), ``serve.py`` printed the traceback and
called ``sys.exit(1)``. Interpreter shutdown then joined the non-daemon
threads -- aiosqlite connections, anyio workers -- which never finish once
their loop is gone: the process stayed up and silent until the guard's probes
failed and its graceful timeout killed it, about 2.5 of the 4 minutes Kazma
was down (four stall dumps showed the main thread in ``threading._shutdown``).

:func:`exit_after_fatal_error` writes every thread's stack (what was still
running is the next question), flushes the streams and the log handlers --
each with a bound, a handler lock held by a stuck thread must not hang the
exit -- and leaves with ``os._exit``, so the guard restarts Kazma at once.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import time
from collections.abc import Callable

__all__ = ["EXIT_MARGIN_S", "exit_after_fatal_error", "exit_after_shutdown", "prepare_server_exit"]

#: Seconds the log handlers get to flush before the process leaves anyway.
_FLUSH_BOUND_S = 2.0

#: What a step of the exit can raise when the process is half gone: a closed
#: or missing stream, a file with no descriptor, a handler on a closed file.
_EXIT_STEP_ERRORS = (OSError, ValueError, RuntimeError, AttributeError)


def _attempt(step: Callable[[], object]) -> bool:
    """Run one step of the exit. A failure is the step's alone: the exit
    happens either way, and there is nowhere left to report it."""
    try:
        step()
    except _EXIT_STEP_ERRORS:
        return False
    return True


def _flush_logs() -> None:
    from kazma_core.logging_config import drain_logs

    # The records still queued for the log writer thread first (FA-135):
    # the fatal error's own lines are among them.
    _attempt(lambda: drain_logs(_FLUSH_BOUND_S))
    for handler in list(logging.getLogger().handlers):
        _attempt(handler.flush)


def _report_and_flush() -> None:
    _attempt(lambda: sys.stderr.write("Threads still running when Kazma stopped:\n"))
    _attempt(lambda: faulthandler.dump_traceback(file=sys.stderr, all_threads=True))
    flusher = threading.Thread(target=_flush_logs, name="kazma-exit-flush", daemon=True)
    flusher.start()
    flusher.join(_FLUSH_BOUND_S)
    for stream in (sys.stdout, sys.stderr):
        _attempt(stream.flush)


def exit_after_fatal_error(code: int = 1) -> None:
    """Dump every thread's stack to stderr, flush, and end the process with
    *code* without joining threads. Never returns: whatever a step raises,
    ``os._exit`` runs."""
    try:
        _report_and_flush()
    finally:
        os._exit(code)


# ── The end of a graceful stop (roadmap theme 2, 2026-11) ─────────────
#
# The reload drill (tests/test_drill_graceful_reload.py) ran the real guard
# over the real serve.py with a turn and an OCR job running. The app's own
# shutdown finished in seconds; the process did not. uvicorn re-raises the
# stop signal once it has restored the handlers it found, and for SIGBREAK
# that was the C runtime's default action: the main thread sat in it, every
# other thread still running, until the guard's grace ran out and it killed
# the server -- and paged a reload the owner had asked for. Behind it,
# interpreter exit joins every non-daemon thread (an executor thread waiting
# on a worker process, an aiosqlite connection: FA-138's lesson).
#
# So the server process ends itself: the stop signals come back to Python as
# KeyboardInterrupt, serve.py leaves through :func:`exit_after_shutdown` once
# uvicorn returns, and a deadline armed when the stop begins ends the process
# at the stop's budget plus :data:`EXIT_MARGIN_S`, whatever is still stuck.

#: Seconds past the stop's budget the server process may live. The guard
#: hands the server its grace less 10 s as the budget, so the deadline
#: still falls inside the grace.
EXIT_MARGIN_S = 5.0

_deadline_armed = threading.Event()
#: The countdown, kept: it ends the process, so nothing joins it.
_deadline_thread: threading.Thread | None = None


#: Seconds the ``atexit`` handlers get at the end of a graceful stop (never
#: more than the stop has left, never less than two).
_EXIT_HANDLERS_BOUND_S = 10.0


def _run_exit_handlers() -> None:
    """The ``atexit`` handlers a normal interpreter exit would run -- the
    procedural recorder's drain, the vector store's release, the session
    store's close, the log writer's stop -- bounded: ``os._exit`` skips them,
    and a stuck one must not hold the exit. (Executor threads are joined by
    ``threading``'s own exit hooks, which this does not run.)"""
    import atexit

    from kazma_core.shutdown import stop_time_left

    bound = max(2.0, min(_EXIT_HANDLERS_BOUND_S, stop_time_left()))
    runner = threading.Thread(target=atexit._run_exitfuncs, name="kazma-exit-handlers", daemon=True)
    runner.start()
    runner.join(bound)


def exit_after_shutdown(code: int = 0) -> None:
    """End the server process once its shutdown has run: run the ``atexit``
    handlers and flush the logs, each bounded, and leave without joining
    threads. Never returns."""
    try:
        _attempt(_run_exit_handlers)
        flusher = threading.Thread(target=_flush_logs, name="kazma-exit-flush", daemon=True)
        flusher.start()
        flusher.join(_FLUSH_BOUND_S)
        for stream in (sys.stdout, sys.stderr):
            _attempt(stream.flush)
    finally:
        os._exit(code)


def _exit_at_the_deadline(seconds: float) -> None:
    time.sleep(max(0.0, seconds))
    _attempt(lambda: sys.stderr.write(
        f"Kazma's stop passed its budget plus {EXIT_MARGIN_S:.0f} s; ending the process.\n"))
    exit_after_fatal_error(1)


def _arm_exit_deadline() -> None:
    """Begin the countdown to the process's end (once): the stop's budget,
    plus the margin."""
    global _deadline_thread
    if _deadline_armed.is_set():
        return
    _deadline_armed.set()
    from kazma_core.shutdown import stop_time_left

    _deadline_thread = threading.Thread(
        target=_exit_at_the_deadline, args=(stop_time_left() + EXIT_MARGIN_S,),
        name="kazma-exit-deadline", daemon=True,
    )
    _deadline_thread.start()


def prepare_server_exit() -> None:
    """For the server's own process (serve.py), before uvicorn starts.

    SIGBREAK and SIGTERM, where nothing handles them, get Python's interrupt
    handler -- the one SIGINT has -- so the stop comes back to Python as
    ``KeyboardInterrupt``, during boot and after uvicorn's re-raise alike,
    never through the C runtime's default action. And when the stop begins,
    the exit deadline is armed. Never in a process that is not the server:
    the deadline ends the process.
    """
    import signal

    for name in ("SIGBREAK", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            if signal.getsignal(sig) in (signal.SIG_DFL, None):
                signal.signal(sig, signal.default_int_handler)
        except (ValueError, OSError):  # not the main thread
            continue
    from kazma_core.shutdown import add_stop_listener

    add_stop_listener(_arm_exit_deadline)
