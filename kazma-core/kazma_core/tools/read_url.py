"""Read URL tool — fetch, page, save, and chunk public web pages for research.

Tiered fetch (public web, not anti-bot invincible):

    1. Optional pre-backends when opted in (Firecrawl key / ``KAZMA_JINA_READER=1``).
    2. httpx fast path with browser-like UA + trafilatura extraction.
    3. **Hard-page recovery** on bot walls / empty extracts:
       Firecrawl (if key) → Jina Reader (unless ``KAZMA_JINA_READER=0``) → Playwright.

Research features
-----------------
* **Paging:** ``offset`` + ``max_chars`` to walk a long page without re-fetch
  (in-process full-text cache keyed by URL).
* **Caps:** default window from ``KAZMA_READ_URL_MAX_CHARS`` (default 16000).
* **Save:** ``read_url_to_file`` writes full extract under the workspace.
* **Chunks:** ``list_research_chunks`` / ``read_research_chunk`` /
  ``summarize_research_file`` for map-style research without flooding context.

SSRF-safe (validate URL + redirects). Knowledge ingest reuses ``_fetch_full_text``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "MAX_CONTENT_CHARS",
    "MIN_USEFUL_CHARS",
    "digest_research_file",
    "list_research_chunks",
    "read_research_chunk",
    "read_url",
    "read_url_to_file",
    "summarize_research_file",
]

logger = logging.getLogger(__name__)

# Default window for a single read_url response (env-overridable).
_DEFAULT_MAX = 16_000
_HARD_MAX = 100_000  # single-window absolute ceiling
_CACHE_MAX_ENTRIES = 32
_CACHE_TTL_S = 900  # 15 minutes

# Short static pages (e.g. example.com) are often ~100–180 chars of real text.
# 200 was too high and forced Playwright / false "bot wall" failures.
MIN_USEFUL_CHARS = 80
DEFAULT_CHUNK_SIZE = 4_000


def _env_int(name: str, default: int, *, lo: int = 500, hi: int = _HARD_MAX) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


def get_default_max_chars() -> int:
    """Default max chars for one ``read_url`` window (``KAZMA_READ_URL_MAX_CHARS``)."""
    return _env_int("KAZMA_READ_URL_MAX_CHARS", _DEFAULT_MAX)


# Back-compat name used by tests / older imports
MAX_CONTENT_CHARS = get_default_max_chars()

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_HTTP_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

_BOT_DETECTION_MARKERS = (
    "cloudflare",
    "checking your browser",
    "enable javascript",
    "datadome",
    "cf-browser-verification",
    "cf-challenge",
    "just a moment",
    "access denied",
    "are you a robot",
    "captcha",
    "incapsula",
    "perimeterx",
    "blocked",
    "unusual traffic",
    "please verify you are a human",
    "security check",
)

_JS_SHELL_MARKERS = (
    "id=\"root\"",
    "id='root'",
    "id=\"app\"",
    "id='app'",
    "id=\"__next\"",
    "__next_data__",
    "ng-version",
    "data-reactroot",
    "window.__INITIAL_STATE__",
    "noscript",
)

# url -> (full_text, timestamp)
_full_text_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()


def _cache_get(url: str) -> str | None:
    item = _full_text_cache.get(url)
    if not item:
        return None
    text, ts = item
    if time.time() - ts > _CACHE_TTL_S:
        _full_text_cache.pop(url, None)
        return None
    _full_text_cache.move_to_end(url)
    return text


def _cache_put(url: str, text: str) -> None:
    _full_text_cache[url] = (text, time.time())
    _full_text_cache.move_to_end(url)
    while len(_full_text_cache) > _CACHE_MAX_ENTRIES:
        _full_text_cache.popitem(last=False)


def clear_url_cache() -> None:
    """Clear the in-process full-text cache (tests)."""
    _full_text_cache.clear()


def _friendly_error(exc: Exception, url: str = "") -> str:
    if isinstance(exc, ConnectionError):
        return f"Error: Could not connect to {url}. Check the URL and your internet connection."
    if isinstance(exc, TimeoutError):
        return f"Error: Request to {url} timed out. The server may be slow or unreachable."
    if isinstance(exc, OSError):
        return f"Error: Network error while fetching {url} — {exc}"

    exc_name = type(exc).__name__
    if "HTTPStatusError" in exc_name:
        status = getattr(exc, "response", None)
        code = getattr(status, "status_code", "unknown")
        return f"Error: Server returned HTTP {code} for {url}."
    if "ConnectError" in exc_name:
        return f"Error: Could not connect to {url}. Check the URL and your internet connection."
    if "TimeoutException" in exc_name:
        return f"Error: Request to {url} timed out. The server may be slow or unreachable."

    return f"Error: Failed to read {url} — {exc}"


def _looks_like_bot_block(html: str, status_code: int) -> bool:
    """True only for *HTML* challenge / hard-block pages — not short static sites.

    Must NOT treat plain extracted text (or tiny legitimate pages like
    example.com) as bot walls — that forced Playwright and failed ingests.
    """
    if not html:
        return False
    # Non-HTML extracts (markdown from Jina, already-plain text) are never
    # Cloudflare challenges.
    sample_head = html[:200].lstrip().lower()
    looks_html = sample_head.startswith("<!doctype") or sample_head.startswith(
        "<html"
    ) or "<html" in html[:500].lower() or "<body" in html[:800].lower()

    if status_code in (403, 429, 503):
        html_lower = html[:5000].lower()
        if any(marker in html_lower for marker in _BOT_DETECTION_MARKERS):
            return True
        # Hard deny without body
        if status_code in (403, 429) and len(html) < 800:
            return True
        return False

    if not looks_html:
        return False

    html_lower = html[:5000].lower()
    # Strong challenge markers on short challenge pages only
    strong = (
        "checking your browser",
        "cf-challenge",
        "cf-browser-verification",
        "just a moment",
        "please verify you are a human",
        "are you a robot",
        "enable javascript and cookies",
    )
    if any(m in html_lower for m in strong):
        return True
    if "cloudflare" in html_lower and len(html) < 4000 and (
        "challenge" in html_lower or "ray id" in html_lower
    ):
        return True
    # Meta-style error stubs
    if "<title>error</title>" in html_lower or "<title>403</title>" in html_lower:
        return True
    return False


def _looks_like_js_shell(html: str) -> bool:
    sample = html[:12000].lower()
    # Require JS app markers AND little article-like content
    if not any(m in sample for m in _JS_SHELL_MARKERS):
        return False
    # Plain sites with a root div + real paragraphs are not shells
    if sample.count("<p") >= 2 or "<article" in sample:
        return False
    return True


def _strip_html_fallback(html: str) -> str:
    """stdlib-only extract when trafilatura is missing or returns nothing."""
    # Drop script/style blocks first
    cleaned = re.sub(
        r"(?is)<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>",
        " ",
        html,
    )
    # Prefer body if present
    body_m = re.search(r"(?is)<body[^>]*>(.*)</body>", cleaned)
    chunk = body_m.group(1) if body_m else cleaned
    text = re.sub(r"(?is)<br\s*/?>", "\n", chunk)
    text = re.sub(r"(?is)</(p|div|h[1-6]|li|tr|section|article)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    # Unescape a few common entities
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _extract_text(html: str) -> str:
    """Extract readable text; always try a plain-HTML fallback for static pages."""
    text = ""
    try:
        import trafilatura

        text = trafilatura.extract(
            html,
            include_links=True,
            include_tables=True,
            favor_recall=True,
            include_comments=False,
        ) or ""
    except ImportError:
        text = ""
    except Exception:
        text = ""

    text = (text or "").strip()
    # Trafilatura often returns None/empty for minimal static pages (example.com).
    # Fall back to a simple strip that still recovers title + body text.
    if len(text) < MIN_USEFUL_CHARS:
        fallback = _strip_html_fallback(html)
        if len(fallback) > len(text):
            text = fallback
    return text[:_HARD_MAX].strip()


def _is_thin_extraction(text: str, html: str) -> bool:
    """Whether Playwright is worth trying.

    Short *but non-empty* extracts from small static HTML are OK (example.com).
    Only treat as thin when empty, or tiny vs a large JS-heavy shell.
    """
    if not text or not text.strip():
        return True
    if len(text) >= MIN_USEFUL_CHARS:
        return False
    # Tiny extract: only escalate if the HTML looks like a SPA shell
    if _looks_like_js_shell(html) and len(html) >= 500:
        return True
    # Small static document with some words — accept as not-thin
    words = len(text.split())
    if words >= 8:
        return False
    if len(html) >= 1500 and words < 5:
        return True
    return False


def _should_try_playwright(
    html: str,
    status_code: int,
    extracted: str | None = None,
) -> bool:
    if _looks_like_bot_block(html, status_code):
        return True
    if extracted is not None and _is_thin_extraction(extracted, html):
        return True
    return False


def _slice_window(
    full: str,
    *,
    offset: int = 0,
    max_chars: int | None = None,
) -> str:
    """Return a window of *full* with paging metadata footer."""
    total = len(full)
    off = max(0, int(offset or 0))
    default_max = get_default_max_chars()
    if max_chars is None:
        window = default_max
    else:
        try:
            window = int(max_chars)
        except (TypeError, ValueError):
            window = default_max
    window = max(200, min(_HARD_MAX, window))

    if off >= total and total > 0:
        return (
            f"[offset {off} past end of content — total {total} chars]\n"
            f"Use offset=0 or a smaller offset. "
            f"Or call read_url_to_file to save the full page."
        )

    chunk = full[off : off + window]
    end = off + len(chunk)
    more = total - end
    header = f"[chars {off}:{end} of {total}"
    if more > 0:
        header += f" — {more} remaining; next offset={end}"
    else:
        header += " — end of content"
    header += f"; window={window}]\n\n"
    return header + chunk


async def _fetch_with_playwright(url: str) -> str | None:
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
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=_BROWSER_UA,
                    locale="en-US",
                    timezone_id="America/New_York",
                )
                await context.add_init_script(
                    """
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    window.chrome = { runtime: {} };
                    """
                )
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)
                html = await page.content()
                text = _extract_text(html)
                if not text or len(text) < MIN_USEFUL_CHARS:
                    body = await page.inner_text("body")
                    if body and body.strip():
                        text = body.strip()
                if text and text.strip():
                    logger.info(
                        "[read_url] Playwright stealth fetch succeeded for %s (%d chars)",
                        url,
                        len(text),
                    )
                    return text.strip()
                return None
            finally:
                await browser.close()
    except Exception as exc:
        logger.debug("[read_url] Playwright fetch failed: %s", exc)
        return None


def _jina_hard_disabled() -> bool:
    return (os.environ.get("KAZMA_JINA_READER") or "").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def _jina_explicit_opt_in() -> bool:
    return (os.environ.get("KAZMA_JINA_READER") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


async def _try_jina_reader(url: str) -> str | None:
    """Jina Reader proxy (``r.jina.ai``).

    Used when explicitly enabled (``KAZMA_JINA_READER=1``), when
    ``KAZMA_FETCH_BACKEND=jina``, or as a **last-resort recovery** after
    local httpx/Playwright fail (unless hard-disabled with ``=0``).
    """
    if _jina_hard_disabled():
        return None
    try:
        import httpx
        from kazma_core.security.ssrf import SSRFError, validate_url

        validate_url(url)
        # Proxy is public; still SSRF-check the *target* URL above.
        jina_url = f"https://r.jina.ai/{url}"
        token = (os.environ.get("JINA_API_KEY") or os.environ.get("KAZMA_JINA_API_KEY") or "").strip()
        headers = {
            "Accept": "text/plain",
            "User-Agent": _BROWSER_UA,
            "X-Return-Format": "markdown",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            r = await client.get(jina_url, headers=headers)
            if r.status_code == 200 and r.text and len(r.text.strip()) > 50:
                logger.info("[read_url] Jina Reader ok for %s (%d chars)", url, len(r.text))
                return r.text.strip()
            logger.debug("[read_url] Jina status %s for %s", r.status_code, url)
    except Exception as exc:
        logger.debug("[read_url] Jina Reader failed: %s", exc)
    return None


async def _try_firecrawl(url: str) -> str | None:
    """Optional Firecrawl API when ``KAZMA_FIRECRAWL_API_KEY`` is set.

    Retries once on HTTP 429 / 502 / 503 with a short backoff — bulk KB
    crawls of 200+ pages routinely trip Firecrawl rate limits mid-job.
    """
    api_key = (os.environ.get("KAZMA_FIRECRAWL_API_KEY") or "").strip()
    if not api_key:
        return None
    base = (os.environ.get("KAZMA_FIRECRAWL_URL") or "https://api.firecrawl.dev").rstrip("/")
    try:
        import asyncio

        import httpx
        from kazma_core.security.ssrf import SSRFError, validate_url

        validate_url(url)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {"url": url, "formats": ["markdown", "html"]}
        async with httpx.AsyncClient(timeout=60.0) as client:
            last_status = 0
            for attempt in range(3):
                r = await client.post(
                    f"{base}/v1/scrape",
                    headers=headers,
                    json=body,
                )
                last_status = r.status_code
                if r.status_code in (429, 502, 503) and attempt < 2:
                    # Honor Retry-After when present; otherwise exponential backoff.
                    ra = r.headers.get("Retry-After")
                    try:
                        delay = float(ra) if ra else (1.5 * (attempt + 1))
                    except ValueError:
                        delay = 1.5 * (attempt + 1)
                    delay = min(max(delay, 0.5), 20.0)
                    logger.info(
                        "[read_url] Firecrawl %s for %s — retry in %.1fs (attempt %d)",
                        r.status_code, url, delay, attempt + 1,
                    )
                    await asyncio.sleep(delay)
                    continue
                if r.status_code != 200:
                    logger.debug(
                        "[read_url] Firecrawl status %s for %s", r.status_code, url
                    )
                    return None
                data = r.json()
                payload = data.get("data") or data
                text = (
                    payload.get("markdown")
                    or payload.get("content")
                    or payload.get("text")
                    or ""
                )
                if text and len(str(text).strip()) > 50:
                    logger.info(
                        "[read_url] Firecrawl ok for %s (%d chars)",
                        url, len(str(text)),
                    )
                    return str(text).strip()
                logger.debug(
                    "[read_url] Firecrawl empty body for %s (status %s)",
                    url, last_status,
                )
                return None
    except Exception as exc:
        logger.debug("[read_url] Firecrawl failed: %s", exc)
    return None


async def _fetch_via_optional_backends(url: str) -> str | None:
    """Try stronger optional backends before local httpx.

    ``KAZMA_FETCH_BACKEND``: auto | httpx | jina | firecrawl
    - auto: firecrawl (if key) → jina (if explicitly opted in) → None (caller uses httpx)
    - Hard-site recovery after local failure uses :func:`_recover_hard_page`.
    """
    backend = (os.environ.get("KAZMA_FETCH_BACKEND") or "auto").strip().lower()
    if backend == "httpx":
        return None
    if backend == "firecrawl":
        return await _try_firecrawl(url)
    if backend == "jina":
        return await _try_jina_reader(url)
    # auto — only *opt-in* backends before the cheap httpx path
    if (os.environ.get("KAZMA_FIRECRAWL_API_KEY") or "").strip():
        text = await _try_firecrawl(url)
        if text:
            return text
    if _jina_explicit_opt_in():
        text = await _try_jina_reader(url)
        if text:
            return text
    return None


async def _recover_hard_page(url: str, *, why: str = "") -> str | None:
    """Last-resort cascade for bot walls / empty extracts.

    Order: Firecrawl (key) → Jina (unless hard-disabled) → Playwright.
    """
    logger.info("[read_url] Hard-page recovery for %s (%s)", url, why or "unspecified")
    if (os.environ.get("KAZMA_FIRECRAWL_API_KEY") or "").strip():
        text = await _try_firecrawl(url)
        if text:
            return text
    if not _jina_hard_disabled():
        text = await _try_jina_reader(url)
        if text:
            return text
    text = await _fetch_with_playwright(url)
    if text:
        return text
    return None


async def _fetch_full_text(url: str) -> str:
    """Fetch and extract full page text (no window truncate). Errors start with 'Error:'."""
    cached = _cache_get(url)
    if cached is not None:
        return cached

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        from kazma_core.security.ssrf import SSRFError, validate_url

        validate_url(url)
    except SSRFError as exc:
        return f"Error: {exc}"
    except ValueError as exc:
        return f"Error: Invalid URL — {exc}"

    # Optional anti-bot / JS-heavy backends (not invincible; improves hard sites)
    opt = await _fetch_via_optional_backends(url)
    if opt:
        _cache_put(url, opt)
        return opt

    try:
        import httpx
    except ImportError:
        return "Error: httpx package not installed. Run: pip install httpx"

    html = ""
    status_code = 0
    tried_playwright = False
    final_url = url

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=30.0,
            headers=_HTTP_HEADERS,
        ) as client:
            response = await client.get(final_url)
            for _ in range(3):
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                redirect_url = response.headers.get("location", "")
                if not redirect_url:
                    break
                if not redirect_url.startswith(("http://", "https://")):
                    redirect_url = str(httpx.URL(final_url).join(redirect_url))
                try:
                    from kazma_core.security.ssrf import SSRFError, validate_url

                    validate_url(redirect_url)
                except SSRFError as exc:
                    return f"Error: Redirect blocked (SSRF): {exc}"
                except ValueError as exc:
                    return f"Error: Redirect target invalid — {exc}"
                response = await client.get(redirect_url)
                final_url = redirect_url

            raw_status = getattr(response, "status_code", 200)
            status_code = raw_status if isinstance(raw_status, int) else 200
            html = response.text or ""

            if _looks_like_bot_block(html, status_code):
                logger.info(
                    "[read_url] Bot detection on %s (status=%s), hard-page recovery",
                    final_url,
                    status_code,
                )
                tried_playwright = True  # recovery includes Playwright
                recovered = await _recover_hard_page(
                    final_url, why=f"bot_wall status={status_code}"
                )
                if recovered:
                    _cache_put(url, recovered)
                    _cache_put(final_url, recovered)
                    return recovered
                if status_code >= 400:
                    return (
                        f"Error: Server returned HTTP {status_code} for {final_url} "
                        "(bot protection or access denied). "
                        "Recovery tried Firecrawl/Jina/Playwright. "
                        "Set ``KAZMA_FIRECRAWL_API_KEY``, ensure Jina is not disabled "
                        "(``KAZMA_JINA_READER=0``), or install Playwright "
                        "(`pip install 'kazma[web]'` + browsers)."
                    )

            if status_code >= 400 and not _looks_like_bot_block(html, status_code):
                try:
                    response.raise_for_status()
                except Exception as exc:
                    # Last chance: paid/proxy backends may still reach the page
                    recovered = await _recover_hard_page(
                        final_url, why=f"http_{status_code}"
                    )
                    if recovered:
                        _cache_put(url, recovered)
                        _cache_put(final_url, recovered)
                        return recovered
                    return _friendly_error(exc, final_url)

    except ConnectionError:
        recovered = await _recover_hard_page(final_url, why="connection_error")
        if recovered:
            _cache_put(url, recovered)
            _cache_put(final_url, recovered)
            return recovered
        return _friendly_error(ConnectionError(), final_url)
    except TimeoutError:
        recovered = await _recover_hard_page(final_url, why="timeout")
        if recovered:
            _cache_put(url, recovered)
            _cache_put(final_url, recovered)
            return recovered
        return _friendly_error(TimeoutError(), final_url)
    except OSError as exc:
        return _friendly_error(exc, final_url)
    except Exception as exc:
        return _friendly_error(exc, final_url)

    text = _extract_text(html)

    # Escalate for true bot walls / empty SPA shells — not short static pages.
    if not tried_playwright and _should_try_playwright(html, status_code, extracted=text):
        logger.info(
            "[read_url] Thin/empty extract or JS shell for %s (%d chars text / %d html)",
            final_url,
            len(text),
            len(html),
        )
        recovered = await _recover_hard_page(
            final_url, why=f"thin_extract text={len(text)} html={len(html)}"
        )
        if recovered and len(recovered) > len(text):
            _cache_put(url, recovered)
            _cache_put(final_url, recovered)
            return recovered

    if not text:
        if not tried_playwright:
            recovered = await _recover_hard_page(final_url, why="empty_extract")
            if recovered:
                _cache_put(url, recovered)
                _cache_put(final_url, recovered)
                return recovered
        return (
            f"Error: Could not extract readable content from {final_url}. "
            "The page may be empty, require JavaScript, or block automated access. "
            "Tried hard-page recovery (Firecrawl/Jina/Playwright). "
            "Set ``KAZMA_FIRECRAWL_API_KEY``, leave Jina enabled, "
            "or `pip install 'kazma[web]'` + `playwright install chromium`."
        )

    _cache_put(url, text)
    _cache_put(final_url, text)
    return text


async def read_url(
    url: str,
    offset: int = 0,
    max_chars: int | None = None,
) -> str:
    """Fetch a URL and return a **window** of readable text.

    Args:
        url: Public http(s) URL.
        offset: Character offset into the full extract (for long-page paging).
        max_chars: Window size (default ``KAZMA_READ_URL_MAX_CHARS``, usually 16000).
            Absolute max per window: 100000.

    Returns:
        Text window with a header like ``[chars 0:16000 of 52000 — N remaining]``,
        or an ``Error:`` message.

    For full-page research, use ``read_url_to_file`` then chunk tools.
    """
    if not url or not str(url).strip():
        return "Error: No URL provided."

    url = str(url).strip()
    full = await _fetch_full_text(url)
    if full.startswith("Error:"):
        return full
    return _slice_window(full, offset=int(offset or 0), max_chars=max_chars)


def _workspace_root() -> Path:
    try:
        from kazma_core.tools.file_write import _get_workspace

        return _get_workspace().resolve()
    except Exception:
        return (Path.cwd() / "kazma-data" / "workspace").resolve()


def _default_research_subdir() -> str:
    """Default relative folder for auto-named saves (``KAZMA_RESEARCH_DIR``)."""
    raw = (os.environ.get("KAZMA_RESEARCH_DIR") or "research").strip().replace("\\", "/")
    raw = raw.lstrip("/")
    if not raw or ".." in raw.split("/"):
        return "research"
    return raw


def _safe_workspace_path(rel_or_name: str | None, url: str) -> Path | str:
    """Resolve a save path **anywhere under the workspace** (not research-only).

    * Explicit ``path`` → workspace-relative (or under research/ if bare name).
    * Omitted → ``{KAZMA_RESEARCH_DIR|research}/<slug>-<hash>.md``
    """
    root = _workspace_root().resolve()
    default_dir = root / _default_research_subdir()
    default_dir.mkdir(parents=True, exist_ok=True)

    if rel_or_name and str(rel_or_name).strip():
        name = str(rel_or_name).strip().replace("\\", "/").lstrip("/")
        if ".." in name.split("/"):
            return "Error: path traversal not allowed."
        # Bare filename → research subdir; paths with / stay workspace-relative
        if "/" not in name and "\\" not in str(rel_or_name):
            target = (default_dir / name).resolve()
        else:
            target = (root / name).resolve()
    else:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", url)[:80].strip("-") or "page"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        name = f"{slug}-{digest}.md"
        target = (default_dir / name).resolve()

    try:
        target.relative_to(root)
    except ValueError:
        return "Error: path must stay inside the active workspace."
    return target


# Back-compat alias
_safe_research_path = _safe_workspace_path


async def read_url_to_file(url: str, path: str | None = None) -> str:
    """Fetch a public URL and save the **full extract** inside the workspace.

    Args:
        url: Public URL.
        path: Optional path **relative to workspace** (any subfolder).
            Bare filenames go under ``KAZMA_RESEARCH_DIR`` (default ``research/``).
            Auto-generated when omitted.

    Returns:
        Success message with path, char count, and suggested next tools.
    """
    if not url or not str(url).strip():
        return "Error: No URL provided."

    url = str(url).strip()
    full = await _fetch_full_text(url)
    if full.startswith("Error:"):
        return full

    target = _safe_workspace_path(path, url)
    if isinstance(target, str):
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Source: {url}\n"
        f"# Saved: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        f"# Chars: {len(full)}\n\n"
    )
    target.write_text(header + full, encoding="utf-8")
    rel = target.name
    try:
        rel = str(target.relative_to(_workspace_root()))
    except ValueError:
        pass

    n_chunks = max(1, (len(full) + DEFAULT_CHUNK_SIZE - 1) // DEFAULT_CHUNK_SIZE)
    return (
        f"Saved full extract ({len(full)} chars) → `{rel}`\n"
        f"Suggested next steps:\n"
        f"- list_research_chunks(path='{rel}')  # ~{n_chunks} chunks @ {DEFAULT_CHUNK_SIZE} chars\n"
        f"- read_research_chunk(path='{rel}', chunk_index=0)\n"
        f"- digest_research_file(path='{rel}')  # full multi-chunk digest (context-safe)\n"
        f"- summarize_research_file(path='{rel}')  # lighter outline\n"
        f"- Or page: read_url(url, offset=0) then offset=N"
    )


def _load_research_body(path: str) -> tuple[str, Path] | str:
    """Load file body (strip our header lines). Returns (body, path) or error."""
    root = _workspace_root()
    raw = str(path).strip().replace("\\", "/")
    p = Path(raw)
    if not p.is_absolute():
        p = (root / raw).resolve()
    else:
        p = p.resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        return "Error: path must be inside the active workspace."
    if not p.is_file():
        return f"Error: file not found: {path}"

    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("# Source:") or line.startswith("# Saved:") or line.startswith("# Chars:"):
            body_start = i + 1
            continue
        if body_start and line.strip() == "":
            body_start = i + 1
            break
        break
    body = "\n".join(lines[body_start:]).lstrip("\n")
    return body, p


async def list_research_chunks(
    path: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> str:
    """List chunk index + previews for a saved research file.

    Args:
        path: Workspace-relative path (usually under research/).
        chunk_size: Characters per chunk (default 4000).
    """
    loaded = _load_research_body(path)
    if isinstance(loaded, str):
        return loaded
    body, _p = loaded
    try:
        size = max(500, min(_HARD_MAX, int(chunk_size or DEFAULT_CHUNK_SIZE)))
    except (TypeError, ValueError):
        size = DEFAULT_CHUNK_SIZE

    total = len(body)
    if total == 0:
        return "Error: file is empty."

    n = max(1, (total + size - 1) // size)
    lines = [
        f"# Research chunks for `{path}`",
        f"Total chars: {total} · chunk_size: {size} · chunks: {n}",
        "",
    ]
    for i in range(n):
        start = i * size
        end = min(total, start + size)
        preview = body[start : start + 120].replace("\n", " ")
        lines.append(f"## chunk_index={i}  [{start}:{end}]")
        lines.append(f"  {preview}…")
        lines.append("")
    lines.append(
        f"Read one chunk: read_research_chunk(path='{path}', chunk_index=0, chunk_size={size})"
    )
    return "\n".join(lines)


async def read_research_chunk(
    path: str,
    chunk_index: int = 0,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> str:
    """Return one chunk of a saved research file.

    Args:
        path: Workspace-relative research file.
        chunk_index: 0-based chunk index.
        chunk_size: Characters per chunk.
    """
    loaded = _load_research_body(path)
    if isinstance(loaded, str):
        return loaded
    body, _p = loaded
    try:
        size = max(500, min(_HARD_MAX, int(chunk_size or DEFAULT_CHUNK_SIZE)))
        idx = max(0, int(chunk_index or 0))
    except (TypeError, ValueError):
        return "Error: invalid chunk_index or chunk_size."

    total = len(body)
    n = max(1, (total + size - 1) // size)
    if idx >= n:
        return f"Error: chunk_index {idx} out of range (0..{n - 1})."

    start = idx * size
    end = min(total, start + size)
    piece = body[start:end]
    next_hint = f" next: chunk_index={idx + 1}" if idx + 1 < n else " (last chunk)"
    return (
        f"[chunk {idx}/{n - 1} chars {start}:{end} of {total}{next_hint}]\n\n"
        f"{piece}"
    )


async def summarize_research_file(
    path: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_chunks: int = 40,
) -> str:
    """Extractive outline of a research file (no LLM call).

    For each chunk: first heading-like line (if any) + first ~200 chars.
    Use this to plan which chunks to ``read_research_chunk`` in full.
    """
    loaded = _load_research_body(path)
    if isinstance(loaded, str):
        return loaded
    body, _p = loaded
    try:
        size = max(500, min(_HARD_MAX, int(chunk_size or DEFAULT_CHUNK_SIZE)))
        limit = max(1, min(200, int(max_chunks or 40)))
    except (TypeError, ValueError):
        size, limit = DEFAULT_CHUNK_SIZE, 40

    total = len(body)
    n = max(1, (total + size - 1) // size)
    lines = [
        f"# Extractive summary of `{path}`",
        f"Total chars: {total} · chunks: {n} (showing up to {limit})",
        "",
    ]
    for i in range(min(n, limit)):
        start = i * size
        end = min(total, start + size)
        piece = body[start:end]
        heading = None
        for line in piece.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#") or (len(s) < 80 and s.endswith(":")):
                heading = s.lstrip("#").strip()
            break
        preview = re.sub(r"\s+", " ", piece)[:200]
        title = heading or f"Chunk {i}"
        lines.append(f"## [{i}] {title}")
        lines.append(f"{preview}…")
        lines.append("")
    if n > limit:
        lines.append(f"… {n - limit} more chunks not shown.")
    lines.append(
        "Next: read_research_chunk for important indices, or digest_research_file for a full digest."
    )
    return "\n".join(lines)


async def digest_research_file(
    path: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_output_chars: int = 12_000,
) -> str:
    """Process **all** chunks in-tool and return one bounded digest.

    Avoids loading the whole file into the model context: walks every chunk
    internally, keeps headings + densest sentences, hard-caps output size
    (default 12k; env ``KAZMA_RESEARCH_DIGEST_MAX``).

    This is extractive (no nested LLM). For deeper synthesis the agent still
    reasons over the digest + selective ``read_research_chunk`` calls.
    """
    loaded = _load_research_body(path)
    if isinstance(loaded, str):
        return loaded
    body, _p = loaded
    try:
        size = max(500, min(_HARD_MAX, int(chunk_size or DEFAULT_CHUNK_SIZE)))
        out_cap = _env_int(
            "KAZMA_RESEARCH_DIGEST_MAX",
            int(max_output_chars or 12_000),
            lo=1000,
            hi=50_000,
        )
    except (TypeError, ValueError):
        size, out_cap = DEFAULT_CHUNK_SIZE, 12_000

    total = len(body)
    if total == 0:
        return "Error: file is empty."

    n = max(1, (total + size - 1) // size)
    parts: list[str] = [
        f"# Research digest of `{path}`",
        f"Source chars: {total} · chunks processed: {n} · output cap: {out_cap}",
        "",
        "## Key points (extractive)",
        "",
    ]

    # Budget per chunk for the digest body
    per = max(80, min(400, out_cap // max(n, 1)))
    used = sum(len(p) for p in parts)

    for i in range(n):
        if used >= out_cap - 200:
            parts.append(f"\n… digest truncated after chunk {i - 1}/{n - 1} (output cap).")
            break
        start = i * size
        end = min(total, start + size)
        piece = body[start:end]
        heading = None
        for line in piece.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#") or (len(s) < 100 and not s.endswith(".")):
                heading = s.lstrip("#").strip()[:120]
            break
        # Prefer sentences; fall back to prefix
        sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", piece).strip())
        take: list[str] = []
        budget = per
        if heading:
            take.append(heading)
            budget -= len(heading)
        for sent in sentences:
            if len(sent) < 20:
                continue
            if budget <= 0:
                break
            snippet = sent[: min(len(sent), budget)]
            take.append(snippet)
            budget -= len(snippet) + 1
        if not take:
            take.append(piece[:per].replace("\n", " "))
        block = f"- **[{i}]** " + " ".join(take)
        if len(block) > per + 80:
            block = block[: per + 80] + "…"
        parts.append(block)
        used += len(block) + 1

    parts.append("")
    parts.append(
        "Digest is extractive only. For quotes/details: "
        f"read_research_chunk(path='{path}', chunk_index=N)."
    )
    out = "\n".join(parts)
    if len(out) > out_cap:
        out = out[:out_cap] + "\n[digest hard-capped]"
    return out
