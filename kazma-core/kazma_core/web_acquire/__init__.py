"""Centralized web search / scrape / crawl for all of Kazma.

**One I/O stack, many product sinks.**

| Consumer | Uses |
|----------|------|
| Research tools / pipeline | :func:`search`, :func:`fetch_text` |
| Knowledge Library ingest | :func:`fetch_text`, discover (KB-owned policy) |
| Chat tools (`read_url`, …) | Same fetch recovery ladder via ``tools/read_url`` |

Profiles (research brief vs KB site) share recovery, proxy, and SSRF —
only caps and discovery strategy differ.

LLM API traffic must **never** use this package (use ``http_pool``).
"""

from __future__ import annotations

from kazma_core.web_acquire.fetch import FetchResult, fetch_text
from kazma_core.web_acquire.profiles import (
    CRAWL_PROFILES,
    CrawlProfile,
    get_crawl_profile,
    profile_to_crawl_kwargs,
)
from kazma_core.web_acquire.rank import RankedUrl, rank_urls, score_url
from kazma_core.web_acquire.search import SearchResult, extract_urls_from_search, search

__all__ = [
    "CrawlProfile",
    "CRAWL_PROFILES",
    "FetchResult",
    "RankedUrl",
    "SearchResult",
    "extract_urls_from_search",
    "fetch_text",
    "get_crawl_profile",
    "profile_to_crawl_kwargs",
    "rank_urls",
    "score_url",
    "search",
]
