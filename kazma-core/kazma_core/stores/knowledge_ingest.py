"""Knowledge ingestion — full-tree discovery + tiered fetch + chunk + index.

This module turns one URL into a fully indexed Knowledge Library.  It is
the most opinionated piece of the Knowledge Library subsystem because it
has to handle real-world doc sites (e.g. Meta WhatsApp Cloud API), which
combine four problems no off-the-shelf crawler solves well together:

1. **Depth.**  A doc site is a tree of dozens-to-hundreds of pages.  The
   shared research ``crawl_site`` caps at 8/50 pages — far too few.
2. **Tabs.**  Tabbed content is shipped in the DOM behind
   ``display:none``/``aria-hidden`` and revealed on click.  The default
   extractor (``trafilatura``) discards hidden DOM, so all per-tab
   parameter tables and examples vanish.
3. **SPA nav.**  Navigation links are injected by JavaScript and absent
   from the initial HTML a plain ``httpx`` fetch returns, so a naive BFS
   link-walker never sees half the tree.
4. **Scope.**  "Same-domain" is too lax — it would let a WhatsApp doc
   crawl wander into Facebook Marketing API docs.

The pipeline:

    discover_pages(seed)
       ├─ robots.txt → Sitemap: directives
       ├─ /sitemap.xml, /sitemap_index.xml, /docs/sitemap.xml
       └─ BFS + Playwright link-walk fallback (path-prefix scoped)

    per page →
       └─ fetch_text(url, purpose="kb", scope_check=...)
          (shared static/hidden-panel extraction and bounded local tab rendering)

    chunk_markdown_doc → KnowledgeIndex.index (SQLite + ChromaDB + FTS5)

The ingestion runs as a background ``asyncio`` task that yields progress
records (discovered / fetched / ingested / failed / current_url) which the
UI and the ``/kb`` slash commands surface live.

Text and link discovery use :func:`kazma_core.web_acquire.fetch_text` with
explicit scope. Knowledge extraction retains hidden panels and captures each
supported lazy tab through the shared bounded renderer. Hosted discovery is
refused when navigation scope cannot be enforced; no second KB text ladder
may bypass a failed or partial shared acquisition.
"""

from __future__ import annotations

from kazma_core.web_acquire.origins import polite_requests
from kazma_core.web_acquire.robots import crawl_policy, robots_for_job
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

import asyncio
import logging
import os
import re
import xml.etree.ElementTree as ET
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urljoin, urlparse

from kazma_core.stores.knowledge import get_knowledge_store
from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict
from kazma_core.stores.knowledge_index import get_knowledge_index
from kazma_core.http_tls import shared_ssl_context
from kazma_core.web_acquire import fetch_text as _web_fetch_text

__all__ = [
    "IngestResult",
    "ProgressUpdate",
    "ingest_url",
    "ingest_site",
    "kb_discover_pages",
]

logger = logging.getLogger(__name__)

# ── Caps & policy (env-overridable) ─────────────────────────────────────────


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


def _kb_max_pages() -> int:
    """Per-job page cap.  Default 200, hard ceiling 1000."""
    return _env_int("KAZMA_KB_MAX_PAGES", 200, lo=1, hi=1000)


def _kb_max_depth() -> int:
    """Max BFS depth when no sitemap is found.  Sitemap-driven crawls are
    flat lists, so depth is effectively irrelevant there."""
    return _env_int("KAZMA_KB_MAX_DEPTH", 10, lo=0, hi=20)


def _kb_delay_ms() -> int:
    return _env_int("KAZMA_KB_DELAY_MS", 300, lo=0, hi=5000)


def _kb_scope_mode() -> str:
    """``tree`` (default) | ``prefix`` | ``domain`` | ``exact``.

    - ``tree``  : same host AND shares at least one *topic* path segment
                  with the seed.  Robust to doc trees that span multiple
                  prefixes (e.g. Meta splits WhatsApp docs across
                  ``/documentation/business-messaging/whatsapp/...`` and
                  ``/docs/whatsapp/...``; ``tree`` accepts both because
                  they share the ``whatsapp`` topic segment).
    - ``prefix``: strict path-prefix (legacy, too narrow for cross-prefix
                  doc trees — kept for explicit opt-in).
    - ``domain``: same host, any path (loosest useful).
    - ``exact`` : single page only.
    """
    mode = (os.environ.get("KAZMA_KB_SCOPE_MODE") or "tree").strip().lower()
    if mode not in ("tree", "prefix", "domain", "exact"):
        mode = "tree"
    return mode


_SKIP_EXT = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".zip",
    ".gz", ".mp4", ".mp3", ".css", ".js", ".ico", ".woff", ".woff2",
    # Source / machine-readable artifacts from Firecrawl /map — not doc pages.
    ".yaml", ".yml", ".map", ".wasm",
)

# Path suffixes that mark infra URLs (not doc pages).  Discovery output
# from Firecrawl /map sometimes includes these — e.g. Meta returns a URL
# like ``/documentation/.../whatsapp/overview/sitemap.xml``.  Fetching and
# chunking them produces garbage (nav boilerplate) and counts as a failed
# page.  Filter them at the discovery boundary.
_INFRA_SUFFIXES = (
    "/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml",
    "/robots.txt", "/ads.txt", "/humans.txt",
    ".xml.gz", ".xml",
    ".rss", ".atom", ".json",  # feeds / API endpoints, not docs
)


def _is_infra_url(url: str) -> bool:
    """Return True for sitemaps, robots.txt, feeds, binary/source assets.

    Firecrawl /map sometimes returns OpenAPI ``.yaml`` files and other
    non-HTML artifacts; fetching them wastes quota and produces garbage.
    """
    if not url:
        return True
    path = (urlparse(url).path or "").lower()
    if any(path.endswith(suf) for suf in _INFRA_SUFFIXES):
        return True
    if any(path.endswith(ext) for ext in _SKIP_EXT):
        return True
    return False


