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

    try:
        code_file.write_text(code, encoding="utf-8")

        # Build subprocess keyword arguments (preexec_fn is POSIX-only)
        subprocess_kwargs: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": tmp_dir,
            "env": {
                "PATH": os.environ.get("PATH", ""),
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
        # Clean up temp directory
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as exc:
            logger.debug("Temp dir cleanup failed: %s", exc)
