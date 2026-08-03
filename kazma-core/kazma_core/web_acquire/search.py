"""Centralized web search for research, agents, and future KB discovery."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

__all__ = ["SearchResult", "extract_urls_from_search", "search"]

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s\)\]\>\"']+", re.I)


@dataclass
class SearchResult:
    """Markdown SERP plus parsed URLs for pipelines."""

    ok: bool
    query: str
    markdown: str = ""
    urls: list[str] = field(default_factory=list)
    error: str | None = None
    latency_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


def extract_urls_from_search(markdown: str) -> list[str]:
    """Extract unique http(s) URLs from search markdown (order preserved)."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_RE.finditer(markdown or ""):
        u = m.group(0).rstrip(".,;:)")
        if u in seen:
            continue
        # Skip obvious non-content
        host = (urlparse(u).hostname or "").lower()
        if not host or host in ("example.com", "localhost"):
            continue
        seen.add(u)
        out.append(u)
    return out


async def search(
    query: str,
    *,
    max_results: int = 8,
    purpose: str = "generic",
) -> SearchResult:
    """Run multi-backend web search (SearXNG → DuckDuckGo → …).

    Args:
        query: Search string.
        max_results: Cap (tool clamps 1–15).
        purpose: Caller tag — ``research`` | ``kb`` | ``generic``.
    """
    q = (query or "").strip()
    t0 = time.perf_counter()
    if not q:
        return SearchResult(
            ok=False, query="", error="empty query", meta={"purpose": purpose}
        )

    try:
        from kazma_core.tools.web_search import web_search

        md = await web_search(q, max_results=max_results)
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        logger.debug("[web_acquire.search] failed purpose=%s", purpose, exc_info=True)
        return SearchResult(
            ok=False,
            query=q,
            error=f"{type(exc).__name__}: {exc}"[:300],
            latency_ms=round(ms, 1),
            meta={"purpose": purpose},
        )

    ms = (time.perf_counter() - t0) * 1000
    md = md if isinstance(md, str) else str(md or "")
    if md.startswith("Error:"):
        return SearchResult(
            ok=False,
            query=q,
            markdown=md,
            error=md[6:].strip() or md,
            latency_ms=round(ms, 1),
            meta={"purpose": purpose},
        )
    urls = extract_urls_from_search(md)
    # Empty SERP is ok=False for pipelines that need candidates
    empty = not urls and "no results" in md.lower()
    return SearchResult(
        ok=not empty,
        query=q,
        markdown=md,
        urls=urls,
        error="no results" if empty else None,
        latency_ms=round(ms, 1),
        meta={"purpose": purpose, "url_count": len(urls)},
    )
