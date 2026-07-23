"""Sandboxed Python code execution tool.

Runs user-provided Python snippets with layered isolation:

1. **Docker jail** (preferred when available) — ``--network none``, memory
   limit, read-only root, ephemeral tmpfs. Enabled by default when the
   ``docker`` CLI is on PATH, or forced via ``KAZMA_CODE_EXEC_DOCKER=1``.
2. **Local subprocess** fallback — ``python -I``, import blocklist, scrubbed
   env, resource limits (POSIX) / Job Object (Windows).

Usage::

    from kazma_core.tools.code_exec import python_exec
    result = await python_exec("print('hello')")
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["DEFAULT_DOCKER_IMAGE", "DEFAULT_TIMEOUT", "MAX_OUTPUT_CHARS", "MEMORY_LIMIT_MB", "docker_available", "python_exec", "reset_docker_probe", "use_docker_jail"]

logger = logging.getLogger(__name__)

# The 'resource' module is POSIX-only and does not exist on Windows.
try:
    import resource as _resource_module
except ImportError:
    _resource_module = None  # type: ignore[assignment]

MAX_OUTPUT_CHARS = 4000
DEFAULT_TIMEOUT = 30  # seconds
MEMORY_LIMIT_MB = 512
DEFAULT_DOCKER_IMAGE = "python:3.12-slim"

# preexec_fn is only supported on POSIX platforms (Unix/Linux/macOS).
_IS_UNIX = sys.platform != "win32" and _resource_module is not None

# Defense-in-depth for local fallback: block imports that enable network,
# process spawn, or native code escapes.
_BLOCKED_IMPORT_ROOTS: frozenset[str] = frozenset({
    # Network / process / native escapes
    "socket", "ssl", "select", "selectors",
    "subprocess", "multiprocessing", "concurrent",
    "ctypes", "cffi",
    "http", "urllib", "urllib3", "requests", "httpx", "aiohttp",
    "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc",
    "webbrowser", "pty", "fcntl", "resource",
    # Host FS / interpreter escapes (audit H3 — local fallback is not a jail)
    "os", "sys", "pathlib", "shutil", "io", "tempfile",
    "importlib", "runpy", "code", "codeop", "builtins",
    "pickle", "marshal", "shelve", "sqlite3",
})

_SANDBOX_PREAMBLE = f'''\
# Auto-injected by kazma code_exec — import blocklist (defense-in-depth)
import builtins as _b
_real_import = _b.__import__

def _safe_import(name, globals=None, locals=None, fromlist=(), level=0,
                 _blocked=frozenset({sorted(_BLOCKED_IMPORT_ROOTS)!r}),
                 _imp=_real_import):
    root = (name or "").split(".", 1)[0]
    if root in _blocked:
        raise ImportError(
            f"Import of {{name!r}} is blocked in the code_exec sandbox"
        )
    return _imp(name, globals, locals, fromlist, level)

_b.__import__ = _safe_import
del _b, _real_import, _safe_import
'''

# Cached docker availability probe
_docker_available: bool | None = None


def _docker_cli() -> str | None:
    return shutil.which("docker")


def docker_available() -> bool:
    """True if the docker CLI is on PATH (does not pull images)."""
    global _docker_available
    if _docker_available is not None:
        return _docker_available
    _docker_available = _docker_cli() is not None
    return _docker_available


def use_docker_jail() -> bool:
    """Whether python_exec should attempt a Docker jail.

    Env ``KAZMA_CODE_EXEC_DOCKER``:
      - ``1`` / ``true`` / ``on``  → force Docker (error if unavailable)
      - ``0`` / ``false`` / ``off`` → force local subprocess
      - ``auto`` / unset           → Docker when CLI present
    """
    raw = (os.environ.get("KAZMA_CODE_EXEC_DOCKER") or "auto").strip().lower()
    if raw in ("0", "false", "off", "no", "local"):
        return False
    if raw in ("1", "true", "on", "yes", "docker", "force"):
        return True
    return docker_available()


def reset_docker_probe() -> None:
    """Clear cached docker availability (tests)."""
    global _docker_available
    _docker_available = None


def _set_limits() -> None:
    """Set resource limits in the child process (pre-exec). POSIX only."""
    if _resource_module is None:
        return

    mem_bytes = MEMORY_LIMIT_MB * 1024 * 1024
    try:
        _resource_module.setrlimit(_resource_module.RLIMIT_AS, (mem_bytes, mem_bytes))  # type: ignore[attr-defined]
    except (ValueError, OSError):
        pass

    try:
        _resource_module.setrlimit(
            _resource_module.RLIMIT_CPU,  # type: ignore[attr-defined]
            (DEFAULT_TIMEOUT + 5, DEFAULT_TIMEOUT + 5),
        )
    except (ValueError, OSError):
        pass


def _assign_to_job_object(proc: Any) -> Any:
    """Windows Job Object memory + kill-on-close limits."""
    if sys.platform != "win32":
        return None

    try:
        import ctypes
        from ctypes import wintypes

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
        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            return None

        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_JOB_MEMORY
        )
        limits.JobMemoryLimit = MEMORY_LIMIT_MB * 1024 * 1024

        res = kernel32.SetInformationJobObject(
            job_handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not res:
            kernel32.CloseHandle(job_handle)
            return None

        proc_handle = None
        if (
            hasattr(proc, "_transport")
            and hasattr(proc._transport, "_proc")
            and hasattr(proc._transport._proc, "_handle")
        ):
            proc_handle = proc._transport._proc._handle

        if proc_handle:
            res = kernel32.AssignProcessToJobObject(job_handle, int(proc_handle))
            if not res:
                kernel32.CloseHandle(job_handle)
                return None
            return job_handle

        kernel32.CloseHandle(job_handle)
        return None
    except Exception as exc:
        logger.debug("[code_exec] Job Object failed: %s", exc, exc_info=True)
        return None


def _format_output(exit_code: int, stdout: bytes, stderr: bytes) -> str:
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    combined = out
    if err:
        combined = (out + "\n" + err).strip() if out else err
    if len(combined) > MAX_OUTPUT_CHARS:
        original_len = len(combined)
        combined = (
            combined[:MAX_OUTPUT_CHARS]
            + f"\n[truncated {original_len - MAX_OUTPUT_CHARS} chars]"
        )
    parts = [f"[Exit code: {exit_code}]"]
    if combined:
        parts.append(combined)
    return "\n".join(parts)


async def _run_local_subprocess(code_file: Path, tmp_dir: str, timeout: int) -> str:
    """Local -I subprocess with import blocklist + resource limits."""
    python_dir = os.path.dirname(sys.executable)
    if sys.platform == "win32":
        sys_root = os.environ.get(
            "SystemRoot", os.path.join(os.environ.get("WINDIR", "C:\\Windows"))
        )
        sys32 = os.path.join(sys_root, "System32")
        path_env = f"{python_dir};{sys32};{sys_root}"
    else:
        path_parts = [python_dir]
        for part in (os.environ.get("PATH") or "").split(os.pathsep):
            p = part.strip()
            if not p:
                continue
            if p.startswith(("/home/", "/Users/", "/tmp", "/var/tmp")):
                continue
            if "/.venv" in p or "/venv" in p or "/site-packages" in p:
                continue
            path_parts.append(p)
        path_env = os.pathsep.join(path_parts) or python_dir

    child_env = {
        "PATH": path_env,
        "HOME": tmp_dir,
        "TMPDIR": tmp_dir,
        "TEMP": tmp_dir,
        "TMP": tmp_dir,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    subprocess_kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "cwd": tmp_dir,
        "env": child_env,
    }
    if _IS_UNIX:
        subprocess_kwargs["preexec_fn"] = _set_limits

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        str(code_file),
        **subprocess_kwargs,
    )

    job_handle = None
    if sys.platform == "win32":
        job_handle = _assign_to_job_object(proc)

    try:
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return f"[Exit code: 124 — timed out after {timeout}s]"
        return _format_output(proc.returncode or 0, stdout, stderr)
    finally:
        if sys.platform == "win32" and job_handle:
            try:
                import ctypes

                ctypes.windll.kernel32.CloseHandle(job_handle)
            except Exception:
                pass


async def _run_docker_jail(code_file: Path, tmp_dir: str, timeout: int) -> str:
    """Run snippet inside a disposable Docker container (network none)."""
    docker = _docker_cli()
    if not docker:
        raise RuntimeError("docker CLI not found")

    image = (os.environ.get("KAZMA_CODE_EXEC_IMAGE") or DEFAULT_DOCKER_IMAGE).strip()
    # Mount work dir read-only; use tmpfs for /tmp. No network. Memory capped.
    # --rm cleans up; --user avoids root when possible (numeric nobody).
    work_mount = f"{tmp_dir}:/work:ro"
    cmd = [
        docker, "run", "--rm",
        "--network", "none",
        "--memory", f"{MEMORY_LIMIT_MB}m",
        "--memory-swap", f"{MEMORY_LIMIT_MB}m",
        "--cpus", "1",
        "--pids-limit", "64",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--tmpfs", "/var/tmp:rw,noexec,nosuid,size=16m",
        "-v", work_mount,
        "-w", "/work",
        "--user", "65534:65534",  # nobody
        image,
        "python", "-I", "/work/snippet.py",
    ]

    logger.info("[code_exec] Docker jail: image=%s timeout=%s", image, timeout)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 15)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"[Exit code: 124 — docker timed out after {timeout}s]"

    # Prefix so operators know isolation mode was used
    body = _format_output(proc.returncode or 0, stdout, stderr)
    return f"[sandbox: docker network=none image={image}]\n{body}"


async def python_exec(code: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Execute Python code in a sandboxed environment.

    Prefers Docker (``--network none``) when ``use_docker_jail()`` is true;
    falls back to a local isolated subprocess with import blocklist.

    Args:
        code:    Python source code to execute.
        timeout: Maximum execution time in seconds (default 30).

    Returns:
        Formatted output with exit code, stdout, and stderr.
    """
    if not code or not code.strip():
        return "Error: No code provided."

    tmp_dir = tempfile.mkdtemp(prefix="kazma_exec_")
    code_file = Path(tmp_dir) / "snippet.py"

    try:
        # Preamble + user code (import blocklist still applies inside Docker)
        code_file.write_text(_SANDBOX_PREAMBLE + "\n" + code, encoding="utf-8")
        # Container runs as nobody — ensure world-readable
        try:
            os.chmod(tmp_dir, 0o755)
            os.chmod(code_file, 0o644)
        except OSError:
            pass

        prod = (os.environ.get("KAZMA_PRODUCTION") or "").lower() in (
            "1", "true", "on", "yes",
        )
        forced = (os.environ.get("KAZMA_CODE_EXEC_DOCKER") or "").lower() in (
            "1", "true", "on", "yes", "docker", "force", "required",
        ) or prod

        if use_docker_jail():
            try:
                return await _run_docker_jail(code_file, tmp_dir, timeout)
            except Exception as exc:
                if forced:
                    logger.error("[code_exec] Docker jail required but failed: %s", exc)
                    return f"[Exit code: 1]\nDocker jail failed: {exc}"
                logger.warning(
                    "[code_exec] Docker jail unavailable (%s) — local fallback", exc
                )
                local = await _run_local_subprocess(code_file, tmp_dir, timeout)
                return f"[sandbox: local-fallback reason={exc}]\n{local}"

        if forced:
            return (
                "[Exit code: 1]\nDocker jail required "
                "(KAZMA_PRODUCTION or KAZMA_CODE_EXEC_DOCKER=force) but Docker is unavailable."
            )

        return await _run_local_subprocess(code_file, tmp_dir, timeout)

    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as exc:
            logger.debug("Temp dir cleanup failed: %s", exc)
