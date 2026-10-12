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


def _worker_checks(capacity):
    definitions = (
        ('dns', 'acquisition_dns', 'DNS validation: 4 owned workers / 16 queued / 10s admission; 5s deadline / 256MiB memory / 16KiB output.'),
        ('parsers', 'acquisition_parsers', 'HTML parsing: 2 owned workers / 8 queued / 10s admission; 15s deadline / 768MiB memory / 24MiB input / 4MiB output.'),
        ('acquisitions', 'acquisition_search', 'Owned search: 90s backend chain / at most 30s per child; HTTP workers 512MiB memory / 1MiB response / 512KiB receipt; no ambient proxy.'),
    )
    return [{'id': ident, 'ok': not capacity[pool]['quarantined'],
        'level': 'error' if capacity[pool]['quarantined'] else 'info',
        'message': f"{pool} capacity quarantined after uncertain cleanup: {capacity[pool]['quarantined']}; check cleanup logs."
            if capacity[pool]['quarantined'] else message} for pool, ident, message in definitions]


def _accounting_check(policy, job_id):
    from kazma_core.web_acquire import accounting, credits
    from kazma_core.web_acquire.result import AcquisitionError

    try:
        # An operator switching back to local-only must still be able to inspect
        # an earlier remote job. This reads cached evidence without granting egress.
        evidence = accounting.snapshot(job_id) if policy.mode != 'local_only' or job_id else None
        if evidence is not None:
            evidence['provider_balances'] = credits.snapshot()
        return evidence, {'id': 'acquisition_accounting', 'ok': True, 'level': 'warn',
            'message': 'Remote requests require durable job/UTC-day reservations; failures and unknown outcomes consume allowance. '
                       'Provider balances are cached separately; per-request charges are unknown. Reservations are request upper bounds, not financial spend caps.'}
    except AcquisitionError as exc:
        return None, {'id': 'acquisition_accounting', 'ok': False, 'level': 'error', 'message': str(exc)}


def _acquisition_status(policy, allowances, durable_jobs, capacity):
    usage = allowances['provider_usage'] if allowances else None
    return {"mode": policy.mode, "allowances": allowances, "crawl_jobs": durable_jobs,
            "resources": capacity,
            "remote_providers": sorted(policy.providers) if policy.mode != "local_only" else [],
            "usage_available": bool(usage), "usage": usage,
            "credits_available": bool(allowances and any(row['fresh'] for row in allowances['provider_balances'])),
            "credits": None}


def research_readiness(*, probe_search: bool = False, job_id: str | None = None) -> dict[str, Any]:
    """Return a structured readiness report for deep research.

    Args:
        probe_search: When explicitly True, run a tiny live search (network).
            Default False keeps status, health and pipeline preflight local.
            Environment variables never override an explicit cached read.
    """
    from kazma_core.diagnostic_scope import read_only_diagnostic

    if probe_search is True:
        return _research_readiness(probe_search=True, job_id=job_id)
    with read_only_diagnostic('cached research readiness'):
        return _research_readiness(probe_search=False, job_id=job_id)


