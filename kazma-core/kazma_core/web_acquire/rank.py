"""Source ranking for deep research — SERP order is not quality.

Heuristic scores favor docs, academia, and primary hosts; demote trackers
and low-signal paths. Does not call the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

__all__ = ["RankedUrl", "rank_urls", "score_url"]

# Higher = more preferred for research grounding
_BOOST_TLDS = (".gov", ".edu", ".mil", ".int")
_BOOST_HOST_SUBSTR = (
    "arxiv.org",
    "doi.org",
    "nature.com",
    "science.org",
    "ieee.org",
    "acm.org",
    "nih.gov",
    "who.int",
    "wikipedia.org",
    "github.com",
    "gitlab.com",
    "docs.",
    "developer.",
    "readthedocs.",
    "rfc-editor.org",
    "w3.org",
    "mozilla.org",
    "python.org",
    "microsoft.com",
    "google.com",
    "openai.com",
    "anthropic.com",
    "deepmind.com",
    "meta.com",
    "nvidia.com",
)
_DEMOTE_HOST_SUBSTR = (
    "pinterest.",
    "facebook.com",
    "fb.com",
    "instagram.",
    "tiktok.",
    "doubleclick.",
    "click.",
    "bit.ly",
    "t.co",
    "scribd.",
    "quizlet.",
    "coursehero.",
)
_DEMOTE_PATH = re.compile(
    r"/(tag|tags|category|categories|login|signup|cart|share|embed)(/|$)",
    re.I,
)


@dataclass(frozen=True)
class RankedUrl:
    url: str
    score: float
    domain: str
    reasons: tuple[str, ...] = ()


def _domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        return host
    except Exception:
        return ""


def score_url(url: str, query: str = "") -> tuple[float, list[str]]:
    """Return (score, reason tags). Higher is better."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return -100.0, ["invalid"]
    host = _domain(u)
    path = (urlparse(u).path or "").lower()
    score = 1.0
    reasons: list[str] = []

    for tld in _BOOST_TLDS:
        if host.endswith(tld):
            score += 4.0
            reasons.append(f"tld:{tld}")
            break
    for frag in _BOOST_HOST_SUBSTR:
        if frag in host or (frag.endswith(".") and host.startswith(frag.rstrip("."))):
            score += 2.5
            reasons.append(f"host:{frag.rstrip('.')}")
            break
        if frag.startswith("docs.") and (host.startswith("docs.") or "/docs" in path):
            score += 2.0
            reasons.append("docs_path")
            break

    for frag in _DEMOTE_HOST_SUBSTR:
        if frag in host:
            score -= 5.0
            reasons.append(f"demote:{frag}")
            break
    if _DEMOTE_PATH.search(path):
        score -= 1.5
        reasons.append("demote_path")

    # Query term overlap in URL
    q = (query or "").lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", q) if len(t) > 2][:8]
    blob = f"{host}{path}".lower()
    hits = sum(1 for t in tokens if t in blob)
    if hits:
        score += min(3.0, hits * 0.6)
        reasons.append(f"qhits:{hits}")

    # Prefer shorter clean paths slightly
    depth = path.count("/")
    if depth <= 3:
        score += 0.3
    elif depth >= 8:
        score -= 0.5

    return score, reasons


def rank_urls(
    urls: Iterable[str],
    query: str = "",
    *,
    max_per_domain: int = 2,
    limit: int | None = None,
) -> list[RankedUrl]:
    """Rank and dedupe URLs; cap per domain for diversity."""
    scored: list[RankedUrl] = []
    seen: set[str] = set()
    for raw in urls:
        u = (raw or "").strip().rstrip(".,;:)")
        if not u or u in seen:
            continue
        seen.add(u)
        sc, reasons = score_url(u, query)
        scored.append(
            RankedUrl(url=u, score=sc, domain=_domain(u), reasons=tuple(reasons))
        )
    scored.sort(key=lambda r: (-r.score, r.url))

    out: list[RankedUrl] = []
    domain_n: dict[str, int] = {}
    for r in scored:
        d = r.domain or "_"
        if domain_n.get(d, 0) >= max(1, int(max_per_domain)):
            continue
        domain_n[d] = domain_n.get(d, 0) + 1
        out.append(r)
        if limit is not None and len(out) >= max(1, int(limit)):
            break
    return out
