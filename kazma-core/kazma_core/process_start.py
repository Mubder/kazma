"""A process's start time: the one place Kazma reads it and compares it.

Kazma records "who owns this" as a process id and that process's start time,
so that a pid the system later gives to another program is not taken for the
owner (pid reuse): crawl and research owners, origin leases, research
storage reservations, document intakes, migration working folders.

**A start time read twice is not always the same number** (2026-11-27). On
Linux psutil computes ``create_time()`` as the start's ticks since boot plus
the boot time, and it reads the boot time again (``/proc/stat`` ``btime``)
on every call. The boot time is the wall clock minus the time since boot, so
it moves whenever the wall clock is stepped. On Docker Desktop's WSL2 VM
(``hv_utils.timesync_implicit=Y``, clocksource ``tsc``) the host's time
samples stepped it by a whole second about every 30 seconds while the PC was
loaded (3.7 s in 90 s, measured), and one process's own ``create_time()``
went from ``...883.48`` to ``...884.48``. Every exact comparison of a
recorded start with a fresh reading then said "another process": a crawl
judged its own live owner gone and the Knowledge ingest took the running
crawl over (``tests/test_kb_api_contract.py``, prepush's Linux part); the
origin table dropped this process's own live leases; research sessions of
this process read as interrupted.

So:

* **This process's start is read once** (:func:`own_start`) and every record
  this process writes carries that value. "Is this owner me?"
  (:func:`is_this_process`) compares two copies of one number, whatever the
  clock did since: exact, not a tolerance, so an earlier process that had
  this pid (a container's pid 1 after a restart) is never taken for this one.
* **Another process's recorded start** is compared with a reading made now
  (:func:`is_start_of`) within :func:`tolerance`: on Windows, whose kernel
  keeps a process's creation time, only the 3-decimal text some records
  keep; elsewhere :data:`CLOCK_STEP_TOLERANCE_S`, for the boot time's steps
  (psutil moves macOS's start with the boot time too). Its limit: a wall
  clock moved by more than that since the other process recorded its start
  makes that live process read as a reused pid. A reused pid read as the
  owner (a new process started within the tolerance of the old one) keeps a
  record a while longer, which is the safe side -- nothing is taken over or
  removed.

No other module calls ``create_time()`` (``tests/test_process_start.py``
enumerates the product source).
"""
from __future__ import annotations

import logging
import math
import os
import sys
import threading

import psutil

logger = logging.getLogger(__name__)

#: Two readings of one kept start differ by at most this much: the 3-decimal
#: text the document intake and migration folder records keep.
FORMAT_SLACK_S = 0.002
#: How far apart two readings of one start may be where the boot time follows
#: the wall clock (Linux, macOS, the BSDs): its steps are whole seconds, one
#: about every 30 s on a loaded WSL2 VM, so this absorbs a few minutes of that
#: drift and any ordinary NTP step. A pid is reused only after its process
#: ended, and the new process starts later than this by far.
CLOCK_STEP_TOLERANCE_S = 10.0
#: Windows keeps a process's creation time in the kernel and psutil returns
#: it as kept. On Linux and the BSDs psutil adds ticks since boot to the
#: moving boot time, and on macOS it moves the start by the boot time's change.
_START_KEPT_BY_KERNEL = sys.platform == "win32"

_OWN: tuple[int, float] | None = None
_OWN_LOCK = threading.Lock()


def own_start() -> float:
    """This process's start time, read once (again after a fork: a new pid)."""
    global _OWN
    pid = os.getpid()
    own = _OWN
    if own is None or own[0] != pid:
        with _OWN_LOCK:
            own = _OWN
            if own is None or own[0] != pid:
                own = _OWN = (pid, psutil.Process(pid).create_time())
    return own[1]


def own_identity() -> list:
    """``[pid, start]`` of this process, as owner records keep it."""
    return [os.getpid(), own_start()]


def tolerance() -> float:
    """How far a reading made now may be from another process's recorded start."""
    return FORMAT_SLACK_S if _START_KEPT_BY_KERNEL else CLOCK_STEP_TOLERANCE_S


def _seconds(recorded) -> float | None:
    """*recorded* as a number of seconds, or None when it is not one."""
    if isinstance(recorded, bool):
        return None
    try:
        value = float(recorded)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def is_this_process(pid, recorded) -> bool:
    """Whether ``(pid, recorded)`` is this process as it recorded itself.

    False for an earlier process that had this pid: its start is another
    number, however close.
    """
    value = _seconds(recorded)
    return value is not None and pid == os.getpid() and abs(value - own_start()) <= FORMAT_SLACK_S


def is_start_of(process: psutil.Process, recorded) -> bool:
    """Whether *recorded* is the start of *process*, not of an earlier
    process that had its pid. Raises what ``create_time()`` raises
    (``psutil.NoSuchProcess``, ``psutil.AccessDenied``)."""
    value = _seconds(recorded)
    if value is None:
        return False
    if process.pid == os.getpid():
        return is_this_process(process.pid, value)
    return abs(value - process.create_time()) <= tolerance()
