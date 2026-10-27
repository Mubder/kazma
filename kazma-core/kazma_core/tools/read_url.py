"""Read URL tool — fetch, page, save, and chunk public web pages for research.

Tiered fetch (public web, not anti-bot invincible):

    1. httpx fast path with browser-like UA + trafilatura extraction.
    2. Local Chromium recovery on bot walls / empty extracts.
    3. Hosted recovery only under explicit acquisition policy + provider allowlist.

Research features
-----------------
* **Paging:** ``offset`` + ``max_chars`` to walk a long page without re-fetch
  (in-process full-text cache keyed by URL).
* **Caps:** default window from ``KAZMA_READ_URL_MAX_CHARS`` (default 16000).
* **Save:** ``read_url_to_file`` writes full extract under the workspace.
* **Chunks:** ``list_research_chunks`` / ``read_research_chunk`` /
  ``summarize_research_file`` for map-style research without flooding context.

Validate public URLs and actual redirects. Knowledge ingest uses the typed
``web_acquire.fetch_text`` façade; the legacy text adapter shares its ladder.
"""

from __future__ import annotations

import asyncio
import httpx
import hashlib
import logging
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any
from collections.abc import Callable
from dataclasses import replace

from kazma_core.safety.prompt_fence import fence_untrusted
from kazma_core.http_tls import shared_ssl_context
from kazma_core.install_hint import extra_install_hint
from kazma_core.errors import redact_secrets
from kazma_core.web_acquire.policy import acquisition_policy, AcquisitionPolicyError
from kazma_core.web_acquire.scope import (
    AcquisitionScope, AcquisitionScopeError, active_scope, require_scope,
)
from kazma_core.web_acquire.result import AcquisitionError, ExtractSaveResult, FetchResult, FetchErrorCode
from kazma_core.web_acquire.trace import AttemptSpan, FetchTrace, active_trace, classify_failure
from kazma_core.web_acquire.parser import parse_document
from kazma_core.web_acquire import dns_worker
from kazma_core.web_acquire import provider_usage

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
_HARD_BODY_MAX = 20_000_000
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
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}


def _random_ua() -> str:
    """A rotated User-Agent for retry attempts (defeats UA-based blocking)."""
    from kazma_core.proxy.client import random_user_agent

    return random_user_agent()


def _fetch_max_bytes() -> int:
    """Hard cap on a single response body (env KAZMA_FETCH_MAX_BYTES, default 5MB).

    Industry-grade crawlers never read unbounded bodies — a multi-GB binary or
    a gzip bomb would otherwise be fully decoded into memory by ``response.text``.
    """
    try:
        return min(_HARD_BODY_MAX, max(65_536, int(os.environ.get("KAZMA_FETCH_MAX_BYTES", "5000000") or "5000000")))
    except ValueError:
        return 5_000_000


_TEXTUAL_CONTENT_TYPES = (
    "text/",
    "application/xhtml",
    "application/xml",
    "application/json",
    "application/rss",
    "application/atom",
)


def _is_textual_content_type(content_type: str) -> bool:
    """True when a Content-Type is worth extracting text from."""
    ct = (content_type or "").lower()
    if not ct:
        return True  # missing header — try anyway
    if any(tok in ct for tok in _TEXTUAL_CONTENT_TYPES):
        return True
    # RFC 6839 structured-syntax suffixes: application/rdap+json,
    # application/hal+json, application/ld+json, application/atom+xml … are
    # all textual API payloads. The gate REJECTED rdap+json (no plain
    # "application/json" substring) and ran BEFORE the JSON short-circuit,
    # so every 200 RDAP reply (domain TAKEN) died here — the 2026-08-27
    # availability-check incident (only the 404/free branch was tested).
    base = ct.split(";", 1)[0].strip()
    if base.endswith("+json") or base.endswith("+xml"):
        return True
    return False


def _is_json_payload(content_type: str, body: str) -> bool:
    """True when the response is a JSON document (registry APIs, oEmbed, etc.).

    JSON must short-circuit the whole HTML pipeline: trafilatura discards it
    ("discarding data: None"), the empty extract is misclassified as a
    thin/JS-shell page, and hard-page recovery then launches Playwright for a
    10s+ browser fetch of what was a 300ms JSON call — the exact slowness the
    2026-08-21 domain-sweep incident flagged (RDAP/oEmbed/Twitter-info JSON
    endpoints each burning ~10s).
    """
    ct = (content_type or "").lower()
    if "json" in ct:
        return True
    sn = (body or "").lstrip()[:1]
    return sn in ("{", "[")


# Firecrawl process-wide cooldown: when the API starts returning 429, parallel
# read_url fan-outs (domain sweeps batch 4-8 URLs) each burn 1.5+3+4.5s of
# backoff against an already-limited quota. After two consecutive 429-exhausted
# attempts, skip Firecrawl entirely for 5 minutes. Local HTTP/Chromium still
# run first (2026-08-21 domain-sweep incident; local-first policy 2026-10-10).
_FIRECRAWL_COOLDOWN: dict[str, float] = {"until": 0.0}


def _firecrawl_in_cooldown() -> bool:
    import time as _time

    return _time.monotonic() < _FIRECRAWL_COOLDOWN["until"]


async def _get_capped(
    client: Any, url: str, max_bytes: int, *,
    validated_ips: tuple[str, ...] = (), via_proxy: bool = False,
    require_peer: bool = False,
) -> tuple[Any, str, bool]:
    """Shared bounded GET; connected-peer evidence survives stream closure."""
    from kazma_core.web_acquire.http import read_capped

    require_scope(url)
    with AttemptSpan("httpx", url, remote=via_proxy) as attempt:
        trace = active_trace.get()

        def observed(response):
            # Retain headers/status even if a later body read/decode fails.
            attempt.response(response.status_code, final_url=url)
            if trace is not None:
                trace.http_source = FetchResult(
                    ok=False, url=trace.url, final_url=url, backend="httpx",
                    http_status=response.status_code, meta={"proxy_used": via_proxy})

        response, text, truncated = await read_capped(
            client, url, max_bytes, validated_ips=validated_ips,
            via_proxy=via_proxy, require_peer=require_peer,
            observe_response=observed,
        )
        attempt.response(response.status_code, final_url=url, truncated=truncated,
                         wire_bytes=response.extensions.get("kazma_wire_bytes"),
                         decoded_bytes=response.extensions.get("kazma_decoded_bytes"))
        if trace is not None:
            trace.http_source = FetchResult(
                ok=response.status_code < 400, url=trace.url, final_url=url,
                backend="httpx", http_status=response.status_code,
                wire_bytes=response.extensions.get("kazma_wire_bytes"),
                decoded_bytes=response.extensions.get("kazma_decoded_bytes"),
                truncated=truncated, truncation_reasons=("body_limit",) if truncated else (),
                meta={"proxy_used": via_proxy},
            )
        return response, text, truncated

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
_full_text_cache: OrderedDict[str, tuple[FetchResult, float]] = OrderedDict()


