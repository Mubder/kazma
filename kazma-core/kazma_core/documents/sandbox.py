"""Isolated subprocess boundary for untrusted document parsers."""

from __future__ import annotations

import logging
import math
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

__all__ = [
    "SandboxRequest",
    "SandboxResult",
    "run_isolated_subprocess",
]

_INHERITED_ENV = frozenset(
    {
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TZ",
        "WINDIR",
    }
)
_SENSITIVE_ENV_MARKERS = (
    "AUTH",
    "COOKIE",
    "CREDENTIAL",
    "KEY",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)
_UNSAFE_ENV_NAMES = frozenset(
    {
        "BASH_ENV",
        "CDPATH",
        "ENV",
        "GCONV_PATH",
        "IFS",
        "LOCALDOMAIN",
        "NLSPATH",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "SHELLOPTS",
    }
)
_UNSAFE_ENV_PREFIXES = ("DYLD_", "LD_")
logger = logging.getLogger(__name__)


class _WindowsJobHandle:
    """Owned Windows Job Object handle."""

    def __init__(self, handle: int, close_handle: Callable[[int], object]) -> None:
        self.handle = handle
        self._close_handle = close_handle
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._close_handle(self.handle)
            self._closed = True


@dataclass(frozen=True, slots=True)
class SandboxRequest:
    """A bounded subprocess invocation."""

    command: tuple[str, ...]
    work_dir: Path
    timeout_seconds: float = 60.0
    stdout_limit_bytes: int = 1_048_576
    stderr_limit_bytes: int = 262_144
    env: Mapping[str, str] | None = None
    memory_limit_bytes: int | None = None
    cpu_limit_seconds: int | None = None

    @classmethod
    def python_module(
        cls,
        module: str,
        *,
        work_dir: str | Path,
        args: Sequence[str] = (),
        **kwargs: object,
    ) -> SandboxRequest:
        """Build a request that invokes a Python module in isolated mode."""
        normalized = str(module).strip()
        if not normalized or any(part in {"", ".", ".."} for part in normalized.split(".")):
            raise ValueError("module must be a valid dotted module name")
        return cls(
            command=(sys.executable, "-I", "-m", normalized, *map(str, args)),
            work_dir=Path(work_dir),
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class SandboxResult:
    command: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    timed_out: bool
    output_limit_exceeded: bool
    resource_limits_enforced: bool
    resource_limit_degraded_reason: str | None


def _validate_request(request: SandboxRequest) -> Path:
    if not request.command or any(
        not isinstance(part, str) or not part or "\x00" in part
        for part in request.command
    ):
        raise ValueError("command must contain non-empty strings without NUL bytes")
    work_dir = Path(request.work_dir).resolve()
    if not work_dir.is_dir():
        raise ValueError("work_dir must be an existing directory")
    if request.timeout_seconds <= 0 or not math.isfinite(request.timeout_seconds):
        raise ValueError("timeout_seconds must be finite and positive")
    for value, name in (
        (request.stdout_limit_bytes, "stdout_limit_bytes"),
        (request.stderr_limit_bytes, "stderr_limit_bytes"),
    ):
        if isinstance(value, bool) or int(value) < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if request.memory_limit_bytes is not None and request.memory_limit_bytes <= 0:
        raise ValueError("memory_limit_bytes must be positive")
    if request.cpu_limit_seconds is not None and request.cpu_limit_seconds <= 0:
        raise ValueError("cpu_limit_seconds must be positive")
    return work_dir


def _sanitized_environment(extra: Mapping[str, str] | None) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _INHERITED_ENV
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    for key, value in (extra or {}).items():
        normalized = str(key).strip()
        upper = normalized.upper()
        if (
            not normalized
            or "=" in normalized
            or "\x00" in normalized
            or upper in _UNSAFE_ENV_NAMES
            or upper.startswith(_UNSAFE_ENV_PREFIXES)
            or any(marker in upper for marker in _SENSITIVE_ENV_MARKERS)
        ):
            raise ValueError(f"unsafe subprocess environment key: {key!r}")
        text = str(value)
        if "\x00" in text:
            raise ValueError(f"subprocess environment value for {key!r} contains NUL")
        environment[normalized] = text
    return environment


def _resource_setup(
    request: SandboxRequest,
) -> tuple[object | None, bool, str | None]:
    requested = (
        request.memory_limit_bytes is not None or request.cpu_limit_seconds is not None
    )
    if not requested:
        return None, False, None
    if os.name == "nt":
        return None, False, None

    def apply_limits() -> None:
        import resource

        if request.memory_limit_bytes is not None:
            memory = int(request.memory_limit_bytes)
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        if request.cpu_limit_seconds is not None:
            cpu = int(request.cpu_limit_seconds)
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))

    return apply_limits, True, None


