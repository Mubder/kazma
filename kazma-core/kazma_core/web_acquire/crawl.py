"""Profile-based bounded crawl — shared engine entry for research (and future KB).

Delegates to ``tools.web_research.crawl_site`` so behavior stays identical;
profiles only supply caps. Knowledge Library may keep its own discovery
(sitemap / Firecrawl map) but should use :func:`fetch_text` per page.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from kazma_core.web_acquire.profiles import get_crawl_profile, profile_to_crawl_kwargs

__all__ = ["CrawlResult", "crawl"]

logger = logging.getLogger(__name__)


@dataclass
class CrawlResult:
    ok: bool
    seed_url: str
    markdown: str = ""
    profile: str = "research_brief"
    error: str | None = None
    latency_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


async def crawl(
    seed_url: str,
    *,
    profile: str = "research_brief",
    max_pages: int | None = None,
    max_depth: int | None = None,
    same_domain_only: bool | None = None,
    delay_ms: int | None = None,
    purpose: str = "research",
) -> CrawlResult:
    """Bounded multi-page crawl with a named :class:`CrawlProfile`.

    Explicit kwargs override the profile when not None.
    """
    seed = (seed_url or "").strip()
    t0 = time.perf_counter()
    if not seed:
        return CrawlResult(ok=False, seed_url="", error="empty seed_url", profile=profile)

    prof = get_crawl_profile(profile)
    kw = profile_to_crawl_kwargs(prof)
    if max_pages is not None:
        kw["max_pages"] = max_pages
    if max_depth is not None:
        kw["max_depth"] = max_depth
    if same_domain_only is not None:
        kw["same_domain_only"] = same_domain_only
    if delay_ms is not None:
        kw["delay_ms"] = delay_ms

    try:
        from kazma_core.tools.web_research import crawl_site

        md = await crawl_site(seed, **kw)
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        logger.debug("[web_acquire.crawl] failed purpose=%s", purpose, exc_info=True)
        return CrawlResult(
            ok=False,
            seed_url=seed,
            profile=prof.name,
            error=f"{type(exc).__name__}: {exc}"[:300],
            latency_ms=round(ms, 1),
            meta={"purpose": purpose},
        )

    ms = (time.perf_counter() - t0) * 1000
    md = md if isinstance(md, str) else str(md or "")
    if md.startswith("Error:"):
        return CrawlResult(
            ok=False,
            seed_url=seed,
            markdown=md,
            profile=prof.name,
            error=md[6:].strip() or md,
            latency_ms=round(ms, 1),
            meta={"purpose": purpose, **kw},
        )
    return CrawlResult(
        ok=True,
        seed_url=seed,
        markdown=md,
        profile=prof.name,
        latency_ms=round(ms, 1),
        meta={"purpose": purpose, **kw},
    )
