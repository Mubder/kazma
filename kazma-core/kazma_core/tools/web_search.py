"""Web search tool — multi-backend search returning markdown.

Resolution order (each step continues on **empty results**, not only errors):
    1. SearXNG — multi-base discovery (``KAZMA_SEARXNG_URL``, ConfigStore
       ``search.searxng_url``, localhost:8088/8080, Docker service names)
    2. DuckDuckGo (``duckduckgo_search`` / DDGS)
    3. Bing HTML scrape
    4. Wikipedia OpenSearch (last-resort for brand/entity queries)

Not a paid search API (Tavily/Brave/Serper). For production reliability, run
SearXNG: ``docker compose --profile search up -d searxng`` and set
``KAZMA_SEARXNG_URL=http://127.0.0.1:8088``.

Usage:
    from kazma_core.tools.web_search import web_search
    results = await web_search("kazma agent framework")
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from typing import Any

__all__ = ["web_search"]

# Silence the noisy duckduckgo_search / ddgs deprecation warnings WITHOUT
# replacing the global warnings.warn. The previous module-level monkeypatch
# rewrote warnings.warn process-wide for EVERY library (masking unrelated
# warnings whose text happened to contain "ddgs", and resetting caller
# stacklevel) — audit finding. filterwarnings only affects matching warnings
# and leaves the global machinery intact.
warnings.filterwarnings(
    "ignore",
    message=r".*(duckduckgo_search|ddgs).*",
)

logger = logging.getLogger(__name__)


def _friendly_error(exc: Exception) -> str:
    """Map low-level exceptions to user-friendly messages."""
    if isinstance(exc, ConnectionError):
        return (
            "Error: Could not connect to any search service. "
            "Check internet connectivity, or set ``KAZMA_SEARXNG_URL`` to a local SearXNG."
        )
    if isinstance(exc, TimeoutError):
        return "Error: Search request timed out. Please try again or rephrase the query."
    if isinstance(exc, OSError):
        logger.debug("[web_search] Network error: %s", exc, exc_info=True)
        return "Error: Network error during search. Check connection or try ``read_url`` on a known URL."
    logger.debug("[web_search] Search failed: %s", exc, exc_info=True)
    return f"Error: Search failed ({type(exc).__name__}). Try rephrasing or use ``read_url``."


# Cache which SearXNG base URL worked (or that none did) for a short TTL
# so multi-query turns don't hammer dead localhost:8088 every time.
_searxng_cache: dict[str, Any] = {"base": None, "dead_until": 0.0, "note": ""}


def _searxng_candidate_bases() -> list[str]:
    """Ordered list of SearXNG base URLs to try."""
    import os

    bases: list[str] = []
    explicit = (os.environ.get("KAZMA_SEARXNG_URL") or "").strip()
    if explicit:
        bases.append(explicit.rstrip("/"))
    # ConfigStore override (settings UI / ops)
    try:
        from kazma_core.config_store import get_config_store

        cfg = get_config_store().get("search.searxng_url")
        if cfg and str(cfg).strip():
            bases.append(str(cfg).strip().rstrip("/"))
    except Exception:
        pass
    # Common local / Docker locations
    for b in (
        "http://127.0.0.1:8088",
        "http://localhost:8088",
        "http://host.docker.internal:8088",
        "http://searxng:8080",
        "http://127.0.0.1:8080",
    ):
        if b not in bases:
            bases.append(b)
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for b in bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _is_loopback_base(base: str) -> bool:
    """True for local SearXNG — must not hairpin through residential proxy."""
    b = (base or "").lower()
    return any(
        x in b
        for x in (
            "127.0.0.1",
            "localhost",
            "0.0.0.0",
            "[::1]",
            "host.docker.internal",
            "searxng:",  # docker service name on internal network
        )
    )


def _searxng_search(query: str, max_results: int) -> tuple[list[dict[str, str]] | None, str]:
    """Try SearXNG across candidate bases. Returns (results|None, status_note)."""
    import time as _time

    import httpx

    now = _time.time()
    if _searxng_cache.get("dead_until", 0) > now and not _searxng_cache.get("base"):
        return None, _searxng_cache.get("note") or "searxng:skipped (recently unavailable)"

    candidates = _searxng_candidate_bases()
    # Prefer last known good base first
    good = _searxng_cache.get("base")
    if good and good in candidates:
        candidates = [good] + [c for c in candidates if c != good]

    last_note = "searxng:unavailable"
    for base in candidates:
        try:
            search_url = f"{base}/search"
            params = {
                "q": query,
                "format": "json",
                "categories": "general",
                "language": "all",
                "pageno": 1,
            }
            headers = {"Accept": "application/json", "User-Agent": "KazmaSearch/0.6"}
            # Remote SearXNG can use Proxy Provider; loopback stays direct.
            if _is_loopback_base(base):
                r = httpx.get(
                    search_url, params=params, timeout=8.0, headers=headers
                )
            else:
                from kazma_core.proxy.client import get_scraping_client_sync

                with get_scraping_client_sync(
                    follow_redirects=True, timeout=8.0, headers=headers
                ) as client:
                    r = client.get(search_url, params=params)
            if r.status_code != 200:
                last_note = f"searxng:http_{r.status_code}@{base}"
                continue
            data = r.json()
            raw_results = data.get("results", [])
            if not raw_results:
                last_note = f"searxng:empty@{base}"
                # empty is still a live instance — keep base cached
                _searxng_cache["base"] = base
                _searxng_cache["dead_until"] = 0.0
                continue

            normalized = []
            for res in raw_results[:max_results]:
                normalized.append(
                    {
                        "title": res.get("title", "Untitled"),
                        "href": res.get("url", ""),
                        "body": res.get("content", ""),
                    }
                )
            _searxng_cache["base"] = base
            _searxng_cache["dead_until"] = 0.0
            _searxng_cache["note"] = f"searxng:ok@{base}"
            logger.info(
                "[web_search] SearXNG %s returned %d results", base, len(normalized)
            )
            return normalized, f"searxng:ok@{base}"
        except Exception as exc:
            last_note = f"searxng:unavailable@{base} ({type(exc).__name__})"
            logger.debug("[web_search] SearXNG %s: %s", base, exc)
            continue

    # All candidates failed — cool down 60s
    _searxng_cache["base"] = None
    _searxng_cache["dead_until"] = now + 60.0
    _searxng_cache["note"] = last_note
    return None, last_note


def _ddg_search(query: str, max_results: int) -> tuple[list[dict[str, str]] | None, str]:
    """DuckDuckGo text search (Proxy Provider when configured)."""
    try:
        from duckduckgo_search import DDGS

        proxy_url = None
        try:
            from kazma_core.proxy.client import get_active_proxy_url

            proxy_url = get_active_proxy_url()
        except Exception:
            proxy_url = None
        # DDGS accepts http/https/socks5 proxy URL
        kwargs: dict = {}
        if proxy_url:
            kwargs["proxy"] = proxy_url
        with DDGS(**kwargs) as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            if results:
                logger.info(
                    "[web_search] DuckDuckGo returned %d results%s",
                    len(results),
                    " (via proxy)" if proxy_url else "",
                )
                return results, "duckduckgo:ok"
            logger.info("[web_search] DuckDuckGo returned 0 results for %r", query)
            return None, "duckduckgo:empty"
    except Exception as exc:
        logger.warning("[web_search] DuckDuckGo failed: %s", exc)
        return None, f"duckduckgo:error ({type(exc).__name__})"


def _bing_search(query: str, max_results: int) -> tuple[list[dict[str, str]] | None, str]:
    """Bing HTML scrape fallback (via Proxy Provider when configured)."""
    try:
        import base64
        import urllib.parse
        from html.parser import HTMLParser

        from kazma_core.proxy.client import get_scraping_client_sync

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        url = f"https://www.bing.com/search?q={urllib.parse.quote_plus(query)}"
        with get_scraping_client_sync(
            follow_redirects=True,
            timeout=10.0,
            headers=headers,
            rotate_ua=True,
        ) as client:
            r = client.get(url)
        if r.status_code != 200:
            logger.warning("[web_search] Bing status %d", r.status_code)
            return None, f"bing:http_{r.status_code}"

        def decode_bing_url(b_url: str) -> str:
            if not b_url.startswith("http"):
                return b_url
            parsed_url = urllib.parse.urlparse(b_url)
            q_params = urllib.parse.parse_qs(parsed_url.query)
            u_param = q_params.get("u", [None])[0]
            if u_param:
                b64_str = u_param[2:] if u_param.startswith("a1") else u_param
                padding = "=" * (4 - len(b64_str) % 4)
                try:
                    return base64.b64decode(b64_str + padding).decode(
                        "utf-8", errors="ignore"
                    )
                except Exception:
                    pass
            return b_url

        class BingHTMLParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.results: list[dict[str, str]] = []
                self.current_result: dict[str, str] | None = None
                self.in_algo = False
                self.in_h2 = False
                self.in_snippet = False
                self.in_a = False
                self.depth_algo = 0
                self.current_text: list[str] = []

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                class_val = attrs_dict.get("class", "")
                if self.in_algo:
                    self.depth_algo += 1
                if tag == "li" and "b_algo" in class_val.split():
                    self.in_algo = True
                    self.depth_algo = 0
                    self.current_result = {"title": "", "href": "", "body": ""}
                    self.results.append(self.current_result)
                if self.in_algo:
                    if tag == "h2":
                        self.in_h2 = True
                        self.current_text = []
                    elif tag == "a" and self.in_h2:
                        self.in_a = True
                        if "href" in attrs_dict and self.current_result is not None:
                            self.current_result["href"] = attrs_dict["href"]
                    elif tag in ("p", "div", "span") and any(
                        x in class_val for x in ("b_caption", "b_linelimit", "tab-content")
                    ):
                        self.in_snippet = True
                        self.current_text = []

            def handle_data(self, data):
                if self.in_algo and (self.in_h2 or self.in_snippet):
                    self.current_text.append(data)

            def handle_endtag(self, tag):
                if not self.in_algo:
                    return
                if tag == "a" and self.in_a:
                    self.in_a = False
                elif tag == "h2" and self.in_h2:
                    self.in_h2 = False
                    if self.current_result is not None:
                        self.current_result["title"] = "".join(self.current_text).strip()
                    self.current_text = []
                elif tag in ("p", "div", "span") and self.in_snippet:
                    self.in_snippet = False
                    if self.current_result is not None:
                        existing = self.current_result["body"]
                        new_text = "".join(self.current_text).strip()
                        if new_text:
                            self.current_result["body"] = (
                                (existing + " " + new_text).strip() if existing else new_text
                            )
                    self.current_text = []
                if tag == "li" and self.depth_algo == 0:
                    self.in_algo = False
                    self.current_result = None
                elif tag == "li":
                    self.depth_algo -= 1

        parser = BingHTMLParser()
        parser.feed(r.text)
        processed: list[dict[str, str]] = []
        for res in parser.results:
            if res.get("title") or res.get("href"):
                processed.append(
                    {
                        "title": res.get("title", "Untitled"),
                        "href": decode_bing_url(res.get("href", "")),
                        "body": res.get("body", ""),
                    }
                )
        if not processed:
            return None, "bing:empty"
        logger.info("[web_search] Bing returned %d results", len(processed))
        return processed[:max_results], "bing:ok"
    except Exception as exc:
        logger.error("[web_search] Bing failed: %s", exc)
        return None, f"bing:error ({type(exc).__name__})"


def _wikipedia_search(query: str, max_results: int) -> tuple[list[dict[str, str]] | None, str]:
    """Wikipedia OpenSearch — useful when brand queries are soft-blocked by DDG/Bing."""
    try:
        from kazma_core.proxy.client import get_scraping_client_sync

        with get_scraping_client_sync(
            follow_redirects=True,
            timeout=10.0,
            headers={
                "User-Agent": "KazmaAgent/0.6 (web_search; +https://github.com/Mubder/kazma)",
                "Accept": "application/json",
            },
            rotate_ua=False,
        ) as client:
            r = client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "opensearch",
                    "search": query,
                    "limit": max(1, min(max_results, 8)),
                    "namespace": 0,
                    "format": "json",
                },
            )
        if r.status_code != 200:
            return None, f"wikipedia:http_{r.status_code}"
        data = r.json()
        # [query, titles[], descriptions[], urls[]]
        if not isinstance(data, list) or len(data) < 4:
            return None, "wikipedia:bad_shape"
        titles, descs, urls = data[1], data[2], data[3]
        if not titles:
            return None, "wikipedia:empty"
        out: list[dict[str, str]] = []
        for i, title in enumerate(titles):
            out.append(
                {
                    "title": str(title),
                    "href": str(urls[i]) if i < len(urls) else "",
                    "body": str(descs[i]) if i < len(descs) else "",
                }
            )
        logger.info("[web_search] Wikipedia returned %d results", len(out))
        return out, "wikipedia:ok"
    except Exception as exc:
        logger.debug("[web_search] Wikipedia failed: %s", exc)
        return None, f"wikipedia:error ({type(exc).__name__})"


def _run_search(query: str, max_results: int) -> tuple[list[dict[str, str]], list[str], str]:
    """Run backend chain. Returns (results, attempt_notes, winning_backend).

    Continues to the next backend when the current one is unavailable **or**
    returns zero hits (rate-limit / soft-block on hot keywords).
    """
    attempts: list[str] = []
    backends: list[tuple[str, Any]] = [
        ("searxng", _searxng_search),
        ("duckduckgo", _ddg_search),
        ("bing", _bing_search),
        ("wikipedia", _wikipedia_search),
    ]
    for name, fn in backends:
        results, note = fn(query, max_results)
        attempts.append(note)
        if results:
            return results, attempts, name
    return [], attempts, ""


def _format_empty(query: str, attempts: list[str]) -> str:
    tried = ", ".join(attempts) if attempts else "(none)"
    return (
        f"No web results for: {query!r}\n\n"
        f"**Backends tried:** {tried}\n\n"
        "This is often **rate-limiting or soft bot-detection** on hot brand "
        "keywords (not necessarily a Kazma bug). Try:\n"
        "1. Rephrase (e.g. add a year or product name: `OpenAI API documentation`).\n"
        "2. Use ``read_url`` on a known URL (docs site, Wikipedia).\n"
        "3. Run a local SearXNG and set ``KAZMA_SEARXNG_URL`` for more reliable search.\n"
    )


def _format_results(
    query: str,
    results: list[dict[str, str]],
    *,
    backend: str,
    attempts: list[str],
) -> str:
    lines: list[str] = [
        f"# Search results for: {query}",
        f"_Source: {backend}_"
        + (f" (after: {', '.join(attempts[:-1])})" if len(attempts) > 1 else ""),
        "",
    ]
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        href = r.get("href", r.get("link", ""))
        body = r.get("body", r.get("snippet", ""))
        lines.append(f"## {i}. {title}")
        if href:
            lines.append(f"**URL:** {href}")
        if body:
            lines.append(body)
        lines.append("")
    return "\n".join(lines)


async def web_search(query: str, max_results: int = 8) -> str:
    """Search the public web; return markdown titles, URLs, and snippets.

    Tries multiple backends and continues on empty results (not only errors).

    Args:
        query: Search query string.
        max_results: Maximum number of results to return (default 8).

    Returns:
        Markdown-formatted search results, or a diagnostic empty/error message.
    """
    q = (query or "").strip()
    if not q:
        return "Error: empty search query."
    max_results = max(1, min(int(max_results or 8), 15))

    try:
        results, attempts, backend = await asyncio.to_thread(_run_search, q, max_results)
    except ConnectionError:
        return _friendly_error(ConnectionError())
    except TimeoutError:
        return _friendly_error(TimeoutError())
    except OSError as exc:
        return _friendly_error(exc)
    except Exception as exc:
        return _friendly_error(exc)

    if not results:
        logger.warning(
            "[web_search] All backends empty for %r attempts=%s", q, attempts
        )
    res_text = _format_results(q, results, backend=backend, attempts=attempts)
    try:
        from kazma_core.tools.research_session import record_chat_research

        record_chat_research(q, tool_name="web_search", result_text=res_text)
    except Exception as exc:
        logger.debug("[web_search] record_chat_research notice: %s", exc)

    return res_text
