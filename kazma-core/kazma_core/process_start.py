"""A process's start: the one place Kazma reads it and compares it.

Kazma records "who owns this" as a process id and that process's start, so
that a pid the system later gives to another program is not taken for the
owner (pid reuse): crawl and research owners, origin leases, research
storage reservations, document intakes, migration working folders.

**A start time read twice is not always the same number** (2026-11-27). On
Linux psutil computes ``create_time()`` as the start's ticks since boot plus
the boot time, and it reads the boot time again (``/proc/stat`` ``btime``)
on every call. The boot time is the wall clock minus the time since boot, so
it moves whenever the wall clock is stepped. On Docker Desktop's WSL2 VM
(``hv_utils.timesync_implicit=Y``, clocksource ``tsc``) the host's time
samples stepped it by a whole second about every 30 seconds while the PC was
loaded (3.7 s in 90 s, measured; 11 s in about 130 s under a second
prepush), and one process's own ``create_time()`` went from ``...883.48`` to
``...884.48``. Every exact comparison of a recorded start with a fresh
reading then said "another process": a crawl judged its own live owner gone
and the Knowledge ingest took the running crawl over
(``tests/test_kb_api_contract.py``, prepush's Linux part); the origin table
dropped this process's own live leases; research sessions of this process
read as interrupted.

So a record carries, beside the start as a time (``started``), a **stamp**
that does not move with the clock where one can be had:

* **Linux**: ``linux:<boot_id>:<start ticks>`` -- field 22 of
  ``/proc/<pid>/stat`` (clock ticks after boot, what psutil's own process
  identity uses) and ``/proc/sys/kernel/random/boot_id``. Neither moves when
  the wall clock steps; the ticks tell a container's pid 1 after a restart
  from the one before it, and the boot id a previous boot. Any process of
  this host is matched by its stamp, exactly.
* **macOS and the BSDs** (and a Linux whose ``/proc`` does not say):
  ``run:<random id>``, made once per process. psutil
  moves the start there with the boot time too (macOS adjusts it by the boot
  time's change; FreeBSD and OpenBSD add the boot time) and offers no
  reading that stays put, and the standard library reads none. The stamp
  tells this process from an earlier one with its pid; another process is
  matched by ``started``, read when the record is written, within
  :data:`CLOCK_STEP_TOLERANCE_S`. A clock stepped more than that since the
  record was written makes a live process read as a reused pid. Kazma runs
  its server on Windows and in Linux containers; macOS and BSD hosts are
  development machines.
* **Windows**: no stamp. The kernel keeps a process's creation time and
  psutil returns it as kept, so ``started`` is matched within
  :data:`FORMAT_SLACK_S` (2 ms, the 3-decimal text some records keep).

A record written before the stamps (``started`` only) is matched by
``started``: to 2 ms for this process's own pid, within the tolerance for
another process. A reused pid read as the owner (a new process started
within the tolerance of the old one) keeps a record a while longer, which is
the safe side: nothing is taken over or removed.

No other module reads a start, the boot time or ``/proc/<pid>/stat``
(``tests/test_process_start.py`` enumerates the product source; the guard,
loaded by path with the standard library only, keeps its own copy of the
Linux stamp and is checked against this one).
"""
from __future__ import annotations

import logging
import math
import os
import sys
import threading
import uuid

import psutil

logger = logging.getLogger(__name__)

#: Two readings of one kept start differ by at most this much: the 3-decimal
#: text the document intake and migration folder records keep.
FORMAT_SLACK_S = 0.002
#: How far apart two readings of one start may be where psutil derives it from
#: the boot time (Linux records without a stamp, macOS, the BSDs). The boot
#: time steps in whole seconds, one about every 30 s on a loaded WSL2 VM, so
#: this absorbs a few minutes of that and any ordinary NTP step. A pid is
#: reused only after its process ended, and the new process starts later.
CLOCK_STEP_TOLERANCE_S = 10.0
#: Windows keeps a process's creation time in the kernel and psutil returns
#: it as kept; everywhere else psutil moves it with the boot time.
_START_KEPT_BY_KERNEL = sys.platform == "win32"
#: Linux: a stamp read from /proc (``stamp_of``).
_LINUX_PROC = sys.platform.startswith("linux")
_LINUX = "linux:"
_RUN = "run:"
_BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"

_OWN: tuple[int, float] | None = None
_OWN_STAMP: tuple[int, str | None] | None = None
_BOOT_ID: list[str | None] = []
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


