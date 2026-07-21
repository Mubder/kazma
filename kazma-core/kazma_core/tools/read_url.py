"""Read URL tool — Fetch and extract readable content from a URL.

Tiered fetch (public web, not anti-bot invincible):

    1. httpx fast path with a **browser-like** User-Agent + Accept headers
       + trafilatura extraction (or crude HTML strip).
    2. Playwright stealth headless Chromium when:
       - bot / challenge markers (Cloudflare, CAPTCHA, 403/429/503, …), or
       - extraction is empty / thin while the HTML shell looks JS-heavy.
    3. If Playwright is missing or fails → return best httpx result or error.

Caps output at 8000 characters. SSRF-safe (validate URL + redirects).

Usage:
    from kazma_core.tools.read_url import read_url
    content = await read_url("https://example.com")
"""

from __future__ import annotations

import logging
import re

__all__ = [
    "MAX_CONTENT_CHARS",
    "MIN_USEFUL_CHARS",
    "read_url",
]

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 8000
# Below this after extraction we consider the page "thin" and may upgrade to browser.
MIN_USEFUL_CHARS = 200

# Real desktop Chrome — avoid "KazmaBot" fingerprint on the fast path.
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

# Markers that indicate a page is blocking us with bot detection
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

# SPA / JS shell signals when extraction is thin
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


def _friendly_error(exc: Exception, url: str = "") -> str:
    """Map low-level exceptions to user-friendly messages."""
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
    """Check if the response is a bot-detection challenge page."""
    if status_code in (403, 429, 503):
        html_lower = html[:5000].lower()
        return any(marker in html_lower for marker in _BOT_DETECTION_MARKERS)
    # Also check 200 pages that are actually challenge pages
    if len(html) < 3000:
        html_lower = html.lower()
        if any(
            marker in html_lower
            for marker in (
                "cloudflare",
                "checking your browser",
                "cf-challenge",
                "just a moment",
                "please verify you are a human",
            )
        ):
            return True
    return False


def _looks_like_js_shell(html: str) -> bool:
    """True if HTML looks like a client-rendered SPA shell."""
    sample = html[:12000].lower()
    return any(m in sample for m in _JS_SHELL_MARKERS)


def _extract_text(html: str) -> str:
    """Extract readable text from HTML via trafilatura or tag strip."""
    try:
        import trafilatura

        text = trafilatura.extract(
            html,
            include_links=True,
            include_tables=True,
            favor_recall=True,
        )
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
    except Exception:
        text = html[:MAX_CONTENT_CHARS]

    return (text or "").strip()


def _is_thin_extraction(text: str, html: str) -> bool:
    """True when extraction is empty/short relative to a substantial HTML body."""
    if not text or len(text) < MIN_USEFUL_CHARS:
        # Only upgrade when there was real HTML to work with
        if len(html) >= 500 or _looks_like_js_shell(html):
            return True
        if not text:
            return True
    return False


def _should_try_playwright(
    html: str,
    status_code: int,
    extracted: str | None = None,
) -> bool:
    """Decide whether to spend a headless browser fetch."""
    if _looks_like_bot_block(html, status_code):
        return True
    if extracted is not None and _is_thin_extraction(extracted, html):
        return True
    return False


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_CONTENT_CHARS:
        return (
            text[:MAX_CONTENT_CHARS]
            + f"\n\n[truncated — {len(text)} chars total, showing first {MAX_CONTENT_CHARS}]"
        )
    return text


async def _fetch_with_playwright(url: str) -> str | None:
    """Fetch URL using Playwright stealth headless browser.

    Returns extracted text content, or None if Playwright is not installed
    or the fetch failed. The caller falls back to httpx result in that case.
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

                # Prefer structured extraction from full HTML when possible
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


async def read_url(url: str) -> str:
    """Fetch a URL and extract readable text content.

    Public pages only (SSRF-guarded). Uses httpx + extraction, then optional
    Playwright for bot walls / thin SPA shells. Not an anti-bot guarantee.

    Args:
        url: The URL to fetch.

    Returns:
        Extracted text content (max 8000 chars), or a friendly error message.
    """
    if not url or not url.strip():
        return "Error: No URL provided."

    url = url.strip()

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        from kazma_core.security.ssrf import SSRFError, validate_url

        validate_url(url)
    except SSRFError as exc:
        return f"Error: {exc}"
    except ValueError as exc:
        return f"Error: Invalid URL — {exc}"

    try:
        import httpx
    except ImportError:
        return "Error: httpx package not installed. Run: pip install httpx"

    html = ""
    status_code = 0
    tried_playwright = False

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=30.0,
            headers=_HTTP_HEADERS,
        ) as client:
            response = await client.get(url)
            # Manually handle up to 3 redirects, re-validating each target
            for _ in range(3):
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                redirect_url = response.headers.get("location", "")
                if not redirect_url:
                    break
                if not redirect_url.startswith(("http://", "https://")):
                    redirect_url = str(httpx.URL(url).join(redirect_url))
                try:
                    from kazma_core.security.ssrf import SSRFError, validate_url

                    validate_url(redirect_url)
                except SSRFError as exc:
                    return f"Error: Redirect blocked (SSRF): {exc}"
                except ValueError as exc:
                    return f"Error: Redirect target invalid — {exc}"
                response = await client.get(redirect_url)
                url = redirect_url

            raw_status = getattr(response, "status_code", 200)
            # Tests may mock response without a real status_code
            status_code = raw_status if isinstance(raw_status, int) else 200
            # Soft-fail status: still read body for challenge pages, then maybe Playwright
            html = response.text or ""

            if _looks_like_bot_block(html, status_code):
                logger.info(
                    "[read_url] Bot detection on %s (status=%s), trying Playwright",
                    url,
                    status_code,
                )
                tried_playwright = True
                pw_text = await _fetch_with_playwright(url)
                if pw_text:
                    return _truncate(pw_text)
                if status_code >= 400:
                    return (
                        f"Error: Server returned HTTP {status_code} for {url} "
                        "(bot protection or access denied). "
                        "Install Playwright (`pip install 'kazma[web]'` + browsers) "
                        "or try a different URL."
                    )

            # Hard HTTP errors without a challenge page
            if status_code >= 400 and not _looks_like_bot_block(html, status_code):
                try:
                    response.raise_for_status()
                except Exception as exc:
                    return _friendly_error(exc, url)

    except ConnectionError:
        return _friendly_error(ConnectionError(), url)
    except TimeoutError:
        return _friendly_error(TimeoutError(), url)
    except OSError as exc:
        return _friendly_error(exc, url)
    except Exception as exc:
        return _friendly_error(exc, url)

    text = _extract_text(html)

    if not tried_playwright and _should_try_playwright(html, status_code, extracted=text):
        logger.info(
            "[read_url] Thin/empty extract or JS shell for %s (%d chars text / %d html), trying Playwright",
            url,
            len(text),
            len(html),
        )
        pw_text = await _fetch_with_playwright(url)
        if pw_text:
            return _truncate(pw_text)

    if not text:
        return (
            f"Error: Could not extract readable content from {url}. "
            "The page may be empty, require JavaScript, or block automated access. "
            "Optional: `pip install 'kazma[web]'` and `playwright install chromium`."
        )

    return _truncate(text)
