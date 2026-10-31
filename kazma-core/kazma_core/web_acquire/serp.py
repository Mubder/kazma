"""Centralized web search for research, agents, and future KB discovery.

The package re-exports :func:`search`; this module is ``serp`` (the results
page it returns) so that ``kazma_core.web_acquire.search`` names one thing,
the function (``tests/test_package_namespaces.py``).
"""

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
    #: Each result's title and snippet, by its URL (bounded): what the
    #: research ranker reads as evidence that a result is about its query.
    snippets: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    latency_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


#: A result's own address in ``web_search``'s markdown.
_RESULT_URL_RE = re.compile(r"^\*\*URL:\*\*\s*(https?://\S+)", re.I | re.M)


def extract_urls_from_search(markdown: str) -> list[str]:
    """Extract unique http(s) URLs from search markdown (order preserved).

    ``web_search``'s markdown names each result on a ``**URL:**`` line; when
    it has those lines they are the results. Every other URL in it is not a
    result: a snippet's text, or the backends it tried
    ("searxng:empty@http://host.docker.internal:8088"), which made an empty
    search "ok" with one candidate -- the search engine's own address (live
    2026-10-31, a deep research). An empty search's text has no result.
    Markdown with no such lines is read for any URL.
    """
    text = markdown or ""
    if text.lstrip().startswith("No web results for:"):
        return []
    found = [m.group(1) for m in _RESULT_URL_RE.finditer(text)] or [m.group(0) for m in _URL_RE.finditer(text)]
    seen: set[str] = set()
    out: list[str] = []
    for raw in found:
        u = raw.rstrip(".,;:)")
        if u in seen:
            continue
        # Skip obvious non-content
        host = (urlparse(u).hostname or "").lower()
        if not host or host in ("example.com", "localhost"):
            continue
        seen.add(u)
        out.append(u)
    return out


#: Characters of a result's title and snippet kept for ranking.
_SNIPPET_MAX = 400


def _snippets_by_url(markdown: str) -> dict[str, str]:
    """Each result's title and snippet from ``web_search``'s markdown
    (``## n. title`` / ``**URL:** href`` / body), by URL."""
    out: dict[str, str] = {}
    title, url, body = "", "", []

    def flush() -> None:
        if url and url not in out:
            out[url] = " ".join([title, *body]).strip()[:_SNIPPET_MAX]

    for line in (markdown or "").splitlines():
        heading = re.match(r"^##\s+\d+\.\s*(.*)$", line)
        if heading:
            flush()
            title, url, body = heading.group(1).strip(), "", []
            continue
        found = _RESULT_URL_RE.match(line)
        if found:
            url = found.group(1).rstrip(".,;:)")
        elif title and line.strip():
            body.append(line.strip())
    flush()
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
        logger.debug("[web_acquire] search failed purpose=%s", purpose, exc_info=True)
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
        snippets={u: t for u, t in _snippets_by_url(md).items() if u in urls},
        error="no results" if empty else None,
        latency_ms=round(ms, 1),
        meta={"purpose": purpose, "url_count": len(urls)},
    )
