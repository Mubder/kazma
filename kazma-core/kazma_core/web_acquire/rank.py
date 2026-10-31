"""Source ranking for deep research — SERP order is not quality.

Heuristic scores favor docs, academia, and primary hosts; demote trackers
and low-signal paths. Does not call the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlparse

__all__ = ["RankedUrl", "rank_by_coverage", "rank_urls", "score_url"]

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
    "studocu.",
    # Fan and meme wikis, copies of Wikipedia (the pipeline reads Wikipedia
    # itself) and pirate document sites: live 2026-10-31 a Kuwait history
    # run took polandball's "Kuwaitball" page, a Wikipedia mirror and a book
    # on dokumen.pub as sources, each the best address its search found.
    "fandom.com",
    "wikia.",
    "miraheze.org",
    "bharatpedia.",
    "wikiwand.com",
    "everybodywiki.",
    "dokumen.pub",
    "pdfcoffee.",
    "epdf.",
    "vdoc.pub",
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


#: Reasons that say a link may answer its query: a query word in its address
#: or in the search result's own title and snippet, or a host or domain
#: research prefers. A demotion says nothing for it.
_SIGNALS = ("tld:", "host:", "docs_path", "qhits:", "text:")


def _text_hits(query: str, text: str) -> int:
    """How many of *query*'s words (3+ letters, any script) *text* holds."""
    words = {w for w in re.findall(r"\w+", (query or "").casefold()) if len(w) > 2}
    held = set(re.findall(r"\w+", (text or "").casefold()))
    return len(words & held)


def _has_signal(r: RankedUrl) -> bool:
    return any(reason.startswith(_SIGNALS) for reason in r.reasons)


def rank_by_coverage(
    results: Mapping[str, Sequence[str]],
    *,
    max_per_domain: int = 2,
    limit: int | None = None,
    exclude: Iterable[str] = (),
    require_signal: bool = False,
    texts: Mapping[str, str] | None = None,
) -> list[RankedUrl]:
    """Rank each search's results against its OWN query, then take them in
    turns: every search's best source before any search's second.

    A research plan asks one search per sub-question. Ranked together against
    the whole topic, the address score decides alone (a `.gov` +4, the topic's
    words rarely in a URL): live 2026-10-31 a Kuwait history paper's first
    source was a COVID-19 hospital cost study, its third NASA's oil fields
    page, and the earliest mentions, 2003 and the 2006-2014 crises had none.
    Within one search the engine's own order breaks ties.

    A link with no sign of relevance (no query word in its address, no
    preferred host or domain, no query word in the search result's own
    title and snippet, *texts*) comes after every link that has one, from
    any search: the next run took a games site and four "Sprunki" game
    pages, each the "best" of a search that had returned nothing else. With
    *require_signal* (a gap fill) such links are not taken at all.
    """
    texts = texts or {}
    skip = {u for u in exclude if u}
    per_query: list[list[RankedUrl]] = []
    for query, urls in results.items():
        scored: list[tuple[float, int, RankedUrl]] = []
        for position, raw in enumerate(urls):
            u = (raw or "").strip().rstrip(".,;:)")
            if not u or u in skip:
                continue
            sc, reasons = score_url(u, query)
            hits = _text_hits(query, texts.get(u, ""))
            if hits:
                sc += min(3.0, hits * 0.6)
                reasons.append(f"text:{hits}")
            scored.append((sc, position, RankedUrl(url=u, score=sc, domain=_domain(u), reasons=tuple(reasons))))
        scored.sort(key=lambda item: (-item[0], item[1]))
        per_query.append([item[2] for item in scored])

    out: list[RankedUrl] = []
    seen: set[str] = set()
    domain_n: dict[str, int] = {}
    cap = max(1, int(max_per_domain))
    full = limit is not None and (lambda: len(out) >= max(1, int(limit)))

    def take_in_turns(queues: list[list[RankedUrl]]) -> None:
        while any(queues):
            for queue in queues:
                while queue:
                    r = queue.pop(0)
                    d = r.domain or "_"
                    if r.url in seen or domain_n.get(d, 0) >= cap:
                        continue
                    seen.add(r.url)
                    domain_n[d] = domain_n.get(d, 0) + 1
                    out.append(r)
                    break
                if full and full():
                    return

    take_in_turns([[r for r in queue if _has_signal(r)] for queue in per_query])
    if not require_signal and not (full and full()):
        take_in_turns([[r for r in queue if not _has_signal(r)] for queue in per_query])
    return out


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
