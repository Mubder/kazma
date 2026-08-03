"""Deep research pipeline — multi-query, multi-source, synthesized report."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "list_research_papers",
    "run_research_pipeline",
]

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, str], Awaitable[None] | None]


def _slug(topic: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (topic or "topic").strip().lower()).strip("-")
    return (s[:48] or "topic")


def _get_ws_root() -> Path:
    try:
        from kazma_core.tools.file_write import _get_workspace

        return _get_workspace().resolve()
    except Exception:
        return (Path.cwd() / "kazma-data" / "workspace").resolve()


def _candidate_report_roots() -> list[Path]:
    """All places we may have written research/reports (workspace can vary)."""
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path | None) -> None:
        if p is None:
            return
        try:
            r = p.resolve()
        except Exception:
            return
        key = str(r).lower()
        if key in seen:
            return
        seen.add(key)
        roots.append(r)

    _add(_get_ws_root())
    try:
        import os

        env = (os.environ.get("KAZMA_WORKSPACE") or "").strip()
        if env:
            _add(Path(env).expanduser())
    except Exception:
        pass
    _add(Path.cwd() / "kazma-data" / "workspace")
    _add(Path.cwd())  # repo-root research/ when tools wrote relative to cwd
    # Active WorkspaceStore (may differ from tool pin at list time)
    try:
        from kazma_core.stores import get_workspace_store

        for row in get_workspace_store().list_workspaces() or []:
            rp = (row or {}).get("root_path")
            if rp:
                _add(Path(rp).expanduser())
    except Exception:
        pass
    return roots


def _workspace_research_dir(topic: str) -> Path:
    root = _get_ws_root()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    d = root / "research" / "reports" / f"{_slug(topic)}-{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rel(path: Path) -> str:
    for root in _candidate_report_roots():
        try:
            return str(path.resolve().relative_to(root)).replace("\\", "/")
        except Exception:
            continue
    return str(path)


def _extract_urls_from_search(md: str) -> list[str]:
    urls: list[str] = []
    for m in re.finditer(r"\*\*URL:\*\*\s*(\S+)", md or ""):
        u = m.group(1).strip().rstrip(")")
        if u.startswith("http"):
            urls.append(u)
    for m in re.finditer(r"https?://[^\s\)\]\>\"']+", md or ""):
        urls.append(m.group(0).rstrip(".,;"))
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


async def _emit(cb: ProgressCb | None, stage: str, message: str) -> None:
    if not cb:
        return
    try:
        res = cb(stage, message)
        if asyncio.iscoroutine(res) or isinstance(res, Awaitable):
            await res  # type: ignore[arg-type]
    except Exception as exc:
        logger.debug("[research_pipeline] progress_cb failed: %s", exc)


def _papers_index_path() -> Path:
    return _get_ws_root() / "research" / "reports" / "index.json"


def _register_paper(meta: dict[str, Any]) -> None:
    """Persist paper meta to ConfigStore (shared) + workspace index.json."""
    for key in ("report_path", "docx_path"):
        p = meta.get(key)
        if p and not Path(str(p)).is_absolute():
            try:
                abs_p = (_get_ws_root() / str(p)).resolve()
                if abs_p.is_file():
                    meta[f"{key}_abs"] = str(abs_p)
            except Exception:
                pass

    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        items = cs.get("research.papers_index") or []
        if not isinstance(items, list):
            items = []
        rp = str(meta.get("report_path") or "")
        items = [
            it
            for it in items
            if isinstance(it, dict) and it.get("report_path") != rp
        ]
        items.insert(0, meta)
        cs.set("research.papers_index", items[:100], category="research")
    except Exception as exc:
        logger.debug("[research_pipeline] ConfigStore paper register failed: %s", exc)

    try:
        path = _papers_index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        items2: list[dict[str, Any]] = []
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    items2 = raw
            except Exception:
                items2 = []
        items2.insert(0, meta)
        path.write_text(
            json.dumps(items2[:100], indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger.debug("[research_pipeline] file paper register failed: %s", exc)


def list_research_papers(*, limit: int = 50) -> list[dict[str, Any]]:
    """List registered paper runs + scan report.md under all known roots."""
    limit = max(1, min(100, int(limit or 50)))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(it: dict[str, Any]) -> None:
        key = str(it.get("report_path") or it.get("id") or "")
        if not key or key in seen:
            return
        seen.add(key)
        out.append(it)

    try:
        from kazma_core.config_store import get_config_store

        items = get_config_store().get("research.papers_index") or []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("report_path"):
                    _add(it)
    except Exception:
        pass

    for root in _candidate_report_roots():
        idx = root / "research" / "reports" / "index.json"
        if idx.is_file():
            try:
                raw = json.loads(idx.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    for it in raw:
                        if isinstance(it, dict) and it.get("report_path"):
                            _add(it)
            except Exception:
                pass
        reports_root = root / "research" / "reports"
        if not reports_root.is_dir():
            continue
        try:
            reports = sorted(reports_root.glob("*/report.md"), reverse=True)
        except Exception:
            reports = []
        for report in reports:
            try:
                rel = str(report.resolve().relative_to(root)).replace("\\", "/")
            except Exception:
                rel = str(report)
            parent = report.parent.name
            docx = report.parent / "report.docx"
            _add(
                {
                    "id": parent,
                    "topic": parent.rsplit("-", 1)[0].replace("-", " "),
                    "report_path": rel,
                    "report_path_abs": str(report.resolve()),
                    "docx_path": (
                        str(docx.relative_to(root)).replace("\\", "/")
                        if docx.is_file()
                        else None
                    ),
                    "docx_path_abs": str(docx.resolve()) if docx.is_file() else None,
                    "created_at": datetime.fromtimestamp(
                        report.stat().st_mtime, tz=UTC
                    ).isoformat(),
                    "sources": None,
                    "kind": "research_paper",
                }
            )

    return out[:limit]


def _md_to_sections(md: str) -> list[dict[str, str]]:
    """Split markdown into {heading, body} for document_generator."""
    lines = (md or "").splitlines()
    sections: list[dict[str, str]] = []
    cur_h = "Body"
    cur_b: list[str] = []
    for line in lines:
        if line.startswith("#"):
            if cur_b or sections:
                sections.append({"heading": cur_h, "body": "\n".join(cur_b).strip()})
            cur_h = line
            cur_b = []
        else:
            cur_b.append(line)
    if cur_b or not sections:
        sections.append({"heading": cur_h, "body": "\n".join(cur_b).strip()})
    return [s for s in sections if s.get("body") or s.get("heading")]


async def _export_docx(title: str, report_md: str, dest_dir: Path) -> str | None:
    try:
        from kazma_skills.native.document_generator.tools import generate_docx
    except Exception:
        try:
            # monorepo path
            from kazma_skills.native.document_generator.tools import generate_docx  # type: ignore
        except Exception as exc:
            logger.debug("[research_pipeline] docx unavailable: %s", exc)
            return None
    try:
        sections = _md_to_sections(report_md)
        msg = await generate_docx(title[:80] or "Research report", sections)
        if isinstance(msg, str) and "Saved to:" in msg:
            # Prefer copy into report dir if generator used global dest
            m = re.search(r"Saved to:\s*(\S+)", msg)
            if m:
                src = Path(m.group(1))
                if src.is_file():
                    dest = dest_dir / "report.docx"
                    try:
                        dest.write_bytes(src.read_bytes())
                        return _rel(dest)
                    except Exception:
                        return str(src)
        # Fallback write via python-docx in place
        return None if msg.startswith("Error:") else msg
    except Exception as exc:
        logger.debug("[research_pipeline] docx export failed: %s", exc)
        return None


async def run_research_pipeline(
    topic: str,
    depth: str = "deep",
    max_sources: int = 8,
    language: str = "",
    *,
    progress_cb: ProgressCb | None = None,
    export_docx: bool = False,
    parallel_acquire: bool = True,
) -> str:
    """Run multi-stage deep research and write a report under research/reports/.

    Stages: plan → parallel search → parallel acquire → digest → synthesize → assemble.
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
    from kazma_core.web_acquire import search as web_acquire_search

    t0 = time.time()
    log: list[str] = []
    out_dir = _workspace_research_dir(topic)
    rel_dir = _rel(out_dir)

    # Industry preflight — fail fast when no search path exists
    await _emit(progress_cb, "preflight", "Checking research stack…")
    try:
        from kazma_core.tools.research_readiness import (
            format_readiness_message,
            research_readiness,
        )

        ready = research_readiness(probe_search=False)
        log.append(f"Preflight: {format_readiness_message(ready)}")
        for c in ready.get("checks") or []:
            log.append(f"- [{c.get('level')}] {c.get('id')}: {c.get('message')}")
        if not ready.get("ready"):
            msg = format_readiness_message(ready)
            await _emit(progress_cb, "error", msg)
            return (
                "Error: research stack not ready — "
                f"{msg}. Configure SearXNG (KAZMA_SEARXNG_URL) or install "
                "duckduckgo_search; see GET /api/research/ready."
            )
        await _emit(progress_cb, "preflight", format_readiness_message(ready))
    except Exception as exc:
        log.append(f"Preflight skipped: {exc}")
        logger.debug("[research_pipeline] preflight failed", exc_info=True)

    await _emit(progress_cb, "plan", f"Planning research on: {topic}")
    log.append(f"## Pipeline start — depth={depth_l} max_sources={max_sources}")
    log.append(f"Topic: {topic}")
    log.append(f"Output dir: `{rel_dir}`")
    log.append("Acquisition stack: kazma_core.web_acquire (shared with KB fetch ladder)")

    # ── Stage 1: adaptive planner (R2) ────────────────────────────────
    from kazma_core.tools.research_planner import (
        critique_synthesis_gaps,
        plan_research_queries,
    )

    plan = await plan_research_queries(
        topic,
        language=language,
        max_queries=max_queries if is_deep else min(4, max_queries),
        is_deep=is_deep,
    )
    queries = list(plan.search_queries)
    try:
        (out_dir / "plan.json").write_text(
            json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    log.append(f"### Plan — method={plan.method} · {len(queries)} search queries")
    for i, q in enumerate(queries, 1):
        log.append(f"{i}. {q}")
    if plan.sub_questions:
        log.append("Sub-questions: " + "; ".join(plan.sub_questions[:6]))
    if plan.success_criteria:
        log.append("Success criteria: " + "; ".join(plan.success_criteria[:5]))

    # ── Stage 2: discover (parallel searches via web_acquire) ─────────
    await _emit(progress_cb, "discover", f"Searching {len(queries)} queries…")
    candidates: list[str] = []
    domain_count: dict[str, int] = {}
    lock = asyncio.Lock()

    async def _one_search(q: str) -> None:
        nonlocal candidates, domain_count
        try:
            sr = await web_acquire_search(
                q, max_results=8 if is_deep else 5, purpose="research"
            )
            async with lock:
                log.append(
                    f"Search `{q}` → ok={sr.ok} urls={len(sr.urls)} "
                    f"latency={sr.latency_ms}ms"
                )
                for u in sr.urls:
                    d = _domain(u)
                    if d and domain_count.get(d, 0) >= 2:
                        continue
                    if u not in candidates:
                        candidates.append(u)
                        domain_count[d] = domain_count.get(d, 0) + 1
        except Exception as exc:
            async with lock:
                log.append(f"Search failed `{q}`: {exc}")

    await asyncio.gather(*[_one_search(q) for q in queries])
    log.append(f"### Discover — {len(candidates)} unique URLs (pre-rank)")
    await _emit(progress_cb, "discover", f"Found {len(candidates)} candidate URLs")

    if not candidates:
        return (
            "Error: deep research found no URLs. Check SearXNG / network "
            f"(`KAZMA_SEARXNG_URL`).\n\n" + "\n".join(log)
        )

    # ── Stage 2b: rank sources (industry R1) ──────────────────────────
    from kazma_core.web_acquire import rank_urls

    ranked = rank_urls(
        candidates,
        topic,
        max_per_domain=2,
        limit=max_sources * 3,
    )
    log.append(f"### Rank — top {min(8, len(ranked))} of {len(ranked)}")
    for r in ranked[:8]:
        log.append(
            f"  score={r.score:.1f} {r.url} ({','.join(r.reasons) or '—'})"
        )
    urls_to_fetch = [r.url for r in ranked]
    if not urls_to_fetch:
        urls_to_fetch = candidates[: max_sources * 2]

    # ── Stage 3: acquire (parallel with cap) ──────────────────────────
    await _emit(progress_cb, "acquire", f"Fetching up to {max_sources} pages…")
    saved: list[str] = []
    acquire_meta: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(4 if parallel_acquire else 1)
    save_lock = asyncio.Lock()

    async def _acquire(url: str, idx: int) -> None:
        nonlocal saved, acquire_meta
        if len(saved) >= max_sources:
            return
        async with sem:
            if len(saved) >= max_sources:
                return
            t_acq = time.time()
            try:
                name = f"src-{idx:02d}.md"
                path = _rel(out_dir / name)
                (out_dir / name).parent.mkdir(parents=True, exist_ok=True)
                res = await read_url_to_file(url, path=path)
                latency = round(time.time() - t_acq, 2)
                if isinstance(res, str) and res.startswith("Error:"):
                    async with save_lock:
                        log.append(f"Acquire fail {url}: {res[:120]}")
                        acquire_meta.append(
                            {
                                "url": url,
                                "ok": False,
                                "error": res[:200],
                                "latency_s": latency,
                                "rank_score": next(
                                    (x.score for x in ranked if x.url == url),
                                    None,
                                ),
                            }
                        )
                    return
                saved_path = path
                if isinstance(res, str) and "saved" in res.lower():
                    m = re.search(r"[`'\"]([^`'\"]+\.md)[`'\"]", res)
                    if m:
                        saved_path = m.group(1)
                async with save_lock:
                    if len(saved) < max_sources:
                        saved.append(saved_path)
                        chars = 0
                        try:
                            p_abs = out_dir / Path(saved_path).name
                            if not p_abs.is_file():
                                p_abs = _get_ws_root() / saved_path
                            if p_abs.is_file():
                                chars = p_abs.stat().st_size
                        except Exception:
                            pass
                        acquire_meta.append(
                            {
                                "url": url,
                                "ok": True,
                                "path": saved_path,
                                "bytes": chars,
                                "latency_s": latency,
                                "rank_score": next(
                                    (x.score for x in ranked if x.url == url),
                                    None,
                                ),
                                "domain": _domain(url),
                            }
                        )
                        log.append(f"Acquired [{len(saved)}] {url} → `{saved_path}`")
                        await _emit(
                            progress_cb,
                            "acquire",
                            f"Acquired {len(saved)}/{max_sources}: {url[:80]}",
                        )
            except Exception as exc:
                async with save_lock:
                    log.append(f"Acquire error {url}: {exc}")
                    acquire_meta.append(
                        {"url": url, "ok": False, "error": str(exc)[:200]}
                    )

    await asyncio.gather(
        *[_acquire(u, i + 1) for i, u in enumerate(urls_to_fetch)]
    )

    # Persist acquire metadata for audit / industry provenance
    try:
        meta_path = out_dir / "sources.json"
        meta_path.write_text(
            json.dumps(acquire_meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.append(f"Wrote `{_rel(meta_path)}`")
    except Exception as exc:
        log.append(f"sources.json write failed: {exc}")

    # Fail-closed for deep (industry R1) — standard may continue with warning
    fail_closed = is_deep and not os_env_truthy("KAZMA_RESEARCH_ALLOW_THIN")
    if len(saved) < min_sources:
        msg = (
            f"only {len(saved)} sources acquired (min {min_sources} for depth={depth_l})"
        )
        if fail_closed:
            return (
                f"Error: deep research aborted — {msg}. "
                "Improve search (SearXNG), raise max_sources, or set "
                "KAZMA_RESEARCH_ALLOW_THIN=1 to continue with thin evidence.\n\n"
                + "\n".join(log)
            )
        log.append(f"WARNING: {msg}; continuing (standard depth).")
    if not saved:
        return "Error: could not acquire any full pages.\n\n" + "\n".join(log)

    # ── Stage 4: digests + evidence claims (parallel) ─────────────────
    await _emit(progress_cb, "map", f"Digesting {len(saved)} sources…")
    digests: list[str] = []

    async def _digest_one(p: str) -> str:
        try:
            dig = await digest_research_file(p)
            dpath = out_dir / f"digest-{Path(p).stem}.md"
            dpath.write_text(
                dig if not dig.startswith("Error:") else dig, encoding="utf-8"
            )
            drel = _rel(dpath)
            log.append(f"Digest `{p}` → `{drel}`")
            return drel
        except Exception as exc:
            log.append(f"Digest fallback raw `{p}`: {exc}")
            return p

    digests = list(await asyncio.gather(*[_digest_one(p) for p in saved]))

    # Heuristic claim map (no nested LLM by default)
    from kazma_core.tools.research_evidence import (
        claims_to_markdown,
        extract_claims_from_file,
        write_claims_json,
    )

    all_claims = []
    claims_md = ""
    url_by_path = {
        str(m.get("path") or ""): str(m.get("url") or "")
        for m in acquire_meta
        if m.get("ok")
    }
    # Map src paths to URLs too (digest paths differ from raw)
    for m in acquire_meta:
        if not m.get("ok"):
            continue
        pth = str(m.get("path") or "")
        u = str(m.get("url") or "")
        if pth:
            url_by_path[pth] = u
            stem = Path(pth).stem
            url_by_path[f"digest-{stem}.md"] = u
    for p in digests:
        src_url = url_by_path.get(p) or url_by_path.get(Path(p).name) or ""
        all_claims.extend(
            extract_claims_from_file(p, source_url=src_url, max_claims=6)
        )
    claims_path = out_dir / "claims.json"
    try:
        write_claims_json(claims_path, all_claims)
        claims_md = claims_to_markdown(all_claims)
        (out_dir / "claims.md").write_text(claims_md, encoding="utf-8")
        log.append(f"Evidence claims: {len(all_claims)} → `{_rel(claims_path)}`")
    except Exception as exc:
        claims_md = ""
        log.append(f"claims extract failed: {exc}")

    # ── Stage 5: synthesize ───────────────────────────────────────────
    await _emit(progress_cb, "reduce", "Synthesizing multi-source analysis…")
    outline = (
        "## Executive summary\n## Background\n## Key findings\n"
        "## Comparison / alternatives\n## Risks and open questions\n"
        "## Conclusions\n## Sources\n"
    )
    synth_paths = digests
    if (out_dir / "claims.md").is_file():
        synth_paths = digests + [_rel(out_dir / "claims.md")]
    synthesis = await synthesize_from_digests(
        synth_paths,
        question=topic,
        outline=outline,
        max_chars=24_000 if is_deep else 12_000,
    )
    log.append(f"Synthesis length={len(synthesis)}")

    # ── Stage 5b: gap critic + optional second acquire (R2) ────────────
    import os as _os

    gap_loops = 0
    _gap_env = (_os.environ.get("KAZMA_RESEARCH_GAP_LOOP") or "1").strip().lower()
    max_gap_loops = 0
    if _gap_env not in ("0", "false", "no", "off"):
        max_gap_loops = 1 if is_deep else 0

    while gap_loops < max_gap_loops and len(saved) < max_sources:
        await _emit(progress_cb, "verify", "Checking gaps in draft…")
        src_summary = "\n".join(
            f"- {m.get('url')} ({m.get('path')})"
            for m in acquire_meta
            if m.get("ok")
        )
        gap = await critique_synthesis_gaps(
            topic,
            synthesis,
            sources_summary=src_summary,
            max_followups=3,
        )
        try:
            (out_dir / f"gap_loop_{gap_loops}.json").write_text(
                json.dumps(gap.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass
        log.append(
            f"Gap loop {gap_loops}: method={gap.method} needs_more={gap.needs_more} "
            f"followups={len(gap.followup_queries)}"
        )
        if gap.gaps:
            log.append("Gaps: " + "; ".join(gap.gaps[:5]))
        if not gap.needs_more or not gap.followup_queries:
            break

        gap_loops += 1
        await _emit(
            progress_cb,
            "discover",
            f"Gap fill: {len(gap.followup_queries)} follow-up searches…",
        )
        await asyncio.gather(*[_one_search(q) for q in gap.followup_queries])
        # Re-rank remaining unused URLs
        already = {m.get("url") for m in acquire_meta if m.get("url")}
        new_cands = [u for u in candidates if u not in already]
        ranked2 = rank_urls(
            new_cands, topic, max_per_domain=2, limit=max(2, max_sources - len(saved)) * 2
        )
        more_urls = [r.url for r in ranked2]
        if not more_urls:
            log.append("Gap loop: no new ranked URLs")
            break
        start_idx = len(saved) + 1
        await _emit(progress_cb, "acquire", f"Gap fill acquire {len(more_urls)}…")
        await asyncio.gather(
            *[_acquire(u, start_idx + i) for i, u in enumerate(more_urls)]
        )
        # Digest any new raw sources not yet covered
        digested_stems = {Path(d).stem.replace("digest-", "") for d in digests}
        to_digest: list[str] = []
        for m in acquire_meta:
            if not m.get("ok") or not m.get("path"):
                continue
            stem = Path(str(m["path"])).stem
            if stem not in digested_stems:
                to_digest.append(str(m["path"]))
                # keep url map fresh for claim extract
                url_by_path[str(m["path"])] = str(m.get("url") or "")
                url_by_path[f"digest-{stem}.md"] = str(m.get("url") or "")
        if to_digest:
            extra = list(await asyncio.gather(*[_digest_one(p) for p in to_digest]))
            digests.extend(extra)
            for p in extra:
                src_url = url_by_path.get(p) or url_by_path.get(Path(p).name) or ""
                all_claims.extend(
                    extract_claims_from_file(p, source_url=src_url, max_claims=6)
                )
            try:
                write_claims_json(claims_path, all_claims)
                claims_md = claims_to_markdown(all_claims)
                (out_dir / "claims.md").write_text(claims_md, encoding="utf-8")
            except Exception:
                pass
            synth_paths = digests + (
                [_rel(out_dir / "claims.md")] if (out_dir / "claims.md").is_file() else []
            )
            await _emit(progress_cb, "reduce", "Re-synthesizing after gap fill…")
            synthesis = await synthesize_from_digests(
                synth_paths,
                question=topic,
                outline=outline,
                max_chars=24_000 if is_deep else 12_000,
            )
            log.append(f"Re-synthesis length={len(synthesis)} after gap loop")
        break  # one gap loop max

    # ── Stage 6: assemble + rubric ────────────────────────────────────
    await _emit(progress_cb, "assemble", "Writing report…")
    elapsed = time.time() - t0
    report = (
        f"# Research report: {topic}\n\n"
        f"_Generated by Kazma research pipeline · depth={depth_l} · "
        f"{len(saved)} sources · ranked · plan={plan.method} · "
        f"gap_loops={gap_loops} · {elapsed:.0f}s_\n\n"
        f"{synthesis}\n\n"
    )
    if claims_md:
        report += f"---\n\n{claims_md}\n"
    report += (
        f"---\n\n## Pipeline log\n\n"
        + "\n".join(f"- {line}" for line in log)
        + "\n"
    )
    report_path = out_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")
    report_rel = _rel(report_path)

    # Structural rubric (R0)
    rubric_dict: dict[str, Any] = {}
    try:
        from kazma_core.tools.research_eval import score_report_markdown

        rub = score_report_markdown(report, min_sources=min_sources)
        rubric_dict = rub.to_dict()
        (out_dir / "rubric.json").write_text(
            json.dumps(rubric_dict, indent=2), encoding="utf-8"
        )
        log.append(f"Rubric score={rub.score} ok={rub.ok}")
    except Exception as exc:
        log.append(f"rubric failed: {exc}")

    docx_rel = None
    if export_docx or (os_env_truthy("KAZMA_RESEARCH_EXPORT_DOCX")):
        await _emit(progress_cb, "export", "Exporting DOCX…")
        docx_rel = await _export_docx(f"Research: {topic}", report, out_dir)
        if docx_rel:
            log.append(f"DOCX: `{docx_rel}`")

    paper_id = out_dir.name
    _register_paper(
        {
            "id": paper_id,
            "topic": topic,
            "depth": depth_l,
            "report_path": report_rel,
            "docx_path": docx_rel,
            "sources": len(saved),
            "claims": len(all_claims),
            "plan_method": plan.method,
            "gap_loops": gap_loops,
            "rubric_score": rubric_dict.get("score"),
            "created_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "kind": "research_paper",
        }
    )

    await _emit(progress_cb, "done", f"Report ready: {report_rel}")
    rubric_line = ""
    if rubric_dict:
        rubric_line = (
            f"**Rubric:** {rubric_dict.get('score')}/100 "
            f"({'pass' if rubric_dict.get('ok') else 'needs work'})\n"
        )
    summary = (
        f"# Deep research complete\n\n"
        f"**Topic:** {topic}\n"
        f"**Sources acquired:** {len(saved)} (target {max_sources}, min {min_sources})\n"
        f"**Evidence claims:** {len(all_claims)}\n"
        f"**Report:** `{report_rel}`\n"
        + (f"**DOCX:** `{docx_rel}`\n" if docx_rel else "")
        + rubric_line
        + f"**Elapsed:** {elapsed:.0f}s\n\n"
        f"## Executive extract\n\n"
        f"{_first_section(synthesis, 2500)}\n\n"
        f"Open the full report file for the complete paper-style write-up.\n"
        f"Also listed under Research panel papers API (`/api/research/papers`).\n"
    )
    return summary


def os_env_truthy(name: str) -> bool:
    import os

    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _first_section(text: str, n: int) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 20] + "\n\n…"
