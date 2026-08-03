"""Named crawl / acquire profiles for research vs Knowledge Library.

Same engine, different product caps — do not force KB through research
defaults (8 pages) or research through KB-scale crawls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "CrawlProfile",
    "CRAWL_PROFILES",
    "get_crawl_profile",
]


@dataclass(frozen=True)
class CrawlProfile:
    """Bounded multi-page crawl policy."""

    name: str
    max_pages: int
    max_depth: int
    same_domain_only: bool = True
    delay_ms: int = 300
    # Soft product ceilings (hard env ceilings still apply in crawl_site)
    description: str = ""


CRAWL_PROFILES: dict[str, CrawlProfile] = {
    "research_brief": CrawlProfile(
        name="research_brief",
        max_pages=8,
        max_depth=2,
        delay_ms=300,
        description="Quick research multi-page (default crawl_site)",
    ),
    "research_deep": CrawlProfile(
        name="research_deep",
        max_pages=20,
        max_depth=2,
        delay_ms=250,
        description="Deeper research site crawl within agent budgets",
    ),
    "single_page": CrawlProfile(
        name="single_page",
        max_pages=1,
        max_depth=0,
        delay_ms=0,
        description="One URL only (fetch_text preferred)",
    ),
    "kb_site": CrawlProfile(
        name="kb_site",
        max_pages=200,
        max_depth=4,
        delay_ms=200,
        description="Knowledge Library site ingest scale (discovery still KB-owned)",
    ),
}


def get_crawl_profile(name: str | None) -> CrawlProfile:
    """Resolve profile by name; unknown → research_brief."""
    key = (name or "research_brief").strip().lower()
    if key in CRAWL_PROFILES:
        return CRAWL_PROFILES[key]
    # aliases
    aliases = {
        "research": "research_brief",
        "brief": "research_brief",
        "deep": "research_deep",
        "kb": "kb_site",
        "docs": "kb_site",
        "knowledge": "kb_site",
        "single": "single_page",
    }
    mapped = aliases.get(key)
    if mapped and mapped in CRAWL_PROFILES:
        return CRAWL_PROFILES[mapped]
    return CRAWL_PROFILES["research_brief"]


def profile_to_crawl_kwargs(profile: CrawlProfile) -> dict[str, Any]:
    return {
        "max_pages": profile.max_pages,
        "max_depth": profile.max_depth,
        "same_domain_only": profile.same_domain_only,
        "delay_ms": profile.delay_ms,
    }
