"""Read URL tool — Fetch and extract readable content from a URL.

Uses Playwright stealth (if installed) for JS-heavy / bot-protected pages,
falls back to httpx + trafilatura for lightweight fetching.
Caps output at 8000 characters with friendly error messages.

Resolution order:
    1. httpx fast fetch — if it works, done (fast path)
    2. If bot-detection detected (Cloudflare, 403/503, "enable JS") →
       retry with Playwright stealth headless browser
    3. If Playwright not installed → return the httpx result or error

Usage:
    from kazma_core.tools.read_url import read_url
    content = await read_url("https://example.com")
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 8000

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
)


def _friendly_error(exc: Exception, url: str = "") -> str:
    """Map low-level exceptions to user-friendly messages."""
    if isinstance(exc, ConnectionError):
        return f"Error: Could not connect to {url}. Check the URL and your internet connection."
    if isinstance(exc, TimeoutError):
        return f"Error: Request to {url} timed out. The server may be slow or unreachable."
    if isinstance(exc, OSError):
        return f"Error: Network error while fetching {url} — {exc}"

    # httpx-specific errors
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
        if any(marker in html_lower for marker in ("cloudflare", "checking your browser", "cf-challenge", "just a moment")):
            return True
    return False


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
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="America/New_York",
            )

            # Stealth: hide webdriver flag
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                window.chrome = { runtime: {} };
            """)

            page = await context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # Wait a bit for any challenge to resolve
            await page.wait_for_timeout(2000)

            # Extract readable text
            text = await page.inner_text("body")
            await browser.close()

            if text and text.strip():
                logger.info("[read_url] Playwright stealth fetch succeeded for %s (%d chars)", url, len(text))
                return text.strip()
            return None

    except Exception as exc:
        logger.debug("[read_url] Playwright fetch failed: %s", exc)
        return None


async def read_url(url: str) -> str:
    """Fetch a URL and extract readable text content.

    Args:
        url: The URL to fetch.

    Returns:
        Extracted text content (max 8000 chars), or a friendly error message.
    """
    if not url or not url.strip():
        return "Error: No URL provided."

    url = url.strip()

    # Basic scheme check
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # ── SSRF protection: reject private/internal/metadata endpoints ──
    # Resolve the hostname and block if any resolved IP is private,
    # loopback, link-local (incl. cloud metadata 169.254.169.254), or
    # reserved. This runs before any network fetch so we never connect.
    try:
        from kazma_core.security.ssrf import SSRFError, validate_url

        validate_url(url)
    except SSRFError as exc:
        return f"Error: {exc}"
    except ValueError as exc:
        return f"Error: Invalid URL — {exc}"

    # Fetch — disable follow_redirects to prevent SSRF redirect bypass.
    # A public URL could 302-redirect to http://169.254.169.254/ (cloud
    # metadata). We validate the URL above, but following redirects would
    # bypass that check. Instead, we manually handle redirects and
    # re-validate each Location header.
    try:
        import httpx
    except ImportError:
        return "Error: httpx package not installed. Run: pip install httpx"

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=30.0,
            headers={"User-Agent": "KazmaBot/1.0 (web reader)"},
        ) as client:
            response = await client.get(url)
            # Manually handle up to 3 redirects, re-validating each target
            for _ in range(3):
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                redirect_url = response.headers.get("location", "")
                if not redirect_url:
                    break
                # Resolve relative redirects
                if not redirect_url.startswith(("http://", "https://")):
                    redirect_url = str(httpx.URL(url).join(redirect_url))
                # Re-validate the redirect target for SSRF
                try:
                    from kazma_core.security.ssrf import SSRFError, validate_url
                    validate_url(redirect_url)
                except SSRFError as exc:
                    return f"Error: Redirect blocked (SSRF): {exc}"
                except ValueError as exc:
                    return f"Error: Redirect target invalid — {exc}"
                response = await client.get(redirect_url)
                url = redirect_url
            response.raise_for_status()
            html = response.text

            # ── Bot-detection retry ──────────────────────────────────
            # If the response looks like a Cloudflare/bot-challenge page,
            # retry with Playwright stealth (if installed).
            if _looks_like_bot_block(html, response.status_code):
                logger.info("[read_url] Bot detection detected on %s, trying Playwright stealth", url)
                pw_text = await _fetch_with_playwright(url)
                if pw_text:
                    if len(pw_text) > MAX_CONTENT_CHARS:
                        pw_text = pw_text[:MAX_CONTENT_CHARS] + f"\n\n[truncated — showing first {MAX_CONTENT_CHARS}]"
                    return pw_text
                logger.warning("[read_url] Playwright not available or failed, returning raw response")
    except ConnectionError:
        return _friendly_error(ConnectionError(), url)
    except TimeoutError:
        return _friendly_error(TimeoutError(), url)
    except OSError as exc:
        return _friendly_error(exc, url)
    except Exception as exc:
        return _friendly_error(exc, url)

    # Extract readable content
    try:
        import trafilatura

        text = trafilatura.extract(
            html,
            include_links=True,
            include_tables=True,
            favor_recall=True,
        )
    except ImportError:
        # Fallback: strip tags crudely
        import re

        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
    except Exception:
        text = html[:MAX_CONTENT_CHARS]

    if not text or not text.strip():
        return f"Error: Could not extract readable content from {url}. The page may be empty or require JavaScript."

    # Truncate
    text = text.strip()
    if len(text) > MAX_CONTENT_CHARS:
        text = (
            text[:MAX_CONTENT_CHARS] + f"\n\n[truncated — {len(text)} chars total, showing first {MAX_CONTENT_CHARS}]"
        )

    return text