def _cache_get(url: str) -> str | None:
    result = _cache_result_get(url)
    return result.text if result is not None else None


def _cache_namespace(result=None):
    from kazma_core.web_acquire.robots import active_robots

    context = active_robots.get()
    mode = result.meta.get('robots', {}).get('mode') if result is not None else None
    enforced = mode == 'enforced' if mode is not None else bool(context is not None and context.enforce())
    profile = active_trace.get().profile if active_trace.get() is not None else "generic"
    return acquisition_policy().cache_namespace + "|" + profile + ("|robots|" if enforced else "|manual|")


def _cache_result_get(url: str) -> FetchResult | None:
    # Legacy cached strings have no redirect provenance to establish scope.
    if active_scope.get() is not None:
        return None
    url = _cache_namespace() + url
    item = _full_text_cache.get(url)
    if not item:
        return None
    result, ts = item
    if time.time() - ts > _CACHE_TTL_S:
        _full_text_cache.pop(url, None)
        return None
    _full_text_cache.move_to_end(url)
    return replace(result, cache_hit=True, attempts=(), meta=dict(result.meta))


def _cache_put(url: str, text: str) -> None:
    _cache_result_put(url, FetchResult(ok=True, url=url, text=text, char_count=len(text)))


def _cache_result_put(url: str, result: FetchResult) -> None:
    if active_scope.get() is not None:
        return
    url = _cache_namespace(result) + url
    _full_text_cache[url] = (replace(result, meta=dict(result.meta)), time.time())
    _full_text_cache.move_to_end(url)
    while len(_full_text_cache) > _CACHE_MAX_ENTRIES:
        _full_text_cache.popitem(last=False)


def clear_url_cache() -> None:
    """Clear the in-process full-text cache (tests)."""
    _full_text_cache.clear()


def _friendly_error(exc: Exception, url: str = "") -> str:
    import httpx

    if isinstance(exc, (ConnectionError, httpx.ConnectError)):
        return f"Error: Could not connect to {url}. Check the URL and your internet connection."
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return f"Error: Request to {url} timed out. The server may be slow or unreachable."
    if isinstance(exc, OSError):
        return f"Error: Network error while fetching {url} — {exc}"
    if isinstance(exc, (httpx.ReadError, httpx.RemoteProtocolError)):
        return f"Error: Connection interrupted while reading {url}. Retry when the server is reachable."

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
    from kazma_core.web_acquire.html_extract import strip_html_fallback

    return strip_html_fallback(html)


def _extract_text(html: str) -> str:
    """Extract readable text; always try a plain-HTML fallback for static pages."""
    trace = active_trace.get()
    if trace is not None and trace.profile == "knowledge":
        from kazma_core.web_acquire.document import extract_knowledge_document

        document = extract_knowledge_document(html, _HARD_MAX)
        trace.extract_truncated, trace.dynamic_tabs = document.truncated, document.dynamic_tabs
        return document.text
    from kazma_core.web_acquire.html_extract import extract_article

    text, truncated = extract_article(html, _HARD_MAX)
    if trace is not None:
        trace.extract_truncated = truncated
    return text


def _is_thin_extraction(text: str, html: str) -> bool:
    """Whether Playwright is worth trying.

    Short *but non-empty* extracts from small static HTML are OK (example.com).
    Only treat as thin when empty, or tiny vs a large JS-heavy shell.
    """
    if not text or not text.strip():
        return True
    # SPA documentation sites (Docusaurus, Next.js docs, GitBook) whose static HTML
    # extract yields under 2000 chars of mostly nav chrome
    html_sample = html[:8000].lower()
    is_spa_doc = any(
        m in html_sample
        for m in ("docusaurus", "next-route-announcer", "__next", "_next/static", "gatsby", "gitbook", "mkdocs")
    )
    if is_spa_doc and len(text) < 2000 and text.count("\n") < 25:
        return True
    if _looks_like_js_shell(html) and len(html) >= 500:
        return True
    if len(text) >= MIN_USEFUL_CHARS:
        return False
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
    from kazma_core.playwright_loop import run_in_browser_loop

    try:
        with AttemptSpan("chromium", url) as attempt:
            try:
                from playwright.async_api import TimeoutError as BrowserTimeoutError
            except ImportError as exc:
                attempt.fail(FetchErrorCode.UNAVAILABLE, str(exc))
                logger.debug("[read_url] optional Playwright is unavailable: %s", exc)
                return None

            trace = active_trace.get()
            if trace is not None:
                trace.browser_source = None
            try:
                text = await run_in_browser_loop(_fetch_with_playwright_impl(url))
            except BrowserTimeoutError as exc:
                attempt.fail(FetchErrorCode.TIMEOUT, str(exc))
                attempt.fields["transient"] = True
                return None
            source = trace.browser_source if trace is not None else None
            if source is not None:
                attempt.fields.update(final_url=source.final_url, http_status=source.http_status,
                                      truncated=source.truncated)
                attempt.remote = bool(source.meta.get("proxy_used"))
            if text:
                attempt.fields["outcome"] = "success"
                if trace is not None:
                    trace.recovered_source = source or FetchResult(ok=True, url=url, backend="chromium")
            else:
                attempt.fail(source.error_code if source and source.error_code else FetchErrorCode.EMPTY,
                             source.error if source and source.error else "Chromium produced no readable content.")
                attempt.fields["transient"] = bool(source and source.transient)
            return text
    except AcquisitionError as exc:
        trace = active_trace.get()
        if trace is not None:
            trace.browser_source = FetchResult(ok=False, url=url, backend="chromium",
                error_code=exc.code, error=str(exc), transient=exc.transient, quality="failed")
        raise



