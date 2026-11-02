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
from collections.abc import Callable

__all__ = ["exit_after_fatal_error"]

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
