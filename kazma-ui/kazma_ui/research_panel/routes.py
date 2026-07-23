"""Research panel API routes — list, detail, compare, and export research results.

All routes reuse the SwarmEngine's TaskStore (no dedicated store needed).
Research tasks are tagged with ``metadata={"kind": "research"}`` at dispatch
time (by the ``dispatch_swarm`` tool).

Routes:
  GET  /api/research/tasks           — list research tasks (filtered)
  GET  /api/research/tasks/{id}      — single research result detail
  POST /api/research/compare         — compare two research runs
  POST /api/research/{id}/export     — export to DOCX/PDF/Markdown
  GET  /api/research/download        — download an exported file
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, FileResponse

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


def create_research_router() -> APIRouter:
    """Create the research API router."""
    router = APIRouter(tags=["research"])

    @router.get("/api/research/tasks")
    async def list_research(
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
    ) -> JSONResponse:
        """List research tasks (filtered by metadata.kind=research)."""
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
        safe_root = os.path.abspath("kazma-data/documents")
        # Accept bare filename, relative path, or absolute path.
        if os.path.isabs(path):
            real_path = os.path.abspath(path)
        else:
            real_path = os.path.abspath(os.path.join("kazma-data/documents", path))
        if not real_path.startswith(safe_root) or not os.path.isfile(real_path):
            return JSONResponse({"error": "invalid file path"}, status_code=403)
        return FileResponse(
            real_path,
            filename=os.path.basename(real_path),
            media_type="application/octet-stream",
        )

    return router
