"""E2B Firecracker sandbox for untrusted ``python_exec``.

Default remains Docker/local (one trusted operator). When an E2B API key is
set, HITL-approved code runs in a microVM instead of on the Kazma host.

Kill-switch: ``KAZMA_E2B=0``. Keys: ``KAZMA_E2B_API_KEY`` or ``E2B_API_KEY``.
SDK is optional (``pip install 'kazma[sandbox]'``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["e2b_api_key", "e2b_available", "e2b_enabled", "run_python"]


def e2b_api_key() -> str:
    return (
        os.environ.get("KAZMA_E2B_API_KEY") or os.environ.get("E2B_API_KEY") or ""
    ).strip()


def e2b_enabled() -> bool:
    """True when the operator asked for E2B and a key is present."""
    raw = (os.environ.get("KAZMA_E2B") or "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    return bool(e2b_api_key())


def e2b_available() -> bool:
    """True when enabled AND an E2B SDK can be imported."""
    if not e2b_enabled():
        return False
    return _sdk() is not None


def _sdk() -> tuple[str, Any] | None:
    try:
        from e2b_code_interpreter import Sandbox  # type: ignore[import-not-found]

        return ("interpreter", Sandbox)
    except ImportError:
        pass
    try:
        from e2b import Sandbox  # type: ignore[import-not-found]

        return ("core", Sandbox)
    except ImportError:
        return None


def _format_result(*, stdout: str, stderr: str, exit_code: int, timeout: int) -> str:
    stdout = (stdout or "").rstrip()
    stderr = (stderr or "").rstrip()
    parts = [f"[sandbox: e2b timeout={timeout}s]", f"[Exit code: {exit_code}]"]
    if stdout:
        parts.append(f"[stdout]\n{stdout}")
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    body = "\n".join(parts)
    max_chars = 4000
    if len(body) > max_chars:
        return body[: max_chars - 20] + "\n…[truncated]"
    return body


def _run_sync(code: str, timeout: int) -> str:
    sdk = _sdk()
    if sdk is None:
        raise RuntimeError(
            "E2B is configured but the SDK is missing. "
            "Install with: pip install 'kazma[sandbox]'"
        )
    kind, Sandbox = sdk
    key = e2b_api_key()
    kwargs: dict[str, Any] = {"api_key": key} if key else {}
    # timeout is seconds for our API; E2B create timeout is seconds in REST,
    # milliseconds in some SDK versions — pass generously.
    try:
        sbx = Sandbox.create(timeout=max(int(timeout), 15), **kwargs)
    except TypeError:
        sbx = Sandbox.create(**kwargs)
    try:
        if kind == "interpreter" and hasattr(sbx, "run_code"):
            execution = sbx.run_code(code)
            logs = getattr(execution, "logs", None)
            stdout = ""
            stderr = ""
            if logs is not None:
                out = getattr(logs, "stdout", "") or ""
                err = getattr(logs, "stderr", "") or ""
                stdout = "".join(out) if isinstance(out, (list, tuple)) else str(out)
                stderr = "".join(err) if isinstance(err, (list, tuple)) else str(err)
            text = getattr(execution, "text", None) or getattr(execution, "results", None)
            if text and not stdout:
                stdout = str(text)
            err_obj = getattr(execution, "error", None)
            exit_code = 1 if err_obj else 0
            if err_obj and not stderr:
                stderr = str(err_obj)
            return _format_result(
                stdout=stdout, stderr=stderr, exit_code=exit_code, timeout=timeout
            )
        # Core SDK: write snippet and run python3
        if hasattr(sbx, "files") and hasattr(sbx.files, "write"):
            sbx.files.write("/tmp/kazma_snippet.py", code)
            cmd = getattr(sbx, "commands", None)
            if cmd is not None and hasattr(cmd, "run"):
                result = cmd.run("python3 /tmp/kazma_snippet.py", timeout=timeout)
                return _format_result(
                    stdout=str(getattr(result, "stdout", "") or ""),
                    stderr=str(getattr(result, "stderr", "") or ""),
                    exit_code=int(getattr(result, "exit_code", 0) or 0),
                    timeout=timeout,
                )
        raise RuntimeError("E2B Sandbox has no run_code or commands.run")
    finally:
        for meth in ("kill", "close"):
            fn = getattr(sbx, meth, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    logger.debug("[e2b] sandbox %s failed", meth, exc_info=True)
                break


async def run_python(code: str, timeout: int = 30) -> str:
    """Run *code* in an E2B microVM. Raises on SDK/config failure."""
    if not e2b_enabled():
        raise RuntimeError("E2B is not enabled")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _run_sync(code, int(timeout)))
