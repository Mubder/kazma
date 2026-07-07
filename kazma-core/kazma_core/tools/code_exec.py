"""Sandboxed Python code execution tool.

Runs user-provided Python snippets in an isolated subprocess with
resource limits (timeout, memory) and isolated mode (-I flag).

Usage:
    from kazma_core.tools.code_exec import python_exec
    result = await python_exec("print('hello')")
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil

logger = logging.getLogger(__name__)
import sys
import tempfile
from pathlib import Path
from typing import Any

# The 'resource' module is POSIX-only and does not exist on Windows.
try:
    import resource as _resource_module
except ImportError:
    _resource_module = None  # type: ignore[assignment]

MAX_OUTPUT_CHARS = 4000
DEFAULT_TIMEOUT = 30  # seconds
MEMORY_LIMIT_MB = 512

# preexec_fn is only supported on POSIX platforms (Unix/Linux/macOS).
_IS_UNIX = sys.platform != "win32" and _resource_module is not None


def _set_limits() -> None:
    """Set resource limits in the child process (pre-exec).

    Only called on POSIX platforms where the ``resource`` module is available.
    """
    if _resource_module is None:
        return

    # Memory limit: 512MB
    mem_bytes = MEMORY_LIMIT_MB * 1024 * 1024
    try:
        _resource_module.setrlimit(_resource_module.RLIMIT_AS, (mem_bytes, mem_bytes))  # type: ignore[attr-defined]
    except (ValueError, OSError):
        pass  # Some systems don't support this

    # CPU time limit (backup)
    try:
        _resource_module.setrlimit(_resource_module.RLIMIT_CPU, (DEFAULT_TIMEOUT + 5, DEFAULT_TIMEOUT + 5))  # type: ignore[attr-defined]
    except (ValueError, OSError):
        pass


def _assign_to_job_object(proc: Any) -> Any:
    """Create a Windows Job Object, configure memory & kill limits, and assign the process.

    Only called on Windows. Returns the job handle if successful, else None.
    """
    if sys.platform != "win32":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        # Define structures
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
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

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JOB_OBJECT_LIMIT_JOB_MEMORY = 0x400
        JobObjectExtendedLimitInformation = 9

        kernel32 = ctypes.windll.kernel32

        # Create job object
        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            logger.debug("[code_exec] CreateJobObjectW failed with error %s", kernel32.GetLastError())
            return None

        # Configure limits
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_JOB_MEMORY
        limits.JobMemoryLimit = MEMORY_LIMIT_MB * 1024 * 1024  # e.g., 512MB

        res = kernel32.SetInformationJobObject(
            job_handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(limits),
            ctypes.sizeof(limits)
        )
        if not res:
            logger.debug("[code_exec] SetInformationJobObject failed with error %s", kernel32.GetLastError())
            kernel32.CloseHandle(job_handle)
            return None

        # Resolve process handle
        proc_handle = None
        if hasattr(proc, "_transport") and hasattr(proc._transport, "_proc") and hasattr(proc._transport._proc, "_handle"):
            proc_handle = proc._transport._proc._handle

        if proc_handle:
            res = kernel32.AssignProcessToJobObject(job_handle, int(proc_handle))
            if not res:
                logger.debug("[code_exec] AssignProcessToJobObject failed with error %s", kernel32.GetLastError())
                kernel32.CloseHandle(job_handle)
                return None
            return job_handle
        else:
            logger.debug("[code_exec] Process handle not found for assignment")
            kernel32.CloseHandle(job_handle)
            return None

    except Exception as exc:
        logger.debug("[code_exec] Failed to configure Windows Job Object: %s", exc, exc_info=True)
        return None


async def python_exec(code: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Execute Python code in a sandboxed subprocess.

    Args:
        code:    Python source code to execute.
        timeout: Maximum execution time in seconds (default 30).

    Returns:
        Formatted output with exit code, stdout, and stderr.
    """
    if not code or not code.strip():
        return "Error: No code provided."

    # Create isolated temp directory
    tmp_dir = tempfile.mkdtemp(prefix="kazma_exec_")
    code_file = Path(tmp_dir) / "snippet.py"
    job_handle = None

    try:
        code_file.write_text(code, encoding="utf-8")

        # Build restricted PATH environment for Windows
        if sys.platform == "win32":
            python_dir = os.path.dirname(sys.executable)
            sys_root = os.environ.get("SystemRoot", "C:\\Windows")
            sys32 = os.path.join(sys_root, "System32")
            path_env = f"{python_dir};{sys32};{sys_root}"
        else:
            path_env = os.environ.get("PATH", "")

        # Build subprocess keyword arguments (preexec_fn is POSIX-only)
        subprocess_kwargs: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": tmp_dir,
            "env": {
                "PATH": path_env,
                "HOME": tmp_dir,
                "LANG": os.environ.get("LANG", "C.UTF-8"),
            },
        }
        if _IS_UNIX:
            subprocess_kwargs["preexec_fn"] = _set_limits

        # Spawn subprocess with isolated mode (-I) and resource limits
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",  # isolated mode: no user site-packages, no PYTHON*
            str(code_file),
            **subprocess_kwargs,
        )

        if sys.platform == "win32":
            job_handle = _assign_to_job_object(proc)

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return f"[Exit code: 124 — timed out after {timeout}s]"

        exit_code = proc.returncode or 0
        out = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")

        # Build output
        parts: list[str] = []
        combined = out
        if err:
            combined = (out + "\n" + err).strip() if out else err

        # Truncate if needed
        if len(combined) > MAX_OUTPUT_CHARS:
            original_len = len(combined)
            combined = combined[:MAX_OUTPUT_CHARS] + f"\n[truncated {original_len - MAX_OUTPUT_CHARS} chars]"

        parts.append(f"[Exit code: {exit_code}]")
        if combined:
            parts.append(combined)

        return "\n".join(parts)

    finally:
        # Clean up job object handle on Windows
        if sys.platform == "win32" and job_handle:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(job_handle)
            except Exception:
                pass

        # Clean up temp directory
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as exc:
            logger.debug("Temp dir cleanup failed: %s", exc)
