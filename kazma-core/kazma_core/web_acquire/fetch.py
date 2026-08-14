"""Centralized page fetch — single recovery ladder for all Kazma products.

Delegates to ``tools.read_url`` implementation (Jina / Firecrawl / httpx /
Playwright) so there is one SoT for hard-page recovery. Callers should use
this module instead of importing private ``_fetch_*`` helpers.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = ["FetchResult", "fetch_text", "jina_reader"]

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Structured fetch outcome for research evidence / KB ingest logging."""

    ok: bool
    url: str
    text: str = ""
    error: str | None = None
    purpose: str = "generic"
    char_count: int = 0
    latency_ms: float = 0.0
    # Optional backend hint when known (often empty — ladder is opaque)
    backend: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_error_stub(self) -> bool:
        return (self.text or "").startswith("Error:")


async def fetch_text(
    url: str,
    *,
    purpose: str = "generic",
) -> FetchResult:
    """Fetch full page text for *url* using the shared recovery ladder.

    Args:
        url: http(s) URL (or bare host; scheme added downstream).
        purpose: Caller tag for logs/metrics — ``research`` | ``kb`` |
            ``crawl`` | ``generic``.

    Returns:
        :class:`FetchResult`. On hard failure ``ok=False`` and ``error`` set;
        ``text`` may still hold the ``Error: …`` stub from the ladder.
    """
    u = (url or "").strip()
    t0 = time.perf_counter()
    if not u:
        return FetchResult(
            ok=False,
            url="",
            error="empty url",
            purpose=purpose,
            latency_ms=0.0,
        )
    try:
        # Public alias first; the private name is the backward-compat fallback.
        try:
            from kazma_core.tools.read_url import fetch_full_text as _ladder
        except ImportError:
            from kazma_core.tools.read_url import _fetch_full_text as _ladder

        text = await _ladder(u)
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        logger.debug("[web_acquire.fetch] failed purpose=%s url=%s", purpose, u[:120], exc_info=True)
        return FetchResult(
            ok=False,
            url=u,
            error=f"{type(exc).__name__}: {exc}"[:300],
            purpose=purpose,
            latency_ms=round(ms, 1),
        )
    ms = (time.perf_counter() - t0) * 1000
    text = text if isinstance(text, str) else str(text or "")
    if text.startswith("Error:"):
        return FetchResult(
            ok=False,
            url=u,
            text=text,
            error=text[6:].strip() or text,
            purpose=purpose,
            char_count=len(text),
            latency_ms=round(ms, 1),
        )
    return FetchResult(
        ok=bool(text.strip()),
        url=u,
        text=text,
        error=None if text.strip() else "empty extract",
        purpose=purpose,
        char_count=len(text),
        latency_ms=round(ms, 1),
    )


async def jina_reader(url: str) -> str | None:
    """Public Jina Reader helper (seed expand / recovery). Prefer :func:`fetch_text`."""
    try:
        from kazma_core.tools.read_url import _try_jina_reader

        return await _try_jina_reader(url)
    except Exception:
        logger.debug("[web_acquire.jina_reader] failed", exc_info=True)
        return None
