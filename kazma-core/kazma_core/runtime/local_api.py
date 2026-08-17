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

    Loopback is always tried first. ``KAZMA_PUBLIC_URL`` is for browsers
    and OAuth — posting the TUI turn at the public host often returns a
    200 HTML/challenge page, which used to look like an empty reply.
    """
    found: list[str] = []
    port = (os.environ.get("KAZMA_PORT") or "").strip()
    if port:
        found.append(f"http://127.0.0.1:{port}")
    found.append("http://127.0.0.1:9090")
    found.append("http://127.0.0.1:8000")
    for key in ("KAZMA_BASE_URL", "KAZMA_PUBLIC_URL"):
        raw = (os.environ.get(key) or "").strip().rstrip("/")
        if raw:
            found.append(raw)
    loopback = [u for u in found if _is_loopback(u)]
    remote = [u for u in found if not _is_loopback(u)]
    out: list[str] = []
    for u in loopback + remote:
        if u not in out:
            out.append(u)
    return out


def auth_headers() -> dict[str, str]:
    secret = (os.environ.get("KAZMA_SECRET") or "").strip()
    if not secret:
        return {}
    return {"X-Kazma-Secret": secret}


def first_reachable(bases: Iterable[str] | None = None) -> list[str]:
    """Return candidate list (reachability is checked by the caller)."""
    return list(bases) if bases is not None else candidate_api_bases()
