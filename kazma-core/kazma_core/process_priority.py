"""Kazma runs at the priority of an interactive program (2026-09-30).

The live install's Scheduled Task was registered without a priority, and
Task Scheduler's default -- 7, which Microsoft reserves for background
tasks -- starts the guard in the BELOW_NORMAL priority class. Windows hands a
below-normal class down to a process's children, and a child also inherits
its parent's memory and I/O priority (measured: even one created at the
NORMAL class), so the server the guard spawns ran below every normal program
on the machine. It held 4.7 GB with 60 MB of it in memory, and whenever
something heavy ran beside it -- a test suite, a benchmark -- its event loop
froze for 15-27 s at arbitrary frames, and twice the guard restarted it.

:func:`ensure_interactive_priority` raises this process -- never lowers it --
to the NORMAL priority class, normal memory priority and normal I/O
priority, and reports what it found, so the log says which of the three the
launcher had lowered. ``KAZMA_PROCESS_PRIORITY=keep`` leaves the process as
it was started (the test runner, which lowers itself on purpose, sets it).
Elsewhere than Windows it does nothing: a service manager there states the
priority itself (systemd ``Nice=``, launchd ``ProcessType``).

Standard library only, by design: the guard (``scripts/service/kazma_guard.py``)
loads this file by path, and the guard never imports the app.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

__all__ = ["ENV", "PriorityReport", "ensure_interactive_priority"]

#: ``normal`` (default) raises a lowered process; ``keep`` leaves it as started.
ENV = "KAZMA_PROCESS_PRIORITY"

# Priority classes (winbase.h).
_IDLE = 0x40
_BELOW_NORMAL = 0x4000
_NORMAL = 0x20
_CLASS_NAMES = {
    0x40: "idle", 0x4000: "below normal", 0x20: "normal",
    0x8000: "above normal", 0x80: "high", 0x100: "realtime",
}
#: Get/SetProcessInformation: ProcessMemoryPriority.
_PROCESS_MEMORY_PRIORITY = 0
#: MEMORY_PRIORITY_NORMAL -- an interactive program's (1 very low .. 5 normal).
_MEMORY_NORMAL = 5
#: NtQuery/NtSetInformationProcess: ProcessIoPriority.
_PROCESS_IO_PRIORITY = 33
#: IoPriorityNormal (0 very low, 1 low, 2 normal, 3 high).
_IO_NORMAL = 2


@dataclass
class PriorityReport:
    """What the process had, what it has now, and what was raised."""

    supported: bool
    before: dict[str, object] = field(default_factory=dict)
    after: dict[str, object] = field(default_factory=dict)
    raised: list[str] = field(default_factory=list)
    kept: bool = False
    errors: list[str] = field(default_factory=list)

    @staticmethod
    def describe(state: dict[str, object]) -> str:
        """``CPU below normal, memory 2, I/O 1`` for a :func:`_read_priority` result."""
        cls = state.get("class")
        name = _CLASS_NAMES.get(cls, f"0x{cls:x}") if isinstance(cls, int) else "?"
        return f"CPU {name}, memory {state.get('memory', '?')}, I/O {state.get('io', '?')}"


def _api():
    """``(ctypes, wintypes, kernel32, ntdll)`` with their signatures, or None
    elsewhere than Windows."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    nt = ctypes.WinDLL("ntdll")
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.GetPriorityClass.argtypes = [wintypes.HANDLE]
    k.GetPriorityClass.restype = wintypes.DWORD
    k.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.SetPriorityClass.restype = wintypes.BOOL
    k.GetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.GetProcessInformation.restype = wintypes.BOOL
    k.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.SetProcessInformation.restype = wintypes.BOOL
    nt.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p]
    nt.NtQueryInformationProcess.restype = ctypes.c_long
    nt.NtSetInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG]
    nt.NtSetInformationProcess.restype = ctypes.c_long
    return ctypes, wintypes, k, nt


def _read_priority() -> dict[str, object]:
    """This process's priority class, memory priority and I/O priority --
    ``{}`` elsewhere than Windows; a value that could not be read is None."""
    api = _api()
    if api is None:
        return {}
    ctypes, wintypes, k, nt = api
    me = k.GetCurrentProcess()
    state: dict[str, object] = {"class": k.GetPriorityClass(me) or None}
    memory = wintypes.ULONG(0)
    ok = k.GetProcessInformation(me, _PROCESS_MEMORY_PRIORITY, ctypes.byref(memory), ctypes.sizeof(memory))
    state["memory"] = memory.value if ok else None
    io = wintypes.ULONG(0)
    status = nt.NtQueryInformationProcess(me, _PROCESS_IO_PRIORITY, ctypes.byref(io), ctypes.sizeof(io), None)
    state["io"] = io.value if status == 0 else None
    return state


def ensure_interactive_priority() -> PriorityReport:
    """Raise this process to interactive priority where its launcher lowered
    it. Never lowers anything; never raises -- a failure is in ``errors``."""
    try:
        return _ensure()
    except (OSError, AttributeError, TypeError, ValueError) as exc:
        # A missing DLL or function (OSError / AttributeError) or an argument
        # ctypes refuses: a priority change must never stop a boot.
        return PriorityReport(supported=sys.platform == "win32", errors=[f"{type(exc).__name__}: {exc}"])


def _ensure() -> PriorityReport:
    api = _api()
    if api is None:
        return PriorityReport(supported=False)
    ctypes, wintypes, k, nt = api
    report = PriorityReport(supported=True, before=_read_priority())
    if os.environ.get(ENV, "").strip().lower() == "keep":
        report.kept = True
        report.after = dict(report.before)
        return report
    me = k.GetCurrentProcess()
    if report.before.get("class") in (_IDLE, _BELOW_NORMAL):
        if k.SetPriorityClass(me, _NORMAL):
            report.raised.append("CPU")
        else:
            report.errors.append(f"SetPriorityClass: error {ctypes.get_last_error()}")
    memory = report.before.get("memory")
    if isinstance(memory, int) and memory < _MEMORY_NORMAL:
        value = wintypes.ULONG(_MEMORY_NORMAL)
        if k.SetProcessInformation(me, _PROCESS_MEMORY_PRIORITY, ctypes.byref(value), ctypes.sizeof(value)):
            report.raised.append("memory")
        else:
            report.errors.append(f"SetProcessInformation(memory): error {ctypes.get_last_error()}")
    io = report.before.get("io")
    if isinstance(io, int) and io < _IO_NORMAL:
        value = wintypes.ULONG(_IO_NORMAL)
        status = nt.NtSetInformationProcess(me, _PROCESS_IO_PRIORITY, ctypes.byref(value), ctypes.sizeof(value))
        if status == 0:
            report.raised.append("I/O")
        else:
            report.errors.append(f"NtSetInformationProcess(I/O): status 0x{status & 0xFFFFFFFF:08x}")
    report.after = _read_priority()
    return report
