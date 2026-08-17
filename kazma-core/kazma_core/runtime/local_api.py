"""How in-process mouths find the running Kazma HTTP API.

TUI / CLI are separate processes. They are mouths of the *same* brain
only if they talk to the server the operator already started — not a
second LLM loop.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

__all__ = ["auth_headers", "candidate_api_bases"]


def _is_loopback(url: str) -> bool:
    low = (url or "").lower()
    return (
        "://127.0.0.1" in low
        or "://localhost" in low
        or "://[::1]" in low
    )


def candidate_api_bases() -> list[str]:
    """Deduped API roots for in-process mouths (TUI / CLI).

    Loopback first. ``KAZMA_BASE_URL`` is an explicit override (may be remote).
    ``KAZMA_PUBLIC_URL`` is *not* used — that host is for browsers/OAuth and
    typically 302s to a login HTML page.
    """
    found: list[str] = []
    port = (os.environ.get("KAZMA_PORT") or "").strip()
    if port:
        found.append(f"http://127.0.0.1:{port}")
    found.append("http://127.0.0.1:9090")
    found.append("http://127.0.0.1:8000")
    raw = (os.environ.get("KAZMA_BASE_URL") or "").strip().rstrip("/")
    if raw:
        found.append(raw)
    loopback = [u for u in found if _is_loopback(u)]
    remote = [u for u in found if not _is_loopback(u)]
    out: list[str] = []
    for u in loopback + remote:
        if u not in out:
            out.append(u)
    return out


def _secret_from_dotenv() -> str:
    """Read KAZMA_SECRET from cwd/.env when the process env is empty."""
    import sys

    if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    try:
        from pathlib import Path

        env_path = Path.cwd() / ".env"
        if not env_path.is_file():
            return ""
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, val = raw.split("=", 1)
            if key.strip() == "KAZMA_SECRET":
                return val.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def auth_headers() -> dict[str, str]:
    secret = (os.environ.get("KAZMA_SECRET") or "").strip()
    if not secret:
        secret = _secret_from_dotenv()
    if not secret:
        return {}
    return {"X-Kazma-Secret": secret}


def first_reachable(bases: Iterable[str] | None = None) -> list[str]:
    """Return candidate list (reachability is checked by the caller)."""
    return list(bases) if bases is not None else candidate_api_bases()