async def _fetch_with_playwright_impl(url: str) -> str | None:
    """Compatibility body on the browser loop; one shared typed renderer."""
    from kazma_core.web_acquire.browser import render_document

    trace = active_trace.get()
    document = await render_document(url, challenge=_looks_like_bot_block,
                                     profile=trace.profile if trace is not None else "generic")
    if trace is not None:
        trace.browser_source = document.result
    return document.result.text if document.result.ok else None


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
    """Policy-gated hosted Reader; each attempted service call is observed."""
    from kazma_core.web_acquire.robots import active_robots

    robot_context = active_robots.get()
    if robot_context is not None and robot_context.enforce():
        return None
    if active_scope.get() is not None:
        return None
    if not acquisition_policy().permits("jina") or _jina_hard_disabled():
        return None
    from kazma_core.security.ssrf import validate_url
    from kazma_core.web_acquire.http import read_response_capped
    from kazma_core.web_acquire.origins import origin_scheduler
    from kazma_core.web_acquire import accounting
    from kazma_core.web_acquire.endpoint_transport import EndpointAsyncTransport

    try:
        await dns_worker.validate_public_url(url, validator=validate_url)
        token = (os.environ.get("JINA_API_KEY") or os.environ.get("KAZMA_JINA_API_KEY") or "").strip()
        from kazma_core.web_acquire.provider_usage import jina_token_budget
        headers = {"Accept": "text/plain", "User-Agent": _BROWSER_UA,
                   "Accept-Encoding": "gzip, deflate", "X-Return-Format": "markdown",
                   "X-Token-Budget": str(jina_token_budget())}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=False, trust_env=False,
                                     transport=EndpointAsyncTransport(verify=shared_ssl_context()),
                                     event_hooks=accounting.httpx_hooks('jina', token, asynchronous=True),
                                     verify=shared_ssl_context()) as client:
            with AttemptSpan("jina", url, remote=True) as attempt:
                async with origin_scheduler().request_many(url, 'https://r.jina.ai/'):
                    async with client.stream("GET", f"https://r.jina.ai/{url}", headers=headers) as response:
                        attempt.response(response.status_code, usage=provider_usage._from_jina(response.headers))
                        from kazma_core.web_acquire.crawl_jobs import call
                        await call(origin_scheduler().observe, 'https://r.jina.ai/', response.status_code, response.headers)
                        r, body, truncated = await read_response_capped(response, _fetch_max_bytes())
                attempt.response(r.status_code, decoded_bytes=len(r.content), truncated=truncated,
                                 wire_bytes=r.extensions.get("kazma_wire_bytes"))
                if truncated:
                    attempt.fail(FetchErrorCode.LIMIT, "Jina response exceeded the acquisition body limit.")
                    return None
                if r.status_code != 200:
                    return None
                text = body.strip()
                if len(text) <= 50:
                    attempt.fail(FetchErrorCode.EMPTY, "Jina returned no useful extract.")
                    return None
                attempt.fields["outcome"] = "success"
                trace = active_trace.get()
                if trace is not None:
                    trace.recovered_source = FetchResult(
                        ok=True, url=trace.url, backend="jina", quality="unverified",
                        meta={"service_http_status": r.status_code, "service_decoded_bytes": len(r.content)},
                        truncated=len(text) > _HARD_MAX,
                        truncation_reasons=("text_limit",) if len(text) > _HARD_MAX else (),
                    )
                return text[:_HARD_MAX]
    except AcquisitionError:
        raise
    except Exception as exc:
        logger.debug("[read_url] Jina Reader failed: %s", exc)
    return None


async def _try_firecrawl(url: str) -> str | None:
    """Policy-gated service; attempts, failures and retry requests stay distinct."""
    from kazma_core.web_acquire.robots import active_robots

    robot_context = active_robots.get()
    if robot_context is not None and robot_context.enforce():
        return None
    if active_scope.get() is not None or not acquisition_policy().permits("firecrawl"):
        return None
    api_key = (os.environ.get("KAZMA_FIRECRAWL_API_KEY") or "").strip()
    if not api_key or _firecrawl_in_cooldown():
        return None
    from kazma_core.security.ssrf import validate_url
    from kazma_core.web_acquire.http import read_response_capped
    from kazma_core.web_acquire.origins import origin_scheduler

    base = (os.environ.get("KAZMA_FIRECRAWL_URL") or "https://api.firecrawl.dev").rstrip("/")
    from kazma_core.web_acquire import accounting
    from kazma_core.web_acquire.endpoint_transport import EndpointAsyncTransport
    try:
        await dns_worker.validate_public_url(url, validator=validate_url)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                   "Accept-Encoding": "gzip, deflate"}
        async with httpx.AsyncClient(timeout=60.0, trust_env=False, verify=shared_ssl_context(),
                transport=EndpointAsyncTransport(verify=shared_ssl_context()),
                event_hooks=accounting.httpx_hooks('firecrawl', base + api_key, asynchronous=True)) as client:
            for retry in range(3):
                with AttemptSpan("firecrawl", url, remote=True) as attempt:
                    async with origin_scheduler().request_many(url, base):
                        async with client.stream("POST", f"{base}/v1/scrape", headers=headers,
                                                 json={"url": url, "formats": ["markdown"]}) as response:
                            attempt.response(response.status_code)
                            from kazma_core.web_acquire.crawl_jobs import call
                            await call(origin_scheduler().observe, base, response.status_code, response.headers)
                            r, _, truncated = await read_response_capped(response, _fetch_max_bytes())
                    attempt.response(r.status_code, decoded_bytes=len(r.content), truncated=truncated,
                                     wire_bytes=r.extensions.get("kazma_wire_bytes"))
                    if truncated:
                        attempt.fail(FetchErrorCode.LIMIT, "Firecrawl response exceeded the acquisition body limit.")
                        return None
                    if r.status_code == 200:
                        try:
                            data = r.json()
                            if not isinstance(data, dict) or data.get("success") is False:
                                raise ValueError("Scrape was not successful.")
                            payload = data.get("data") or data
                            text = payload.get("markdown") or payload.get("content") or payload.get("text") or ""
                            if not isinstance(text, str):
                                raise ValueError("Extract must be text.")
                        except (ValueError, AttributeError, TypeError):
                            attempt.fail(FetchErrorCode.PROVIDER_RESPONSE, "Firecrawl returned an invalid scrape response.")
                            return None
                        if len(text.strip()) <= 50:
                            attempt.fail(FetchErrorCode.EMPTY, "Firecrawl returned no useful extract.")
                            return None
                        attempt.fields["outcome"] = "success"
                        trace = active_trace.get()
                        if trace is not None:
                            trace.recovered_source = FetchResult(
                                ok=True, url=trace.url, backend="firecrawl", quality="unverified",
                                meta={"service_http_status": r.status_code, "service_decoded_bytes": len(r.content)},
                                truncated=len(text.strip()) > _HARD_MAX,
                                truncation_reasons=("text_limit",) if len(text.strip()) > _HARD_MAX else (),
                            )
                        return text.strip()[:_HARD_MAX]
                if r.status_code in (429, 502, 503) and retry < 2:
                    # The origin scheduler retains the complete server delay;
                    # a local retry must never shorten it to fit this call.
                    await asyncio.sleep(1.5 * (retry + 1))
                    continue
                if r.status_code == 429:
                    _FIRECRAWL_COOLDOWN["until"] = time.monotonic() + 300.0
                    logger.warning("[read_url] Firecrawl rate-limited — cooling down for 300s")
                return None
    except AcquisitionError:
        raise
    except Exception as exc:
        logger.debug("[read_url] Firecrawl failed: %s", exc)
    return None


