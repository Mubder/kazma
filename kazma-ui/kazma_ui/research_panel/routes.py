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

Handlers that only read or write the stores are plain ``def`` (FastAPI runs
them in its threadpool, AGENTS §35); the async ones hold the work that must
await (the document generators, the progress stream), with their store
reads in ``asyncio.to_thread``. A report a request names is read only
through ``resolve_report_file`` -- inside a ``research/reports`` folder.
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
from kazma_core.errors import safe_error

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


def _find_task(task_id: str) -> tuple[Any, Any]:
    """(store, task): the TaskStore's row, else the engine's in-memory task.

    Blocking (a store read): call it from a thread or a plain-def handler.
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
            logger.debug("[research] engine task lookup failed", exc_info=True)
    return store, task


def _report_sections(md: str) -> list[dict[str, str]]:
    """Split a report's markdown into generator sections, one per heading."""
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
    return sections


def _session_markdown(sess: Any) -> str:
    """A session's report.md when it resolves, else its summary and log. Blocking."""
    from kazma_core.tools.research_pipeline import resolve_report_file

    md = ""
    target = resolve_report_file(sess.report_path, stored=True) if sess.report_path else None
    if target is not None:
        md = target.read_text(encoding="utf-8", errors="replace")
    if not md:
        md = (
            f"# {(sess.topic or 'Research session')[:120]}\n\n"
            f"{sess.summary or sess.message or ''}\n\n"
            "## Log\n\n"
            + "\n".join(f"- {entry}" for entry in (sess.log or []))
        )
    return md


async def _generate(fmt: str, title: str, sections: list[dict[str, str]]) -> str:
    """Render *sections* through the document generator in *fmt*."""
    if fmt == "docx":
        from kazma_skills.native.document_generator.tools import generate_docx

        return await generate_docx(title, sections)
    if fmt == "pdf":
        from kazma_skills.native.document_generator.tools import generate_pdf

        return await generate_pdf(title, sections)
    from kazma_skills.native.document_generator.tools import generate_markdown_doc

    return await generate_markdown_doc(title, sections)


def _export_reply(fmt: str, msg: Any) -> JSONResponse:
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
            "download_url": (f"/api/research/download?path={filename}" if filename else ""),
        }
    )


def _set_archived(task_id: str, archived: bool) -> JSONResponse:
    """Toggle the archived flag on a research task's metadata.

    Loads the task from the TaskStore (or in-memory engine), mutates
    ``metadata["archived"]``, and re-persists. This respects the store's
    locking and works for both SQLite and Postgres. Blocking.
    """
    store, task = _find_task(task_id)
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
            return JSONResponse({"error": safe_error(exc)}, status_code=500)
    else:
        return JSONResponse({"error": "store unavailable"}, status_code=503)

    return JSONResponse({"ok": True, "task_id": task_id, "archived": archived})


