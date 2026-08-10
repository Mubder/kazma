"""Advanced Web Crawler Native Skill — tools for search, crawling, and document parsing."""

from __future__ import annotations

import logging
from pathlib import Path

from kazma_core.agent.tool_registry import _workspace_scope_error
from kazma_core.documents.errors import DocumentParseError
from kazma_core.documents.service import DocumentService
from kazma_core.tools.read_url import read_url
from kazma_core.tools.web_search import web_search

logger = logging.getLogger(__name__)


async def web_search_duckduckgo(query: str, limit: int = 5) -> str:
    """Search the public web via core ``web_search`` (not DDG-only).

    Resolution: SearXNG → DuckDuckGo → Bing HTML. Prefer ``KAZMA_SEARXNG_URL``
    for reliability.

    Args:
        query: The search query string.
        limit: Max number of results.

    Returns:
        A markdown-formatted string with search results.
    """
    try:
        return await web_search(query, max_results=limit)
    except Exception as e:
        logger.error("Error executing web search: %s", e)
        return f"Error executing web search: {e}"


async def crawl_page(
    url: str,
    offset: int = 0,
    max_chars: int | None = None,
) -> str:
    """Fetch **one** public URL and extract a readable text window (``read_url``).

    Not a multi-page site crawler. Supports offset/max_chars paging.

    Args:
        url: The URL of the web page to fetch.
        offset: Character offset into the full extract.
        max_chars: Window size (default from KAZMA_READ_URL_MAX_CHARS).

    Returns:
        Extracted text content, or an error message.
    """
    try:
        return await read_url(url, offset=offset, max_chars=max_chars)
    except Exception as e:
        logger.error("Error crawling webpage: %s", e)
        return f"Error crawling webpage: {e}"


async def parse_document(
    path: str,
    page: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    block: str | int | None = None,
    offset: int = 0,
    max_chars: int = 8_000,
) -> str:
    """Parse a runtime-ready local document with deterministic paging.

    Args:
        path: Path to the local file to parse.

    Returns:
        Organized textual representation of the document contents.
    """
    p = Path(path).expanduser().resolve()
    scope_err = _workspace_scope_error(p, path, "reads")
    if scope_err:
        return scope_err

    if not p.exists():
        return f"Error: Document not found: {path}"
    if not p.is_file():
        return f"Error: Path is not a file: {path}"

    try:
        result = await DocumentService().read_transient(
            p,
            approved_path=p,
            page=page,
            page_start=page_start,
            page_end=page_end,
            block=block,
            offset=offset,
            max_chars=max_chars,
            fence=True,
        )
        return result.as_tool_output()
    except DocumentParseError as exc:
        return f"Error: {exc.safe_message}"
    except Exception as e:
        logger.error("Document service failed for %s (%s)", path, type(e).__name__)
        return f"Error parsing document: {type(e).__name__}"
