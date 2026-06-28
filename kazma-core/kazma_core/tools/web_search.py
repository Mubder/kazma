"""Web search tool — DuckDuckGo-powered search returning markdown results.

Usage:
    from kazma_core.tools.web_search import web_search
    results = await web_search("kazma agent framework")
"""

from __future__ import annotations

import asyncio


def _friendly_error(exc: Exception) -> str:
    """Map low-level exceptions to user-friendly messages."""
    if isinstance(exc, ConnectionError):
        return "Error: Could not connect to search service. Check your internet connection."
    if isinstance(exc, TimeoutError):
        return "Error: Search request timed out. Please try again."
    if isinstance(exc, OSError):
        return f"Error: Network error — {exc}"
    return f"Error: Search failed — {exc}"


def _run_search(query: str, max_results: int) -> list[dict[str, str]]:
    """Synchronous DuckDuckGo search — called via ``asyncio.to_thread``.

    Kept as a standalone module-level function so it is picklable and
    easy to test in isolation.  MUST be executed in a worker thread
    because ``DDGS().text()`` performs blocking network I/O.
    """
    from duckduckgo_search import DDGS

    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


async def web_search(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo and return results as markdown.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return (default 5).

    Returns:
        Markdown-formatted search results, or an error message.
    """
    try:
        import duckduckgo_search  # noqa: F401
    except ImportError:
        return "Error: duckduckgo-search package not installed. Run: pip install duckduckgo-search"

    try:
        # DDGS() uses blocking ``requests`` / ``httpx`` I/O. Run it in a
        # thread pool so it does NOT stall the asyncio event loop.
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
