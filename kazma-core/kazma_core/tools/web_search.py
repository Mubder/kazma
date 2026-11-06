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

import logging
import warnings
from typing import Any


from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

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
    from kazma_core.web_acquire.policy import AcquisitionPolicyError

    if isinstance(exc, AcquisitionPolicyError):
        return f"Error: policy_error — {exc}"
    if isinstance(exc, AcquisitionError):
        return f"Error: {exc.code} — {exc}"
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
    import ipaddress
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(base).hostname or '').lower().rstrip('.')
    except ValueError:
        return False
    if host in {'localhost', 'host.docker.internal', 'searxng', '0.0.0.0'}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _note_rank(note: str) -> int:
    """Lower = more informative. Keeps a REACHABLE-instance note from being
    overwritten by a later dead candidate's connection error — the loop tries
    5 bases and the last one (127.0.0.1:8080, refused) used to hide the real
    story (empty@8088, engines suspended) with a bogus 'unavailable'
    (2026-08-27 'why is Kazma not using my SearXNG' report)."""
    if note.startswith("searxng:empty@"):
        return 0
    if note.startswith("searxng:http_"):
        return 1
    return 2


def _better_note(current: str, new: str) -> str:
    return new if _note_rank(new) <= _note_rank(current) else current


def _searxng_search(query: str, max_results: int) -> tuple[list[dict[str, str]] | None, str]:
    """Try SearXNG across candidate bases. Returns (results|None, status_note)."""
    import time as _time


    now = _time.time()
    if _searxng_cache.get("dead_until", 0) > now and not _searxng_cache.get("base"):
        return None, _searxng_cache.get("note") or "searxng:skipped (recently unavailable)"

    candidates = _searxng_candidate_bases()
    # Prefer last known good base first
    good = _searxng_cache.get("base")
    if good and good in candidates:
        candidates = [good] + [c for c in candidates if c != good]

    from kazma_core.web_acquire.search_http import run_http_search

    if len(candidates) > 8:
        raise AcquisitionError(FetchErrorCode.LIMIT, 'SearXNG discovery exceeds eight service endpoints.')
    last_note = "searxng:unavailable"
    reached_any = False
    for base in candidates:
        from urllib.parse import urlsplit

        parsed = urlsplit(base)
        if (len(base) > 8192 or parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise AcquisitionError(FetchErrorCode.INVALID_URL, 'Invalid SearXNG endpoint; configure an HTTP(S) base without credentials or query.')
        receipt = run_http_search('searxng', query, max_results, base=base, local=_is_loopback_base(base))
        reached_any |= receipt['reached']
        results = receipt['results']
        status = receipt['status']
        if status == 'ok':
            _searxng_cache.update(base=base, dead_until=0.0)
            note = f"searxng:ok@{base}" if results else f"searxng:empty@{base}{receipt['detail']}"
            if results:
                _searxng_cache['note'] = note
                _searxng_answered()
                return results, note
        elif status == 'http':
            note = f"searxng:http_{receipt['detail']}@{base}"
        else:
            note = f"searxng:unavailable@{base} ({status})"
        last_note = _better_note(last_note, note)
    if not reached_any:
        _searxng_cache.update(base=None, dead_until=now + 60.0)
    _searxng_cache['note'] = last_note
    _note_searxng_silent(now, last_note)
    return None, last_note


#: SearXNG answering nothing for this long is a page (resilience plan F1).
SEARXNG_SILENT_PAGE_S = 3600.0


def _note_searxng_silent(now: float, note: str) -> None:
    """Count how long SearXNG has answered nothing; page once at an hour.

    Its answer resets the count (``_searxng_answered``). The page says what
    SearXNG last answered and that searches go to the next service.
    """
    since = _searxng_cache.setdefault("silent_since", now)
    if now - since >= SEARXNG_SILENT_PAGE_S and not _searxng_cache.get("silent_paged"):
        _searxng_cache["silent_paged"] = True
        from kazma_core.observability.ops_alerts import alert

        alert("search.searxng_silent", "Local search (SearXNG) has answered nothing for an hour",
              f"Its last answer: {note[:300]}. Searches go to the next service meanwhile. "
              "Check the SearXNG container and its engines.", severity="warn", cooldown_s=6 * 3600.0)


def _searxng_answered() -> None:
    _searxng_cache.pop("silent_since", None)
    _searxng_cache.pop("silent_paged", None)


def tavily_key() -> str:
    """Tavily's key: ``KAZMA_TAVILY_API_KEY``, else ``TAVILY_API_KEY``; empty
    when neither is set."""
    import os

    return (os.environ.get("KAZMA_TAVILY_API_KEY") or os.environ.get("TAVILY_API_KEY") or "").strip()


def _tavily_search(query: str, max_results: int, *, run: Any = None) -> tuple[list[dict[str, str]] | None, str]:
    """Tavily, the search service after SearXNG (resilience plan F1).

    A remote provider: used only when the acquisition policy names it
    (``KAZMA_ACQUISITION_MODE=local_first_with_remote_fallback`` and
    ``tavily`` in ``KAZMA_ACQUISITION_REMOTE_PROVIDERS``) and a key is set.
    Each query reserves one request of the remote allowance before it leaves,
    and its HTTP status is recorded on that reservation. An allowance that is
    spent passes the query on to the next backend.
    """
    from kazma_core.web_acquire.policy import acquisition_policy

    if not acquisition_policy().permits("tavily"):
        return None, "tavily:not permitted"
    key = tavily_key()
    if not key:
        return None, "tavily:no key"
    from kazma_core.web_acquire import accounting

    if run is None:
        from kazma_core.web_acquire.search_http import run_http_search as run
    try:
        reservation = accounting.reserve("tavily", accounting.account_fingerprint("tavily", key), units=1)
    except AcquisitionError as exc:
        logger.warning("[web_search] Tavily not asked: %s", exc)
        return None, f"tavily:{exc.code}"
    receipt = run("tavily", query, max_results, key=key)
    rows, status = receipt["results"], receipt["status"]
    answered = 200 if status == "ok" else int(receipt["detail"]) if status == "http" and receipt["detail"].isdigit() else None
    if answered is not None:
        accounting.record_response(reservation, answered)
    if status == "ok":
        return (rows, "tavily:ok") if rows else (None, "tavily:empty")
    return None, "tavily:" + ("http_" + receipt["detail"] if status == "http" else status)


def _ddg_search(query: str, max_results: int) -> tuple[list[dict[str, str]] | None, str]:
    """DuckDuckGo text search (Proxy Provider when configured)."""
    from kazma_core.proxy.client import get_active_proxy_url

    proxy_url = get_active_proxy_url()  # fail closed before constructing DDGS
    if proxy_url:
        return None, 'duckduckgo:skipped_unbounded_proxy_requests'
    return None, 'duckduckgo:skipped_opaque_origin_fanout'


def _http_backend(backend, query, max_results):
    from kazma_core.web_acquire.search_http import run_http_search

    receipt = run_http_search(backend, query, max_results)
    rows = receipt['results']
    status = receipt['status']
    if status == 'ok':
        return (rows, backend + ':ok') if rows else (None, backend + ':empty')
    suffix = 'http_' + receipt['detail'] if status == 'http' else status
    return None, backend + ':' + suffix


def _bing_search(query: str, max_results: int) -> tuple[list[dict[str, str]] | None, str]:
    """Bing fallback with bounded native HTTP, DNS and HTML parsing."""
    return _http_backend('bing', query, max_results)


def _wikipedia_search(query: str, max_results: int) -> tuple[list[dict[str, str]] | None, str]:
    """Wikipedia fallback with bounded native HTTP, DNS and JSON parsing."""
    return _http_backend('wikipedia', query, max_results)


def _answers_the_query(query: str, results: list[dict[str, str]]) -> bool:
    """Whether a backend's results are about the query at all.

    Half of them at least must hold one of the query's content words in
    their title, snippet or address. With its engines rate-limited, Bing
    answered "Kuwait independence 1961 Iraqi claim Qasim British withdrawal"
    with Telegram's download pages, and other history questions with a
    perfume shop, a sports channel and Windows help (live 2026-10-31): the
    chain stopped at the first non-empty answer and research read them as
    its sources. A query with no content word is not judged.
    """
    from kazma_core.memory.query_terms import content_terms, mentions

    terms = content_terms(query)
    if not terms or not results:
        return True
    related = sum(
        1 for row in results
        if mentions(" ".join(str(row.get(key) or "") for key in ("title", "body", "href")), terms)
    )
    return related * 2 >= len(results)


def _run_search(query: str, max_results: int) -> tuple[list[dict[str, str]], list[str], str]:
    """Run backend chain. Returns (results, attempt_notes, winning_backend).

    Continues to the next backend when the current one is unavailable,
    returns zero hits (rate-limit / soft-block on hot keywords), or answers
    with results that are not about the query (:func:`_answers_the_query`).
    """
    attempts: list[str] = []
    backends: list[tuple[str, Any]] = [
        ("searxng", _searxng_search),
        ("tavily", _tavily_search),
        ("duckduckgo", _ddg_search),
        ("bing", _bing_search),
        ("wikipedia", _wikipedia_search),
    ]
    from kazma_core.web_acquire.search_sdk import search_cancel

    from kazma_core.web_acquire.search_runtime import remaining

    for name, fn in backends:
        remaining()
        cancelled = search_cancel.get()
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError('Search cancelled')
        results, note = fn(query, max_results)
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError('Search cancelled')
        if results and not _answers_the_query(query, results):
            logger.warning(
                "[web_search] %s answered %r with %d unrelated results; trying the next backend",
                name, query, len(results),
            )
            attempts.append(f"{name}:unrelated")
            continue
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
    if len(q) > 4096:
        return "Error: search query exceeds the 4096-character limit."
    max_results = max(1, min(int(max_results or 8), 15))

    try:
        from kazma_core.web_acquire.search_runtime import run_search

        results, attempts, backend = await run_search(_run_search, q, max_results)
    except (ConnectionError, TimeoutError, OSError) as exc:
        return _friendly_error(exc)
    except Exception as exc:
        return _friendly_error(exc)

    if not results:
        logger.warning(
            "[web_search] All backends empty for %r attempts=%s", q, attempts
        )
        # Actionable empty-result guidance (rephrase / read_url / local SearXNG).
        return _format_empty(q, attempts)
    res_text = _format_results(q, results, backend=backend, attempts=attempts)
    try:
        from kazma_core.tools.research_session import record_chat_research

        record_chat_research(
            q, tool_name="web_search", result_text=res_text, result_count=len(results)
        )
    except Exception as exc:
        # The search answered; only its Research-tab entry is missing.
        logger.warning("[web_search] search not recorded in Research history: %s", exc)

    # Titles and snippets are remote-authored text (audit F-09) — a result
    # snippet is a cheap injection vector precisely because it looks like
    # metadata rather than content.
    from kazma_core.safety.prompt_fence import fence_untrusted

    return fence_untrusted(res_text, source=f"web_search:{q[:80]}")
