"""Deep research pipeline — multi-query, multi-source, synthesized report."""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

__all__ = ["run_research_pipeline"]

logger = logging.getLogger(__name__)


def _slug(topic: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (topic or "topic").strip().lower()).strip("-")
    return (s[:48] or "topic")


def _workspace_research_dir(topic: str) -> Path:
    try:
        from kazma_core.tools.file_write import _get_workspace

        root = _get_workspace().resolve()
    except Exception:
        root = (Path.cwd() / "kazma-data" / "workspace").resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    d = root / "research" / "reports" / f"{_slug(topic)}-{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _extract_urls_from_search(md: str) -> list[str]:
    urls: list[str] = []
    for m in re.finditer(r"\*\*URL:\*\*\s*(\S+)", md or ""):
        u = m.group(1).strip().rstrip(")")
        if u.startswith("http"):
            urls.append(u)
    for m in re.finditer(r"https?://[^\s\)\]\>\"']+", md or ""):
        urls.append(m.group(0).rstrip(".,;"))
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


async def run_research_pipeline(
    topic: str,
    depth: str = "deep",
    max_sources: int = 8,
    language: str = "",
) -> str:
    """Run multi-stage deep research and write a report under research/reports/.

    Stages: plan queries → search → acquire pages → digest → synthesize → assemble.
    """
    topic = (topic or "").strip()
    if not topic:
        return "Error: topic is required. Usage: run_research_pipeline(topic=...)"

    depth_l = (depth or "deep").strip().lower()
    is_deep = depth_l in ("deep", "comprehensive", "full", "paper")
    max_sources = max(2, min(15, int(max_sources or (8 if is_deep else 4))))
    max_queries = 10 if is_deep else 4
    min_sources = min(max_sources, 4 if is_deep else 2)

    from kazma_core.tools.read_url import digest_research_file, read_url_to_file
    from kazma_core.tools.research_synthesize import synthesize_from_digests
    from kazma_core.tools.web_search import web_search

    t0 = time.time()
    log: list[str] = []
    out_dir = _workspace_research_dir(topic)
    rel_dir = str(out_dir.relative_to(out_dir.parents[2] if len(out_dir.parts) > 2 else out_dir.parent))
    try:
        from kazma_core.tools.file_write import _get_workspace

        rel_dir = str(out_dir.relative_to(_get_workspace().resolve()))
    except Exception:
        rel_dir = str(out_dir)

    log.append(f"## Pipeline start — depth={depth_l} max_sources={max_sources}")
    log.append(f"Topic: {topic}")
    log.append(f"Output dir: `{rel_dir}`")

    # ── Stage 1: query plan (heuristic + optional LLM polish) ─────────
    base = topic if not language else f"{topic} {language}"
    queries = [
        base,
        f"{topic} overview analysis",
        f"{topic} benefits risks",
        f"{topic} latest developments",
        f"{topic} comparison alternatives",
        f"{topic} official documentation",
        f"{topic} research paper study",
        f"{topic} criticism limitations",
    ]
    if not is_deep:
        queries = queries[:3]
    else:
        queries = queries[:max_queries]
    log.append(f"### Plan — {len(queries)} search queries")
    for i, q in enumerate(queries, 1):
        log.append(f"{i}. {q}")

    # ── Stage 2: discover URLs ────────────────────────────────────────
    candidates: list[str] = []
    domain_count: dict[str, int] = {}
    for q in queries:
        try:
            md = await web_search(q, max_results=8 if is_deep else 5)
            log.append(f"Search `{q}` → {len(md)} chars")
            for u in _extract_urls_from_search(md):
                d = _domain(u)
                if d and domain_count.get(d, 0) >= 2:
                    continue  # diversify domains
                if u not in candidates:
                    candidates.append(u)
                    domain_count[d] = domain_count.get(d, 0) + 1
        except Exception as exc:
            log.append(f"Search failed `{q}`: {exc}")

    log.append(f"### Discover — {len(candidates)} unique URLs")
    if not candidates:
        return (
            "Error: deep research found no URLs. Check SearXNG / network "
            f"(`KAZMA_SEARXNG_URL`).\n\n" + "\n".join(log)
        )

    # ── Stage 3: acquire ──────────────────────────────────────────────
    saved: list[str] = []
    for i, url in enumerate(candidates):
        if len(saved) >= max_sources:
            break
        try:
            # save under report dir
            name = f"src-{len(saved)+1:02d}.md"
            path = str((out_dir / name).relative_to(
                _get_ws_root()
            ))
            res = await read_url_to_file(url, path=path)
            if isinstance(res, str) and res.startswith("Error:"):
                log.append(f"Acquire fail {url}: {res[:120]}")
                continue
            # read_url_to_file returns path or message with path
            saved_path = path
            if isinstance(res, str) and "saved" in res.lower():
                m = re.search(r"[`'\"]([^`'\"]+\.md)[`'\"]", res)
                if m:
                    saved_path = m.group(1)
            saved.append(saved_path)
            log.append(f"Acquired [{len(saved)}] {url} → `{saved_path}`")
        except Exception as exc:
            log.append(f"Acquire error {url}: {exc}")

    if len(saved) < min_sources:
        log.append(
            f"WARNING: only {len(saved)} sources (min {min_sources}); continuing with what we have."
        )
    if not saved:
        return "Error: could not acquire any full pages.\n\n" + "\n".join(log)

    # ── Stage 4: map digests ──────────────────────────────────────────
    digests: list[str] = []
    for p in saved:
        try:
            dig = await digest_research_file(p)
            dpath = out_dir / f"digest-{Path(p).stem}.md"
            dpath.write_text(dig if not dig.startswith("Error:") else dig, encoding="utf-8")
            try:
                from kazma_core.tools.file_write import _get_workspace

                drel = str(dpath.relative_to(_get_workspace().resolve()))
            except Exception:
                drel = str(dpath)
            digests.append(drel)
            log.append(f"Digest `{p}` → `{drel}`")
        except Exception as exc:
            digests.append(p)
            log.append(f"Digest fallback raw `{p}`: {exc}")

    # ── Stage 5: synthesize ───────────────────────────────────────────
    outline = (
        "## Executive summary\n## Background\n## Key findings\n"
        "## Comparison / alternatives\n## Risks and open questions\n## Conclusions\n## Sources\n"
    )
    synthesis = await synthesize_from_digests(
        digests,
        question=topic,
        outline=outline,
        max_chars=24_000 if is_deep else 12_000,
    )
    log.append(f"Synthesis length={len(synthesis)}")

    # ── Stage 6: assemble report ──────────────────────────────────────
    elapsed = time.time() - t0
    report = (
        f"# Research report: {topic}\n\n"
        f"_Generated by Kazma research pipeline · depth={depth_l} · "
        f"{len(saved)} sources · {elapsed:.0f}s_\n\n"
        f"{synthesis}\n\n"
        f"---\n\n## Pipeline log\n\n" + "\n".join(f"- {line}" for line in log) + "\n"
    )
    report_path = out_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")
    try:
        from kazma_core.tools.file_write import _get_workspace

        report_rel = str(report_path.relative_to(_get_workspace().resolve()))
    except Exception:
        report_rel = str(report_path)

    summary = (
        f"# Deep research complete\n\n"
        f"**Topic:** {topic}\n"
        f"**Sources acquired:** {len(saved)} (target {max_sources})\n"
        f"**Report:** `{report_rel}`\n"
        f"**Elapsed:** {elapsed:.0f}s\n\n"
        f"## Executive extract\n\n"
        f"{_first_section(synthesis, 2500)}\n\n"
        f"Open the full report file for the complete paper-style write-up.\n"
    )
    return summary


def _get_ws_root() -> Path:
    try:
        from kazma_core.tools.file_write import _get_workspace

        return _get_workspace().resolve()
    except Exception:
        return (Path.cwd() / "kazma-data" / "workspace").resolve()


def _first_section(text: str, n: int) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 20] + "\n\n…"