def _is_registry_or_api_url(url: str) -> bool:
    """URLs that never benefit from scraping backends (Firecrawl/Jina).

    Registry RDAP endpoints, oEmbed lookups, and .json API paths return pure
    JSON in ~300ms via direct httpx — routing them through Firecrawl adds
    ~1s of API latency per call (and its 429 backoffs) for zero gain. Domain
    sweeps batch dozens of these, so the fast-path is the difference between
    a 15s and a 90s sweep (2026-08-21 domain-sweep follow-up).
    """
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        path = (urlparse(url).path or "").lower()
        q = (urlparse(url).query or "").lower()
    except Exception:
        return False
    if host.startswith("rdap.") or ".rdap." in host:
        return True
    if path.endswith(".json") or "/api/" in path or "oembed" in path or "oembed" in q:
        return True
    # Well-known registry endpoints that don't carry rdap in the host.
    for marker in ("verisign.com", "identitydigital", "iana.org"):
        if marker in host:
            return True
    return False


async def _fetch_via_optional_backends(url: str) -> str | None:
    """Compatibility hook: acquisition always starts locally now."""
    return None


async def _recover_hard_page(url: str, *, why: str = "") -> str | None:
    """Local browser first; explicitly permitted hosted fallbacks afterwards."""
    policy = acquisition_policy()
    trace = active_trace.get()
    if trace is not None:
        trace.recovered_source = None
    logger.info("[read_url] Hard-page recovery for %s (%s), policy=%s", url, why or "unspecified", policy.mode)
    text = await _fetch_with_playwright(url)
    if text:
        if trace is not None and trace.recovered_source is None:
            trace.recovered_source = FetchResult(ok=True, url=trace.url, backend="chromium")
        return text
    if trace is not None and trace.browser_source is not None and trace.browser_source.error_code in (
        FetchErrorCode.LOGIN, FetchErrorCode.SECURITY, FetchErrorCode.SCOPE,
        FetchErrorCode.POLICY, FetchErrorCode.LIMIT, FetchErrorCode.REDIRECT, FetchErrorCode.ROUTING, FetchErrorCode.THROTTLED, FetchErrorCode.ROBOTS, FetchErrorCode.ROBOTS_UNAVAILABLE,
    ):
        source = trace.browser_source
        raise AcquisitionError(source.error_code, source.error or "Browser acquisition was refused.",
                               transient=source.transient)
    if _is_registry_or_api_url(url):
        return None
    backend = (os.environ.get("KAZMA_FETCH_BACKEND") or "auto").strip().lower()
    # Legacy selections can narrow the policy, never broaden it.
    if backend == "httpx":
        return None
    if policy.permits("firecrawl") and backend in ("auto", "firecrawl"):
        text = await _try_firecrawl(url)
        if text:
            return text
    if policy.permits("jina") and backend in ("auto", "jina") and not _jina_hard_disabled():
        return await _try_jina_reader(url)
    return None


def _failed(code: FetchErrorCode, message: str, *, transient: bool = False) -> FetchResult:
    trace = active_trace.get()
    source = (trace.browser_source if trace.browser_source is not None and trace.browser_source.error_code == code
              else trace.http_source) if trace is not None else None
    source = source or FetchResult(ok=False, url=trace.url if trace is not None else "")
    return replace(source, ok=False, text="", error=redact_secrets(message.removeprefix("Error: ")),
                   error_code=code, transient=transient, quality="failed")


def _failed_exception(exc: BaseException, url: str = "") -> FetchResult:
    code, transient = classify_failure(exc)
    message = str(exc) if code in (FetchErrorCode.POLICY, FetchErrorCode.SECURITY,
                                   FetchErrorCode.SCOPE, FetchErrorCode.INVALID_URL, FetchErrorCode.LOGIN,
                                   FetchErrorCode.ROUTING, FetchErrorCode.THROTTLED, FetchErrorCode.ROBOTS, FetchErrorCode.ROBOTS_UNAVAILABLE) else _friendly_error(exc, url)
    return _failed(code, message, transient=transient)


def _succeeded(text: str, *, recovery: bool = False, quality: str = "usable") -> FetchResult:
    trace = active_trace.get()
    source = None if trace is None else (trace.recovered_source if recovery else trace.http_source)
    source = source or FetchResult(ok=True, url=trace.url if trace is not None else "")
    if recovery and source.quality == "unverified":
        quality = "unverified"
    if quality == "json" and source.truncated:
        return _failed(FetchErrorCode.LIMIT, "JSON response exceeded the body limit; incomplete JSON was refused.")
    if source.truncated and quality != "json":
        quality = "partial"
    return replace(source, ok=True, text=text, error=None, error_code=None, transient=False,
                   quality=quality, char_count=len(text),
                   links_complete=True if quality == "json" else source.links_complete)


