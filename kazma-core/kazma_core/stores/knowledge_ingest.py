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
       ├─ static:  _fetch_full_text(url)   (tiered: Jina/Firecrawl/httpx/Playwright)
       └─ tabbed:  Playwright full-DOM extraction (hidden panels included)

    chunk_markdown_doc → KnowledgeIndex.index (SQLite + ChromaDB + FTS5)

The ingestion runs as a background ``asyncio`` task that yields progress
records (discovered / fetched / ingested / failed / current_url) which the
UI and the ``/kb`` slash commands surface live.

The fetch tiering reuses :func:`kazma_core.tools.read_url._fetch_full_text`
(SSRF-safe, with optional Jina/Firecrawl/Playwright fallbacks) so we do
not reinvent extraction.  The Playwright *full-DOM* extractor is new and
lives here because trafilatura's "main visible article" assumption is
wrong for tabbed doc sites.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncIterator
from urllib.parse import urldefrag, urljoin, urlparse

from kazma_core.stores.knowledge import get_knowledge_store
from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict
from kazma_core.stores.knowledge_index import get_knowledge_index
from kazma_core.tools.read_url import _fetch_full_text

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
)

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
    skipped: int = 0         # chunks skipped (dedup)
    failed: int = 0          # pages that failed
    current_url: str = ""
    message: str = ""
    errors: list[str] = field(default_factory=list)  # per-page failure reasons
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class IngestResult:
    """Final summary of an ingest (single page or whole tree)."""

    pages_discovered: int = 0
    pages_fetched: int = 0
    pages_failed: int = 0
    chunks_new: int = 0
    chunks_skipped: int = 0
    failed_urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ── URL helpers ─────────────────────────────────────────────────────────────


def _normalize_url(base: str, href: str) -> str | None:
    if not href or href.startswith(("#", "mailto:", "javascript:", "tel:", "data:")):
        return None
    abs_url = urljoin(base, href)
    abs_url, _frag = urldefrag(abs_url)
    parsed = urlparse(abs_url)
    if parsed.scheme not in ("http", "https"):
        return None
    if any((parsed.path or "").lower().endswith(ext) for ext in _SKIP_EXT):
        return None
    return abs_url


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
            return False  # can't determine topic → reject to be safe
        cand_segments = {s.lower() for s in (urlparse(candidate).path or "").split("/") if s}
        return bool(seed_topic & cand_segments)
    # prefix (legacy default — kept for explicit opt-in)
    seed_host = (urlparse(seed_url).hostname or "").lower().removeprefix("www.")
    cand_host = (urlparse(candidate).hostname or "").lower().removeprefix("www.")
    if seed_host != cand_host:
        return False
    return (urlparse(candidate).path or "").startswith(_seed_prefix(seed_url))


# ── Discovery: sitemap-first, BFS fallback ──────────────────────────────────


async def _http_get_text(url: str, *, timeout: float = 20.0) -> tuple[str | None, str]:
    """Lightweight GET.  Returns (text, final_url).  None text on failure.

    Transparently handles gzip/deflate Content-Encoding AND ``.gz`` URLs
    (some sites — e.g. developers.facebook.com — ship sitemaps as
    ``sitemap.xml.gz`` even though the response is the raw gzip bytes).
    """
    try:
        import gzip
        import httpx
        from kazma_core.security.ssrf import SSRFError, validate_url

        validate_url(url)
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=timeout,
            headers={"User-Agent": _BROWSER_UA, "Accept-Encoding": "gzip, deflate"},
        ) as client:
            r = await client.get(url)
            final = str(r.url)
            try:
                validate_url(final)
            except SSRFError:
                return None, url
            if r.status_code >= 400:
                return None, final
            content = r.content or b""
            # Case 1: URL ends in .gz → content is raw gzip bytes regardless
            # of Content-Encoding.  Decompress and inspect.
            if final.lower().endswith(".gz"):
                try:
                    decompressed = gzip.decompress(content).decode("utf-8", errors="replace")
                    if decompressed.lstrip().startswith("<"):
                        return decompressed, final
                except Exception as exc:
                    logger.debug("[kb_discover] .gz decompress failed %s: %s", final, exc)
                # Decompression failed or didn't yield XML — likely a bot-walled
                # HTML error page returned for a .gz URL.  Drop it.
                return None, final
            # Case 2: response body (httpx already decompresses Content-Encoding
            # for us, so r.text is plain text).
            return r.text or "", final
    except Exception as exc:
        logger.debug("[kb_discover] GET %s failed: %s", url, exc)
        return None, url


