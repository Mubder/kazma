"""Read URL tool — Fetch and extract readable content from a URL.

Uses httpx for fetching and trafilatura for content extraction.
Caps output at 8000 characters with friendly error messages.

Usage:
    from kazma_core.tools.read_url import read_url
    content = await read_url("https://example.com")
"""

from __future__ import annotations

MAX_CONTENT_CHARS = 8000


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

    try:
        import httpx
    except ImportError:
        return "Error: httpx package not installed. Run: pip install httpx"

    # Fetch
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": "KazmaBot/1.0 (web reader)"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
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