async def fetch_result(
    url: str, *, purpose: str = "generic", scope_check: Callable[[str], bool] | None = None,
) -> FetchResult:
    """Typed entry to the single bounded ladder; legacy text is an adapter."""
    started = time.perf_counter()
    trace = FetchTrace(url=(url or "").strip(),
                       profile="knowledge" if purpose == "kb" else "discovery" if purpose == "kb_discovery" else "generic")
    from kazma_core.web_acquire.origins import origin_delay

    delay_token = origin_delay.set(max(origin_delay.get(), 0.3 if purpose in {'crawl', 'kb', 'kb_discovery'} else 0))
    trace_token = active_trace.set(trace)
    from kazma_core.web_acquire.robots import crawl_policy, robots_evidence

    try:
        with crawl_policy(purpose=purpose):
            result = await _bounded_fetch_result(trace.url, scope_check=scope_check)
            result.meta = {**result.meta, 'robots': robots_evidence()}
        result = replace(result, url=trace.url, purpose=purpose,
                         char_count=len(result.text),
                         latency_ms=round((time.perf_counter() - started) * 1000, 1),
                         attempts=tuple(trace.attempts),
                         usage=provider_usage._summary([(i, attempt.usage) for i, attempt in enumerate(trace.attempts) if attempt.remote]))
        result.meta = {**result.meta, "profile": trace.profile}
        if scope_check is None and result.ok and not result.cache_hit and not result.truncated:
            _cache_result_put(trace.url, result)
            if result.final_url:
                _cache_result_put(result.final_url, result)
        return result
    finally:
        active_trace.reset(trace_token)
        origin_delay.reset(delay_token)


async def _fetch_full_text(url: str, *, scope_check: Callable[[str], bool] | None = None) -> str:
    result = await fetch_result(url, scope_check=scope_check)
    return result.legacy_text()


async def _bounded_fetch_result(
    url: str, *, scope_check: Callable[[str], bool] | None = None,
) -> FetchResult:
    """Bound the complete ladder, including DNS, retries and local rendering."""
    try:
        deadline = float(os.environ.get("KAZMA_FETCH_DEADLINE_SECONDS", "90"))
        if not 1 <= deadline <= 300:
            raise ValueError
    except ValueError:
        return _failed(FetchErrorCode.POLICY, "KAZMA_FETCH_DEADLINE_SECONDS must be between 1 and 300.")
    scope = AcquisitionScope(scope_check) if scope_check is not None else None
    token = active_scope.set(scope)
    try:
        try:
            async with asyncio.timeout(deadline):
                from kazma_core.web_acquire.policy import policy_scope
                from kazma_core.web_acquire.accounting import remote_scope

                policy = acquisition_policy()  # validate before admission
                with policy_scope(policy), remote_scope():
                    from kazma_core.web_acquire.robots import require_robots

                    await require_robots(url)  # enforce current rules before cache reuse or admission
                    cached = _cache_result_get(url)
                    if cached is not None:
                        return cached
                    from kazma_core.web_acquire import resources

                    async with await resources.acquisition_capacity().acquire():
                        text = await _fetch_full_text_impl(url)
                    if scope is not None and scope.violation:
                        return _failed(FetchErrorCode.SCOPE, scope.violation)
                    return text
        except AcquisitionScopeError as exc:
            return _failed(FetchErrorCode.SCOPE, str(exc))
        except TimeoutError:
            return _failed(FetchErrorCode.DEADLINE,
                           f"Acquisition deadline exceeded ({deadline:g}s); unfinished work was cancelled.",
                           transient=True)
        except Exception as exc:
            return _failed_exception(exc, url)
    finally:
        active_scope.reset(token)


