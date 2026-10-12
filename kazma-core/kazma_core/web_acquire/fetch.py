"""Centralized page fetch — single recovery ladder for all Kazma products.

Delegates to ``tools.read_url`` implementation (Jina / Firecrawl / httpx /
Playwright) so there is one SoT for hard-page recovery. Callers should use
this module instead of importing private ``_fetch_*`` helpers.
"""

from __future__ import annotations

from collections.abc import Callable

from kazma_core.web_acquire.result import FetchResult

__all__ = ["FetchResult", "fetch_text", "jina_reader"]

async def fetch_text(
    url: str,
    *,
    purpose: str = "generic",
    scope_check: Callable[[str], bool] | None = None,
) -> FetchResult:
    """Acquire once through the public typed ladder, preserving provenance."""
    from kazma_core.tools.read_url import fetch_result

    return await fetch_result(url, purpose=purpose, scope_check=scope_check)


async def jina_reader(url: str) -> str | None:
    """Public Jina Reader helper (seed expand / recovery). Prefer :func:`fetch_text`."""
    from kazma_core.tools.read_url import _try_jina_reader

    return await _try_jina_reader(url)
