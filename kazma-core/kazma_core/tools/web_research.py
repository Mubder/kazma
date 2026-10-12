"""Bounded multi-page site crawl for research (same-domain spider).

Safety:
  * SSRF on every URL
  * same-domain only by default
  * max_pages / max_depth caps (env-overridable ceilings)
  * polite delay between fetches
  * no credentialed / private network targets

Not a full enterprise crawler; designed for agent research, not scraping the web.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from collections import deque
from typing import Callable
from urllib.parse import urlparse

from kazma_core.tools.read_url import (
    _default_research_subdir,
    _safe_workspace_path,
    _workspace_root,
)

__all__ = ["crawl_site"]

from kazma_core.web_acquire import fetch_text as _central_fetch
from kazma_core.web_acquire.origins import polite_requests
from kazma_core.web_acquire.robots import crawl_policy
from kazma_core.web_acquire import accounting
from kazma_core.web_acquire.result import AcquisitionError
from kazma_core.web_acquire.trace import classify_failure

logger = logging.getLogger(__name__)

_SKIP_EXT = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".zip",
    ".gz",
    ".mp4",
    ".mp3",
    ".css",
    ".js",
    ".ico",
    ".woff",
    ".woff2",
)


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


def _normalize_url(base: str, href: str) -> str | None:
    if not href or href.startswith(("#", "mailto:", "javascript:", "tel:", "data:")):
        return None
    from kazma_core.web_acquire.links import resolve_document_url

    abs_url = resolve_document_url(base, href)
    parsed = urlparse(abs_url)
    if parsed.scheme not in ("http", "https") or parsed.username is not None or parsed.password is not None:
        return None
    path_l = (parsed.path or "").lower()
    if any(path_l.endswith(ext) for ext in _SKIP_EXT):
        return None
    # Drop common tracking query noise lightly — keep full query for correctness
    return abs_url


def _same_domain(a: str, b: str) -> bool:
    ha = (urlparse(a).hostname or "").lower().removeprefix("www.")
    hb = (urlparse(b).hostname or "").lower().removeprefix("www.")
    return bool(ha) and ha == hb


def _extract_links(html: str, base_url: str) -> list[str]:
    # Lightweight href scrape (no BS4 dependency)
    hrefs = re.findall(r'''href\s*=\s*["']([^"']+)["']''', html, flags=re.I)
    out: list[str] = []
    seen: set[str] = set()
    for h in hrefs:
        u = _normalize_url(base_url, h)
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


async def _fetch_html(url: str, *, scope: Callable[[str], bool] | None = None) -> tuple[str | None, str]:
    """Return (html_or_none, final_url)."""
    try:
        from kazma_core.web_acquire.http import fetch_public

        result = await fetch_public(url, max_bytes=5_000_000, timeout=30.0, scope=scope)
        if result.response.status_code >= 400 or result.truncated:
            return None, result.url
        return result.text, result.url
    except Exception as exc:
        logger.debug("[crawl_site] html fetch failed %s: %s", url, exc)
        return None, url


async def crawl_site(
    start_url: str,
    max_pages: int | None = None,
    max_depth: int | None = None,
    same_domain_only: bool | None = None,
    delay_ms: int | None = None,
    profile: str | None = None,
    save: bool = True,
    path_prefix: str | None = None,
    respect_robots: bool | None = None,
    resume_job_id: str | None = None,
) -> str:
    """Bounded multi-page crawl starting from *start_url*.

    Args:
        start_url: Seed public URL.
        profile: Optional named cap preset (kazma_core.web_acquire.profiles)
            — ``research_brief`` (default: 8 pages/depth 2), ``research_deep``
            (20/2), ``kb_site`` (200/4), ``single_page`` (1/0). Explicit
            numeric args below win over the profile; hard env ceilings still
            clamp.
        max_pages: Max pages to fetch (default from profile; hard cap via
            ``KAZMA_CRAWL_MAX_PAGES``, max 50).
        max_depth: Link depth from seed (0 = seed only; default from
            profile; hard max 5).
        same_domain_only: Stay on seed hostname (recommended True).
        delay_ms: Pause between fetches (politeness).
        save: Save each page under workspace research dir.
        path_prefix: Optional workspace-relative folder for saves.
        respect_robots: Honor the site's robots.txt. Default ``None`` → env
            ``KAZMA_CRAWL_RESPECT_ROBOTS`` (default on). Failed robots checks
            refuse crawling; explicit opt-out is visible in acquisition receipts. Skipped pages are reported as ``blocked_robots``.

    Returns:
        Markdown index, durable job identity and page outcomes.
        Resume an interrupted job with resume_job_id and identical options/workspace.
        Completed or uncertain page effects are never replayed.
    """
    if not start_url or not str(start_url).strip():
        return "Error: No start_url provided."

    start = str(start_url).strip()
    if not start.startswith(("http://", "https://")):
        start = "https://" + start
    if len(start) > 8192:
        return "Error: Crawl seed exceeds its 8192-character URL budget."

    # Named profile supplies cap defaults; explicit args win; the hard env
    # ceilings below still clamp (presets: web_acquire.profiles).
    from kazma_core.web_acquire import get_crawl_profile, profile_to_crawl_kwargs

    _pk = profile_to_crawl_kwargs(get_crawl_profile(profile))
    if max_pages is None:
        max_pages = _pk["max_pages"]
    if max_depth is None:
        max_depth = _pk["max_depth"]
    if same_domain_only is None:
        same_domain_only = _pk["same_domain_only"]
    if delay_ms is None:
        delay_ms = _pk["delay_ms"]

    hard_pages = _env_int("KAZMA_CRAWL_MAX_PAGES", 50, lo=1, hi=50)
    hard_depth = _env_int("KAZMA_CRAWL_MAX_DEPTH", 5, lo=0, hi=5)
    try:
        max_pages = max(1, min(hard_pages, int(max_pages)))
        max_depth = max(0, min(hard_depth, int(max_depth if max_depth is not None else 2)))
        delay_ms = max(0, min(5000, int(delay_ms if delay_ms is not None else 300)))
    except (TypeError, ValueError):
        return "Error: Crawl page, depth and delay limits must be integers. No crawl was started."

    try:
        from kazma_core.security.ssrf import validate_url
        from kazma_core.web_acquire.dns_worker import validate_public_url

        await validate_public_url(start, validator=validate_url)
    except Exception as exc:
        return f"Error: {exc}"

    prefix = (path_prefix or f"{_default_research_subdir()}/crawl").strip().replace("\\", "/")
    if ".." in prefix.split("/"):
        return "Error: invalid path_prefix."

    try:
        with crawl_policy(respect=respect_robots) as job_policy:
            job_policy.enforce()
    except AcquisitionError as exc:
        return f"Error: {exc}"

    from kazma_core.ide.workspace_scope import workspace_path_scope

    from kazma_core.web_acquire.research_crawl import research_job
    from kazma_core.web_acquire.crawl_jobs import call

    workspace = await asyncio.to_thread(_workspace_root)
    options = dict(max_pages=max_pages, max_depth=max_depth, same_domain_only=bool(same_domain_only),
                   delay_ms=delay_ms, save=bool(save), path_prefix=prefix, robots=job_policy.enabled)
    durable_id = resume_job_id
    try:
        async with workspace_path_scope(workspace), research_job(start, workspace, options,
                resume_job_id=resume_job_id) as durable:
            durable_id = durable.job.job_id
            with crawl_policy(job=job_policy), accounting.remote_scope():
                pending, known, results, frontier_limited = await durable.state()
                queue: deque[tuple[str, int]] = deque(pending)
                frontier_cap = max_pages * 4
                seen: set[str] = known - {url for url, _ in pending}

                while queue and len(results) < max_pages:
                    url, depth = queue.popleft()
                    if url in seen:
                        continue
                    seen.add(url)
                    await durable.begin(url)

                    if same_domain_only and not _same_domain(start, url):
                        raise AcquisitionError('scope_blocked', 'Persisted research frontier is outside the frozen scope.')

                    try:
                        from kazma_core.security.ssrf import validate_url
                        from kazma_core.web_acquire.dns_worker import validate_public_url

                        await validate_public_url(url, validator=validate_url)
                    except Exception as exc:
                        code, _ = classify_failure(exc)
                        from kazma_core.errors import redact_secrets

                        results.append({"url": url, "status": str(code), "chars": 0, "path": "",
                                        "depth": depth, "error": redact_secrets(str(exc))[:200]})
                        await durable.settle(url, results[-1], list(queue), frontier_limited)
                        continue

                    if delay_ms and results:
                        await asyncio.sleep(delay_ms / 1000.0)

                    scope = (lambda candidate: _same_domain(start, candidate)) if same_domain_only else None
                    async with polite_requests(delay_ms / 1000):
                        fr = await _central_fetch(url, purpose="crawl", scope_check=scope)
                    await durable.receipt(url, fr)
                    final_url = fr.final_url or url
                    if scope is not None and not scope(final_url):
                        results.append({"url": url, "depth": depth, "status": "blocked_scope",
                                        "chars": 0, "path": "", "error": "Redirect outside crawl scope"})
                        await durable.settle(url, results[-1], list(queue), frontier_limited)
                        continue
                    text = fr.text
                    status = "partial" if fr.truncated else "ok"
                    chars = 0
                    rel = ""
                    if not fr.ok:
                        status = "blocked_scope" if fr.error_code == "scope_blocked" else "blocked_robots" if fr.error_code == "robots_disallowed" else "error"
                        err = (fr.error or "empty extract")[:200]
                    else:
                        chars = len(text)
                        err = f"Acquisition partial: {', '.join(fr.truncation_reasons)}" if fr.truncated else ""
                        if depth < max_depth and fr.links_complete is not True:
                            status = "partial"
                            err = (err + "; " if err else "") + "Link discovery incomplete or unavailable from the acquired document."
                        if save and fr.truncated:
                            err += "; Partial extract was not saved."
                        if save and not fr.truncated:
                            slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", final_url or url)[:60].strip("-")
                            digest = hashlib.sha256((final_url or url).encode()).hexdigest()[:8]
                            fname = f"{prefix.rstrip('/')}/{slug}-{digest}.md"
                            target = await call(_safe_workspace_path, fname, final_url or url)
                            if isinstance(target, str):
                                status = "save_error"
                                err = target
                            else:
                                header = (
                                    f"# Source: {final_url or url}\n"
                                    f"# Crawl seed: {start}\n"
                                    f"# Depth: {depth}\n"
                                    f"# Saved: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
                                    f"# Chars: {chars}\n\n"
                                )
                                from kazma_core.tools.research_files import create_extract

                                try:
                                    await call(create_extract, target, header + text)
                                    try:
                                        rel = str(target.relative_to(await asyncio.to_thread(_workspace_root)))
                                    except ValueError:
                                        rel = str(target)
                                except FileExistsError:
                                    status, err = "save_error", "File already exists; existing content was preserved."
                                except OSError as exc:
                                    logger.warning("[crawl_site] save failed: %s", exc)
                                    status, err = "save_error", f"Could not publish extract ({type(exc).__name__}); check workspace storage."

                    results.append(
                        {
                            "url": final_url or url,
                            "status": status,
                            "chars": chars,
                            "path": rel,
                            "depth": depth,
                            "error": err,
                            "acquisition": fr.notice(),
                        }
                    )

                    # Enqueue children
                    if depth < max_depth and fr.ok:
                        for candidate in fr.links:
                            link = _normalize_url(final_url, candidate)
                            if not link:
                                continue
                            if link in known:
                                continue
                            if same_domain_only and not _same_domain(start, link):
                                continue
                            if len(link) > 8192 or len(known) >= frontier_cap:
                                frontier_limited = True
                                continue
                            known.add(link)
                            queue.append((link, depth + 1))

                    await durable.settle(url, results[-1], list(queue), frontier_limited)

                partial = bool(frontier_limited or queue or any(r['status'] != 'ok' for r in results))
                await durable.finish(partial)
                ok = sum(1 for r in results if r["status"] == "ok")
                lines = [
                    f"# Crawl index — seed `{start}`",
                    f"Durable job: `{durable_id}` · status={'partial' if partial else 'completed'}",
                    f"Pages fetched: {len(results)} (ok={ok}) · max_pages={max_pages} · max_depth={max_depth} · same_domain={same_domain_only}",
                    "",
                    "| # | depth | status | chars | path | url | error | acquisition |",
                    "|---|-------|--------|-------|------|-----|-------|-------------|",
                ]
                if frontier_limited or queue:
                    lines.insert(2, f"Coverage partial: crawl frontier/page limit reached (URL cap={frontier_cap}, pending={len(queue)}).")
                for i, r in enumerate(results):
                    lines.append(
                        f"| {i} | {r.get('depth', 0)} | {r['status']} | {r['chars']} | "
                        f"`{r['path'] or '—'}` | {r['url'][:80]} | "
                        + str(r.get("error", "")).replace("|", "\\|").replace("\n", " ") + " | "
                        + str(r.get("acquisition", "")).replace("|", "\\|").replace("\n", " ") + " |"
                    )
                lines.append("")
                lines.append(
                    "Next: digest_research_file / list_research_chunks on saved paths, "
                    "or read_url_to_file for a single deep page."
                )
                if not ok:
                    lines.append(
                        "\nNo pages completed successfully. Check the page errors, local "
                        "Chromium installation and acquisition policy. Hosted services "
                        "require explicit permission; stored keys alone do not enable them."
                    )
                return "\n".join(lines)
    except AcquisitionError as exc:
        return f"Error: {exc}\nDurable job: {durable_id or 'not admitted'}. Acquisition stopped; inspect readiness before retrying."