async def _fetch_full_text_impl(url: str) -> FetchResult:
    """Single typed ladder implementation; never infer failure from content."""
    if not url:
        return _failed(FetchErrorCode.INVALID_URL, "No URL provided.")
    try:
        acquisition_policy()
    except AcquisitionPolicyError as exc:
        return _failed_exception(exc, url)
    cached = _cache_result_get(url)
    if cached is not None:
        return cached

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    require_scope(url)
    try:
        from kazma_core.security.ssrf import SSRFError, validate_url

        validated_ips = await dns_worker.validate_public_url(url, validator=validate_url) or ()
    except SSRFError as exc:
        return _failed_exception(exc, url)
    except ValueError as exc:
        return _failed(FetchErrorCode.INVALID_URL, f"Error: Invalid URL — {exc}")

    # Optional anti-bot / JS-heavy backends (not invincible; improves hard sites)
    opt = await _fetch_via_optional_backends(url)
    if opt:
        return _succeeded(opt, recovery=True)

    html = ""
    status_code = 0
    tried_playwright = False
    final_url = url

    try:
        from urllib.parse import urlparse as _urlparse

        from kazma_core.proxy.client import get_active_proxy_url, get_scraping_client
        from kazma_core.security.ssrf import assert_peer_public

        _pins: dict[str, str] = {}
        _host = (_urlparse(url).hostname or "").lower()
        if _host and validated_ips:
            _pins[_host] = validated_ips[0]
        _proxy_url = await asyncio.to_thread(get_active_proxy_url)  # settings (audit 2026-10-26 AUD-111)
        _via_proxy = bool(_proxy_url)

        async with await asyncio.to_thread(
            get_scraping_client,
            follow_redirects=False,
            timeout=30.0,
            headers=dict(_HTTP_HEADERS),
            rotate_ua=True,
            pin_hosts=_pins if _pins and not _via_proxy else None,
            proxy=_proxy_url,
        ) as client:
            _max_bytes = _fetch_max_bytes()
            response, html_raw, _trunc = await _get_capped(
                client, final_url, _max_bytes, validated_ips=validated_ips,
                via_proxy=_via_proxy, require_peer=True,
            )
            try:
                assert_peer_public(
                    response,
                    url=final_url,
                    validated_ips=validated_ips,
                    via_proxy=_via_proxy,
                )
            except SSRFError as exc:
                return _failed_exception(exc, url)
            # Retry with backoff on 429/403 (bot wall) AND 5xx (transient)
            # origin errors) — industry-standard: a 502/503 deserves one or
            # two retries with jitter before the recovery cascade fires.
            import asyncio as _asyncio
            import random as _random

            for _retry in range(3):
                if response.status_code not in (429, 403, 500, 502, 503, 504):
                    break
                delay = 1.5 * (_retry + 1) + _random.uniform(0, 0.75)
                logger.info(
                    "[read_url] HTTP %d on %s — retry %d/3 in %.1fs",
                    response.status_code, final_url, _retry + 1, delay,
                )
                await _asyncio.sleep(delay)
                # Rotate UA (+ proxy exit IP when configured) for the retry.
                if getattr(client, "headers", None) is not None:
                    client.headers["user-agent"] = _random_ua()
                response, html_raw, _trunc = await _get_capped(
                    client, final_url, _max_bytes, validated_ips=validated_ips,
                    via_proxy=_via_proxy, require_peer=True,
                )
                try:
                    assert_peer_public(
                        response,
                        url=final_url,
                        validated_ips=validated_ips,
                        via_proxy=_via_proxy,
                    )
                except SSRFError as exc:
                    return _failed_exception(exc, url)
            for _ in range(3):
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                redirect_url = response.headers.get("location", "")
                if not redirect_url:
                    break
                if not redirect_url.startswith(("http://", "https://")):
                    redirect_url = str(httpx.URL(final_url).join(redirect_url))
                require_scope(redirect_url)
                try:
                    from kazma_core.security.ssrf import SSRFError, validate_url

                    validated_ips = await dns_worker.validate_public_url(redirect_url, validator=validate_url) or ()
                except SSRFError as exc:
                    return _failed(FetchErrorCode.SECURITY, f"Error: Redirect blocked (SSRF): {exc}")
                except ValueError as exc:
                    return _failed(FetchErrorCode.INVALID_URL, f"Error: Redirect target invalid — {exc}")
                _rh = (_urlparse(redirect_url).hostname or "").lower()
                if _rh and validated_ips and not _via_proxy:
                    _pins[_rh] = validated_ips[0]
                response, html_raw, _trunc = await _get_capped(
                    client, redirect_url, _max_bytes, validated_ips=validated_ips,
                    via_proxy=_via_proxy, require_peer=True,
                )
                try:
                    assert_peer_public(
                        response,
                        url=redirect_url,
                        validated_ips=validated_ips,
                        via_proxy=_via_proxy,
                    )
                except SSRFError as exc:
                    return _failed(FetchErrorCode.SECURITY, f"Error: Redirect blocked (SSRF): {exc}")
                final_url = redirect_url

            if response.status_code in (301, 302, 303, 307, 308):
                return _failed(FetchErrorCode.REDIRECT, "Error: Redirect limit exceeded or redirect Location missing.")

            # Content-type gate: binary payloads (PDF, images, archives) are
            # not text-extractable — fail fast with a clear pointer instead of
            # feeding megabytes of binary into the extractor.
            raw_status = getattr(response, "status_code", 200)
            status_code = raw_status if isinstance(raw_status, int) else 200
            # Hoisted so the 4xx JSON branch below can read it (a 404 JSON
            # body is a valid registry answer — see the JSON short-circuit).
            _ct = response.headers.get("content-type", "")
            if not isinstance(_ct, str):
                _ct = ""
            if status_code < 400:
                if not _is_textual_content_type(_ct):
                    return _failed(FetchErrorCode.UNSUPPORTED_CONTENT, f"Error: URL returned non-text content ({_ct or 'unknown'}) — "
                        "not extractable as a page. If this is a document you need, "
                        "save it with read_url_to_file or add a document-ingest path.")

            html = html_raw
            if _trunc:
                logger.info(
                    "[read_url] body capped at %d bytes for %s", _max_bytes, final_url
                )

            # JSON short-circuit (2026-08-21 domain-sweep incident): JSON
            # bodies must NEVER enter the HTML pipeline — trafilatura discards
            # them, the empty extract is misread as a JS shell, and recovery
            # launches Playwright (~10s) for what was a fast API call
            # (RDAP / oEmbed / Twitter info.json). Return the JSON as text.
            if status_code < 400 and _is_json_payload(_ct, html):
                return _succeeded(html, quality="json")

            if (not _ct or "html" in _ct.lower()) and (await parse_document(html, final_url, login_only=True))['login']:
                return _failed(FetchErrorCode.LOGIN, "The page requires login; credentials were not supplied. "
                               "Use a public page or an explicitly supported authenticated source.")

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
                    return _succeeded(recovered, recovery=True)
                return _failed(FetchErrorCode.CHALLENGE, f"Error: Server returned HTTP {status_code} for {final_url} "
                        "(bot protection or access denied). "
                        "Local recovery did not yield readable content. "
                        "Hosted services require explicit acquisition policy permission. "
                        "Check Chromium installation "
                        f"(the web extra: {extra_install_hint('web')}; then "
                        "`playwright install chromium`).")

            if status_code >= 400 and not _looks_like_bot_block(html, status_code):
                # JSON APIs answer with status codes: RDAP returns **404 with
                # a JSON body for a FREE/unregistered domain** — that body IS
                # the answer, and "recovering" it via Firecrawl/Playwright
                # burns 10s+ per name launching a browser to re-fetch a 404
                # (2026-08-21 domain-sweep incident). Return JSON bodies
                # directly; for non-JSON, only recover statuses a browser can
                # plausibly fix (403/429 bot walls, 5xx) — a 404/410 means
                # the resource does not exist.
                if _is_json_payload(_ct, html):
                    return _succeeded(html, quality="json")
                if status_code in (404, 410):
                    return _failed(FetchErrorCode.HTTP, f"Error: HTTP {status_code} for {final_url} — the resource "
                        "does not exist (no recovery attempted: a browser cannot "
                        "invent a missing page). If this was a registry/availability "
                        "check, 404 usually means FREE/unregistered.")
                try:
                    response.raise_for_status()
                except Exception as exc:
                    # Last chance: paid/proxy backends may still reach the page
                    recovered = await _recover_hard_page(
                        final_url, why=f"http_{status_code}"
                    )
                    if recovered:
                        return _succeeded(recovered, recovery=True)
                    return _failed_exception(exc, final_url)

    except (ConnectionError, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
        recovered = await _recover_hard_page(final_url, why="connection_error")
        if recovered:
            return _succeeded(recovered, recovery=True)
        return _failed_exception(exc, final_url)
    except (TimeoutError, httpx.TimeoutException) as exc:
        recovered = await _recover_hard_page(final_url, why="timeout")
        if recovered:
            return _succeeded(recovered, recovery=True)
        return _failed_exception(exc, final_url)
    except OSError as exc:
        return _failed_exception(exc, final_url)
    except Exception as exc:
        return _failed_exception(exc, final_url)

    is_html = not _ct or "html" in _ct.lower()
    if is_html:
        trace = active_trace.get()
        parsed = await parse_document(html, final_url, profile='knowledge' if trace and trace.profile == 'knowledge' else 'generic')
        text, links, links_complete = parsed['text'], parsed['links'], parsed['links_complete']
    else:
        text = html[:_HARD_MAX]
        links, links_complete = (), True
    trace = active_trace.get()
    if trace is not None and trace.http_source is not None:
        text_capped = trace.extract_truncated if is_html else len(html) > _HARD_MAX
        reasons = trace.http_source.truncation_reasons + (("text_limit",) if text_capped else ())
        trace.http_source = replace(trace.http_source, truncated=bool(reasons),
                                    truncation_reasons=reasons, links=links,
                                    links_complete=links_complete and not trace.http_source.truncated)

    # Knowledge needs lazy tab panels; discovery needs SPA navigation even
    # when an initial static article is already readable.
    enrich_tabs = bool(trace and trace.profile == "knowledge" and trace.dynamic_tabs)
    enrich_links = bool(trace and trace.profile == "discovery" and is_html and any(
        marker in html[:16000].lower() for marker in
        ("_next/static", "__next", "docusaurus", "gitbook", "gatsby", "data-reactroot", "app-root")))
    if not tried_playwright and (enrich_tabs or enrich_links or _should_try_playwright(html, status_code, extracted=text)):
        tried_playwright = True
        logger.info(
            "[read_url] Thin/empty extract or JS shell for %s (%d chars text / %d html)",
            final_url,
            len(text),
            len(html),
        )
        recovered = await _recover_hard_page(
            final_url, why=f"thin_extract text={len(text)} html={len(html)}"
        )
        if recovered and (enrich_tabs or enrich_links or len(recovered) > len(text)):
            return _succeeded(recovered, recovery=True)
        if enrich_tabs:
            return _failed(FetchErrorCode.UNAVAILABLE,
                           "Dynamic documentation panels could not be verified. Check local Chromium; incomplete tabs were not indexed.")
        if enrich_links and trace is not None and trace.http_source is not None:
            source = trace.http_source
            trace.http_source = replace(source, truncated=True, links_complete=False,
                                        truncation_reasons=source.truncation_reasons + ("navigation_enrichment_unavailable",))

    if not text:
        if not tried_playwright:
            recovered = await _recover_hard_page(final_url, why="empty_extract")
            if recovered:
                return _succeeded(recovered, recovery=True)
        return _failed(FetchErrorCode.EMPTY, f"Error: Could not extract readable content from {final_url}. "
            "The page may be empty, require JavaScript, or block automated access. "
            "Local recovery did not yield readable content. "
            "Hosted services require explicit acquisition policy permission. "
            f"Check the web extra ({extra_install_hint('web')}) "
            "and run `playwright install chromium`.")

    return _succeeded(text, quality="thin" if is_html and _is_thin_extraction(text, html) else "usable")


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
    result = await fetch_result(url, purpose="chat")
    if not result.ok:
        return result.legacy_text() + "\n" + result.notice()
    full = result.text
    window = _slice_window(full, offset=int(offset or 0), max_chars=max_chars)
    # Fenced as untrusted (audit F-09). A fetched page is the single largest
    # source of attacker-controlled text in the system, and it used to reach
    # the model raw — despite the README claiming all retrieved context is
    # fenced. The window header stays outside the fence so paging metadata is
    # still readable as our own output.
    return result.notice() + "\n" + fence_untrusted(window, source=f"web:{result.final_url or url}")


def _workspace_root() -> Path:
    # The binding ladder ends in the default sandbox under data_dir() and does
    # not raise short of an unwritable data dir; the old CWD-relative
    # fallback only ever fired then, and pointed somewhere else entirely.
    from kazma_core.tools.file_write import _get_workspace

    return _get_workspace().resolve()


# Public legacy text adapter. New product consumers use fetch_result (typed)
# through web_acquire.fetch_text; both enter exactly the same ladder.
fetch_full_text = _fetch_full_text


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


async def save_url_extract(
    url: str, path: str | None = None, *, allow_partial: bool = False, receipt_cb=None,
) -> ExtractSaveResult:
    """Acquire once and atomically publish without clobbering workspace files."""
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.web_acquire.crawl_jobs import call

    try:
        root = await call(_workspace_root)
    except OSError as exc:
        result = FetchResult(ok=False, url=url, error=f'Workspace unavailable ({type(exc).__name__}); acquisition was not started.',
                             error_code=FetchErrorCode.WORKSPACE)
        return ExtractSaveResult(result, error=result.error, error_code=result.error_code)
    async with workspace_path_scope(root):
        return await _save_url_extract(url, path, allow_partial=allow_partial, root=root, receipt_cb=receipt_cb)


async def _save_url_extract(url, path, *, allow_partial, root, receipt_cb=None):
    from kazma_core.web_acquire.crawl_jobs import call

    result = await fetch_result((url or "").strip(), purpose="research_save")
    if receipt_cb is not None:
        await receipt_cb(result)  # durable evidence must commit before publication
    if not result.ok:
        return ExtractSaveResult(result, error=result.error, error_code=result.error_code)
    if result.truncated and not allow_partial:
        return ExtractSaveResult(result, error="Partial acquisition was not saved as a complete research source.",
                                 error_code=FetchErrorCode.LIMIT)
    try:
        target = await call(_safe_workspace_path, path, url)
        if isinstance(target, str):
            return ExtractSaveResult(result, error=target.removeprefix("Error: "),
                                     error_code=FetchErrorCode.WORKSPACE)
        if await call(target.exists):
            return ExtractSaveResult(result, error=f"Refusing to overwrite existing file `{target.name}`. Use a different path.",
                                     error_code=FetchErrorCode.EXISTS)
        header = (
            f"# Source: {result.final_url or url}\n"
            f"# Requested source: {url}\n"
            f"# Backend: {result.backend or 'unknown'}\n"
            f"# Partial: {result.truncated}\n"
            f"# Limits: {', '.join(result.truncation_reasons) or 'none observed'}\n"
            f"# Saved: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            f"# Chars: {len(result.text)}\n\n"
        )
        from kazma_core.tools.research_files import create_extract

        await call(create_extract, target, header + result.text)
    except FileExistsError:
        return ExtractSaveResult(result, error="Refusing to overwrite an existing extract. Use a different path.",
                                 error_code=FetchErrorCode.EXISTS)
    except OSError as exc:
        logger.warning("[read_url] extract save failed: %s", exc)
        return ExtractSaveResult(result, error=f"Could not save extract ({type(exc).__name__}). Check workspace storage.",
                                 error_code=FetchErrorCode.STORAGE)
    try:
        relative = str(target.relative_to(root))
    except ValueError:
        relative = str(target)
    return ExtractSaveResult(result, path=relative, absolute_path=str(target),
                             saved_bytes=len((header + result.text).encode("utf-8")))


async def read_url_to_file(url: str, path: str | None = None) -> str:
    """Save a public extract without overwriting; report partial content visibly."""
    saved = await save_url_extract(url, path=path, allow_partial=True)
    if not saved.ok:
        return f"Error: {saved.error or 'Could not save extract.'}\n{saved.acquisition.notice()}"
    full, rel = saved.acquisition.text, saved.path
    n_chunks = max(1, (len(full) + DEFAULT_CHUNK_SIZE - 1) // DEFAULT_CHUNK_SIZE)
    extent = "partial" if saved.acquisition.truncated else "full"
    return (
        f"Saved {extent} extract ({len(full)} chars) → `{rel}`\n"
        f"{saved.acquisition.notice()}\n"
        f"Suggested next steps:\n"
        f"- list_research_chunks(path='{rel}')  # ~{n_chunks} chunks @ {DEFAULT_CHUNK_SIZE} chars\n"
        f"- read_research_chunk(path='{rel}', chunk_index=0)\n"
        f"- digest_research_file(path='{rel}')  # multi-chunk digest (context-safe)\n"
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
    # Kazma's own databases are refused here as at every other door
    # (store_registry, AGENTS.md section 37): on an install whose workspace
    # holds kazma-data/, these read-tier readers returned raw store bytes
    # that file_read refuses (audit 2026-10-26 AUD-086).
    from kazma_core.store_registry import is_kazma_store, store_refusal

    if is_kazma_store(p):
        return "Error: " + store_refusal(p, door="research readers")
    if not p.is_file():
        return f"Error: file not found: {path}"

    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith(("# Source:", "# Requested source:", "# Backend:", "# Partial:",
                            "# Limits:", "# Crawl seed:", "# Depth:", "# Saved:", "# Chars:")):
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
    loaded = await asyncio.to_thread(_load_research_body, path)
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
    header = [
        f"# Research chunks for `{path}`",
        f"Total chars: {total} · chunk_size: {size} · chunks: {n}",
        "",
    ]
    # 120-char previews of remote-authored text — fenced below (F-2).
    lines: list[str] = []
    for i in range(n):
        start = i * size
        end = min(total, start + size)
        preview = body[start : start + 120].replace("\n", " ")
        lines.append(f"## chunk_index={i}  [{start}:{end}]")
        lines.append(f"  {preview}…")
        lines.append("")
    footer = (
        f"\nRead one chunk: read_research_chunk(path='{path}', "
        f"chunk_index=0, chunk_size={size})"
    )
    return (
        "\n".join(header)
        + fence_untrusted("\n".join(lines), source=f"research_index:{path}")
        + footer
    )


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
    loaded = await asyncio.to_thread(_load_research_body, path)
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
    # Saved research files hold fetched page text, so a chunk is still
    # remote-authored content even though it now comes off local disk (F-09).
    return (
        f"[chunk {idx}/{n - 1} chars {start}:{end} of {total}{next_hint}]\n\n"
        + fence_untrusted(piece, source="research_chunk")
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
    loaded = await asyncio.to_thread(_load_research_body, path)
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
    header = [
        f"# Extractive summary of `{path}`",
        f"Total chars: {total} · chunks: {n} (showing up to {limit})",
        "",
    ]
    # Remote-authored headings + 200-char previews — fenced below (F-2).
    lines: list[str] = []
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
    footer = (
        "\nNext: read_research_chunk for important indices, "
        "or digest_research_file for a full digest."
    )
    return (
        "\n".join(header)
        + fence_untrusted("\n".join(lines), source=f"research_summary:{path}")
        + footer
    )


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
    loaded = await asyncio.to_thread(_load_research_body, path)
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
    header: list[str] = [
        f"# Research digest of `{path}`",
        f"Source chars: {total} · chunks processed: {n} · output cap: {out_cap}",
        "",
        "## Key points (extractive)",
        "",
    ]
    # Everything appended to `parts` below is verbatim remote-authored text
    # and is fenced before it is returned (audit 2026-09-16 F-2). Keep the
    # trusted scaffolding in `header`/`footer`, never in here.
    parts: list[str] = []

    # Budget per chunk for the digest body
    per = max(80, min(400, out_cap // max(n, 1)))
    used = sum(len(p) for p in header)

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

    footer = (
        "\nDigest is extractive only. For quotes/details: "
        f"read_research_chunk(path='{path}', chunk_index=N)."
    )
    body = "\n".join(parts)
    if len(body) > out_cap:
        body = body[:out_cap] + "\n[digest hard-capped]"
    # The digest is verbatim sentences lifted out of a fetched page. It used
    # to be returned raw while its sibling read_research_chunk fenced — and
    # the tool descriptions steer the model to THIS one for research, so the
    # recommended path was the unfenced one (audit 2026-09-16 F-2).
    return "\n".join(header) + fence_untrusted(body, source=f"research_digest:{path}") + footer
