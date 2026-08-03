"""Research stack readiness probe (industry preflight).

Call before a deep run (or from ``GET /api/research/ready``) so operators
see *why* search/fetch may fail instead of a multi-minute opaque hang.
"""

from __future__ import annotations

import logging
import os
from typing import Any

__all__ = ["research_readiness", "format_readiness_message"]

logger = logging.getLogger(__name__)


def research_readiness(*, probe_search: bool = False) -> dict[str, Any]:
    """Return a structured readiness report for deep research.

    Args:
        probe_search: When True, run a tiny live search (network). Default
            False so UI/API boot stays cheap; pipeline preflight may set True
            only when env ``KAZMA_RESEARCH_PREFLIGHT_LIVE=1``.
    """
    checks: list[dict[str, Any]] = []
    ok = True

    # ── Search backends ─────────────────────────────────────────────
    searx_url = (os.environ.get("KAZMA_SEARXNG_URL") or "").strip()
    try:
        from kazma_core.config_store import get_config_store

        cfg = get_config_store().get("search.searxng_url")
        if cfg and str(cfg).strip():
            searx_url = searx_url or str(cfg).strip()
    except Exception:
        pass

    checks.append(
        {
            "id": "searxng_configured",
            "ok": bool(searx_url),
            "level": "info" if searx_url else "warn",
            "message": (
                f"SearXNG URL set ({searx_url})"
                if searx_url
                else "SearXNG not configured — will try DuckDuckGo/Bing (less reliable)"
            ),
        }
    )

    ddg_ok = False
    try:
        import duckduckgo_search  # noqa: F401

        ddg_ok = True
    except ImportError:
        pass
    checks.append(
        {
            "id": "duckduckgo_pkg",
            "ok": ddg_ok,
            "level": "info" if ddg_ok else "warn",
            "message": (
                "duckduckgo_search package available"
                if ddg_ok
                else "duckduckgo_search not installed"
            ),
        }
    )

    # ── Proxy ───────────────────────────────────────────────────────
    proxy_name = "none"
    proxy_on = False
    try:
        from kazma_core.proxy.registry import get_proxy_provider

        p = get_proxy_provider()
        proxy_on = bool(p.is_configured())
        proxy_name = getattr(p, "name", "none") or "none"
    except Exception as exc:
        checks.append(
            {
                "id": "proxy",
                "ok": True,
                "level": "info",
                "message": f"Proxy check skipped: {exc}",
            }
        )
    else:
        checks.append(
            {
                "id": "proxy",
                "ok": True,
                "level": "info",
                "message": (
                    f"Proxy Provider active: {proxy_name}"
                    if proxy_on
                    else "Proxy Provider off (direct scrape)"
                ),
            }
        )

    # ── Optional hard-page backends ─────────────────────────────────
    firecrawl = bool((os.environ.get("KAZMA_FIRECRAWL_API_KEY") or "").strip())
    jina = (os.environ.get("KAZMA_JINA_READER") or "").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )
    checks.append(
        {
            "id": "hard_page_backends",
            "ok": True,
            "level": "info",
            "message": (
                f"Firecrawl={'on' if firecrawl else 'off'}; "
                f"Jina recovery={'allowed' if jina else 'disabled'}"
            ),
        }
    )

    # ── Optional live micro-search ──────────────────────────────────
    search_note = "live probe skipped (set KAZMA_RESEARCH_PREFLIGHT_LIVE=1)"
    search_live_ok = None
    if probe_search or (os.environ.get("KAZMA_RESEARCH_PREFLIGHT_LIVE") or "").strip() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        try:
            import asyncio

            from kazma_core.web_acquire import search as web_search

            async def _probe() -> Any:
                return await web_search("kazma research readiness", max_results=2)

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                # Sync caller inside async — skip to avoid deadlock
                search_note = "live probe skipped (async loop running)"
            else:
                sr = asyncio.run(_probe())
                search_live_ok = bool(sr.ok and (sr.urls or sr.markdown))
                search_note = (
                    f"live search ok ({len(sr.urls or [])} urls)"
                    if search_live_ok
                    else f"live search weak: {sr.error or 'empty'}"
                )
                if not search_live_ok:
                    ok = False
        except Exception as exc:
            search_live_ok = False
            search_note = f"live search failed: {type(exc).__name__}: {exc}"[:200]
            ok = False
        checks.append(
            {
                "id": "search_live",
                "ok": bool(search_live_ok),
                "level": "error" if search_live_ok is False else "info",
                "message": search_note,
            }
        )
    else:
        checks.append(
            {
                "id": "search_live",
                "ok": True,
                "level": "info",
                "message": search_note,
            }
        )

    # Soft fail only when no search path at all is plausible
    if not searx_url and not ddg_ok:
        ok = False
        checks.append(
            {
                "id": "search_path",
                "ok": False,
                "level": "error",
                "message": (
                    "No search path: configure KAZMA_SEARXNG_URL or install "
                    "duckduckgo_search"
                ),
            }
        )

    return {
        "ok": ok,
        "ready": ok,
        "checks": checks,
        "hints": _hints(checks),
    }


def _hints(checks: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    by_id = {c["id"]: c for c in checks}
    if not by_id.get("searxng_configured", {}).get("ok"):
        out.append(
            "For production research, run SearXNG "
            "(docker compose --profile search) and set KAZMA_SEARXNG_URL"
        )
    live = by_id.get("search_live")
    if live and live.get("ok") is False:
        out.append(
            "Search returned no results — check network, proxy, or SearXNG JSON format"
        )
    if not by_id.get("search_path", {}).get("ok", True):
        out.append("Install duckduckgo_search or configure SearXNG before deep research")
    return out


def format_readiness_message(report: dict[str, Any]) -> str:
    """One-line status for progress logs / Telegram."""
    if report.get("ready"):
        return "Research stack ready"
    hints = report.get("hints") or []
    errs = [
        c.get("message")
        for c in (report.get("checks") or [])
        if c.get("level") == "error"
    ]
    parts = errs or hints or ["Research stack not fully ready"]
    return str(parts[0])[:200]
