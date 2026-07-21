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
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from kazma_core.tools.read_url import (
    _default_research_subdir,
    _fetch_full_text,
    _safe_workspace_path,
    _workspace_root,
)

__all__ = ["crawl_site"]

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
    abs_url = urljoin(base, href)
    abs_url, _frag = urldefrag(abs_url)
    parsed = urlparse(abs_url)
    if parsed.scheme not in ("http", "https"):
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


async def _fetch_html(url: str) -> tuple[str | None, str]:
    """Return (html_or_none, final_url)."""
    try:
        import httpx
        from kazma_core.security.ssrf import SSRFError, validate_url

        validate_url(url)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        ) as client:
            r = await client.get(url)
            # Re-validate final URL after redirects
            final = str(r.url)
            try:
                validate_url(final)
            except SSRFError:
                return None, url
            if r.status_code >= 400:
                return None, final
            return r.text or "", final
    except Exception as exc:
        logger.debug("[crawl_site] html fetch failed %s: %s", url, exc)
        return None, url


async def crawl_site(
    start_url: str,
    max_pages: int = 8,
    max_depth: int = 2,
    same_domain_only: bool = True,
    delay_ms: int = 300,
    save: bool = True,
    path_prefix: str | None = None,
) -> str:
    """Bounded multi-page crawl starting from *start_url*.

    Args:
        start_url: Seed public URL.
        max_pages: Max pages to fetch (default 8; hard cap via
            ``KAZMA_CRAWL_MAX_PAGES``, max 50).
        max_depth: Link depth from seed (0 = seed only; default 2; hard max 5).
        same_domain_only: Stay on seed hostname (recommended True).
        delay_ms: Pause between fetches (politeness).
        save: Save each page under workspace research dir.
        path_prefix: Optional workspace-relative folder for saves.

    Returns:
        Markdown index of crawled pages + paths + char counts.
    """
    if not start_url or not str(start_url).strip():
        return "Error: No start_url provided."

    start = str(start_url).strip()
    if not start.startswith(("http://", "https://")):
        start = "https://" + start

    hard_pages = _env_int("KAZMA_CRAWL_MAX_PAGES", 50, lo=1, hi=50)
    hard_depth = _env_int("KAZMA_CRAWL_MAX_DEPTH", 5, lo=0, hi=5)
    try:
        max_pages = max(1, min(hard_pages, int(max_pages or 8)))
        max_depth = max(0, min(hard_depth, int(max_depth if max_depth is not None else 2)))
        delay_ms = max(0, min(5000, int(delay_ms or 300)))
    except (TypeError, ValueError):
        max_pages, max_depth, delay_ms = 8, 2, 300

    try:
        from kazma_core.security.ssrf import SSRFError, validate_url

        validate_url(start)
    except Exception as exc:
        return f"Error: {exc}"

    prefix = (path_prefix or f"{_default_research_subdir()}/crawl").strip().replace("\\", "/")
    if ".." in prefix.split("/"):
        return "Error: invalid path_prefix."

    queue: deque[tuple[str, int]] = deque([(start, 0)])
    seen: set[str] = set()
    results: list[dict[str, Any]] = []

    while queue and len(results) < max_pages:
        url, depth = queue.popleft()
        if url in seen:
            continue
        seen.add(url)

        if same_domain_only and not _same_domain(start, url):
            continue

        try:
            from kazma_core.security.ssrf import validate_url

            validate_url(url)
        except Exception:
            results.append({"url": url, "status": "blocked_ssrf", "chars": 0, "path": ""})
            continue

        if delay_ms and results:
            await asyncio.sleep(delay_ms / 1000.0)

        html, final_url = await _fetch_html(url)
        text = await _fetch_full_text(final_url if final_url else url)
        status = "ok"
        chars = 0
        rel = ""
        if text.startswith("Error:"):
            status = "error"
            err = text[:200]
        else:
            chars = len(text)
            err = ""
            if save:
                slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", final_url or url)[:60].strip("-")
                digest = hashlib.sha256((final_url or url).encode()).hexdigest()[:8]
                fname = f"{prefix.rstrip('/')}/{slug}-{digest}.md"
                target = _safe_workspace_path(fname, final_url or url)
                if isinstance(target, str):
                    status = "save_error"
                    err = target
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    header = (
                        f"# Source: {final_url or url}\n"
                        f"# Crawl seed: {start}\n"
                        f"# Depth: {depth}\n"
                        f"# Saved: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
                        f"# Chars: {chars}\n\n"
                    )
                    target.write_text(header + text, encoding="utf-8")
                    try:
                        rel = str(target.relative_to(_workspace_root()))
                    except ValueError:
                        rel = str(target)

        results.append(
            {
                "url": final_url or url,
                "status": status,
                "chars": chars,
                "path": rel,
                "depth": depth,
                "error": err,
            }
        )

        # Enqueue children
        if depth < max_depth and html:
            for link in _extract_links(html, final_url or url):
                if link in seen:
                    continue
                if same_domain_only and not _same_domain(start, link):
                    continue
                queue.append((link, depth + 1))

    ok = sum(1 for r in results if r["status"] == "ok")
    lines = [
        f"# Crawl index — seed `{start}`",
        f"Pages fetched: {len(results)} (ok={ok}) · max_pages={max_pages} · max_depth={max_depth} · same_domain={same_domain_only}",
        "",
        "| # | depth | status | chars | path | url |",
        "|---|-------|--------|-------|------|-----|",
    ]
    for i, r in enumerate(results):
        lines.append(
            f"| {i} | {r.get('depth', 0)} | {r['status']} | {r['chars']} | "
            f"`{r['path'] or '—'}` | {r['url'][:80]} |"
        )
    lines.append("")
    lines.append(
        "Next: digest_research_file / list_research_chunks on saved paths, "
        "or read_url_to_file for a single deep page."
    )
    if not ok:
        lines.append(
            "\nNo pages saved successfully. Try KAZMA_JINA_READER=1 or "
            "KAZMA_FIRECRAWL_API_KEY for harder sites."
        )
    return "\n".join(lines)
