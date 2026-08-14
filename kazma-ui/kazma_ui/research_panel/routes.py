"""Research panel API routes — list, detail, compare, export, and live sessions.

Swarm research tasks are tagged with ``metadata={"kind": "research"}`` at
dispatch time. Deep pipeline runs use ``research_session`` (SQLite + SSE).

Routes:
  GET  /api/research/tasks           — list research tasks (filtered)
  GET  /api/research/tasks/{id}      — single research result detail
  GET  /api/research/papers          — deep research pipeline paper runs
  POST /api/research/sessions        — start a deep research session
  GET  /api/research/sessions        — list durable research sessions
  GET  /api/research/sessions/{id}   — session detail / status
  GET  /api/research/sessions/{id}/stream — SSE progress for a live run
  POST /api/research/compare         — compare two research runs
  POST /api/research/{id}/export     — export to DOCX/PDF/Markdown
  GET  /api/research/download        — download an exported file
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from kazma_ui.rate_limit import rate_limit
from kazma_ui.sse_utils import sse_frame

logger = logging.getLogger(__name__)

__all__ = ["create_research_router"]


def _get_store():
    """Resolve the SwarmEngine's TaskStore singleton."""
    try:
        from kazma_ui.services import get_swarm_service
        svc = get_swarm_service()
        engine = svc.resolve_engine(None) if svc.has_swarm_core() else None
        if engine:
            return getattr(engine, "task_store", None) or getattr(engine, "_task_store", None)
    except Exception:
        pass
    return None


def _flatten(task: Any) -> dict[str, Any]:
    """Flatten a SwarmTask + its result into a UI-friendly dict."""
    result = task.result
    rdict = result.to_dict() if result else {}
    return {
        "id": task.id,
        "prompt": task.prompt,
        "status": str(task.status).lower().replace("taskstatus.", ""),
        "workers": task.workers,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
        "cost": rdict.get("total_cost", 0.0),
        "tokens": rdict.get("total_tokens", 0),
        "duration": rdict.get("duration_seconds", 0.0),
        "aggregated_output": rdict.get("aggregated_output", ""),
        "synthesized_output": rdict.get("synthesized_output", ""),
        "worker_results": rdict.get("worker_results", []),
        "error": rdict.get("error"),
        "metadata": {**(task.metadata or {}), **rdict.get("metadata", {})},
    }


async def _set_archived(task_id: str, archived: bool) -> JSONResponse:
    """Toggle the archived flag on a research task's metadata.

    Loads the task from the TaskStore (or in-memory engine), mutates
    ``metadata["archived"]``, and re-persists. This respects the store's
    locking and works for both SQLite and Postgres.
    """
    store = _get_store()
    task = store.get_task(task_id) if store else None
    if task is None:
        try:
            from kazma_core.swarm import get_swarm_engine
            engine = get_swarm_engine()
            if engine:
                task = engine.get_task(task_id) or engine.get_active_task(task_id)
        except Exception:
            pass
    if task is None:
        return JSONResponse({"error": "task not found"}, status_code=404)

    # Mutate metadata and re-persist.
    if task.metadata is None:
        task.metadata = {}
    task.metadata["archived"] = archived

    if store is not None:
        try:
            store.persist_task(task)
        except Exception as exc:
            logger.exception("[research] archive persist failed")
            return JSONResponse({"error": str(exc)}, status_code=500)
    else:
        return JSONResponse({"error": "store unavailable"}, status_code=503)

    return JSONResponse({"ok": True, "task_id": task_id, "archived": archived})