def _jina_opt_in() -> bool:
    """User explicitly enabled Jina Reader via ``KAZMA_JINA_READER``."""
    return (os.environ.get("KAZMA_JINA_READER") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _jina_hard_disabled() -> bool:
    """``KAZMA_JINA_READER=0|false|off`` refuses Jina even as last-resort."""
    return (os.environ.get("KAZMA_JINA_READER") or "").strip().lower() in (
        "0", "false", "no", "off",
    )


def _kb_jina_fallback_allowed() -> bool:
    """Hosted Jina requires policy permission; legacy flags only veto it."""
    from kazma_core.web_acquire.policy import acquisition_policy

    if not acquisition_policy().permits("jina") or _jina_hard_disabled():
        return False
    raw = (os.environ.get("KAZMA_KB_JINA_FALLBACK") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return True  # provider was explicitly allowed by acquisition policy above

# Below which an extract is considered "thin" and we retry with the
# Playwright full-DOM extractor (likely a tabbed / JS doc page).
MIN_USEFUL_CHARS = 200

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ── Result / progress records ───────────────────────────────────────────────


@dataclass
class ProgressUpdate:
    """One progress tick yielded by :func:`ingest_site`."""

    phase: str               # "discover" | "fetch" | "ingest" | "done" | "error"
    discovered: int = 0
    fetched: int = 0
    ingested: int = 0        # chunks written (after dedup)
    skipped: int = 0         # chunks skipped (dedup / unchanged pages)
    failed: int = 0          # pages that failed
    pages_unchanged: int = 0  # smart re-index full-page skips
    current_url: str = ""
    message: str = ""
    errors: list[str] = field(default_factory=list)  # per-page failure reasons
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    job_id: str = ""


@dataclass
class IngestResult:
    """Final summary of an ingest (single page or whole tree)."""

    pages_discovered: int = 0
    pages_fetched: int = 0
    pages_failed: int = 0
    chunks_new: int = 0
    chunks_skipped: int = 0   # chunks of pages stored unchanged
    pages_unchanged: int = 0
    library_created: bool = False
    failed_urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: Refused before anything was fetched or stored (a URL that is not
    #: public http(s), a store at its quota): the caller may answer it as
    #: a correctable refusal, not a failure of unknown effect.
    refused: bool = False


# ── URL helpers ─────────────────────────────────────────────────────────────


def _normalize_url(base: str, href: str) -> str | None:
    if not href or href.startswith(("#", "mailto:", "javascript:", "tel:", "data:")):
        return None
    from kazma_core.web_acquire.links import resolve_document_url

    abs_url = resolve_document_url(base, href)
    parsed = urlparse(abs_url)
    if parsed.scheme not in ("http", "https"):
        return None
    if any((parsed.path or "").lower().endswith(ext) for ext in _SKIP_EXT):
        return None
    return abs_url


def _canonical_page_url(url: str) -> str:
    """Drop fragments and fold authority case, preserving resource identity.

    A slash, .md suffix, matrix parameter, query order or escape can select
    different content. Only an observed redirect may establish an alias.
    """
    if not url:
        return ""
    abs_url = url.strip().partition('#')[0]
    try:
        parsed = urlparse(abs_url)
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return ''
        parsed.port  # refuse malformed authorities before storing a frontier
    except ValueError:
        return ''
    if parsed.scheme not in ("http", "https"):
        return ''
    if any(ord(char) < 32 or ord(char) == 127 for char in abs_url):
        return ''
    rest = abs_url[abs_url.index('://') + 3 + len(parsed.netloc):]
    if not rest or rest.startswith('?'):
        rest = '/' + rest
    return f'{parsed.scheme}://{parsed.netloc.lower()}{rest}'


def _llms_txt_urls_for_seed(seed: str) -> list[str]:
    """Candidate llmstxt.org index URLs for a docs seed (origin first)."""
    parsed = urlparse(seed)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return []
    origin = f"{parsed.scheme}://{parsed.netloc}"
    out = [f"{origin}/llms-full.txt", f"{origin}/llms.txt"]
    path = parsed.path or "/"
    if path not in ("", "/"):
        parent = path.rsplit("/", 1)[0]
        if parent:
            out.append(f"{origin}{parent}/llms.txt")
    seen: set[str] = set()
    ordered: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered


def _urls_from_llms_txt(text: str, base_url: str) -> list[str]:
    """Pull page URLs out of an llmstxt.org index (markdown links + bare URLs)."""
    if not text or not str(text).strip():
        return []
    return _extract_links_from_text(text, base_url)


async def _llms_txt_discover(seed: str) -> list[str]:
    """Fetch /llms-full.txt then /llms.txt and harvest linked pages.

    Missing/tiny files contribute no links. Durable storage/budget failures
    stop discovery; an interrupted metadata request is not blindly replayed.
    """
    found: list[str] = []
    from kazma_core.web_acquire.discovery_metadata import _acquire

    for url in _llms_txt_urls_for_seed(seed):
        async def acquire():
            text, final = await _http_get_text(url, timeout=15.0, scope_check=lambda target: _in_scope(seed, target, "domain"))
            pages = (_urls_from_llms_txt(text, final or url)
                     if text and len(text) >= 40 and not _looks_like_bot_block_html(text) else [])
            return dict(pages=list(dict.fromkeys(pages)), children=[], bytes=len((text or '').encode('utf-8')))

        result = await _acquire(url, acquire)
        if result is None:
            continue
        pages = result['pages']
        if pages:
            logger.info(
                "[kb_discover] %s contributed %d URLs", url, len(pages)
            )
            found.extend(pages)
            # llms-full.txt is the complete corpus; no need to also fetch
            # the shorter /llms.txt from the same origin.
            if url.endswith("llms-full.txt"):
                break
    return found


def _order_urls_seed_first(seed: str, urls: list[str]) -> list[str]:
    """De-dupe by canonical form (seed first). Entries are canonical URLs."""
    seen: set[str] = set()
    ordered: list[str] = []
    for u in [seed, *urls]:
        if not u or _is_infra_url(u):
            continue
        key = _canonical_page_url(u)
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _is_sparse_discovery(seed: str, urls: list[str], *, min_others: int = 3) -> bool:
    """True when a discovery set is basically just the seed (not a real tree).

    Firecrawl ``/v1/map`` on a Meta *leaf* page (``.../whatsapp/overview``)
    returns only the page itself + trailing-slash + sitemap.xml — 3 URLs.
    Mapping the *section* parent (``.../whatsapp/``) returns ~280.  Treating
    sparse results as success short-circuits the better tiers.
    """
    if not urls:
        return True
    seed_key = _canonical_page_url(seed)
    others = {_canonical_page_url(u) for u in urls} - {seed_key, ""}
    return len(others) < min_others


def _section_map_url(seed_url: str) -> str | None:
    """Parent section URL to re-map when the seed is a leaf page.

    ``.../whatsapp/overview`` → ``.../whatsapp/``
    ``.../whatsapp/`` (already a directory) → ``None`` (no second hop)
    """
    parsed = urlparse(seed_url)
    path = parsed.path or "/"
    if path == "/" or path.endswith("/"):
        return None
    parent = path.rsplit("/", 1)[0]
    if not parent:
        return None
    return f"{parsed.scheme}://{parsed.netloc}{parent}/"


def _seed_prefix(seed_url: str) -> str:
    """Return the path prefix used for ``prefix`` scoping.

    Doc sites are organised as section trees: a seed like
    ``/docs/whatsapp/overview`` should crawl everything under
    ``/docs/whatsapp/`` (the section it belongs to), not just paths
    starting with ``/docs/whatsapp/overview``.  So we **always walk up one
    segment** unless the seed path already ends in ``/`` (an explicit
    directory).

    Examples:
      ``/docs/whatsapp/overview`` → ``/docs/whatsapp/``
      ``/docs/whatsapp/``         → ``/docs/whatsapp/``
      ``/api/v1/messages.json``   → ``/api/v1/``  (file → parent)
      ``/``                       → ``/``
    """
    parsed = urlparse(seed_url)
    path = parsed.path or "/"
    if path == "/" or path.endswith("/"):
        return path
    parent = path.rsplit("/", 1)[0]
    return (parent + "/") or "/"


def _seed_topic_segments(seed_url: str) -> set[str]:
    """Path segments that identify the doc *topic* (used by ``tree`` scope).

    For ``/documentation/business-messaging/whatsapp/overview`` this yields
    ``{'whatsapp'}`` — the topic word.  We drop generic path segments
    (``docs``, ``documentation``, ``guide``, ``reference``, ``api``,
    ``overview``, version numbers, language codes) so the topic is what
    survives.  This lets a crawl starting at a landing page reach the
    actual reference docs even when they live under a different prefix
    (Meta splits WhatsApp docs across ``/documentation/.../whatsapp/...``
    and ``/docs/whatsapp/...``).
    """
    parsed = urlparse(seed_url)
    segments = [s for s in (parsed.path or "").split("/") if s]
    _NOISE = {
        "docs", "documentation", "doc", "guide", "guides", "reference",
        "references", "api", "apis", "overview", "introduction", "intro",
        "start", "started", "getting-started", "tutorial", "learn",
        "en", "ar", "v1", "v2", "v3", "v4", "v5", "latest", "stable",
        "cloud-api",  # Meta-specific: appears in both prefixes
        "business-messaging",
    }
    topic = {s.lower() for s in segments if s.lower() not in _NOISE and not s.startswith("v")}
    return topic


def _in_scope(seed_url: str, candidate: str, mode: str) -> bool:
    if mode == "exact":
        return candidate == seed_url
    if mode == "domain":
        sh = (urlparse(seed_url).hostname or "").lower().removeprefix("www.")
        ch = (urlparse(candidate).hostname or "").lower().removeprefix("www.")
        return bool(sh) and sh == ch
    if mode == "tree":
        # Same host AND shares at least one topic segment with the seed.
        # This is the right default for doc trees that span multiple path
        # prefixes (e.g. Meta's /documentation/... + /docs/... split).
        sh = (urlparse(seed_url).hostname or "").lower().removeprefix("www.")
        ch = (urlparse(candidate).hostname or "").lower().removeprefix("www.")
        if not sh or sh != ch:
            return False
        seed_topic = _seed_topic_segments(seed_url)
        if not seed_topic:
            # Docs-root / noise-only seeds (`docs.typesafe.ai/`,
            # `/introduction`) have no product topic. Fail-closed used to
            # drop a 234-URL Firecrawl map to the seed page alone. Fall
            # back to path-prefix: a docs root prefixes `/` (whole host);
            # `/docs/overview` prefixes `/docs/`.
            return (urlparse(candidate).path or "").startswith(
                _seed_prefix(seed_url)
            )
        cand_segments = {s.lower() for s in (urlparse(candidate).path or "").split("/") if s}
        return bool(seed_topic & cand_segments)
    # prefix (legacy default — kept for explicit opt-in)
    seed_host = (urlparse(seed_url).hostname or "").lower().removeprefix("www.")
    cand_host = (urlparse(candidate).hostname or "").lower().removeprefix("www.")
    if seed_host != cand_host:
        return False
    return (urlparse(candidate).path or "").startswith(_seed_prefix(seed_url))


# ── Discovery: sitemap-first, BFS fallback ──────────────────────────────────


@polite_requests()
async def _http_get_text(url: str, *, timeout: float = 20.0, scope_check=None) -> tuple[str | None, str]:
    """Lightweight GET.  Returns (text, final_url).  None text on failure.

    Transparently handles gzip/deflate Content-Encoding AND ``.gz`` URLs
    (some sites — e.g. developers.facebook.com — ship sitemaps as
    ``sitemap.xml.gz`` even though the response is the raw gzip bytes).
    """
    try:
        from kazma_core.web_acquire.http import fetch_public

        limit = 5_000_000
        with crawl_policy(purpose='kb_discovery'):
            result = await fetch_public(url, timeout=timeout, max_bytes=limit, scope=scope_check,
                                        headers={"User-Agent": _BROWSER_UA, "Accept-Encoding": "gzip, deflate"})
        final = result.url
        if result.response.status_code >= 400 or result.truncated:
            return None, final
        if urlparse(final).path.lower().endswith(".gz"):
            from kazma_core.web_acquire.http import decompress_gzip

            # Content-Encoding may already have removed the gzip envelope.
            text = (await asyncio.to_thread(decompress_gzip, result.response.content, limit)
                    if result.response.content.startswith(b"\x1f\x8b") else result.text)
            return (text if text.lstrip().startswith("<") else None), final
        return result.text, final
    except AcquisitionError as exc:
        if exc.code in {FetchErrorCode.STORAGE, FetchErrorCode.LIMIT, FetchErrorCode.POLICY, FetchErrorCode.ROUTING}:
            raise
        logger.debug("[kb_discover] GET failed: %s", exc.code)
        return None, url
    except Exception as exc:
        logger.debug("[kb_discover] GET %s failed: %s", url, exc)
        return None, url


async def _note_partial_sitemap(on_progress, message):
    from kazma_core.web_acquire import crawl_jobs

    job = crawl_jobs.current_crawl.get()
    if job is not None:
        await crawl_jobs.call(job.update, lambda row: row.update(metadata_limited=True))
    await _safe_progress(on_progress, message)


class _SitemapDocuments(list):
    def __init__(self):
        super().__init__()
        self.pages = []


async def _sitemap_links(text, final, docs, *, on_progress):
    from kazma_core.web_acquire import crawl_jobs
    from kazma_core.web_acquire.parser import parse_sitemap
    from kazma_core.web_acquire.links import resolve_document_url

    result = dict(pages=[], children=[], bytes=len((text or '').encode('utf-8')))
    if not text:
        return result
    try:
        parsed = await parse_sitemap(text, final)
    except AcquisitionError as exc:
        if exc.code is not FetchErrorCode.LIMIT:
            raise
        # One sitemap past the parser's budget leaves discovery partial; it
        # never fails the crawl (the seed and the other sitemaps still count).
        await _note_partial_sitemap(on_progress, 'a sitemap exceeded the parser budget and was skipped')
        return result
    if parsed['limited']:
        await _note_partial_sitemap(on_progress, f'a sitemap held more URLs than discovery reads; its first {len(parsed["urls"])} were kept')
    if parsed['kind'] == 'invalid':
        await _safe_progress(on_progress, 'invalid/unsafe sitemap ignored')
        return result
    kind = parsed['kind']
    if kind == 'urlset':
        result['pages'] = parsed['urls']
        # Keep the historical raw-document API for standalone discovery users.
        # Durable jobs retain parsed links only, never multi-megabyte bodies.
        if crawl_jobs.current_crawl.get() is None:
            docs.append(text)
    elif kind == 'sitemapindex':
        result['children'] = list(dict.fromkeys(resolve_document_url(final, url) for url in parsed['urls']))
    return result


async def _fetch_sitemaps(seed_url: str, *, on_progress: Any = None) -> list[str]:
    """Collect sitemap URLs from robots.txt + well-known locations.

    Follow indexes recursively; return leaf documents only. Bound the frontier,
    depth, total XML bytes and elapsed time independently of page-fetch caps.
    """
    from kazma_core.web_acquire.discovery_metadata import _acquire

    parsed = urlparse(seed_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates: list[str] = []

    # 1. robots.txt Sitemap: directives (authoritative).
    metadata_scope = lambda target: _in_scope(seed_url, target, "domain")
    async def robots():
        text, _ = await _http_get_text(f"{origin}/robots.txt", timeout=10.0, scope_check=metadata_scope)
        children = [line.split(':', 1)[1].strip() for line in (text or '').splitlines()
                    if line.strip().lower().startswith('sitemap:')]
        return dict(pages=[], children=list(dict.fromkeys(child for child in children if child)), bytes=len((text or '').encode('utf-8')))

    control = await _acquire(f"{origin}/robots.txt", robots)
    if control is not None:
        candidates.extend(control['children'])

    # 2. Well-known sitemap locations.
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/docs/sitemap.xml", "/docs/sitemap-index.xml"):
        candidates.append(f"{origin}{path}")

    seen: set[str] = set()
    docs = _SitemapDocuments()
    queue = deque((url, 0) for url in dict.fromkeys(candidates[:64]))
    queued = {url for url, _ in queue}
    total_bytes = 0
    limited = len(candidates) > 64
    try:
        async with asyncio.timeout(60):
            while queue and len(seen) < 64:
                url, depth = queue.popleft()
                if url in seen:
                    continue
                seen.add(url)
                async def acquire():
                    text, final = await _http_get_text(url, timeout=15.0, scope_check=metadata_scope)
                    return await _sitemap_links(text, final, docs, on_progress=on_progress)

                result = await _acquire(url, acquire)
                if result is None:
                    limited = True
                    continue
                size = result['bytes']
                if total_bytes + size > 20_000_000:
                    limited = True
                    break
                total_bytes += size
                docs.pages.extend(result['pages'])
                if result['children']:
                    if depth >= 4:
                        limited = True
                        continue
                    for child in result['children']:
                        if child in queued:
                            continue
                        if len(queued) >= 64:
                            limited = True
                            break
                        queued.add(child)
                        queue.append((child, depth + 1))
    except TimeoutError:
        limited = True
    if limited or queue:
        from kazma_core.web_acquire import crawl_jobs

        job = crawl_jobs.current_crawl.get()
        if job is not None:
            await crawl_jobs.call(job.update, lambda row: row.update(metadata_limited=True))
        await _safe_progress(on_progress, "partial sitemap discovery: time, document, depth or XML byte limit reached; retained pages are preserved")
    return docs


async def _firecrawl_map_site(
    seed_url: str,
    *,
    search: str | None = None,
) -> list[str] | None:
    """Use Firecrawl's ``/v1/map`` endpoint to enumerate all URLs on a site.

    This is the strongest discovery tier for bot-walled doc sites (Meta,
    Cloudflare-protected, etc.): Firecrawl runs the actual crawl server-side
    on their managed browser farm, so the target site's bot-wall never sees
    our IP.  ``/v1/map`` is purpose-built for "give me every URL on this
    site without scraping content" — exactly the discovery problem.

    Returns the list of URLs, or ``None`` if Firecrawl isn't configured or
    the call failed (caller falls through to sitemap/BFS tiers).

    Response shape (per Firecrawl OpenAPI spec):
        {"success": bool, "links": [{"url": "...", "title": "...", ...}, ...]}
    """
    from kazma_core.web_acquire.policy import acquisition_policy

    if not acquisition_policy().permits("firecrawl"):
        return None
    api_key = (os.environ.get("KAZMA_FIRECRAWL_API_KEY") or "").strip()
    if not api_key:
        return None
    base = (os.environ.get("KAZMA_FIRECRAWL_URL") or "https://api.firecrawl.dev").rstrip("/")
    try:
        import httpx
        from kazma_core.security.ssrf import validate_url

        from kazma_core.web_acquire.dns_worker import validate_public_url

        await validate_public_url(seed_url, validator=validate_url)
        # ``search`` ranks URLs by relevance; omit it for section-root maps
        # so Firecrawl returns the full URL set rather than a seed-ranked
        # slice.  ``limit`` caps the result set; we scope/filter locally.
        body: dict[str, Any] = {
            "url": seed_url,
            "limit": 2000,
            "includeSubdomains": False,
        }
        if search:
            body["search"] = search
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        from kazma_core.web_acquire import accounting
        from kazma_core.web_acquire.endpoint_transport import EndpointAsyncTransport

        async with httpx.AsyncClient(timeout=45.0, trust_env=False, verify=shared_ssl_context(),
                transport=EndpointAsyncTransport(verify=shared_ssl_context()),
                event_hooks=accounting.httpx_hooks('firecrawl', base + api_key, asynchronous=True)) as client:
            # Firecrawl has historically shipped both /v1 and /v2 of the
            # map endpoint (the scrape side is on /v1 in this codebase and
            # works).  Try /v1/map first (matches the working scrape path),
            # fall back to /v2/map if /v1 404s (some plans only have v2).
            r = await client.post(f"{base}/v1/map", headers=headers, json=body)
            if r.status_code == 404:
                logger.debug("[kb_discover] Firecrawl /v1/map 404, trying /v2/map")
                r = await client.post(f"{base}/v2/map", headers=headers, json=body)
            if r.status_code != 200:
                logger.debug("[kb_discover] Firecrawl /map status %s", r.status_code)
                return None
            data = r.json()
            if not data.get("success"):
                logger.debug("[kb_discover] Firecrawl /map success=false")
                return None
            links = data.get("links") or []
            # links may be list of {"url": ...} OR list of bare strings
            # depending on Firecrawl version — handle both.
            urls: list[str] = []
            for entry in links:
                if isinstance(entry, dict):
                    u = entry.get("url")
                else:
                    u = entry
                if u and isinstance(u, str):
                    urls.append(u)
            logger.info(
                "[kb_discover] Firecrawl /map returned %d URLs for %s",
                len(urls), seed_url,
            )
            return urls
    except ImportError:
        return None
    except Exception as exc:
        logger.debug("[kb_discover] Firecrawl /map failed: %s", exc)
        return None


async def _firecrawl_map_discover(seed: str) -> list[str] | None:
    """Firecrawl /map with parent-section retry for leaf seeds.

    Mapping a Meta leaf like ``.../whatsapp/overview`` returns ~3 URLs.
    Mapping the section parent ``.../whatsapp/`` returns ~280.  We try the
    seed first, then the parent section when the first result is sparse.
    """
    mapped = await _firecrawl_map_site(seed)
    if mapped and not _is_sparse_discovery(seed, mapped):
        return mapped

    parent = _section_map_url(seed)
    if parent and parent.rstrip("/") != seed.rstrip("/"):
        logger.info(
            "[kb_discover] Firecrawl /map sparse (%d) for leaf %s; retrying section %s",
            len(mapped or []), seed, parent,
        )
        parent_mapped = await _firecrawl_map_site(parent)
        if parent_mapped:
            # Merge seed + parent results (parent usually supersets).
            merged = list(mapped or []) + list(parent_mapped)
            if not _is_sparse_discovery(seed, merged):
                return merged
            return merged  # even if still sparse, return what we have

    return mapped  # may be sparse or None — caller decides fallthrough


_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _extract_urls_from_sitemap(xml_text: str) -> list[str]:
    """Pull every ``<loc>`` URL out of a sitemap XML (index or leaf)."""
    from kazma_core.security.safe_xml import UnsafeXMLError, parse_untrusted_xml

    urls: list[str] = []
    try:
        # Fetched from the web: DTDs/entities are refused before parsing.
        root = parse_untrusted_xml(xml_text)
    except (ET.ParseError, UnsafeXMLError) as exc:
        logger.debug("[kb_discover] sitemap parse failed: %s", exc)
        return urls
    # Sitemap URLs live in <url><loc> or <sitemap><loc>; just grab all <loc>.
    for loc in root.iter():
        tag = loc.tag.split("}")[-1].lower()  # strip namespace
        if tag == "loc" and loc.text:
            urls.append(loc.text.strip())
    return urls


@robots_for_job("kb_discovery")
async def kb_discover_pages(
    seed_url: str,
    *,
    mode: str | None = None,
    on_progress: Any = None,
) -> list[str]:
    """Discover the full set of in-scope URLs for a library.

    Local sitemaps, llms.txt and BFS run before explicitly permitted hosted
    mapping/seed expansion. Sparse discovery never authorizes data deletion.

    ``on_progress`` is an optional **plain async** callable ``async (msg) -> None``
    receiving a short human-readable status string (e.g. ``"fetching
    robots.txt…"``) so the caller can show live discovery activity
    instead of a silent 0/0/0.  It must NOT be a generator.

    Returns a de-duplicated, in-scope, ordered URL list, seed first.
    """
    if not seed_url.strip():
        return []
    from kazma_core.web_acquire.policy import acquisition_policy

    policy = acquisition_policy()
    seed = seed_url.strip()
    if not seed.startswith(("http://", "https://")):
        seed = "https://" + seed
    scope_mode = (mode or _kb_scope_mode()).lower()
    if scope_mode not in ("tree", "prefix", "domain", "exact"):
        scope_mode = "tree"
    if scope_mode == "exact":
        return [_canonical_page_url(seed) or seed]
    from kazma_core.web_acquire import crawl_jobs

    checkpoint = crawl_jobs.current_crawl.get()
    if checkpoint is not None:
        saved = await crawl_jobs.call(checkpoint.read)
        if saved.get('discovery') is not None:
            retained = saved['discovery'].get('extra', [])
            resumed = await _bfs_discover(seed, scope_mode, on_progress=on_progress)
            return _order_urls_seed_first(seed, [url for url in resumed + retained if _in_scope(seed, url, scope_mode)])

    def _scope_and_order(raw: list[str]) -> list[str]:
        in_scope = [
            u for u in raw
            if not _is_infra_url(u) and _in_scope(seed, u, scope_mode)
        ]
        ordered = _order_urls_seed_first(seed, in_scope)
        dropped = max(0, len({_canonical_page_url(u) for u in raw if u}) - len(ordered))
        if dropped >= 10 and len(raw) >= 10 and len(ordered) < 5:
            logger.warning(
                "[kb_discover] scope=%s kept %d/%d URLs for %s "
                "(dropped %d — seed has no topic segment?)",
                scope_mode, len(ordered), len(raw), seed, dropped,
            )
        return ordered

    # llmstxt.org index: merge into every later tier so a docs host that
    # publishes /llms.txt is crawled as the author intended, not as whatever
    # a map/sitemap happened to return.
    llms_urls = await _llms_txt_discover(seed)
    if llms_urls:
        await _safe_progress(
            on_progress,
            f"llms.txt contributed {len(llms_urls)} URLs",
        )

    discovered: list[str] = []
    def _with_llms(raw: list[str] | None) -> list[str]:
        merged: list[str] = []
        if raw:
            merged.extend(raw)
        if llms_urls:
            merged.extend(llms_urls)
        merged.extend(discovered)
        return merged

    await _safe_progress(on_progress, f"acquisition policy: {policy.mode}; trying local discovery first")

    # ── 1. Sitemap discovery ─────────────────────────────────────────
    await _safe_progress(on_progress, "fetching robots.txt + sitemaps…")
    sitemap_docs = await _fetch_sitemaps(seed, on_progress=on_progress)
    if getattr(sitemap_docs, 'pages', None) is not None:
        discovered.extend(sitemap_docs.pages)
    if sitemap_docs or discovered:
        await _safe_progress(on_progress, f"parsing {len(sitemap_docs)} sitemap doc(s)…")
        if not hasattr(sitemap_docs, 'pages'):
            from kazma_core.web_acquire.parser import parse_sitemap

            for doc in sitemap_docs:
                discovered.extend((await parse_sitemap(doc, seed))['urls'])
        ordered = _scope_and_order(_with_llms(discovered))
        if ordered and not _is_sparse_discovery(seed, ordered):
            logger.info(
                "[kb_discover] sitemap yielded %d in-scope URLs for %s",
                len(ordered), seed,
            )
            return ordered
        await _safe_progress(on_progress, "sitemap had 0/sparse in-scope URLs; falling back")
    else:
        await _safe_progress(on_progress, "no sitemap reachable; falling back")

    # llms.txt alone may be enough when local sitemaps were sparse.
    if llms_urls:
        ordered = _scope_and_order(llms_urls)
        if ordered and not _is_sparse_discovery(seed, ordered):
            logger.info(
                "[kb_discover] llms.txt yielded %d in-scope URLs for %s",
                len(ordered), seed,
            )
            return ordered

    # ── 2. Local BFS link-walk fallback ─────────
    await _safe_progress(on_progress, "rendering seed page to discover nav links…")
    if checkpoint is not None:
        await crawl_jobs.call(checkpoint.discovery, dict(stage='bfs', queue=[[seed, 0]], known=[seed],
            ordered=[], extra=_scope_and_order(_with_llms([]))[:1000],
            ambiguous=[], inflight=None, visited=0, limited=False))
    bfs_urls = await _bfs_discover(seed, scope_mode, on_progress=on_progress)
    if bfs_urls and not _is_sparse_discovery(seed, bfs_urls):
        return _scope_and_order(_with_llms(bfs_urls))
    # Hosted map/seed services do not provide enforceable navigation scope.
    # Post-fetch URL filtering cannot undo an out-of-scope remote acquisition.
    if policy.permits("firecrawl") or policy.permits("jina"):
        await _safe_progress(on_progress, "hosted discovery refused: provider cannot enforce this crawl's navigation scope")

    if bfs_urls:
        # Sparse but non-empty — still better than seed-only.
        return _scope_and_order(_with_llms(bfs_urls))
    # BFS also found nothing usable — the seed page itself was unfetchable
    # (likely bot-walled).  Tell the user why this dropped to "just the
    # seed" so they know to enable a fetch backend rather than seeing an
    # unexplained discovered=1.
    await _safe_progress(
        on_progress,
        "link-walk found no nav links (seed page unreadable — site is likely "
        "bot-walled; check Chromium installation and acquisition policy)",
    )
    return [_canonical_page_url(seed) or seed]


async def _jina_expand_seed(seed: str) -> list[str] | None:
    """Fetch the seed via Jina Reader and harvest every linked URL.

    Returns ``None`` on failure, or the raw (unscoped) link list.
    """
    try:
        from kazma_core.web_acquire.fetch import jina_reader

        text = await jina_reader(seed)
    except Exception as exc:
        logger.debug("[kb_discover] Jina expand failed: %s", exc)
        return None
    if not text or len(text) < MIN_USEFUL_CHARS:
        return None
    return _extract_links_from_text(text, seed)  # type: ignore[arg-type]


async def _safe_progress(on_progress: Any, msg: str) -> None:
    """Invoke an optional progress callback, swallowing any error.

    The callback must be a plain ``async (str) -> None`` (NOT a generator).
    """
    if on_progress is not None:
        try:
            res = on_progress(msg)
            if hasattr(res, "__await__"):
                await res
        except Exception:
            pass


async def _bfs_discover(
    seed: str,
    scope_mode: str,
    *,
    on_progress: Any = None,
) -> list[str]:
    """Use links from the same scoped typed acquisition as the page text."""
    from kazma_core.web_acquire import crawl_jobs

    checkpoint = crawl_jobs.current_crawl.get()
    saved = await crawl_jobs.call(checkpoint.read) if checkpoint is not None else None
    max_pages = saved['policy']['max_pages'] if saved else _kb_max_pages()
    max_depth = saved['policy'].get('max_depth', _kb_max_depth()) if saved else _kb_max_depth()
    seed = _canonical_page_url(seed)
    queue: deque[tuple[str, int]] = deque([(seed, 0)])
    queued = {seed}
    ordered: list[str] = []
    visited = 0
    work_cap = max_pages * 4
    limited = False
    state = saved.get('discovery') if saved else None
    if state is not None:
        queue = deque((url, depth) for url, depth in state['queue'])
        queued, ordered = set(state['known']), list(state['ordered'])
        visited, limited = state['visited'], state['limited']
    async def persist(inflight=None):
        if checkpoint is not None:
            await crawl_jobs.call(checkpoint.discovery, dict(stage='bfs', queue=[list(node) for node in queue],
                known=sorted(queued), ordered=ordered, extra=(state or {}).get('extra', []),
                ambiguous=(state or {}).get('ambiguous', []), inflight=inflight, visited=visited, limited=limited))
    checker = lambda target: _in_scope(seed, target, scope_mode)
    async with _pw_browser_scope():
        while queue and len(ordered) < max_pages and visited < work_cap:
            url, depth = queue.popleft()
            visited += 1
            await persist([url, depth])  # claim before acquisition; never replay an uncertain effect
            await _safe_progress(on_progress, f"link-walk depth {depth}: {url[:80]}")
            result = await _web_fetch_text(url, purpose="kb_discovery", scope_check=checker)
            if not result.ok:
                await persist()
                if result.error_code in {FetchErrorCode.STORAGE, FetchErrorCode.LIMIT, FetchErrorCode.POLICY, FetchErrorCode.ROUTING}:
                    raise AcquisitionError(result.error_code, result.error or 'Discovery acquisition refused.')
                await _safe_progress(on_progress, f"link-walk failed: {url}: {result.error or result.error_code}")
                continue
            ordered.append(_canonical_page_url(result.final_url or url))
            if result.truncated or not result.links_complete:
                await _safe_progress(on_progress, f"partial link discovery: {url}; {', '.join(result.truncation_reasons) or 'link completeness unavailable'}")
            if depth >= max_depth:
                limited |= bool(result.links)
                await persist()
                continue
            for href in result.links:
                key = _canonical_page_url(href)
                if not key or key in queued or _is_infra_url(href) or not checker(href):
                    continue
                if len(queued) >= work_cap:
                    limited = True
                    break
                queued.add(key)
                queue.append((key, depth + 1))
            await persist()
    if limited or queue:
        await _safe_progress(on_progress, "partial link discovery: page, depth or frontier limit reached; retained pages are not a deletion manifest")
    return list(dict.fromkeys(ordered))




def _extract_links_from_html(html: str, base_url: str) -> list[str]:
    hrefs = re.findall(r'''(?:href|data-href)\s*=\s*["']([^"']+)["']''', html, flags=re.I)
    out: list[str] = []
    seen: set[str] = set()
    for h in hrefs:
        u = _normalize_url(base_url, h)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _extract_links_from_text(text: str, base_url: str) -> list[str]:
    """Extract links from HTML **or** markdown (Jina / Firecrawl output)."""
    if not text:
        return []
    # HTML hrefs
    hrefs = re.findall(r'''(?:href|data-href)\s*=\s*["']([^"']+)["']''', text, flags=re.I)
    # Markdown links: [label](url)
    hrefs.extend(re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", text))
    # Bare absolute URLs (same host preferred later via scope filter)
    try:
        host = (urlparse(base_url).hostname or "").removeprefix("www.")
        if host:
            # Escape dots for regex
            host_re = re.escape(host)
            hrefs.extend(re.findall(rf"https?://(?:www\.)?{host_re}[^\s\)\]\"'<>]+", text, flags=re.I))
    except Exception:
        pass

    out: list[str] = []
    seen: set[str] = set()
    for h in hrefs:
        h = (h or "").strip().rstrip(".,;)")
        # Drop markdown title suffix: url "title"
        if " " in h:
            h = h.split(" ", 1)[0]
        u = _normalize_url(base_url, h)
        if not u:
            continue
        key = _canonical_page_url(u)
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


async def _fetch_html_raw(url: str) -> str | None:
    text, _ = await _http_get_text(url)
    return text


def _looks_like_bot_block_html(html: str | None) -> bool:
    """Heuristic: did the server return an error/bot-block page instead of content?

    Many bot-walled sites (Meta especially) return HTTP 200 with a tiny
    ``<title>Error</title>`` HTML stub to non-browser clients.  Detecting
    this lets us fall through to Playwright instead of treating the stub
    as a (worthless) successful fetch.

    IMPORTANT: This is for **HTML** only. Do not pass already-extracted
    plain text/markdown — short real pages (example.com) look like
    ``len < 500`` without ``<p>`` tags and were false-positive bot walls.
    """
    if not html:
        return True
    sample = html[:2000].lower()
    # Plain text / markdown extract (no tags) with readable words = content
    if "<" not in sample[:80] and len(html.strip()) >= 40:
        return False
    if "<title>error</title>" in sample:
        return True
    if "<title>404</title>" in sample or "<title>403</title>" in sample:
        return True
    # Very short *HTML* with no real body content (challenge stubs).
    looks_html = (
        "<!doctype" in sample
        or "<html" in sample
        or "<body" in sample
        or "<head" in sample
    )
    if looks_html and len(html) < 500 and "<p>" not in sample and "<article" not in sample:
        # Allow tiny static pages that still have an h1 + some text
        if "<h1" in sample and len(re.sub(r"<[^>]+>", " ", html).split()) >= 8:
            return False
        return True
    return False


async def _fetch_html_playwright(url: str) -> str | None:
    """Render a page with Playwright and return its full HTML.

    Used for SPA doc sites whose nav links are absent from the static
    HTML.  Optional — returns None if Playwright isn't installed.
    """
    html = await _render_with_playwright(url, want_text=False)
    return html


# ── Page extraction (static + tabbed/JS) ────────────────────────────────────

# Compatibility names share the real acquisition owner/context; no parallel
# browser launch or network continuation lives in the KB layer.
from kazma_core.web_acquire.browser import BrowserJob as _pw_browser_scope
from kazma_core.web_acquire.browser import current_browser_job as _pw_scope


async def _render_with_playwright(url: str, *, want_text: bool) -> str | None:
    from kazma_core.playwright_loop import run_in_browser_loop

    return await run_in_browser_loop(_render_with_playwright_impl(url, want_text=want_text))


async def _render_with_playwright_impl(url: str, *, want_text: bool) -> str | None:
    from kazma_core.web_acquire.browser import render_document
    from kazma_core.tools.read_url import _looks_like_bot_block

    document = await render_document(url, challenge=_looks_like_bot_block,
                                     profile="knowledge" if want_text else "discovery")
    if not document.result.ok:
        logger.debug('[kb] local render refused: %s', document.result.error)
        return None
    return document.result.text if want_text else document.html


async def _extract_page(url: str, *, scope_check=None) -> tuple[str | None, str, str]:
    """One scoped knowledge-profile acquisition; never start a second ladder.

    The shared boundary owns static/hidden-panel extraction, local rendering,
    deadlines and policy. Partial or failed results never reach the index.
    """
    checker = scope_check or (lambda target: _in_scope(url, target, "domain"))
    _OBSERVED_URL.set(None)
    fr = await _web_fetch_text(url, purpose="kb", scope_check=checker)
    from kazma_core.web_acquire.crawl_jobs import call, current_crawl

    job = current_crawl.get()
    if job is not None:
        await call(job.receipt, fr)
    if fr.truncated:
        return None, "error", f"Acquisition resource limit ({', '.join(fr.truncation_reasons)}); partial content was not indexed."
    if not fr.ok or (fr.http_status is not None and fr.http_status >= 400):
        return None, "error", fr.error or f"Acquisition failed (HTTP {fr.http_status}); no page was indexed."
    text = (fr.text or "").strip()
    if not text:
        return None, "empty", "Acquisition produced no readable content."
    if len(text) < MIN_USEFUL_CHARS:
        if len(text.split()) >= 8:
            return text, "ok", f"short static page ({len(text)} chars)"
        return None, "error", f"Insufficient readable content ({len(text)} chars); no page was indexed."
    _OBSERVED_URL.set(_observed_page_url(url, fr.final_url, checker))
    return text, "ok", ""


def _observed_page_url(requested: str, final: str | None, checker: Any) -> str | None:
    """The page an acquisition landed on, as page identity (KB-E14), when it
    was observed, is http(s) and is inside the crawl's scope."""
    if not final or final == requested:
        return None
    from kazma_core.stores.knowledge import KnowledgeUrlRefused, validate_web_url

    try:
        final = validate_web_url(final)
    except KnowledgeUrlRefused:
        return None
    try:
        if not checker(final):
            return None
    except Exception:
        return None
    return _canonical_page_url(final) or None


def _check_playwright_available() -> str:
    """Return ``""`` if Playwright + Chromium are usable, else a short reason.

    Distinguishes the two common silent failures:
      - Playwright Python package not installed (``ImportError``).
      - Package installed but Chromium binary missing (need
        ``playwright install chromium``).  This is the failure that produces
        the opaque "1 failed" — without this check the error stays at
        ``logger.debug`` and the user can't tell why.
    """
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        from kazma_core.install_hint import extra_install_hint

        return f"playwright package not installed (the web extra: {extra_install_hint('web')})"
    # The Python package is present; check whether the Chromium binary is
    # actually installed by looking for the playwright driver cache.  We
    # can't easily run a launch() here (it's async), so probe the known
    # install locations.  If the user ran `playwright install chromium`
    # there will be a chromium-* dir under the playwright cache.
    import os
    import sys
    candidates = []
    env_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_dir:
        candidates.append(os.path.join(env_dir, "chromium-*"))
    if sys.platform.startswith("win"):
        candidates.append(os.path.join(os.path.expanduser("~"), "AppData", "Local", "ms-playwright", "chromium-*"))
    else:
        candidates.append(os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright", "chromium-*"))
    import glob
    for pattern in candidates:
        if glob.glob(pattern):
            return ""  # Chromium binary looks present.
    return "Chromium binary not installed (run: playwright install chromium)"


async def _extract_playwright_full_dom(url: str) -> str | None:
    """Back-compat thin wrapper around :func:`_render_with_playwright`.

    Kept so external callers/tests that referenced the old name keep
    working.  New code should call ``_render_with_playwright`` directly.
    """
    return await _render_with_playwright(url, want_text=True)


# ── Ingestion: single page + whole tree ─────────────────────────────────────


#: The most pages one library keeps a fetched copy of (KB-E08); past it the
#: oldest copies go first.
_PROVENANCE_MAX_FILES = 5000


def _provenance_dir(library_id: str) -> Path:
    """Where a library's fetched pages are kept: ``<data dir>/
    knowledge_provenance/<library>``. They used to go into the ACTIVE
    WORKSPACE (``research/kb/<library>``) -- after a Switch Repo, the
    operator's own repository -- and stayed there after the library was
    deleted (Knowledge audit KB-E08)."""
    from kazma_core.paths import data_dir

    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in library_id)[:80] or "library"
    return data_dir() / "knowledge_provenance" / safe


def _save_provenance(library_id: str, url: str, text: str) -> None:
    """Keep a fetched page's raw markdown with its library.

    ``<data dir>/knowledge_provenance/<library>/<slug>-<digest>.md``: the
    original page text, inspectable later (the chunker is lossy at the
    boundaries). Bounded at :data:`_PROVENANCE_MAX_FILES` per library and
    removed with the library (:func:`delete_provenance`). A failure is a
    WARNING; the page is stored either way.
    """
    try:
        import hashlib

        folder = _provenance_dir(library_id)
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", url)[:80].strip("-") or "page"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
        target = folder / f"{slug}-{digest}.md"
        new = not target.exists()
        header = (
            f"# Source: {url}\n"
            f"# Library: {library_id}\n"
            f"# Saved: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"# Chars: {len(text)}\n\n"
        )
        target.write_text(header + text, encoding="utf-8")
        if new:
            files = [entry for entry in os.scandir(folder) if entry.is_file()]
            if len(files) > _PROVENANCE_MAX_FILES:
                files.sort(key=lambda entry: entry.stat().st_mtime)
                for entry in files[: len(files) - _PROVENANCE_MAX_FILES]:
                    os.unlink(entry.path)
    except Exception as exc:
        logger.warning("[kb_ingest] the fetched copy of %s was not kept: %s", url[:120], exc)


def delete_provenance(library_id: str) -> bool:
    """Remove a library's fetched pages (its delete, KB-E08). True when
    nothing is left; a failure is a WARNING and False."""
    import shutil

    folder = _provenance_dir(library_id)
    if not folder.exists():
        return True
    try:
        shutil.rmtree(folder)
        return True
    except OSError as exc:
        logger.warning("[kb_ingest] the fetched pages of library %s were not removed: %s",
                       library_id, exc)
        return False


#: The page a fetch landed on, when the acquisition observed it (set by
#: :func:`_extract_page`, read by the crawl in the same task): a page is
#: stored under where it was found, so two links that redirect to one page
#: index it once (KB-E14).
_OBSERVED_URL: ContextVar[str | None] = ContextVar("kb_observed_url", default=None)


async def ingest_url(
    library_id: str,
    url: str,
    *,
    document_title: str = "",
    name: str = "",
) -> IngestResult:
    """Ingest a single page into a library.

    Fetches → chunks → publishes. Returns an :class:`IngestResult` summary.
    The library is created only once the page was fetched (KB-S09: a
    refused or failed first fetch left an empty library behind, with the
    refused URL as its seed). A URL that is not http(s), or a store at its
    quota, is refused before anything is fetched.
    """
    from kazma_core.stores.knowledge import (
        KnowledgeStorageFull,
        KnowledgeUrlRefused,
        validate_web_url,
    )

    result = IngestResult(pages_discovered=1)
    try:
        url = validate_web_url(url)
    except KnowledgeUrlRefused as exc:
        result.errors.append(f"refused URL: {exc}")
        result.failed_urls.append(str(url)[:200])
        result.pages_failed = 1
        result.refused = True
        return result
    store = await asyncio.to_thread(get_knowledge_store)
    try:
        await asyncio.to_thread(store.ensure_capacity, library_id)
    except KnowledgeStorageFull as exc:
        result.errors.append(str(exc))
        result.failed_urls.append(url)
        result.pages_failed = 1
        result.refused = True
        return result
    index = await asyncio.to_thread(get_knowledge_index)
    try:
        from kazma_core.security.ssrf import validate_url

        from kazma_core.web_acquire.dns_worker import validate_public_url

        await validate_public_url(url, validator=validate_url)
    except Exception as exc:
        result.errors.append(f"SSRF/invalid URL {url}: {exc}")
        result.failed_urls.append(url)
        result.pages_failed = 1
        result.refused = True
        return result

    text, status, reason = await _extract_page(url)
    if text is None or status == "error":
        result.pages_failed = 1
        result.failed_urls.append(url)
        # Surface the actual reason (bot-wall vs. Chromium-missing vs.
        # timeout) instead of an opaque "fetch failed".
        msg = f"fetch failed: {url}"
        if reason:
            msg += f" — {reason}"
        result.errors.append(msg)
        return result

    page_url = _OBSERVED_URL.get() or url

    def _ensure_library() -> bool:
        if store.get_library(library_id) is not None:
            return False
        store.create_library(library_id, (name or "").strip() or library_id,
                             description="", seed_url=url)
        return True

    result.library_created = await asyncio.to_thread(_ensure_library)
    try:
        new, unchanged, _ = await asyncio.to_thread(
            _store_page, index, library_id, page_url, text, document_title
        )
    except Exception as exc:
        result.pages_failed = 1
        result.failed_urls.append(url)
        result.errors.append(f"not stored: {url} — {exc}")
        return result
    result.pages_fetched = 1
    result.chunks_new += new
    result.chunks_skipped += unchanged
    result.pages_unchanged = int(bool(new == 0 and unchanged))
    return result


def _store_page(
    index: Any, library_id: str, url: str, text: str, document_title: str = ""
) -> tuple[int, int, int]:
    """Chunking, publication and provenance for one fetched page.

    Returns ``(new, unchanged, chunk_count)``. The page is published in one
    transaction (KB-E02); a page that could not be stored raises
    (:class:`~kazma_core.stores.knowledge_index.KnowledgePublishError`, or
    ``KnowledgeLibraryGone`` when the library was deleted) -- it is never
    counted as unchanged. All of it blocks (the vector store, SQLite), so
    the async ingest paths run it through ``asyncio.to_thread``.
    """
    chunks = chunk_markdown_doc(
        text,
        source_url=url,
        library_id=library_id,
        document_title=document_title or _derive_title(text, url),
    )
    if not chunks:
        return 0, 0, 0
    new, unchanged = index.index(library_id, [chunk_to_dict(c) for c in chunks])
    _save_provenance(library_id, url, text)
    return new, unchanged, len(chunks)


def _refresh_chunk_count(store: Any, library_id: str) -> None:
    """Record the library's final chunk count after a crawl.

    A crawl never deletes a page it did not find (KB-S14): discovery is not
    an authoritative deletion manifest -- a sitemap or map can omit pages,
    and a page cap, a bot wall or a sparse fallback makes absence weaker
    still. Retained content stays until it is removed explicitly.
    Blocking (SQLite): called through ``asyncio.to_thread``.
    """
    try:
        store.set_chunk_count(library_id, store.count_chunks(library_id))
    except Exception as exc:
        logger.warning("[kb_ingest] the chunk count of %s was not refreshed: %s", library_id, exc)


async def _admit_public_seed(seed_url: str) -> None:
    """Refuse a crawl seed acquisition would refuse, before anything exists.

    The same check the crawl makes of its seed before discovery
    (``web_acquire.dns_worker.validate_public_url``: every address the host
    resolves to, in the owned resolver child; never ``to_thread``), made at
    admission. Until 2026-10-15 a seed on a private, loopback or link-local
    address was admitted: its library and journal row were created, the
    crawl then failed on the seed, and the library stayed empty with no
    refusal shown. A private address is ``security_blocked``; a host that
    resolves to nothing is ``connect_error`` (transient: the resolver may be
    down).
    """
    from kazma_core.security.ssrf import UnresolvedHostError, validate_url
    from kazma_core.web_acquire.dns_worker import validate_public_url
    from kazma_core.web_acquire.trace import classify_failure

    try:
        await validate_public_url(seed_url, validator=validate_url)
    except UnresolvedHostError as exc:
        raise AcquisitionError(FetchErrorCode.CONNECT, f"crawl seed refused: {exc}",
                               transient=True) from None
    except ValueError as exc:  # SSRFError is one: what validate_url refuses with
        code, transient = classify_failure(exc)
        raise AcquisitionError(code, f"crawl seed refused: {exc}", transient=transient) from None


async def _prepare_ingest_site(library_id, seed_url, *, max_pages=None, job_id=None, resume=False):
    """Admit a crawl: a refused seed (not http(s), KB-S09; on a private
    network or not resolving, :func:`_admit_public_seed`) or a store at its
    quota (KB-E10) is refused here, before anything is fetched or created;
    the journal refuses a second crawl of the same library (KB-E14)."""
    from kazma_core.stores.knowledge import KnowledgeUrlRefused, validate_web_url
    from kazma_core.web_acquire import crawl_jobs
    from kazma_core.web_acquire.policy import acquisition_policy

    try:
        seed_url = validate_web_url(seed_url)
    except KnowledgeUrlRefused as exc:
        raise AcquisitionError(FetchErrorCode.INVALID_URL, str(exc)) from None
    store = await asyncio.to_thread(get_knowledge_store)
    await asyncio.to_thread(store.ensure_capacity, library_id)  # KnowledgeStorageFull, typed
    policy = acquisition_policy()
    with crawl_policy(purpose="kb") as robots:
        respect = robots.enforce()
    cap = _kb_max_pages()
    try:
        if isinstance(max_pages, bool):
            raise ValueError
        page_cap = max(1, min(cap, int(max_pages or cap)))
    except (ValueError, TypeError, OverflowError):
        raise AcquisitionError(FetchErrorCode.POLICY, 'max_pages must be an integer; crawl admission refused.') from None
    snapshot = dict(mode=policy.mode, providers=sorted(policy.providers), robots=respect,
        scope=_kb_scope_mode(), max_pages=page_cap, max_depth=_kb_max_depth(), delay_ms=_kb_delay_ms())
    # Last of the checks (it starts a resolver child), and before the
    # journal row: a refused seed leaves no job and, its callers creating
    # the library only after this returns, no library.
    await _admit_public_seed(seed_url)
    job = await crawl_jobs.call(crawl_jobs.start, library_id, seed_url, snapshot, job_id=job_id,
        resume=resume, on_cancel=lambda created: created.finish('interrupted'))
    return job, policy, respect, page_cap


async def ingest_site(
    library_id: str,
    seed_url: str,
    *,
    max_pages: int | None = None,
    job_id: str | None = None,
    resume: bool = False,
    _prepared=None,
) -> AsyncIterator[ProgressUpdate]:
    """Run a durable crawl; an explicit resume skips settled/ambiguous pages."""
    from kazma_core.web_acquire import crawl_jobs
    from kazma_core.web_acquire.policy import acquisition_policy, policy_scope
    from kazma_core.playwright_loop import finish_owned

    try:
        job, policy, respect, page_cap = _prepared or await _prepare_ingest_site(
            library_id, seed_url, max_pages=max_pages, job_id=job_id, resume=resume)
    except AcquisitionError as exc:
        if exc.code != FetchErrorCode.POLICY:
            raise
        yield ProgressUpdate(phase='error', message=f'{exc.code.value}: {exc}', errors=[f'{exc.code.value}: {exc}'])
        return
    iterator = _ingest_site_impl(library_id, seed_url, max_pages=page_cap, checkpoint=job)
    status = 'interrupted'
    try:
        while True:
            token = crawl_jobs.current_crawl.set(job)
            try:
                with policy_scope(policy), crawl_policy(respect=respect):
                    update = await anext(iterator)
            except StopAsyncIteration:
                break
            finally:
                crawl_jobs.current_crawl.reset(token)
            update.job_id = job.job_id
            await crawl_jobs.call(job.progress, update)
            if update.phase in ('done', 'error'):
                status = 'failed' if update.phase == 'error' else 'partial' if update.failed else 'completed'
                if status == 'completed':
                    row = await crawl_jobs.call(job.read)
                    state = row.get('discovery') or {}
                    if row.get('metadata_limited') or state.get('limited') or state.get('ambiguous') or any(record['status'] == 'ambiguous' for record in row.get('metadata', {}).values()):
                        status = 'partial'
            yield update
    finally:
        try:
            await finish_owned(iterator.aclose())
        finally:
            await finish_owned(crawl_jobs.call(job.finish, status))


async def _ingest_site_impl(
    library_id: str,
    seed_url: str,
    *,
    max_pages: int | None = None,
    checkpoint=None,
) -> AsyncIterator[ProgressUpdate]:
    """Ingest a whole doc tree, yielding progress as it goes.

    Discovery is sitemap-first; per-page extraction is tiered with a
    Playwright full-DOM fallback for tabbed/JS pages.  Per-page dedup via
    content hash means a refresh only re-indexes changed pages.
    """
    # Capture job policy without leaving ContextVars set while yielding progress.
    try:
        with crawl_policy(purpose="kb") as job_policy:
            job_policy.enforce()
    except AcquisitionError as exc:
        yield ProgressUpdate(phase="error", message=f"crawl policy failed: {exc}")
        return
    from kazma_core.web_acquire.crawl_jobs import call

    saved = await call(checkpoint.read)
    page_cap = saved['policy']['max_pages']
    delay_ms = saved['policy']['delay_ms']
    started = datetime.now(UTC).isoformat()
    result = IngestResult()

    # ── Discover ─────────────────────────────────────────────────────
    yield ProgressUpdate(phase="discover", started_at=started, message=f"discovering pages under {seed_url}")

    # Discovery runs as a task so we can stream its progress messages to
    # the caller live (otherwise the slow robots.txt/sitemap/Playwright
    # sequence looks like a silent hang).  The callback pushes messages
    # into a queue; we drain it while awaiting the discovery result.
    import asyncio as _aio
    progress_q: _aio.Queue[str] = _aio.Queue(maxsize=64)

    async def _on_discovery_progress(msg: str) -> None:
        if progress_q.full():
            progress_q.get_nowait()
        progress_q.put_nowait(msg[:2000])

    try:
        from kazma_core.security.ssrf import validate_url

        from kazma_core.web_acquire.dns_worker import validate_public_url

        await validate_public_url(seed_url, validator=validate_url)
    except Exception as exc:
        yield ProgressUpdate(phase="error", message=f"invalid seed: {exc}", started_at=started)
        return

    scope_mode = saved['policy']['scope']
    checker = lambda target: _in_scope(seed_url, target, scope_mode)
    async def discover_or_resume():
        if saved['discovered']:
            return [page['url'] for page in saved['pages']]
        return await kb_discover_pages(seed_url, mode=scope_mode, on_progress=_on_discovery_progress)

    with crawl_policy(job=job_policy):
        disco_task = _aio.create_task(discover_or_resume())
    try:
        while not disco_task.done():
            # Drain any progress messages that arrived, with a short timeout
            # so we still poll the task promptly.
            try:
                msg = await _aio.wait_for(progress_q.get(), timeout=0.5)
                yield ProgressUpdate(
                    phase="discover", started_at=started,
                    message=f"discovery: {msg}",
                )
            except _aio.TimeoutError:
                # No message yet; yield control so the task can progress.
                await _aio.sleep(0.1)
        # Task finished — drain any remaining messages.
        while not progress_q.empty():
            msg = progress_q.get_nowait()
            yield ProgressUpdate(phase="discover", started_at=started, message=f"discovery: {msg}")
        pages = await disco_task
    except Exception as exc:
        disco_task.cancel()
        yield ProgressUpdate(phase="error", message=f"discovery failed: {exc}", started_at=started)
        return
    finally:
        # Includes cancellation and async-generator close (BaseExceptions).
        # Await ownership cleanup so a cancelled crawl cannot keep discovering.
        if not disco_task.done():
            disco_task.cancel()
        await _aio.gather(disco_task, return_exceptions=True)

    pages = pages[:page_cap]
    if not saved['discovered']:
        await call(checkpoint.frontier, pages)
    result.pages_discovered = len(pages)
    yield ProgressUpdate(
        phase="discover", discovered=len(pages), started_at=started,
        message=f"discovered {len(pages)} pages — starting fetch",
    )

    # ── Fetch + ingest each page (shared Playwright for the job) ─────
    from kazma_core.stores.knowledge import KnowledgeLibraryGone, KnowledgeStorageFull

    index = await call(get_knowledge_index)
    store = await call(get_knowledge_store)
    fetched = saved['fetched']
    failed = saved['failed'] + sum(page['status'] == 'ambiguous' for page in saved['pages'])
    chunks_new = saved['ingested']
    chunks_skipped = saved['skipped']
    stored_as: set[str] = set()  # page identities stored by this run (KB-E14)
    stopped = ""  # why the crawl stopped early, if it did

    pages_unchanged = saved['pages_unchanged']
    async with _pw_browser_scope():
        for i, url in enumerate(pages):
            # A deleted library stops its crawl before the next fetch: no
            # more egress, allowance or writes for it (KB-E02).
            if not await call(store.library_exists, library_id):
                stopped = "the library was deleted; the crawl stopped"
                break
            if not await call(checkpoint.begin_page, url):
                continue
            yield ProgressUpdate(
                phase="fetch",
                discovered=len(pages), fetched=fetched, ingested=chunks_new,
                skipped=chunks_skipped, failed=failed,
                current_url=url,
                message=f"[{i + 1}/{len(pages)}] {url}",
                started_at=started,
            )
            try:
                with crawl_policy(job=job_policy):
                    text, status, reason = await _extract_page(url, scope_check=checker)
                if text is None or status == "error":
                    failed += 1
                    result.failed_urls.append(url)
                    if reason:
                        err_msg = f"{url}: {reason}"
                        result.errors.append(err_msg)
                        logger.warning("[kb_ingest] page failed %s: %s", url, reason)
                    else:
                        err_msg = f"{url}: fetch returned no content"
                        result.errors.append(err_msg)
                    await call(checkpoint.end_page, url, error=reason or 'No readable content')
                    yield ProgressUpdate(
                        phase="fetch",
                        discovered=len(pages), fetched=fetched, ingested=chunks_new,
                        skipped=chunks_skipped, failed=failed,
                        current_url=url,
                        message=f"[{i + 1}/{len(pages)}] failed: {reason or 'no content'}",
                        errors=list(result.errors[-5:]),
                        started_at=started,
                    )
                    continue
                # The page is stored under where the fetch landed: two links
                # that redirect to one page index it once (KB-E14).
                identity = _OBSERVED_URL.get() or url
                if identity in stored_as:
                    fetched += 1
                    result.pages_fetched += 1
                    await call(checkpoint.end_page, url, unchanged=True)
                    continue
                new, unchanged_chunks, n_chunks = await call(
                    _store_page, index, library_id, identity, text
                )
                stored_as.add(identity)
                chunks_new += new
                chunks_skipped += unchanged_chunks
                # Unchanged means the page's stored chunks equal the fetched
                # ones; a page that could not be stored raises below and is
                # failed, never counted here (KB-E02).
                unchanged = bool(n_chunks and new == 0 and unchanged_chunks >= n_chunks)
                pages_unchanged += int(unchanged)
                fetched += 1
                result.pages_fetched += 1
                await call(checkpoint.end_page, url, new=new, skipped=unchanged_chunks,
                    unchanged=unchanged)
            except KnowledgeLibraryGone:
                failed += 1
                result.failed_urls.append(url)
                await call(checkpoint.end_page, url, error="The library was deleted.")
                stopped = "the library was deleted; the crawl stopped"
                break
            except KnowledgeStorageFull as exc:
                failed += 1
                result.failed_urls.append(url)
                result.errors.append(f"{url}: {exc}")
                await call(checkpoint.end_page, url, error=str(exc))
                stopped = str(exc)
                break
            except Exception as exc:
                if isinstance(exc, AcquisitionError) and exc.code.value in ('storage_error', 'policy_error', 'resource_limit'):
                    raise
                failed += 1
                result.failed_urls.append(url)
                result.errors.append(f"{url}: {exc}")
                logger.warning("[kb_ingest] page failed %s: %s", url, exc)
                await call(checkpoint.end_page, url, error=str(exc))

            if delay_ms:
                await asyncio.sleep(delay_ms / 1000.0)

    # Refresh counts; discovery absence never authorizes deletion of old pages.
    await call(_refresh_chunk_count, store, library_id)

    if stopped:
        yield ProgressUpdate(
            phase="error",
            discovered=len(pages), fetched=fetched, ingested=chunks_new,
            skipped=chunks_skipped, failed=failed, pages_unchanged=pages_unchanged,
            message=f"stopped: {stopped}", errors=list(result.errors) or [stopped],
            started_at=started,
        )
        return

    # If everything failed, surface the FIRST failure reason in the done
    # message — that's what the user sees in the toast/progress panel, and
    # "0/1 pages, 0 chunks, 1 failed" without a reason is unactionable.
    done_msg = (
        f"{'done' if fetched else 'failed'}: {fetched}/{len(pages)} pages, "
        f"{chunks_new} new chunks"
        + (f", {pages_unchanged} pages unchanged" if pages_unchanged else "")
        + f", {failed} failed"
    )
    if failed > 0 and fetched == 0 and result.errors:
        # All pages failed — append the first reason to the summary so the
        # user immediately sees *why* (bot-wall / Chromium-missing / etc.).
        first_reason = result.errors[0]
        done_msg += f" — first failure: {first_reason}"

    yield ProgressUpdate(
        phase="done" if fetched else "error",
        discovered=len(pages), fetched=fetched, ingested=chunks_new,
        skipped=chunks_skipped, failed=failed,
        pages_unchanged=pages_unchanged,
        message=done_msg,
        errors=list(result.errors),
        started_at=started,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)

def _derive_title(text: str, url: str) -> str:
    """Best-effort page title from the first H1, else the URL's last segment."""
    m = _TITLE_RE.search(text[:2000])
    if m:
        return m.group(1).strip()[:200]
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").strip()[:200] or url
