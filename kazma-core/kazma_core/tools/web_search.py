"""Web search tool — DuckDuckGo-powered search returning markdown results.

Usage:
    from kazma_core.tools.web_search import web_search
    results = await web_search("kazma agent framework")
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def _friendly_error(exc: Exception) -> str:
    """Map low-level exceptions to user-friendly messages."""
    if isinstance(exc, ConnectionError):
        return "Error: Could not connect to search service. Check your internet connection."
    if isinstance(exc, TimeoutError):
        return "Error: Search request timed out. Please try again."
    if isinstance(exc, OSError):
        logger.debug("[web_search] Network error: %s", exc, exc_info=True)
        return "Error: Network error occurred during search. Please check your connection."
    logger.debug("[web_search] Search failed: %s", exc, exc_info=True)
    return "Error: Search failed. Please try again."


def _run_search(query: str, max_results: int) -> list[dict[str, str]]:
    """Synchronous search — tries DuckDuckGo, falls back to Bing scraping.

    Kept as a standalone module-level function so it is picklable and
    easy to test in isolation. MUST be executed in a worker thread
    because it performs blocking network I/O.
    """
    import logging
    logger = logging.getLogger(__name__)

    # 1. Try DuckDuckGo first
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            if results:
                logger.info("[web_search] DuckDuckGo search returned %d results.", len(results))
                return results
    except Exception as exc:
        logger.warning("[web_search] DuckDuckGo search failed or blocked, trying Bing fallback. Error: %s", exc)

    # 2. Fallback to Bing scraping
    try:
        import httpx
        from html.parser import HTMLParser
        import base64
        import urllib.parse

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0'
        }
        url = f"https://www.bing.com/search?q={urllib.parse.quote_plus(query)}"
        r = httpx.get(url, headers=headers, follow_redirects=True, timeout=10)
        if r.status_code != 200:
            logger.warning("[web_search] Bing fallback returned status code %d", r.status_code)
            return []

        def decode_bing_url(b_url: str) -> str:
            if not b_url.startswith('http'):
                return b_url
            parsed_url = urllib.parse.urlparse(b_url)
            q_params = urllib.parse.parse_qs(parsed_url.query)
            u_param = q_params.get('u', [None])[0]
            if u_param:
                if u_param.startswith('a1'):
                    b64_str = u_param[2:]
                else:
                    b64_str = u_param
                # Add padding
                padding = '=' * (4 - len(b64_str) % 4)
                try:
                    decoded = base64.b64decode(b64_str + padding).decode('utf-8', errors='ignore')
                    return decoded
                except Exception:
                    pass
            return b_url

        class BingHTMLParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.results = []
                self.current_result = None
                self.in_algo = False
                self.in_h2 = False
                self.in_snippet = False
                self.in_a = False
                self.depth_algo = 0
                self.current_text = []

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                class_val = attrs_dict.get('class', '')

                if self.in_algo:
                    self.depth_algo += 1

                if tag == 'li' and 'b_algo' in class_val.split():
                    self.in_algo = True
                    self.depth_algo = 0
                    self.current_result = {'title': '', 'href': '', 'body': ''}
                    self.results.append(self.current_result)

                if self.in_algo:
                    if tag == 'h2':
                        self.in_h2 = True
                        self.current_text = []
                    elif tag == 'a' and self.in_h2:
                        self.in_a = True
                        if 'href' in attrs_dict:
                            self.current_result['href'] = attrs_dict['href']
                    elif tag in ('p', 'div', 'span') and any(x in class_val for x in ('b_caption', 'b_linelimit', 'tab-content')):
                        self.in_snippet = True
                        self.current_text = []

            def handle_data(self, data):
                if self.in_algo:
                    if self.in_h2 or self.in_snippet:
                        self.current_text.append(data)

            def handle_endtag(self, tag):
                if self.in_algo:
                    if tag == 'a' and self.in_a:
                        self.in_a = False
                    elif tag == 'h2' and self.in_h2:
                        self.in_h2 = False
                        if self.current_result:
                            self.current_result['title'] = "".join(self.current_text).strip()
                        self.current_text = []
                    elif tag in ('p', 'div', 'span') and self.in_snippet:
                        self.in_snippet = False
                        if self.current_result:
                            existing = self.current_result['body']
                            new_text = "".join(self.current_text).strip()
                            if new_text:
                                self.current_result['body'] = (existing + " " + new_text).strip() if existing else new_text
                        self.current_text = []

                    if tag == 'li' and self.depth_algo == 0:
                        self.in_algo = False
                        self.current_result = None
                    elif tag == 'li':
                        self.depth_algo -= 1

        parser = BingHTMLParser()
        parser.feed(r.text)

        processed_results = []
        for res in parser.results:
            if res.get('title') or res.get('href'):
                real_url = decode_bing_url(res.get('href', ''))
                processed_results.append({
                    'title': res.get('title', 'Untitled'),
                    'href': real_url,
                    'body': res.get('body', '')
                })

        logger.info("[web_search] Bing fallback returned %d parsed results.", len(processed_results))
        return processed_results[:max_results]

    except Exception as exc:
        logger.error("[web_search] Bing fallback search failed. Error: %s", exc)
        raise


async def web_search(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo or Bing fallback and return results as markdown.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return (default 5).

    Returns:
        Markdown-formatted search results, or an error message.
    """
    try:
        results = await asyncio.to_thread(_run_search, query, max_results)
    except ConnectionError:
        return _friendly_error(ConnectionError())
    except TimeoutError:
        return _friendly_error(TimeoutError())
    except OSError as exc:
        return _friendly_error(exc)
    except Exception as exc:
        return _friendly_error(exc)

    if not results:
        return f"No results found for: {query}"

    lines: list[str] = [f"# Search results for: {query}\n"]
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