def _assign_windows_job(
    process: subprocess.Popen[bytes],
    memory_limit_bytes: int,
) -> tuple[_WindowsJobHandle | None, str | None]:
    """Assign a process to a kill-on-close, memory-bounded Job Object."""

    try:
        import ctypes
        from ctypes import wintypes

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _BasicLimits(ctypes.Structure):
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

        class _ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        raw_job = kernel32.CreateJobObjectW(None, None)
        if not raw_job:
            return None, "Windows Job Object creation failed"
        job_value = int(raw_job)
        owned = _WindowsJobHandle(job_value, kernel32.CloseHandle)
        limits = _ExtendedLimits()
        JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        limits.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        limits.JobMemoryLimit = int(memory_limit_bytes)
        if not kernel32.SetInformationJobObject(
            raw_job,
            9,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            owned.close()
            return None, "Windows Job Object memory-limit configuration failed"
        raw_process = getattr(process, "_handle", None)
        if raw_process is None or not kernel32.AssignProcessToJobObject(
            raw_job, wintypes.HANDLE(int(raw_process))
        ):
            owned.close()
            return None, "Windows Job Object process assignment failed"
        return owned, None
    except Exception as exc:
        logger.debug(
            "[documents.sandbox] Windows Job Object setup failed: %s",
            exc,
            exc_info=True,
        )
        return None, f"Windows Job Object setup failed ({type(exc).__name__})"


def _windows_limit_status(
    request: SandboxRequest,
    job_assigned: bool,
    job_error: str | None,
) -> tuple[bool, str | None]:
    reasons: list[str] = []
    if request.memory_limit_bytes is not None and not job_assigned:
        reasons.append(job_error or "Windows Job Object memory limit was not enforced")
    if request.cpu_limit_seconds is not None:
        reasons.append("Windows Job Objects do not enforce the requested CPU-time quota")
    requested = (
        request.memory_limit_bytes is not None or request.cpu_limit_seconds is not None
    )
    return requested and not reasons, "; ".join(reasons) or None


def _drain_bounded(
    pipe: BinaryIO,
    limit: int,
    retained: bytearray,
    limit_hit: threading.Event,
) -> None:
    try:
        while True:
            chunk = pipe.read(65_536)
            if not chunk:
                return
            remaining = limit - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])
            if len(chunk) > max(remaining, 0) or (limit == 0 and chunk):
                limit_hit.set()
    finally:
        pipe.close()


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
        try:
            process.wait(timeout=1.0)
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            return
    try:
        import psutil

        parent = psutil.Process(process.pid)
        descendants = parent.children(recursive=True)
        for child in descendants:
            child.terminate()
        parent.terminate()
        _, alive = psutil.wait_procs([*descendants, parent], timeout=1.0)
        for item in alive:
            item.kill()
    except (ImportError, OSError, Exception):
        # NoSuchProcess (child already exited between kill and psutil lookup),
        # ImportError (psutil not installed), or OSError — fall through to the
        # stdlib terminate path.
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()


def run_isolated_subprocess(request: SandboxRequest) -> SandboxResult:
    """Run a subprocess with bounded time/output and a scrubbed environment."""
    work_dir = _validate_request(request)
    environment = _sanitized_environment(request.env)
    preexec_fn, limits_enforced, degraded_reason = _resource_setup(request)
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    started = time.monotonic()
    process = subprocess.Popen(
        list(request.command),
        cwd=str(work_dir),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name != "nt",
        creationflags=creationflags,
        preexec_fn=preexec_fn,  # type: ignore[arg-type]
    )
    job: _WindowsJobHandle | None = None
    try:
        if os.name == "nt":
            job_error = None
            if request.memory_limit_bytes is not None:
                job, job_error = _assign_windows_job(
                    process, int(request.memory_limit_bytes)
                )
            limits_enforced, degraded_reason = _windows_limit_status(
                request, job is not None, job_error
            )

        if process.stdout is None or process.stderr is None:
            _terminate_process_tree(process)
            raise RuntimeError("failed to establish parser output pipes")

        stdout = bytearray()
        stderr = bytearray()
        output_limit_hit = threading.Event()
        readers = [
            threading.Thread(
                target=_drain_bounded,
                args=(
                    process.stdout,
                    int(request.stdout_limit_bytes),
                    stdout,
                    output_limit_hit,
                ),
                daemon=True,
            ),
            threading.Thread(
                target=_drain_bounded,
                args=(
                    process.stderr,
                    int(request.stderr_limit_bytes),
                    stderr,
                    output_limit_hit,
                ),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()

        deadline = started + float(request.timeout_seconds)
        timed_out = False
        while process.poll() is None:
            if output_limit_hit.is_set():
                _terminate_process_tree(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _terminate_process_tree(process)
                break
            time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))

        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process)
            process.wait(timeout=2.0)
        for reader in readers:
            reader.join(timeout=2.0)
        duration = time.monotonic() - started
        return SandboxResult(
            command=request.command,
            returncode=int(process.returncode),
            stdout=bytes(stdout),
            stderr=bytes(stderr),
            duration_seconds=duration,
            timed_out=timed_out,
            output_limit_exceeded=output_limit_hit.is_set(),
            resource_limits_enforced=limits_enforced,
            resource_limit_degraded_reason=degraded_reason,
        )
    finally:
        if job is not None:
            job.close()