async def _fetch_sitemaps(seed_url: str) -> list[str]:
    """Collect sitemap URLs from robots.txt + well-known locations.

    Returns the raw XML text of every sitemap we could fetch (index or
    leaf).  Parsing happens in :func:`_extract_urls_from_sitemap`.
    """
    parsed = urlparse(seed_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates: list[str] = []

    # 1. robots.txt Sitemap: directives (authoritative).
    robots_txt, _ = await _http_get_text(f"{origin}/robots.txt", timeout=10.0)
    if robots_txt:
        for line in robots_txt.splitlines():
            line = line.strip()
            if line.lower().startswith("sitemap:"):
                sitemap_url = line.split(":", 1)[1].strip()
                if sitemap_url:
                    candidates.append(sitemap_url)

    # 2. Well-known sitemap locations.
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/docs/sitemap.xml", "/docs/sitemap-index.xml"):
        candidates.append(f"{origin}{path}")

    # Fetch each candidate once, keep non-empty XML.
    seen: set[str] = set()
    docs: list[str] = []
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        text, _ = await _http_get_text(url, timeout=15.0)
        if text and (text.lstrip().startswith("<") or "<?xml" in text[:80]):
            docs.append(text)
    return docs


_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _extract_urls_from_sitemap(xml_text: str) -> list[str]:
    """Pull every ``<loc>`` URL out of a sitemap XML (index or leaf)."""
    urls: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.debug("[kb_discover] sitemap parse failed: %s", exc)
        return urls
    # Sitemap URLs live in <url><loc> or <sitemap><loc>; just grab all <loc>.
    for loc in root.iter():
        tag = loc.tag.split("}")[-1].lower()  # strip namespace
        if tag == "loc" and loc.text:
            urls.append(loc.text.strip())
    return urls


async def kb_discover_pages(
    seed_url: str,
    *,
    mode: str | None = None,
    on_progress: Any = None,
) -> list[str]:
    """Discover the full set of in-scope URLs for a library.

    Strategy (first wins):
      1. Sitemap (robots.txt + well-known) — authoritative complete list.
      2. BFS link-walk using Playwright-rendered HTML so SPA nav links are
         captured.  Path-prefix scoped.

    ``on_progress`` is an optional **plain async** callable ``async (msg) -> None``
    receiving a short human-readable status string (e.g. ``"fetching
    robots.txt…"``) so the caller can show live discovery activity
    instead of a silent 0/0/0.  It must NOT be a generator.

    Returns a de-duplicated, in-scope, ordered URL list, seed first.
    """
    if not seed_url.strip():
        return []
    seed = seed_url.strip()
    if not seed.startswith(("http://", "https://")):
        seed = "https://" + seed
    scope_mode = (mode or _kb_scope_mode()).lower()
    if scope_mode not in ("tree", "prefix", "domain", "exact"):
        scope_mode = "tree"

    # ── 1. Sitemap discovery ─────────────────────────────────────────
    await _safe_progress(on_progress, "fetching robots.txt + sitemaps…")
    discovered: list[str] = []
    sitemap_docs = await _fetch_sitemaps(seed)
    if sitemap_docs:
        await _safe_progress(on_progress, f"parsing {len(sitemap_docs)} sitemap doc(s)…")
        for doc in sitemap_docs:
            discovered.extend(_extract_urls_from_sitemap(doc))
        discovered = [u for u in discovered if _in_scope(seed, u, scope_mode)]
        if discovered:
            # De-dup, keep order, seed first.
            seen: set[str] = set()
            ordered: list[str] = []
            for u in [seed, *discovered]:
                if u not in seen:
                    seen.add(u)
                    ordered.append(u)
            logger.info(
                "[kb_discover] sitemap yielded %d in-scope URLs for %s",
                len(ordered), seed,
            )
            return ordered
        await _safe_progress(on_progress, "sitemap had 0 in-scope URLs; falling back to link-walk")
    else:
        await _safe_progress(on_progress, "no sitemap reachable; falling back to link-walk")

    # ── 2. BFS link-walk fallback (Playwright for SPA nav) ──────────
    if scope_mode == "exact":
        return [seed]
    await _safe_progress(on_progress, "rendering seed page to discover nav links…")
    bfs_urls = await _bfs_discover(seed, scope_mode, on_progress=on_progress)
    if bfs_urls:
        return bfs_urls
    # BFS also found nothing usable — the seed page itself was unfetchable
    # (likely bot-walled).  Tell the user why this dropped to "just the
    # seed" so they know to enable a fetch backend rather than seeing an
    # unexplained discovered=1.
    await _safe_progress(
        on_progress,
        "link-walk found no nav links (seed page unreadable — site is likely "
        "bot-walled; enable KAZMA_FIRECRAWL_API_KEY or KAZMA_JINA_READER=1)",
    )
    return [seed]


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
    """BFS link-walk using Playwright-rendered HTML (so SPA nav links are seen)."""
    max_pages = _kb_max_pages()
    max_depth = _kb_max_depth()
    queue: deque[tuple[str, int]] = deque([(seed, 0)])
    seen: set[str] = set()
    ordered: list[str] = []
    while queue and len(ordered) < max_pages:
        url, depth = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        await _safe_progress(on_progress, f"link-walk depth {depth}: {url[:80]}")
        # Depth 0 (seed) and bot-walled sites → use Playwright first.
        # Deeper pages are usually inter-page links the static fetcher can read.
        html = await _fetch_html_playwright(url) if depth == 0 else await _fetch_html_raw(url)
        if not html or _looks_like_bot_block_html(html):
            # Fall back to the other fetcher.
            html = await _fetch_html_raw(url) if depth == 0 else await _fetch_html_playwright(url)
        if not html or _looks_like_bot_block_html(html):
            continue
        ordered.append(url)
        if depth < max_depth:
            for href in _extract_links_from_html(html, url):
                if href in seen:
                    continue
                if not _in_scope(seed, href, scope_mode):
                    continue
                queue.append((href, depth + 1))
    return ordered


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


async def _fetch_html_raw(url: str) -> str | None:
    text, _ = await _http_get_text(url)
    return text


def _looks_like_bot_block_html(html: str | None) -> bool:
    """Heuristic: did the server return an error/bot-block page instead of content?

    Many bot-walled sites (Meta especially) return HTTP 200 with a tiny
    ``<title>Error</title>`` HTML stub to non-browser clients.  Detecting
    this lets us fall through to Playwright instead of treating the stub
    as a (worthless) successful fetch.
    """
    if not html:
        return True
    sample = html[:2000].lower()
    if "<title>error</title>" in sample:
        return True
    if "<title>404</title>" in sample or "<title>403</title>" in sample:
        return True
    # Very short HTML with no real body content.
    if len(html) < 500 and "<p>" not in sample and "<article" not in sample:
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


async def _render_with_playwright(url: str, *, want_text: bool) -> str | None:
    """Render a URL with a stealth Chromium context, return HTML or full-DOM text.

    This is the **primary** fetcher for bot-walled SPA doc sites (Meta,
    etc.) where httpx returns a 400/error stub regardless of UA.  We:

    - Use ``domcontentloaded`` instead of ``networkidle`` so SPAs that
      keep long-poll connections open don't hang forever (the cause of
      the original crawl hanging at 0/0/0 on Meta).
    - Apply the same stealth init scripts as ``read_url._fetch_with_playwright``
      (``navigator.webdriver`` removal, fake plugins, etc.).
    - Wait for *content* (``body`` innerText to exceed a threshold) rather
      than network quiescence.
    - Detect a returned bot-block/error page and return None so the caller
      can fall through or surface a clean error.

    Args:
        want_text: ``False`` → return raw HTML (for link discovery).
                   ``True``  → return the full-DOM textContent (for chunking;
                   includes hidden-tab panels).
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            try:
                ctx = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=_BROWSER_UA,
                    locale="en-US",
                    timezone_id="America/New_York",
                )
                await ctx.add_init_script(
                    """
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    window.chrome = { runtime: {} };
                    """
                )
                page = await ctx.new_page()
                # ``domcontentloaded`` returns as soon as the DOM parses —
                # NOT after network quiescence.  SPAs with analytics/beacon
                # traffic never reach networkidle and would hang here.
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                # Wait for real content: body innerText must exceed the
                # useful-chars threshold within a bounded budget.
                try:
                    await page.wait_for_function(
                        f"() => (document.body && (document.body.innerText || '').length) >= {MIN_USEFUL_CHARS}",
                        timeout=20000,
                    )
                except Exception:
                    # Content never reached the threshold — give up gracefully
                    # rather than hanging.  Caller will treat as a failed page.
                    logger.debug("[kb_ingest] Playwright content wait failed for %s", url)
                    return None

                if not want_text:
                    html = await page.content()
                    if _looks_like_bot_block_html(html):
                        return None
                    return html

                # want_text: click tabs to render hidden panels, then pull
                # full-DOM textContent (includes display:none/aria-hidden).
                try:
                    await _click_tabs(page)
                except Exception as exc:
                    logger.debug("[kb_ingest] tab click pass skipped: %s", exc)
                text = await page.evaluate(
                    """() => {
                        const root = document.body || document.documentElement;
                        return root ? (root.textContent || '') : '';
                    }"""
                )
                if not text or len(text) < MIN_USEFUL_CHARS:
                    return None
                # Collapse whitespace; the chunker re-introduces structure.
                text = re.sub(r"[ \t]+\n", "\n", text)
                text = re.sub(r"\n{3,}", "\n\n", text)
                return text.strip()
            finally:
                await browser.close()
    except Exception as exc:
        logger.debug("[kb_ingest] Playwright render failed %s: %s", url, exc)
        return None


async def _extract_page(url: str) -> tuple[str | None, str, str]:
    """Extract markdown from one page.

    Tiered:
      1. ``_fetch_full_text`` (Jina → Firecrawl → httpx+trafilatura → Playwright).
      2. If (1) returns thin content, an error stub, or fails on what looks
         like a bot-walled JS/tab page, fall back to Playwright full-DOM
         extraction (hidden panels included).

    Returns ``(markdown_or_None, status, reason)`` where:
      status is one of ``"ok"``, ``"thin"``, ``"empty"``, ``"error"``;
      reason is a short human-readable diagnostic (empty when ok).  The
      reason is what surfaces in the job's failed-URL log so the user can
      tell *why* a page failed (bot-wall vs. Chromium-missing vs. timeout)
      instead of seeing an opaque "1 failed".
    """
    text = await _fetch_full_text(url)
    looks_blocked = text.startswith("Error:") or _looks_like_bot_block_html(text)
    if looks_blocked:
        # Try Playwright full-DOM before giving up.
        pw_reason = _check_playwright_available()
        if pw_reason:
            return None, "error", f"blocked by site; Playwright unavailable ({pw_reason})"
        pw = await _render_with_playwright(url, want_text=True)
        if pw and len(pw) >= MIN_USEFUL_CHARS:
            return pw, "ok", ""
        # Distinguish "Chromium missing" from "bot-walled even for browser".
        return None, "error", (
            "page is bot-walled (httpx returned an error stub) and Playwright "
            "could not extract usable content — install a fetch backend: "
            "KAZMA_FIRECRAWL_API_KEY (best for bot-walled sites), "
            "KAZMA_JINA_READER=1, or run `playwright install chromium`"
        )
    if len(text) < MIN_USEFUL_CHARS:
        pw_reason = _check_playwright_available()
        if pw_reason:
            # No point trying Playwright if it can't launch.
            if not text.strip():
                return None, "empty", f"empty extract; Playwright unavailable ({pw_reason})"
            return text, "thin", f"thin extract ({len(text)} chars); Playwright unavailable ({pw_reason})"
        pw = await _render_with_playwright(url, want_text=True)
        if pw and len(pw) > len(text):
            return pw, "ok", ""
        if not text.strip():
            return None, "empty", "empty extract after all tiers"
        return text, "thin", f"thin extract ({len(text)} chars) — likely a JS-only page"
    return text, "ok", ""


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
        return "playwright package not installed (pip install kazma[web])"
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


async def _click_tabs(page: Any) -> None:
    """Best-effort: click role=tab / tablist controls so panels render.

    Doc sites commonly render only the active tab's content into the DOM
    on first paint; clicking each tab forces the others into the DOM so
    the subsequent ``textContent`` pull captures them.  Failures are
    swallowed — the full-DOM pull still catches content already in DOM.
    """
    # Use the Playwright role selector; clicks are silent if no matches.
    for selector in ("[role=tab]", "button[aria-selected]", ".tab-button", "[data-tab]"):
        try:
            locs = page.locator(selector)
            count = await locs.count()
            for i in range(min(count, 30)):  # cap to avoid runaway clicks
                try:
                    await locs.nth(i).click(timeout=1500)
                    await page.wait_for_timeout(150)
                except Exception:
                    continue
        except Exception:
            continue


# ── Ingestion: single page + whole tree ─────────────────────────────────────


def _save_provenance(library_id: str, url: str, text: str) -> None:
    """Best-effort save of a fetched page's raw markdown under the workspace.

    Stored under ``{KAZMA_RESEARCH_DIR}/kb/{library_id}/<slug>.md`` so the
    original page text is inspectable later (the chunker is lossy at the
    boundaries; this is the source-of-truth copy for re-debugging).  All
    failures are swallowed — ingestion must not depend on it.
    """
    try:
        import hashlib
        from pathlib import Path

        from kazma_core.tools.read_url import _workspace_root

        root = _workspace_root()
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", url)[:80].strip("-") or "page"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
        fname = f"{slug}-{digest}.md"
        target = (root / "research" / "kb" / library_id / fname).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        # Containment check — never escape the workspace.
        target.relative_to(root.resolve())
        header = (
            f"# Source: {url}\n"
            f"# Library: {library_id}\n"
            f"# Saved: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"# Chars: {len(text)}\n\n"
        )
        target.write_text(header + text, encoding="utf-8")
    except Exception as exc:
        logger.debug("[kb_ingest] provenance save failed for %s: %s", url, exc)


async def ingest_url(
    library_id: str,
    url: str,
    *,
    document_title: str = "",
) -> IngestResult:
    """Ingest a single page into a library.

    Fetches → chunks → indexes.  Returns a :class:`IngestResult` summary.
    """
    result = IngestResult(pages_discovered=1)
    index = get_knowledge_index()
    try:
        from kazma_core.security.ssrf import SSRFError, validate_url

        validate_url(url)
    except Exception as exc:
        result.errors.append(f"SSRF/invalid URL {url}: {exc}")
        result.failed_urls.append(url)
        result.pages_failed = 1
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

    _save_provenance(library_id, url, text)

    chunks = chunk_markdown_doc(
        text,
        source_url=url,
        library_id=library_id,
        document_title=document_title or _derive_title(text, url),
    )
    if not chunks:
        result.pages_fetched = 1
        return result

    chunk_dicts = [chunk_to_dict(c) for c in chunks]
    new, skipped = index.index(library_id, chunk_dicts)
    result.pages_fetched = 1
    result.chunks_new += new
    result.chunks_skipped += skipped
    return result


async def ingest_site(
    library_id: str,
    seed_url: str,
    *,
    max_pages: int | None = None,
) -> AsyncIterator[ProgressUpdate]:
    """Ingest a whole doc tree, yielding progress as it goes.

    Discovery is sitemap-first; per-page extraction is tiered with a
    Playwright full-DOM fallback for tabbed/JS pages.  Per-page dedup via
    content hash means a refresh only re-indexes changed pages.
    """
    cap = _kb_max_pages()
    page_cap = max(1, min(cap, int(max_pages or cap)))
    delay_ms = _kb_delay_ms()
    started = datetime.now(UTC).isoformat()
    result = IngestResult()

    # ── Discover ─────────────────────────────────────────────────────
    yield ProgressUpdate(phase="discover", started_at=started, message=f"discovering pages under {seed_url}")

    # Discovery runs as a task so we can stream its progress messages to
    # the caller live (otherwise the slow robots.txt/sitemap/Playwright
    # sequence looks like a silent hang).  The callback pushes messages
    # into a queue; we drain it while awaiting the discovery result.
    import asyncio as _aio
    progress_q: _aio.Queue[str] = _aio.Queue()

    async def _on_discovery_progress(msg: str) -> None:
        await progress_q.put(msg)

    try:
        from kazma_core.security.ssrf import SSRFError, validate_url

        validate_url(seed_url)
    except Exception as exc:
        yield ProgressUpdate(phase="error", message=f"invalid seed: {exc}", started_at=started)
        return

    disco_task = _aio.create_task(kb_discover_pages(seed_url, on_progress=_on_discovery_progress))
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

    pages = pages[:page_cap]
    result.pages_discovered = len(pages)
    yield ProgressUpdate(
        phase="discover", discovered=len(pages), started_at=started,
        message=f"discovered {len(pages)} pages — starting fetch",
    )

    # ── Fetch + ingest each page ─────────────────────────────────────
    index = get_knowledge_index()
    store = get_knowledge_store()
    fetched = 0
    failed = 0
    chunks_new = 0
    chunks_skipped = 0

    for i, url in enumerate(pages):
        yield ProgressUpdate(
            phase="fetch",
            discovered=len(pages), fetched=fetched, ingested=chunks_new,
            skipped=chunks_skipped, failed=failed,
            current_url=url,
            message=f"[{i + 1}/{len(pages)}] {url}",
            started_at=started,
        )
        try:
            text, status, reason = await _extract_page(url)
            if text is None or status == "error":
                failed += 1
                result.failed_urls.append(url)
                # Surface the per-tier failure reason in the job log so
                # the user can tell bot-wall from Chromium-missing from
                # timeout — previously this was an opaque "failed=1".
                if reason:
                    err_msg = f"{url}: {reason}"
                    result.errors.append(err_msg)
                    logger.warning("[kb_ingest] page failed %s: %s", url, reason)
                else:
                    err_msg = f"{url}: fetch returned no content"
                    result.errors.append(err_msg)
                # Push a progress tick so the UI's failed counter and the
                # error list update live (not just at the end).
                yield ProgressUpdate(
                    phase="fetch",
                    discovered=len(pages), fetched=fetched, ingested=chunks_new,
                    skipped=chunks_skipped, failed=failed,
                    current_url=url,
                    message=f"[{i + 1}/{len(pages)}] failed: {reason or 'no content'}",
                    errors=list(result.errors[-5:]),  # last 5 to bound size
                    started_at=started,
                )
                continue
            _save_provenance(library_id, url, text)
            chunks = chunk_markdown_doc(
                text,
                source_url=url,
                library_id=library_id,
                document_title=_derive_title(text, url),
            )
            if chunks:
                chunk_dicts = [chunk_to_dict(c) for c in chunks]
                new, skipped = index.index(library_id, chunk_dicts)
                chunks_new += new
                chunks_skipped += skipped
            fetched += 1
            result.pages_fetched += 1
        except Exception as exc:
            failed += 1
            result.failed_urls.append(url)
            result.errors.append(f"{url}: {exc}")
            logger.warning("[kb_ingest] page failed %s: %s", url, exc)

        if delay_ms:
            await asyncio.sleep(delay_ms / 1000.0)

    # Persist final chunk_count on the library row (index() also updates it,
    # but this is the authoritative post-crawl value).
    try:
        store.set_chunk_count(library_id, store.count_chunks(library_id))
    except Exception as exc:
        logger.debug("[kb_ingest] set_chunk_count failed: %s", exc)

    # If everything failed, surface the FIRST failure reason in the done
    # message — that's what the user sees in the toast/progress panel, and
    # "0/1 pages, 0 chunks, 1 failed" without a reason is unactionable.
    done_msg = (
        f"done: {fetched}/{len(pages)} pages, "
        f"{chunks_new} new chunks (+{chunks_skipped} deduped), "
        f"{failed} failed"
    )
    if failed > 0 and fetched == 0 and result.errors:
        # All pages failed — append the first reason to the summary so the
        # user immediately sees *why* (bot-wall / Chromium-missing / etc.).
        first_reason = result.errors[0]
        done_msg += f" — first failure: {first_reason}"

    yield ProgressUpdate(
        phase="done",
        discovered=len(pages), fetched=fetched, ingested=chunks_new,
        skipped=chunks_skipped, failed=failed,
        message=done_msg,
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