def _boot_id() -> str | None:
    """This boot's id (Linux), read once; None when it cannot be read."""
    if not _BOOT_ID:
        try:
            with open(_BOOT_ID_PATH, encoding="ascii") as fh:
                value = fh.read().strip()
        except OSError:
            value = ""
        _BOOT_ID[:] = [value or None]
    return _BOOT_ID[0]


def _ticks_from_stat(data: bytes) -> int:
    """Field 22 of a ``/proc/<pid>/stat`` line: the start, in clock ticks
    after boot. The name (field 2) may hold spaces and parentheses, so the
    fields are counted from its last closing parenthesis."""
    return int(data[data.rindex(b")") + 2:].split()[19])


def _start_ticks(pid) -> int | None:
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as fh:
            return _ticks_from_stat(fh.read())
    except (OSError, TypeError, ValueError, IndexError):
        return None


def stamp_of(pid) -> str | None:
    """The Linux stamp of process *pid* (``linux:<boot_id>:<ticks>``), or None
    off Linux or when ``/proc`` does not say (the process ended, a hidden pid)."""
    if not _LINUX_PROC:
        return None
    boot = _boot_id()
    ticks = _start_ticks(pid) if boot else None
    return None if ticks is None else f"{_LINUX}{boot}:{ticks}"


def own_stamp() -> str | None:
    """This process's stamp: its Linux stamp, or ``run:<id>`` made once for this
    process where no reading stays put (macOS, the BSDs, a Linux whose
    ``/proc`` does not say); None on Windows."""
    global _OWN_STAMP
    pid = os.getpid()
    own = _OWN_STAMP
    if own is None or own[0] != pid:
        with _OWN_LOCK:
            own = _OWN_STAMP
            if own is None or own[0] != pid:
                if _START_KEPT_BY_KERNEL:
                    stamp = None
                else:
                    stamp = stamp_of(pid) or _RUN + uuid.uuid4().hex
                own = _OWN_STAMP = (pid, stamp)
    return own[1]


def _record_start() -> float:
    """The start a record of this process carries as ``started``: the one read
    once where the stamp or the kernel holds the identity (Linux, Windows); a
    reading made now elsewhere, so that another process compares it with a
    reading of its own made close to it."""
    stamp = own_stamp()
    if stamp is None or stamp.startswith(_LINUX):
        return own_start()
    return psutil.Process(os.getpid()).create_time()


def own_identity() -> list:
    """``[pid, started]`` and the stamp when there is one, as list records keep it."""
    stamp = own_stamp()
    return [os.getpid(), _record_start()] + ([stamp] if stamp is not None else [])


def own_fields() -> dict:
    """``pid``, ``started`` and ``stamp`` (when there is one), as dict records keep them."""
    pid, started, *stamp = own_identity()
    return {"pid": pid, "started": started, **({"stamp": stamp[0]} if stamp else {})}


def tolerance() -> float:
    """How far a reading made now may be from another process's recorded
    start when no stamp settles it."""
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


def is_this_process(pid, started, stamp=None) -> bool:
    """Whether a record of ``pid``, ``started`` and *stamp* is this process.

    By the stamp when the record and this process have one (exact); by
    ``started`` within 2 ms otherwise (a record from before the stamps, or
    Windows). An earlier process that had this pid is not this one.
    """
    if isinstance(pid, bool) or pid != os.getpid():
        return False
    own = own_stamp()
    if own is not None and isinstance(stamp, str):
        return stamp == own
    value = _seconds(started)
    return value is not None and abs(value - own_start()) <= FORMAT_SLACK_S


def identity_is_this_process(identity) -> bool:
    """:func:`is_this_process` for a list record ``[pid, started(, stamp)]``;
    False for anything else."""
    if not isinstance(identity, (list, tuple)) or len(identity) not in (2, 3):
        return False
    return is_this_process(*identity)


def is_start_of(process: psutil.Process, started, stamp=None) -> bool:
    """Whether the recorded ``started`` and *stamp* are the start of *process*,
    not of an earlier process that had its pid.

    This process: :func:`is_this_process`. Another: by its Linux stamp,
    exactly, when the record has one and ``/proc`` answers; else by
    ``started`` within :func:`tolerance`. Raises what ``create_time()``
    raises (``psutil.NoSuchProcess``, ``psutil.AccessDenied``).
    """
    if process.pid == os.getpid():
        return is_this_process(process.pid, started, stamp)
    if isinstance(stamp, str) and stamp.startswith(_LINUX):
        now = stamp_of(process.pid)
        if now is not None:
            return now == stamp
    value = _seconds(started)
    if value is None:
        return False
    return abs(value - process.create_time()) <= tolerance()
