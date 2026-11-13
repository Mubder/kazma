"""A program Kazma started that Kazma's own stop ended: interrupted, not failed.

The guard stops the server with CTRL_BREAK (Windows) or SIGTERM (POSIX) sent
to the server's whole process group, so a program the server started in that
group ends with it. A backup tool caught mid-run that way reported a failure
and paged the owner: "Postgres dump failed ... pg_dump: terminated by user"
at an ordinary reload on 2026-11-12, twice. (restic runs in a group of its
own instead, so a long remote check finishes: ``backup.restic_repo``.)

The exit code alone cannot tell Kazma's stop from someone else's, and the
child sees the signal a moment before the server's own handler marks the
stop (the live log: 1-3 ms), so a stop's exit code counts as Kazma's own only
when Kazma is stopping within :data:`STOP_WAIT_S`.
:func:`ended_by_kazmas_stop` is the one answer; it waits, so it is called from
a worker thread, never on the event loop. ``tests/test_child_stop.py`` holds it.
"""

from __future__ import annotations

import signal
import time

__all__ = ["STOP_EXIT_CODES", "STOP_WAIT_S", "StoppedByKazma", "ended_by_kazmas_stop"]

#: How a program ends when a console stop reaches its group: Windows'
#: STATUS_CONTROL_C_EXIT (CTRL_C or CTRL_BREAK; unsigned and signed), and a
#: POSIX SIGINT or SIGTERM -- as ``subprocess`` reports a signal (minus the
#: number) and as a shell reports it (128 plus the number).
STOP_EXIT_CODES = frozenset({
    0xC000013A, 0xC000013A - 2**32,
    -int(signal.SIGINT), -int(signal.SIGTERM),
    128 + int(signal.SIGINT), 128 + int(signal.SIGTERM),
})

#: How long a stop's exit code waits for Kazma's own stop to be marked.
STOP_WAIT_S = 3.0


class StoppedByKazma(RuntimeError):
    """A tool Kazma's own stop ended: no failure, the next pass runs it again."""


def ended_by_kazmas_stop(returncode: int | None, *, wait_s: float = STOP_WAIT_S) -> bool:
    """True when a program that exited with *returncode* was ended by Kazma's
    own stop: a stop's exit code, and Kazma stopping within *wait_s*."""
    if returncode not in STOP_EXIT_CODES:
        return False
    from kazma_core.shutdown import is_shutting_down

    deadline = time.monotonic() + max(0.0, wait_s)
    while not is_shutting_down():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True