def _research_readiness(*, probe_search, job_id):
    checks: list[dict[str, Any]] = []
    ok = True
    from kazma_core.web_acquire.policy import AcquisitionPolicyError, acquisition_policy

    try:
        policy = acquisition_policy()
    except AcquisitionPolicyError as exc:
        return {"ok": False, "ready": False,
                "checks": [{"id": "acquisition_policy", "ok": False, "level": "error", "message": str(exc)}],
                "hints": [str(exc)]}
    checks.append({"id": "acquisition_policy", "ok": True, "level": "info",
                   "message": f"Page acquisition: {policy.mode}; allowed remote providers: {', '.join(sorted(policy.providers)) if policy.mode != 'local_only' else 'none'}"})
    from kazma_core.web_acquire.resources import resource_snapshot

    from kazma_core.web_acquire.origins import origin_scheduler

    origins = origin_scheduler().snapshot()
    from kazma_core.web_acquire.browser_isolation import capability
    isolation = capability()
    checks.append({'id': 'browser_network_isolation', 'ok': isolation['mode'] == 'required',
        'level': 'info' if isolation['mode'] == 'required' else 'warning',
        'message': ('Strict OS network isolation is required; unsupported hosts refuse browser launch. HTTP extraction remains available.'
                    if isolation['mode'] == 'required' else isolation['compatibility_warning'])})
    checks.append({'id': 'origin_scheduler', 'ok': True, 'level': 'info',
                   'message': f"HTTP origins: {origins['active']} active origin reservations, {origins['waiting']} waiting, "
                              f"{origins['cooling']} cooling; at most 2 requests and 32 waiters per origin. "
                              "Cooldowns are shared in this process; durable scheduling is not yet available."})
    capacity = resource_snapshot()
    checks.append({'id': 'research_capacity', 'ok': True, 'level': 'info',
        'message': f"Research admission in this process: {capacity['research']['active']}/4 active pipelines, "
                   f"{capacity['research_sessions']['active']}/4 background sessions; no waiting queue. "
                   "Pipeline scope is frozen; the cooperative execution deadline is 30 minutes. "
                   "This does not establish durable pipeline recovery or native filesystem deadlines."})
    quarantined = capacity["browsers"]["quarantined"]
    checks.append({"id": "acquisition_capacity", "ok": not quarantined,
                   "level": "error" if quarantined else "info",
                   "message": (f"Local browser capacity quarantined after incomplete cleanup: {quarantined}. Check cleanup logs."
                               if quarantined else
                               f"Local admission limits active: {capacity['browsers']['capacity']} browser jobs, "
                               f"{capacity['acquisitions']['capacity']} acquisitions plus "
                               f"{capacity['browser_owner_acquisitions']['capacity']} reserved for browser owners; "
                               "queues and admission waits are bounded.")})
    if quarantined:
        ok = False
    worker_checks = _worker_checks(capacity)
    checks.extend(worker_checks)
    if any(not check['ok'] for check in worker_checks):
        ok = False
    from kazma_core.web_acquire.crawl_jobs import inspect_jobs, projection
    from kazma_core.web_acquire.result import AcquisitionError

    durable_jobs = None
    try:
        durable_jobs = projection(job_id) if job_id else inspect_jobs()
        checks.append({'id': 'durable_crawls', 'ok': True, 'level': 'info',
            'message': 'KB and crawl_site frontiers/outcomes persist before acquisition; four active jobs, 24 retained, 8MiB registry cap. Interrupted work resumes explicitly; uncertain effects are not replayed. Research pipelines remain separate.'})
    except AcquisitionError as exc:
        checks.append({'id': 'durable_crawls', 'ok': False, 'level': 'error', 'message': str(exc)})
        ok = False

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
                "SearXNG URL configured"
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
    from kazma_core.proxy.client import get_active_proxy_url
    from kazma_core.proxy.config import proxy_config_scope, proxy_value
    from kazma_core.web_acquire.result import AcquisitionError

    try:
        proxy_name, proxy_on = 'none', False
        if policy.permits('proxy'):
            with proxy_config_scope():
                proxy_on = bool(get_active_proxy_url())
                proxy_name = proxy_value('provider', 'none')
    except AcquisitionError as exc:
        ok = False
        checks.append({'id': 'proxy', 'ok': False, 'level': 'error', 'message': str(exc)})
    else:
        checks.append({'id': 'proxy', 'ok': True, 'level': 'info',
                       'message': f'Proxy Provider active: {proxy_name}' if proxy_on
                       else 'Proxy Provider off (direct scrape)'})

    # ── Optional hard-page backends ─────────────────────────────────
    firecrawl = policy.permits("firecrawl") and bool((os.environ.get("KAZMA_FIRECRAWL_API_KEY") or "").strip())
    jina = policy.permits("jina") and (os.environ.get("KAZMA_JINA_READER") or "").strip().lower() not in (
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

    allowances, allowance_check = _accounting_check(policy, job_id)
    checks.append(allowance_check)
    ok = ok and allowance_check['ok']

    # ── Optional live micro-search ──────────────────────────────────
    search_note = "live probe skipped (request probe_search=True explicitly)"
    search_live_ok = None
    if probe_search is True:
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
        "acquisition": _acquisition_status(policy, allowances, durable_jobs, capacity),
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
