"""Bounded child processes, with process-tree termination and drained output.

Windows launches suspended until a memory/process-count Job Object owns the
tree. POSIX applies inherited address-space/CPU limits through the existing
launcher and starts a new process group. These host limits are resource budgets,
not a sandbox; production code execution still requires its Docker jail.
"""
from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import time
from typing import Any

MEMORY_BYTES = 2 * 1024 ** 3
MAX_PROCESSES = 32
MAX_OUTPUT_BYTES = 256 * 1024


class OutputLimitExceeded(subprocess.SubprocessError):
    """A child exceeded the combined stdout/stderr budget."""


def _windows_job(proc: subprocess.Popen, memory_bytes: int):
    """Assign a suspended child before it can spawn, then resume it."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    native = ctypes.WinDLL("ntdll")
    native.NtResumeProcess.argtypes = [wintypes.HANDLE]
    native.NtResumeProcess.restype = wintypes.LONG
    job = kernel.CreateJobObjectW(None, None)
    retained = False
    try:
        if not job:
            raise OSError("Could not create the child resource job")
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000 | 0x200 | 0x8
        info.BasicLimitInformation.ActiveProcessLimit = MAX_PROCESSES
        info.JobMemoryLimit = memory_bytes
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError("Could not set child resource limits")
        if not kernel.AssignProcessToJobObject(job, int(proc._handle)):
            raise OSError("Could not contain the child process tree")
        if native.NtResumeProcess(int(proc._handle)) < 0:
            raise OSError("Could not resume the contained child")
        retained = True
        return job
    finally:
        if not retained:
            if job:
                kernel.CloseHandle(job)
            proc.kill()
            proc.wait(timeout=5)


class _ProcessTree:
    """Own the operating-system containment for one child and its descendants."""

    def __init__(self, proc: subprocess.Popen, memory_bytes: int) -> None:
        self.proc = proc
        self.job = _windows_job(proc, memory_bytes) if os.name == "nt" else None
        self.closed = False

    def finished(self) -> bool:
        return self.returncode() is not None

    def returncode(self) -> int | None:
        if self.proc.returncode is not None:
            return self.proc.returncode
        if os.name == "nt":
            return self.proc.poll()
        # Keep the leader unreaped until close: its group ID cannot be reused
        # for an unrelated process between observing completion and cleanup.
        if hasattr(os, "waitid") and hasattr(os, "WNOWAIT"):
            result = os.waitid(os.P_PID, self.proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if result is None:
                return None
            return result.si_status if result.si_code == os.CLD_EXITED else -result.si_status
        raise OSError("Host process budgets require waitid/WNOWAIT on this platform")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.job is not None:
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(self.job)
        elif os.name != "nt":
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            self.proc.kill()


def _start_process(
    command: list[str], *, env: dict[str, str], cwd: str | None = None,
    memory_bytes: int = MEMORY_BYTES, cpu_seconds: int | None = None,
) -> tuple[subprocess.Popen, _ProcessTree]:
    """Start a binary under inherited limits; containment failures refuse it."""
    from kazma_core.security.rlimits import limited_command

    if memory_bytes < 1:
        raise ValueError("memory_bytes must be positive")
    if os.name != "nt" and not (hasattr(os, "waitid") and hasattr(os, "WNOWAIT")):
        raise OSError("Host process budgets require waitid/WNOWAIT on this platform")
    argv = limited_command(command, memory_bytes=memory_bytes, cpu_seconds=cpu_seconds)
    extra = {"creationflags": 0x4} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, cwd=cwd, **extra,
    )
    return proc, _ProcessTree(proc, memory_bytes)


async def start_process_async(*args: Any, **kwargs: Any) -> tuple[subprocess.Popen, _ProcessTree]:
    """An interrupted launch retains ownership until its child is cleaned up."""
    import asyncio

    launch = asyncio.create_task(asyncio.to_thread(_start_process, *args, **kwargs))
    try:
        return await asyncio.shield(launch)
    except asyncio.CancelledError:
        proc, tree = await asyncio.shield(launch)
        tree.close()
        await asyncio.to_thread(proc.wait, timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()
        raise


def run_bounded(
    command: list[str] | str, *, env: dict[str, str], cwd: str | None = None,
    input: str | bytes | None = None, timeout: float = 90,
    capture_output: bool = True, text: bool = False, encoding: str = "utf-8",
    errors: str = "replace", check: bool = False, shell: bool = False,
    memory_bytes: int = MEMORY_BYTES, output_bytes: int = MAX_OUTPUT_BYTES,
    cancel_event: threading.Event | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Drain both pipes within one byte budget; kill the entire tree on failure.

    Output excess is an explicit failure, never a successful truncated result.
    The cleanup also ends background descendants left by an exited leader.
    """
    if not math.isfinite(timeout) or timeout <= 0 or output_bytes < 1:
        raise ValueError("Process budgets must be finite and positive")
    timeout = min(timeout, 300)
    if shell:
        if not isinstance(command, str):
            raise ValueError("A shell hook requires a string command")
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/c", command] if os.name == "nt" else ["/bin/sh", "-c", command]
    if isinstance(command, str):
        raise ValueError("Use an argument list for a non-shell command")
    proc, tree = _start_process(command, env=env, cwd=cwd, memory_bytes=memory_bytes,
                               cpu_seconds=math.ceil(timeout) + 1)
    buffers = [bytearray(), bytearray()]
    lock = threading.Lock()
    excess = threading.Event()

    def drain(stream, index):
        while True:
            chunk = stream.read1(min(65536, output_bytes + 1))
            if not chunk:
                return
            with lock:
                if sum(map(len, buffers)) + len(chunk) > output_bytes:
                    excess.set()
                    return
                buffers[index].extend(chunk)

    def feed():
        try:
            if input is not None:
                proc.stdin.write(input.encode(encoding, errors) if isinstance(input, str) else input)
                proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # a command may deliberately exit without consuming its input
        finally:
            proc.stdin.close()

    threads = [threading.Thread(target=drain, args=(stream, i), daemon=True)
               for i, stream in enumerate((proc.stdout, proc.stderr))]
    threads.append(threading.Thread(target=feed, daemon=True))
    deadline = time.monotonic() + timeout
    try:
        for thread in threads:
            thread.start()
        while not tree.finished():
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Command cancelled")
            if excess.is_set():
                raise OutputLimitExceeded("Command exceeded its output budget")
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, timeout)
            time.sleep(0.01)
        tree.close()
        code = proc.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
        if excess.is_set():
            raise OutputLimitExceeded("Command exceeded its output budget")
        out, err = map(bytes, buffers)
        result = subprocess.CompletedProcess(command, code, out.decode(encoding, errors) if text else out,
                                              err.decode(encoding, errors) if text else err)
        if check:
            result.check_returncode()
        return result
    finally:
        tree.close()
        proc.wait(timeout=5)
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=5)
        for stream in (proc.stdout, proc.stderr):
            stream.close()


async def run_bounded_async(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """Run off the event loop; cancellation tells the worker to kill its tree."""
    import asyncio

    cancelled = threading.Event()
    try:
        return await asyncio.to_thread(run_bounded, *args, cancel_event=cancelled, **kwargs)
    finally:
        cancelled.set()