def create_research_router() -> APIRouter:
    """Create the research API router."""
    router = APIRouter(tags=["research"])

    @router.get("/api/research/papers")
    def list_papers(limit: int = 50) -> JSONResponse:
        """List deep research pipeline paper runs (report.md under research/reports/)."""
        try:
            from kazma_core.tools.research_pipeline import list_research_papers

            papers = list_research_papers(limit=limit)
            return JSONResponse({"ok": True, "papers": papers, "count": len(papers)})
        except Exception as exc:
            logger.exception("[research] list papers failed")
            return JSONResponse({"ok": False, "error": safe_error(exc), "papers": []}, status_code=500)

    @router.get("/api/research/papers/file")
    def get_paper_file(path: str) -> Any:
        """Serve a research report file (under research/reports/ in any known workspace).

        Only a relative path inside a reports folder: an absolute path once
        bypassed the containment check through a substring fallback.
        """
        from kazma_core.tools.research_pipeline import resolve_report_file

        target = resolve_report_file(path)
        if target is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        media = (
            "text/markdown; charset=utf-8"
            if target.suffix.lower() == ".md"
            else "application/octet-stream"
        )
        return FileResponse(str(target), filename=target.name, media_type=media)

    # ── Live deep-research sessions (R3) ──────────────────────────────

    @router.get("/api/research/ready", dependencies=[Depends(rate_limit("research", 10))])
    def research_ready(live: bool = False) -> JSONResponse:
        """Industry preflight: search backends, proxy, optional live probe."""
        try:
            from kazma_core.tools.research_readiness import research_readiness

            report = research_readiness(probe_search=bool(live))
            return JSONResponse({"ok": True, **report})
        except Exception as exc:
            logger.exception("[research] ready probe failed")
            return JSONResponse(
                {"ok": False, "ready": False, "error": safe_error(exc), "checks": []},
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
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    @router.get("/api/research/sessions")
    def list_research_sessions(
        limit: int = 50, archived: bool = False
    ) -> JSONResponse:
        """List durable deep-research sessions (newest first).

        Archived sessions are excluded by default (mirrors the tasks list);
        pass ``archived=true`` for the Archived tab.
        """
        try:
            from kazma_core.tools.research_session import list_sessions

            sessions = list_sessions(limit=limit, archived=archived)
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
                {"ok": False, "error": safe_error(exc), "sessions": []}, status_code=500
            )

    @router.post("/api/research/sessions/{session_id}/archive")
    def archive_research_session(session_id: str) -> JSONResponse:
        """Soft-hide a session from the main Research list."""
        try:
            from kazma_core.tools.research_session import archive_session, get_session

            if get_session(session_id) is None:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            sess = archive_session(session_id, archived=True)
            return JSONResponse({"ok": True, "session": sess.to_dict() if sess else None})
        except Exception as exc:
            logger.exception("[research] archive session failed")
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    @router.post("/api/research/sessions/{session_id}/unarchive")
    def unarchive_research_session(session_id: str) -> JSONResponse:
        """Restore an archived session to the main Research list."""
        try:
            from kazma_core.tools.research_session import archive_session, get_session

            if get_session(session_id) is None:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            sess = archive_session(session_id, archived=False)
            return JSONResponse({"ok": True, "session": sess.to_dict() if sess else None})
        except Exception as exc:
            logger.exception("[research] unarchive session failed")
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    @router.delete("/api/research/sessions/{session_id}")
    def delete_research_session(session_id: str) -> JSONResponse:
        """Delete a session row (cancels first when still running)."""
        try:
            from kazma_core.tools.research_session import delete_session

            deleted = delete_session(session_id)
            if not deleted:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            return JSONResponse({"ok": True, "deleted": session_id})
        except Exception as exc:
            logger.exception("[research] delete session failed")
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    @router.get("/api/research/sessions/{session_id}")
    def get_research_session(session_id: str) -> JSONResponse:
        """Get one research session (status, log, report path)."""
        try:
            from kazma_core.tools.research_session import get_session

            sess = get_session(session_id)
            if sess is None:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            return JSONResponse({"ok": True, "session": sess.to_dict()})
        except Exception as exc:
            logger.exception("[research] get session failed")
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    @router.post("/api/research/sessions/{session_id}/cancel")
    def cancel_research_session(session_id: str) -> JSONResponse:
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
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    @router.post("/api/research/sessions/{session_id}/export")
    async def export_research_session(session_id: str, body: dict[str, Any]) -> JSONResponse:
        """Export a durable research session to markdown / docx / pdf.

        Sessions live in research_sessions.db (NOT the swarm TaskStore), so
        routing their ids to /api/research/{task_id}/export 404'd. Prefers
        the session's report.md when it resolves (absolute or under the
        candidate report roots); otherwise builds the document from the
        stored summary (full chat-research output) + log. Mirrors
        export_paper's sectioning/generator/JSON shape.
        """
        fmt = str((body or {}).get("format") or "markdown").strip().lower()
        try:
            from kazma_core.tools.research_session import get_session

            sess = await asyncio.to_thread(get_session, session_id)
        except Exception as exc:
            logger.exception("[research] session export lookup failed")
            return JSONResponse({"error": safe_error(exc)}, status_code=500)
        if sess is None:
            return JSONResponse({"error": "session not found"}, status_code=404)

        try:
            md = await asyncio.to_thread(_session_markdown, sess)
        except Exception as exc:
            logger.exception("[research] session report read failed")
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

        title = (sess.topic or "Research session")[:120]
        try:
            msg = await _generate(fmt, title, _report_sections(md))
        except Exception as exc:
            logger.exception("[research] session export failed")
            return JSONResponse({"error": safe_error(exc)}, status_code=500)
        return _export_reply(fmt, msg)

    @router.get("/api/research/eval")
    def eval_research_report(
        path: str = "",
        session_id: str = "",
        min_sources: int = 4,
    ) -> JSONResponse:
        """Score a report file (or a session's report) with the structural rubric.

        *path* names a report inside a ``research/reports`` folder, like every
        route that reads one; it scored any file on disk until 2026-10-01
        (its size, links and words: an oracle for files outside Kazma).
        """
        try:
            from kazma_core.tools.research_eval import evaluate_report_path
            from kazma_core.tools.research_pipeline import resolve_report_file

            report_path = (path or "").strip()
            stored = False
            if session_id and not report_path:
                from kazma_core.tools.research_session import get_session

                sess = get_session(session_id)
                if not sess:
                    return JSONResponse(
                        {"ok": False, "error": "session not found"}, status_code=404
                    )
                report_path = sess.report_path or ""
                stored = True
            if not report_path:
                return JSONResponse(
                    {"ok": False, "error": "path or session_id with report required"},
                    status_code=400,
                )
            target = resolve_report_file(report_path, stored=stored)
            if target is None:
                return JSONResponse(
                    {"ok": False, "error": "report not found"}, status_code=404
                )
            result = evaluate_report_path(target, min_sources=min_sources)
            return JSONResponse({"ok": True, "eval": result})
        except Exception as exc:
            logger.exception("[research] eval failed")
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

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

        sess = await asyncio.to_thread(get_session, session_id)
        if sess is None:
            return JSONResponse(  # type: ignore[return-value]
                {"ok": False, "error": "not found"}, status_code=404
            )

        async def event_gen() -> AsyncGenerator[str, None]:
            # Subscribed from a thread (it reads the session for the first
            # snapshot); the events are read here, on this loop.
            q = await asyncio.to_thread(
                subscribe_progress, session_id, loop=asyncio.get_running_loop()
            )
            try:
                # Initial snapshot is already queued by subscribe_progress
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        cur = await asyncio.to_thread(get_session, session_id)
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
        """Export a pipeline paper (report.md) to markdown / docx / pdf.

        *report_path* names a report inside a ``research/reports`` folder.
        Until 2026-10-01 an absolute path (or ``..``) named any file the
        server could read, which this exported and ``/download`` served.
        """
        from kazma_core.tools.research_pipeline import resolve_report_file

        fmt = str(body.get("format") or "markdown").strip().lower()
        report_path = str(body.get("report_path") or "").strip()
        topic = str(body.get("topic") or "Research report").strip()
        if not report_path:
            return JSONResponse({"error": "report_path required"}, status_code=400)

        target = await asyncio.to_thread(resolve_report_file, report_path)
        if target is None:
            return JSONResponse({"error": "report not found"}, status_code=404)
        try:
            md = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
        except Exception as exc:
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

        title = topic.replace("[Paper] ", "")[:120] or "Research report"
        try:
            msg = await _generate(fmt, title, _report_sections(md))
        except Exception as exc:
            logger.exception("[research] paper export failed")
            return JSONResponse({"error": safe_error(exc)}, status_code=500)
        return _export_reply(fmt, msg)

    @router.get("/api/research/tasks")
    def list_research(
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
    def research_detail(task_id: str) -> JSONResponse:
        """Get a single research result with full output."""
        _store, task = _find_task(task_id)
        if task is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"task": _flatten(task)})

    @router.post("/api/research/compare")
    def compare_research(body: dict[str, Any]) -> JSONResponse:
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
        # TaskStore first, then the engine's in-memory tasks (not yet persisted).
        _store, task = await asyncio.to_thread(_find_task, task_id)
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
            msg = await _generate(fmt, title, sections)
        except Exception as exc:
            logger.exception("[research] export failed")
            return JSONResponse({"error": safe_error(exc)}, status_code=500)
        return _export_reply(fmt, msg)

    @router.get("/api/research/download")
    def download_export(path: str) -> Any:
        """Download an exported research file.

        Accepts both absolute paths and bare filenames (looked up in
        kazma-data/documents/). Security: only serves files from that dir.
        """
        from kazma_core.paths import data_dir as _dd

        safe_root = os.path.realpath(str(_dd() / "documents"))
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
    def archive_research(task_id: str) -> JSONResponse:
        """Archive a research task (sets metadata.archived = true)."""
        return _set_archived(task_id, archived=True)

    @router.post("/api/research/tasks/{task_id}/unarchive")
    def unarchive_research(task_id: str) -> JSONResponse:
        """Restore an archived research task (sets metadata.archived = false)."""
        return _set_archived(task_id, archived=False)

    @router.delete("/api/research/tasks/{task_id}")
    def delete_research(task_id: str) -> JSONResponse:
        """Delete a research task from the TaskStore.

        A plain ``def``: every line is a database call, and as ``async def``
        they ran on the event loop (AGENTS §35).
        """
        store = _get_store()
        if store is None:
            return JSONResponse({"error": "store unavailable"}, status_code=503)
        try:
            # Idempotent: an id already gone is the state the caller wanted.
            store.delete_task(task_id)
            return JSONResponse({"ok": True, "deleted": task_id})
        except Exception as exc:
            logger.exception("[research] delete failed")
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

    return router