def create_research_router() -> APIRouter:
    """Create the research API router."""
    router = APIRouter(tags=["research"])

    @router.get("/api/research/papers")
    async def list_papers(limit: int = 50) -> JSONResponse:
        """List deep research pipeline paper runs (report.md under research/reports/)."""
        try:
            from kazma_core.tools.research_pipeline import list_research_papers

            papers = list_research_papers(limit=limit)
            return JSONResponse({"ok": True, "papers": papers, "count": len(papers)})
        except Exception as exc:
            logger.exception("[research] list papers failed")
            return JSONResponse({"ok": False, "error": str(exc), "papers": []}, status_code=500)

    @router.get("/api/research/papers/file")
    async def get_paper_file(path: str) -> Any:
        """Serve a research report file (under research/reports/ in any known workspace)."""
        from kazma_core.tools.research_pipeline import _candidate_report_roots

        raw = (path or "").strip().replace("\\", "/")
        if not raw or ".." in raw.split("/"):
            return JSONResponse({"error": "invalid path"}, status_code=400)
        # Reject absolute paths outright — only relative paths under a known
        # reports tree are served. (Previously an absolute path whose string
        # contained "/research/reports/" bypassed the relative_to containment
        # check via the substring fallback → arbitrary file read.)
        candidates: list[Path] = []
        if Path(raw).is_absolute():
            return JSONResponse({"error": "absolute paths are not allowed"}, status_code=400)
        elif raw.startswith("research/reports/"):
            for root in _candidate_report_roots():
                candidates.append((root / raw).resolve())
        else:
            return JSONResponse(
                {"error": "path must be under research/reports/"}, status_code=403
            )

        target: Path | None = None
        for cand in candidates:
            try:
                if not cand.is_file():
                    continue
                # Containment: must live under some root's research/reports.
                # (Substring matching removed — it allowed sibling dirs whose
                # name merely extends "reports" to pass.)
                for root in _candidate_report_roots():
                    try:
                        cand.relative_to((root / "research" / "reports").resolve())
                        target = cand
                        break
                    except ValueError:
                        continue
                if target is not None:
                    break
            except Exception:
                continue
        if target is None or not target.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        media = (
            "text/markdown; charset=utf-8"
            if target.suffix.lower() == ".md"
            else "application/octet-stream"
        )
        return FileResponse(str(target), filename=target.name, media_type=media)

    # ── Live deep-research sessions (R3) ──────────────────────────────

    @router.get("/api/research/ready")
    async def research_ready(live: bool = False) -> JSONResponse:
        """Industry preflight: search backends, proxy, optional live probe."""
        try:
            from kazma_core.tools.research_readiness import research_readiness

            report = research_readiness(probe_search=bool(live))
            return JSONResponse({"ok": True, **report})
        except Exception as exc:
            logger.exception("[research] ready probe failed")
            return JSONResponse(
                {"ok": False, "ready": False, "error": str(exc), "checks": []},
                status_code=500,
            )

    @router.post("/api/research/sessions", dependencies=[Depends(rate_limit("research", 10))])
    async def start_research_session(body: dict[str, Any]) -> JSONResponse:
        """Start a deep research pipeline run in the background.

        Body: ``{"topic": "...", "depth": "deep"|"brief", "max_sources": 8,
        "export_docx": false}``
        """
        topic = str(body.get("topic") or "").strip()
        if not topic:
            return JSONResponse({"ok": False, "error": "topic required"}, status_code=400)
        depth = str(body.get("depth") or "deep").strip().lower() or "deep"
        try:
            max_sources = int(body.get("max_sources") or 8)
        except (TypeError, ValueError):
            max_sources = 8
        export_docx = bool(body.get("export_docx") or False)
        try:
            from kazma_core.tools.research_session import start_deep_research

            sess = await start_deep_research(
                topic,
                depth=depth,
                max_sources=max_sources,
                export_docx=export_docx,
            )
            return JSONResponse({"ok": True, "session": sess.to_dict()})
        except Exception as exc:
            logger.exception("[research] start session failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @router.get("/api/research/sessions")
    async def list_research_sessions(limit: int = 50) -> JSONResponse:
        """List durable deep-research sessions (newest first)."""
        try:
            from kazma_core.tools.research_session import list_sessions

            sessions = list_sessions(limit=limit)
            return JSONResponse(
                {
                    "ok": True,
                    "sessions": [s.to_dict() for s in sessions],
                    "count": len(sessions),
                }
            )
        except Exception as exc:
            logger.exception("[research] list sessions failed")
            return JSONResponse(
                {"ok": False, "error": str(exc), "sessions": []}, status_code=500
            )

    @router.get("/api/research/sessions/{session_id}")
    async def get_research_session(session_id: str) -> JSONResponse:
        """Get one research session (status, log, report path)."""
        try:
            from kazma_core.tools.research_session import get_session

            sess = get_session(session_id)
            if sess is None:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            return JSONResponse({"ok": True, "session": sess.to_dict()})
        except Exception as exc:
            logger.exception("[research] get session failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @router.post("/api/research/sessions/{session_id}/cancel")
    async def cancel_research_session(session_id: str) -> JSONResponse:
        """Cancel a running deep-research session (best-effort)."""
        try:
            from kazma_core.tools.research_session import cancel_session, get_session

            if get_session(session_id) is None:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            sess = cancel_session(session_id)
            return JSONResponse(
                {"ok": True, "session": sess.to_dict() if sess else None}
            )
        except Exception as exc:
            logger.exception("[research] cancel session failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @router.get("/api/research/eval")
    async def eval_research_report(
        path: str = "",
        session_id: str = "",
        min_sources: int = 4,
    ) -> JSONResponse:
        """Score a report file (or a session's report) with the structural rubric."""
        try:
            from kazma_core.tools.research_eval import evaluate_report_path
            from kazma_core.tools.research_pipeline import _candidate_report_roots

            report_path = (path or "").strip().replace("\\", "/")
            if session_id and not report_path:
                from kazma_core.tools.research_session import get_session

                sess = get_session(session_id)
                if not sess:
                    return JSONResponse(
                        {"ok": False, "error": "session not found"}, status_code=404
                    )
                report_path = sess.report_path or ""
            if not report_path:
                return JSONResponse(
                    {"ok": False, "error": "path or session_id with report required"},
                    status_code=400,
                )
            if ".." in report_path.split("/"):
                return JSONResponse({"ok": False, "error": "invalid path"}, status_code=400)

            target: Path | None = None
            cand = Path(report_path)
            if cand.is_file():
                target = cand
            else:
                for root in _candidate_report_roots():
                    p = (root / report_path).resolve()
                    if p.is_file():
                        target = p
                        break
            if target is None:
                return JSONResponse(
                    {"ok": False, "error": "report not found"}, status_code=404
                )
            result = evaluate_report_path(target, min_sources=min_sources)
            return JSONResponse({"ok": True, "eval": result})
        except Exception as exc:
            logger.exception("[research] eval failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @router.get("/api/research/sessions/{session_id}/stream")
    async def stream_research_session(
        session_id: str, request: Request
    ) -> StreamingResponse:
        """SSE stream of progress events for a research session.

        Emits ``snapshot`` (current state), then ``progress`` updates, then
        ``done`` / ``error``. Heartbeats every 15s while the run is live.
        """
        from kazma_core.tools.research_session import (
            get_session,
            subscribe_progress,
            unsubscribe_progress,
        )

        sess = get_session(session_id)
        if sess is None:
            return JSONResponse(  # type: ignore[return-value]
                {"ok": False, "error": "not found"}, status_code=404
            )

        async def event_gen() -> AsyncGenerator[str, None]:
            q = subscribe_progress(session_id)
            try:
                # Initial snapshot is already queued by subscribe_progress
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        cur = get_session(session_id)
                        if cur and cur.status in ("done", "error", "cancelled"):
                            yield sse_frame(
                                "done",
                                {
                                    "type": "done",
                                    "session_id": session_id,
                                    "status": cur.status,
                                    "session": cur.to_dict(),
                                },
                            )
                            break
                        yield sse_frame(
                            "heartbeat",
                            {"type": "heartbeat", "session_id": session_id},
                        )
                        continue

                    etype = str(event.get("type") or "progress")
                    yield sse_frame(etype, event)
                    if etype in ("done", "error"):
                        break
                    # Terminal via progress payload status
                    st = str(event.get("status") or "")
                    if st in ("done", "error", "cancelled"):
                        yield sse_frame(
                            "done",
                            {
                                "type": "done",
                                "session_id": session_id,
                                "status": st,
                            },
                        )
                        break
            finally:
                unsubscribe_progress(session_id, q)

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @router.post("/api/research/papers/export")
    async def export_paper(body: dict[str, Any]) -> JSONResponse:
        """Export a pipeline paper (report.md) to markdown / docx / pdf."""
        fmt = str(body.get("format") or "markdown").strip().lower()
        report_path = str(body.get("report_path") or "").strip().replace("\\", "/")
        topic = str(body.get("topic") or "Research report").strip()
        if not report_path:
            return JSONResponse({"error": "report_path required"}, status_code=400)

        # Resolve file via same logic as get_paper_file
        from kazma_core.tools.research_pipeline import _candidate_report_roots

        target: Path | None = None
        if Path(report_path).is_absolute() and Path(report_path).is_file():
            target = Path(report_path)
        else:
            for root in _candidate_report_roots():
                cand = (root / report_path).resolve()
                if cand.is_file():
                    target = cand
                    break
        if target is None or not target.is_file():
            return JSONResponse({"error": "report not found"}, status_code=404)

        try:
            md = target.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

        # Build sections for document generator
        sections: list[dict[str, str]] = []
        cur_h = "Report"
        cur_b: list[str] = []
        for line in md.splitlines():
            if line.startswith("#"):
                if cur_b or sections:
                    sections.append({"heading": cur_h, "body": "\n".join(cur_b).strip()})
                cur_h = line
                cur_b = []
            else:
                cur_b.append(line)
        if cur_b or not sections:
            sections.append({"heading": cur_h, "body": "\n".join(cur_b).strip()})

        title = topic.replace("[Paper] ", "")[:120] or "Research report"
        try:
            if fmt == "docx":
                from kazma_skills.native.document_generator.tools import generate_docx

                msg = await generate_docx(title, sections)
            elif fmt == "pdf":
                from kazma_skills.native.document_generator.tools import generate_pdf

                msg = await generate_pdf(title, sections)
            else:
                from kazma_skills.native.document_generator.tools import generate_markdown_doc

                msg = await generate_markdown_doc(title, sections)
        except Exception as exc:
            logger.exception("[research] paper export failed")
            return JSONResponse({"error": str(exc)}, status_code=500)

        path = ""
        if isinstance(msg, str) and "Saved to:" in msg:
            path = msg.split("Saved to:")[-1].strip()
        filename = Path(path).name if path else ""
        return JSONResponse(
            {
                "ok": True,
                "format": fmt,
                "message": msg,
                "path": path,
                "filename": filename,
                "download_url": (
                    f"/api/research/download?path={filename}" if filename else ""
                ),
            }
        )

    @router.get("/api/research/tasks")
    async def list_research(
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
        archived: bool = False,
    ) -> JSONResponse:
        """List research tasks (filtered by metadata.kind=research).

        Args:
            archived: When False (default), exclude archived tasks.
                      When True, show only archived tasks.
        """
        store = _get_store()
        if store is None:
            return JSONResponse({"tasks": [], "count": 0})

        tasks, total = store.list_tasks(
            page=page,
            page_size=page_size,
            metadata_filter={"kind": "research"},
            include_count=True,
        )
        if q:
            q_lower = q.lower()
            tasks = [t for t in tasks if q_lower in (t.prompt or "").lower()]
            total = len(tasks)

        # Filter by archived flag in metadata.
        def _is_archived(t: Any) -> bool:
            meta = t.metadata or {}
            return bool(meta.get("archived", False))

        if archived:
            tasks = [t for t in tasks if _is_archived(t)]
        else:
            tasks = [t for t in tasks if not _is_archived(t)]
        total = len(tasks)

        return JSONResponse({
            "tasks": [_flatten(t) for t in tasks],
            "count": len(tasks),
            "total": total,
        })

    @router.get("/api/research/tasks/{task_id}")
    async def research_detail(task_id: str) -> JSONResponse:
        """Get a single research result with full output."""
        store = _get_store()
        task = store.get_task(task_id) if store else None
        if task is None:
            # Fall back to the engine's in-memory tasks.
            try:
                from kazma_core.swarm import get_swarm_engine
                engine = get_swarm_engine()
                if engine:
                    task = engine.get_task(task_id) or engine.get_active_task(task_id)
            except Exception:
                pass
        if task is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"task": _flatten(task)})

    @router.post("/api/research/compare")
    async def compare_research(body: dict[str, Any]) -> JSONResponse:
        """Compare two research runs side-by-side.

        Body: ``{"a": "task-id-a", "b": "task-id-b"}``
        """
        from kazma_core.swarm.task import compare_task_results

        store = _get_store()
        if store is None:
            return JSONResponse({"error": "store unavailable"}, status_code=503)
        a_id = body.get("a", "")
        b_id = body.get("b", "")
        if not a_id or not b_id:
            return JSONResponse({"error": "a and b task IDs required"}, status_code=400)
        task_a = store.get_task(a_id)
        task_b = store.get_task(b_id)
        if task_a is None or task_b is None:
            return JSONResponse({"error": "one or both tasks not found"}, status_code=404)
        if task_a.result is None or task_b.result is None:
            return JSONResponse({"error": "one or both tasks have no result"}, status_code=400)
        diff = compare_task_results(task_a.result, task_b.result)
        return JSONResponse({
            "diff": diff,
            "a": {"id": a_id, "prompt": task_a.prompt[:100]},
            "b": {"id": b_id, "prompt": task_b.prompt[:100]},
        })

    @router.post("/api/research/{task_id}/export")
    async def export_research(task_id: str, body: dict[str, Any]) -> JSONResponse:
        """Export a research result to DOCX, PDF, or Markdown.

        Body: ``{"format": "docx" | "pdf" | "markdown"}``
        """
        # Try TaskStore first, then engine's in-memory active/completed tasks.
        store = _get_store()
        task = store.get_task(task_id) if store else None
        if task is None:
            # Fall back to the engine's in-memory tasks (not yet persisted).
            try:
                from kazma_core.swarm import get_swarm_engine
                engine = get_swarm_engine()
                if engine:
                    task = engine.get_task(task_id) or engine.get_active_task(task_id)
            except Exception:
                pass
        if task is None or task.result is None:
            return JSONResponse({"error": "task or result not found"}, status_code=404)

        fmt = (body.get("format") or "markdown").lower()
        result = task.result
        output = (
            result.aggregated_output
            or result.synthesized_output
            or (result.worker_results[0].output if result.worker_results else "")
            or "(no output)"
        )

        # Build sections: summary + one per worker.
        sections: list[dict[str, str]] = [{"heading": "Research Summary", "body": output}]
        for wr in result.worker_results:
            w = wr if isinstance(wr, dict) else wr.to_dict() if hasattr(wr, "to_dict") else {}
            worker_name = w.get("worker", "worker")
            worker_output = w.get("output", "")
            if worker_output and worker_output != output:
                sections.append({"heading": f"Worker: {worker_name}", "body": worker_output})

        title = (task.prompt or "Research Report")[:80]

        try:
            if fmt == "docx":
                from kazma_skills.native.document_generator.tools import generate_docx
                msg = await generate_docx(title, sections)
            elif fmt == "pdf":
                from kazma_skills.native.document_generator.tools import generate_pdf
                msg = await generate_pdf(title, sections)
            else:
                from kazma_skills.native.document_generator.tools import generate_markdown_doc
                msg = await generate_markdown_doc(title, sections)
        except Exception as exc:
            logger.exception("[research] export failed")
            return JSONResponse({"error": str(exc)}, status_code=500)

        # Parse the file path from the success message.
        path = ""
        if "Saved to:" in msg:
            path = msg.split("Saved to:")[-1].strip()

        return JSONResponse({
            "ok": True,
            "format": fmt,
            "message": msg,
            "path": path,
            "filename": Path(path).name if path else "",
        })

    @router.get("/api/research/download")
    async def download_export(path: str) -> Any:
        """Download an exported research file.

        Accepts both absolute paths and bare filenames (looked up in
        kazma-data/documents/). Security: only serves files from that dir.
        """
        safe_root = os.path.realpath("kazma-data/documents")
        # Resolve under the safe root and enforce segment-aware containment
        # (relative_to semantics via relpath), not a byte-prefix startswith()
        # check: the latter let sibling dirs like "documents_secret" pass.
        # Absolute caller paths are still honored ONLY if they resolve inside
        # safe_root.
        if os.path.isabs(path):
            real_path = os.path.realpath(path)
        else:
            real_path = os.path.realpath(os.path.join(safe_root, path))
        try:
            rel = os.path.relpath(real_path, safe_root)
            if rel.startswith("..") or os.path.isabs(rel):
                raise ValueError
        except ValueError:
            return JSONResponse({"error": "invalid file path"}, status_code=403)
        if not os.path.isfile(real_path):
            return JSONResponse({"error": "invalid file path"}, status_code=403)
        return FileResponse(
            real_path,
            filename=os.path.basename(real_path),
            media_type="application/octet-stream",
        )

    @router.post("/api/research/tasks/{task_id}/archive")
    async def archive_research(task_id: str) -> JSONResponse:
        """Archive a research task (sets metadata.archived = true)."""
        return await _set_archived(task_id, archived=True)

    @router.post("/api/research/tasks/{task_id}/unarchive")
    async def unarchive_research(task_id: str) -> JSONResponse:
        """Restore an archived research task (sets metadata.archived = false)."""
        return await _set_archived(task_id, archived=False)

    @router.delete("/api/research/tasks/{task_id}")
    async def delete_research(task_id: str) -> JSONResponse:
        """Delete a research task from the TaskStore."""
        store = _get_store()
        if store is None:
            return JSONResponse({"error": "store unavailable"}, status_code=503)
        try:
            # TaskStore doesn't have a delete method — use direct SQL.
            # Check Postgres FIRST (calling _get_conn() on a PG backend raises).
            if getattr(store, "_pg", False):
                from kazma_core.db.pg_helpers import get_pool
                get_pool().execute("DELETE FROM kazma_swarm_tasks WHERE id = %s", (task_id,))
            elif hasattr(store, "_get_conn"):
                conn = store._get_conn()
                conn.execute("DELETE FROM swarm_tasks WHERE id = ?", (task_id,))
                conn.commit()
            else:
                return JSONResponse({"error": "cannot access store"}, status_code=500)
            return JSONResponse({"ok": True, "deleted": task_id})
        except Exception as exc:
            logger.exception("[research] delete failed")
            return JSONResponse({"error": str(exc)}, status_code=500)

    return router
