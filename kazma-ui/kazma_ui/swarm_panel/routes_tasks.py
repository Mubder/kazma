"""Task routes for the swarm panel.

Extracted for maintainability.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from kazma_gateway.telegram_format import (
    tg_escape,
    md_to_tg_html,
    tg_quote,
    tg_heading,
    HEADING_RULE,
)
from kazma_ui.services import get_swarm_service

logger = logging.getLogger(__name__)

__all__ = ["register_tasks_routes"]

try:
    from kazma_core.swarm import (
        SwarmTask,
        TaskType,
    )
except ImportError:
    SwarmTask = None
    TaskType = None


def _coerce_task_type(payload: dict[str, Any], worker_names: list[str]) -> Any:
    """Resolve the requested swarm task type from the API payload."""
    if TaskType is None:
        return None

    raw_value = str(payload.get("pattern") or payload.get("type") or "").strip().lower()
    normalized = raw_value.replace("-", "_")

    if normalized in {"fan_out", "fanout"}:
        return TaskType.FAN_OUT
    if normalized == "pipeline":
        return TaskType.PIPELINE
    if normalized == "consult":
        return TaskType.CONSULT
    if normalized == "conditional":
        return TaskType.CONDITIONAL
    if normalized == "broadcast":
        return TaskType.BROADCAST
    if normalized == "dispatch":
        return TaskType.DISPATCH
    return TaskType.DISPATCH if len(worker_names) == 1 else TaskType.BROADCAST


def _coerce_timeout(payload: dict[str, Any]) -> float:
    """Return a numeric task timeout from the API payload."""
    try:
        return float(payload.get("timeout", 300.0))
    except (TypeError, ValueError):
        return 300.0


def _flatten_swarm_task(task: Any) -> dict[str, Any]:
    """Flatten a SwarmTask (with nested result) to the UI-expected shape."""
    data = task.to_dict() if hasattr(task, "to_dict") else dict(task)
    result = data.get("result") or {}
    return {
        "id": data.get("id"),
        "task_id": data.get("id"),
        "type": data.get("type"),
        "prompt": data.get("prompt"),
        "context": data.get("context"),
        "workers": data.get("workers", []),
        "status": result.get("status", data.get("status")),
        "created_at": data.get("created_at"),
        "started_at": data.get("started_at"),
        "completed_at": data.get("completed_at"),
        "duration_seconds": result.get("duration_seconds"),
        "total_cost": result.get("total_cost"),
        "total_tokens": result.get("total_tokens"),
        "worker_results": result.get("worker_results", []),
        "individual_opinions": result.get("individual_opinions", []),
        "aggregated_output": result.get("aggregated_output"),
        "synthesized_output": result.get("synthesized_output"),
        "error": result.get("error"),
        "metadata": result.get("metadata", data.get("metadata", {})),
    }


def register_tasks_routes(
    router: APIRouter,
    templates: Any,
    swarm_manager: Any = None,
    config_store: Any = None,
) -> None:
    """Register task related routes."""

    def _current_engine() -> Any:
        svc = get_swarm_service()
        return svc.resolve_engine(swarm_manager)

    async def _maybe_send_to_output_target_fallback(
        text: str, *, is_html: bool = False
    ) -> bool:
        """Send text to output target, resolving GatewayManager from ServiceContainer.

        When ``is_html`` is True the text is already Telegram HTML (built by
        ``_route_task_result``) and must NOT be re-converted — passing it
        through ``md_to_tg_html`` would double-escape the tags (``<b>`` →
        ``&lt;b&gt;``) so Telegram renders them as literal text.
        """
        try:
            from kazma_core.service_container import get_container
            from kazma_gateway import GatewayManager
            from kazma_gateway.agent_handler import _maybe_send_to_output_target
        except ImportError:
            logger.debug("[Swarm] Gateway or Core modules not available for output target routing.")
            return False

        try:
            container = get_container()
            manager = container.get(GatewayManager) if container.has(GatewayManager) else None
            return await _maybe_send_to_output_target(manager, text, is_html=is_html)
        except Exception as exc:
            logger.debug("[Swarm] Failed resolving/invoking output routing fallback: %s", exc)
            return False

    async def _route_task_result(result: Any) -> bool:
        """Format and send the completed TaskResult to the output target."""
        task_id = ""
        status = ""
        aggregated_output = ""
        error = ""
        duration = 0.0
        tokens = 0
        worker_results = []

        if result is None:
            status = "failed"
            error = "No result returned from swarm."
        elif hasattr(result, "task_id"):
            task_id = getattr(result, "task_id", "") or ""
            status = getattr(result, "status", "") or ""
            aggregated_output = getattr(result, "aggregated_output", "") or ""
            error = getattr(result, "error", "") or ""
            duration = getattr(result, "duration_seconds", 0.0) or 0.0
            tokens = getattr(result, "total_tokens", 0) or 0
            worker_results = getattr(result, "worker_results", []) or []
        elif isinstance(result, dict):
            task_id = result.get("task_id", "") or ""
            status = result.get("status", "") or ""
            aggregated_output = result.get("aggregated_output", "") or ""
            error = result.get("error", "") or ""
            duration = result.get("duration_seconds", 0.0) or 0.0
            tokens = result.get("total_tokens", 0) or 0
            worker_results = result.get("worker_results", []) or []
        else:
            status = "failed"
            error = "No result returned from swarm."

        status_lower = str(status).lower()
        if status_lower == "success":
            status_icon = "✅"
            status_text = "SUCCESS"
        elif status_lower == "partial_success":
            status_icon = "⚠️"
            status_text = "PARTIAL SUCCESS"
        else:
            status_icon = "❌"
            status_text = "FAILED" if status_lower else "UNKNOWN"

        # Telegram HTML: bold headings + plain worker labels + Quote blockquotes
        lines: list[str] = []
        lines.append(f"🚀 {tg_heading('Swarm Task Execution Report')}")
        lines.append(HEADING_RULE)
        if task_id:
            lines.append(f"🆔 {tg_heading('Task ID:')} <code>{tg_escape(task_id)}</code>")
        lines.append(f"📊 {tg_heading('Status:')} {status_icon} {tg_heading(status_text)}")
        if duration > 0:
            dur = f"<code>{duration:.2f}s</code>"
            tok = f" | 🪙 {tg_heading('Tokens:')} <code>{tokens}</code>" if tokens > 0 else ""
            lines.append(f"⏱️ {tg_heading('Duration:')} {dur}{tok}")
        lines.append(HEADING_RULE)

        if error:
            lines.append(f"⚠️ {tg_heading('Error Details:')}")
            lines.append(tg_quote(error))
            lines.append("")

        if aggregated_output:
            lines.append(f"✨ {tg_heading('Final Aggregated Output:')}")
            lines.append(tg_quote(aggregated_output))
            lines.append("")

        if worker_results:
            lines.append(f"👥 {tg_heading('Worker Breakdowns:')}")
            lines.append("")
            for wr in worker_results:
                wr_name = ""
                wr_status = ""
                wr_output = ""
                wr_error = ""
                wr_duration = 0.0
                wr_tokens = 0

                if hasattr(wr, "worker"):
                    wr_name = getattr(wr, "worker", "unknown") or "unknown"
                    wr_status = getattr(wr, "status", "") or ""
                    wr_output = getattr(wr, "output", "") or ""
                    wr_error = getattr(wr, "error", "") or ""
                    wr_duration = getattr(wr, "duration_seconds", 0.0) or 0.0
                    wr_tokens = getattr(wr, "tokens_used", 0) or 0
                elif isinstance(wr, dict):
                    wr_name = wr.get("worker", "unknown") or "unknown"
                    wr_status = wr.get("status", "") or ""
                    wr_output = wr.get("output", "") or ""
                    wr_error = wr.get("error", "") or ""
                    wr_duration = wr.get("duration_seconds", 0.0) or 0.0
                    wr_tokens = wr.get("tokens_used", 0) or 0

                wr_status_lower = str(wr_status).lower()
                wr_icon = "✅" if wr_status_lower == "success" else "❌"

                # Plain worker line — no bold/italic (user preference)
                lines.append(
                    f"• {tg_escape(wr_name)} ({wr_icon} {wr_status_lower.upper() or 'UNKNOWN'})"
                )
                meta_parts = []
                if wr_duration > 0:
                    meta_parts.append(f"<code>{wr_duration:.2f}s</code>")
                if wr_tokens > 0:
                    meta_parts.append(f"<code>{wr_tokens}</code> tokens")
                if meta_parts:
                    lines.append("⏱️ " + " | ".join(meta_parts))

                raw_content = wr_output if wr_output else (wr_error or "no output")
                # Full worker output for complete reference — long messages are
                # split into multiple Telegram chunks by chunk_html_message.
                lines.append(tg_quote(raw_content.strip() or "no output"))
                lines.append("")

        text = "\n".join(lines).strip()
        # text is already Telegram HTML (tg_heading/tg_quote) — flag it so the
        # routing layer skips md_to_tg_html and avoids double-escaping the tags.
        return await _maybe_send_to_output_target_fallback(text, is_html=True)

    async def _run_and_route_task(
        engine: Any,
        swarm_task: Any,
        is_broadcast: bool = False,
    ) -> Any:
        """Execute dispatch/broadcast and automatically route the result on completion."""
        try:
            if is_broadcast:
                result = await engine.broadcast(swarm_task)
            else:
                result = await engine.dispatch(swarm_task)
            await _route_task_result(result)
            return result
        except Exception as exc:
            logger.exception("[Swarm] Task execution failed under auto-routing wrapper")
            error_msg = f"⚠️ Swarm task failed: {exc}"
            await _maybe_send_to_output_target_fallback(error_msg)
            raise exc

    @router.post("/api/swarm/dispatch")
    async def swarm_dispatch(payload: dict[str, Any]) -> JSONResponse:
        """Dispatch a task to one or more workers."""
        worker_names = payload.get("workers", [])
        task = str(payload.get("task", "")).strip()
        context = str(payload.get("context", ""))
        task_type = _coerce_task_type(payload, worker_names)
        timeout = _coerce_timeout(payload)
        background = bool(payload.get("background", False))

        if task_type == getattr(TaskType, "PIPELINE", None) and not worker_names:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Pipeline requires at least one worker.",
                },
                status_code=400,
            )
        if task_type == getattr(TaskType, "CONSULT", None) and not worker_names:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Consult requires at least one worker.",
                },
                status_code=400,
            )
        if task_type == getattr(TaskType, "CONDITIONAL", None) and not worker_names:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Conditional requires at least one worker.",
                },
                status_code=400,
            )
        if task_type == getattr(TaskType, "CONDITIONAL", None) and not payload.get("metadata", {}).get("routes"):
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Conditional requires a 'routes' mapping in task metadata.",
                },
                status_code=400,
            )
        if not worker_names:
            return JSONResponse(
                {"status": "error", "message": "No workers specified"},
                status_code=400,
            )
        if not task:
            return JSONResponse(
                {"status": "error", "message": "No task specified"},
                status_code=400,
            )

        engine = _current_engine()
        svc = get_swarm_service()
        if not svc.has_swarm_core() or engine is None:
            return JSONResponse(
                {
                    "status": "warning",
                    "message": (
                        "kazma_core.swarm is not installed — task recorded locally "
                        "but no workers will execute it. Install: pip install kazma-core[swarm]"
                    ),
                    "dispatched": [],
                    "missing": list(worker_names),
                    "results": [],
                }
            )

        dispatched = [name for name in worker_names if engine.get_worker(name) is not None]
        missing = [name for name in worker_names if name not in dispatched]
        if not dispatched:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "None of the specified workers are registered",
                    "missing": missing,
                },
                status_code=400,
            )
        results: list[dict[str, Any]] = []
        task_result: Any | None = None
        task_metadata = dict(payload.get("metadata", {})) if isinstance(
            payload.get("metadata"), dict
        ) else {}
        if "max_concurrent" in payload:
            task_metadata["max_concurrent"] = payload.get("max_concurrent")
        if "max_retries" in payload:
            task_metadata["max_retries"] = payload.get("max_retries")

        manager_engine = getattr(swarm_manager, "engine", None)
        from kazma_core.swarm import SwarmEngine
        if not isinstance(manager_engine, SwarmEngine):
            manager_engine = None
        uses_external_dispatch = (
            swarm_manager is not None
            and manager_engine is None
            and task_type
            not in {
                getattr(TaskType, "PIPELINE", None),
                getattr(TaskType, "FAN_OUT", None),
                getattr(TaskType, "CONSULT", None),
                getattr(TaskType, "CONDITIONAL", None),
            }
        )
        if uses_external_dispatch:
            for name in dispatched:
                worker = engine.get_worker(name)
                if worker is not None:
                    worker.mark_dispatched(task)
                try:
                    result = await swarm_manager.dispatch(name, task, context)
                except Exception as exc:
                    logger.exception("[Swarm] delegated dispatch failed for worker '%s'", name)
                    result = {
                        "worker": name,
                        "task_id": "",
                        "status": "error",
                        "output": "",
                        "error": "Dispatch failed — check server logs for details.",
                    }
                if worker is not None:
                    worker.mark_completed(result.get("status", "error"))
                results.append(result)
            return JSONResponse(
                {
                    "status": "ok",
                    "message": f"Task delegated to SwarmManager for {len(dispatched)} worker(s)",
                    "dispatched": dispatched,
                    "missing": missing,
                    "task": task,
                    "results": results,
                    "task_id": None,
                    "result_status": "success" if any(r.get("status") == "success" for r in results) else "error",
                }
            )

        swarm_task = SwarmTask(
            prompt=task,
            context=context,
            workers=dispatched,
            type=task_type,
            timeout=timeout,
            aggregation=str(payload.get("aggregation") or "collect"),
            fallback_chain=list(payload.get("fallback_chain", [])),
            metadata=task_metadata,
        )
        if task_type == TaskType.BROADCAST:
            if background:
                _handle = asyncio.create_task(_run_and_route_task(engine, swarm_task, is_broadcast=True))
                svc.register_task_handle(swarm_task.id, _handle)
                # Clean up handle on completion to prevent memory leak
                _handle.add_done_callback(
                    lambda h, tid=swarm_task.id: svc.unregister_task_handle(tid)
                )
                return JSONResponse({
                    "status": "ok",
                    "message": f"Task dispatched (background) to {len(dispatched)} worker(s)",
                    "task_id": swarm_task.id,
                    "result_status": "running",
                    "dispatched": dispatched,
                })
            task_result = await _run_and_route_task(engine, swarm_task, is_broadcast=True)
        else:
            if background:
                _handle = asyncio.create_task(_run_and_route_task(engine, swarm_task, is_broadcast=False))
                svc.register_task_handle(swarm_task.id, _handle)
                # Clean up handle on completion to prevent memory leak
                _handle.add_done_callback(
                    lambda h, tid=swarm_task.id: svc.unregister_task_handle(tid)
                )
                return JSONResponse({
                    "status": "ok",
                    "message": f"Task dispatched (background) to {len(dispatched)} worker(s)",
                    "task_id": swarm_task.id,
                    "result_status": "running",
                    "dispatched": dispatched,
                })
            task_result = await _run_and_route_task(engine, swarm_task, is_broadcast=False)
        results = [item.to_dict() for item in task_result.worker_results]

        # Include checkpoint info for HITL paused pipelines.
        checkpoint_info = None
        if (
            task_result is not None
            and task_result.status == "paused"
            and task_result.metadata
        ):
            checkpoint_info = task_result.metadata.get("checkpoint")

        return JSONResponse(
            {
                "status": "ok",
                "message": f"Task dispatched to {len(dispatched)} worker(s)",
                "dispatched": dispatched,
                "missing": missing,
                "task": task,
                "results": results,
                "task_id": None if task_result is None else task_result.task_id,
                "result_status": None if task_result is None else task_result.status,
                "aggregated_output": None if task_result is None else task_result.aggregated_output,
                "individual_opinions": (
                    []
                    if task_result is None
                    else [item.to_dict() for item in task_result.individual_opinions]
                ),
                "synthesized_output": (
                    None if task_result is None else task_result.synthesized_output
                ),
                "error": None if task_result is None else task_result.error,
                "metadata": None if task_result is None else task_result.metadata,
                "checkpoint": checkpoint_info,
            }
        )

    @router.get("/api/swarm/tasks/active")
    async def swarm_active_tasks() -> JSONResponse:
        """Return all in-flight (running or paused) tasks."""
        engine = _current_engine()
        svc = get_swarm_service()
        if not svc.has_swarm_core() or engine is None:
            return JSONResponse({"tasks": [], "count": 0})
        active = engine.list_active_tasks()
        flat = [_flatten_swarm_task(t) for t in active]
        return JSONResponse({"tasks": flat, "count": len(flat)})

    @router.get("/api/swarm/tasks")
    async def swarm_tasks(
        task_type: str | None = Query(default=None, alias="type"),
        status: str | None = Query(default=None),
        worker: str | None = Query(default=None),
        q: str | None = Query(default=None),
        kind: str | None = Query(default=None),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    ) -> JSONResponse:
        """Return completed swarm tasks with pagination, filtering, and search."""
        engine = _current_engine()
        svc = get_swarm_service()
        if not svc.has_swarm_core() or engine is None:
            return JSONResponse({"tasks": [], "count": 0})

        metadata_filter = {"kind": kind} if kind else None

        # Use TaskStore for paginated queries when available.
        store = getattr(engine, "task_store", None) or getattr(engine, "_task_store", None)
        if store is not None:
            if q:
                all_tasks, _ = store.list_tasks(
                    page=1,
                    page_size=10000,
                    status=status,
                    task_type=task_type,
                    worker=worker,
                    metadata_filter=metadata_filter,
                    include_count=False,
                )
                q_lower = q.lower()
                filtered = [t for t in all_tasks if q_lower in (t.prompt or "").lower()]
                total = len(filtered)
                start_idx = (page - 1) * page_size
                tasks = filtered[start_idx:start_idx + page_size]
            else:
                tasks, total = store.list_tasks(
                    page=page,
                    page_size=page_size,
                    status=status,
                    task_type=task_type,
                    worker=worker,
                    metadata_filter=metadata_filter,
                    include_count=True,
                )
            return JSONResponse({
                "tasks": [_flatten_swarm_task(task) for task in tasks],
                "count": len(tasks),
                "total": total,
                "page": page,
                "pageSize": page_size,
            })

        # Fallback to in-memory history.
        tasks = [task.to_dict() if hasattr(task, 'to_dict') else dict(task) for task in engine.list_tasks(task_type)]
        return JSONResponse({"tasks": tasks, "count": len(tasks)})

    @router.get("/api/swarm/tasks/export")
    async def swarm_tasks_export(
        format: str = Query(default="json"),
        status: str | None = Query(default=None),
    ):
        """Export task history as JSON or CSV."""
        engine = _current_engine()
        svc = get_swarm_service()
        if not svc.has_swarm_core() or engine is None:
            return JSONResponse({"tasks": []})

        store = getattr(engine, "task_store", None) or getattr(engine, "_task_store", None)
        if store is not None:
            tasks, _ = store.list_tasks(page=1, page_size=1000, status=status, include_count=True)
        else:
            tasks = []

        flat = [_flatten_swarm_task(task) for task in tasks]

        if format.lower() == "csv":
            import csv
            import io

            output = io.StringIO()
            if flat:
                writer = csv.DictWriter(output, fieldnames=flat[0].keys())
                writer.writeheader()
                writer.writerows(flat)
            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=swarm_tasks.csv"},
            )

        return JSONResponse({"tasks": flat, "count": len(flat)})

    @router.get("/api/swarm/tasks/{task_id}")
    async def swarm_task_detail(task_id: str) -> JSONResponse:
        """Return full detail for a single swarm task."""
        engine = _current_engine()
        svc = get_swarm_service()
        if not svc.has_swarm_core() or engine is None:
            return JSONResponse(
                {"status": "error", "message": "Swarm core is not available"},
                status_code=503,
            )

        # Try TaskStore first (survives restart), then in-memory history.
        store = getattr(engine, "task_store", None) or getattr(engine, "_task_store", None)
        task = None
        if store is not None:
            task = store.get_task(task_id)
        if task is None:
            task = engine.get_task(task_id)
        if task is None:
            return JSONResponse(
                {"status": "error", "message": f"Task '{task_id}' not found"},
                status_code=404,
            )

        return JSONResponse({
            "task": _flatten_swarm_task(task),
        })

    @router.post("/api/swarm/tasks/{task_id}/approve")
    async def swarm_approve_checkpoint(task_id: str) -> JSONResponse:
        """Approve an HITL checkpoint and resume the pipeline."""
        engine = _current_engine()
        svc = get_swarm_service()
        if not svc.has_swarm_core() or engine is None:
            return JSONResponse(
                {"status": "error", "message": "Swarm core is not available"},
                status_code=503,
            )

        checkpoint_info = engine.get_checkpoint_info(task_id) if hasattr(engine, "get_checkpoint_info") else None
        if checkpoint_info is None:
            # Check if the task exists but is not paused.
            task_obj = engine.get_task(task_id)
            if task_obj is not None and task_obj.status != "paused":
                return JSONResponse(
                    {
                        "status": "error",
                        "message": f"Task '{task_id}' is not paused (status: {task_obj.status.value})",
                    },
                    status_code=409,
                )
            return JSONResponse(
                {"status": "error", "message": f"Task '{task_id}' not found"},
                status_code=404,
            )

        result = await engine.approve_checkpoint(task_id)
        if result is None:
            return JSONResponse(
                {"status": "error", "message": f"Failed to approve checkpoint for task '{task_id}'"},
                status_code=500,
            )

        return JSONResponse({
            "status": result.status,
            "message": "Checkpoint approved, pipeline resumed",
            "task_id": result.task_id,
            "worker_results": [item.to_dict() for item in result.worker_results],
            "aggregated_output": result.aggregated_output,
            "error": result.error,
            "metadata": result.metadata,
        })

    @router.post("/api/swarm/tasks/{task_id}/reject")
    async def swarm_reject_checkpoint(task_id: str) -> JSONResponse:
        """Reject an HITL checkpoint and abort the pipeline."""
        engine = _current_engine()
        svc = get_swarm_service()
        if not svc.has_swarm_core() or engine is None:
            return JSONResponse(
                {"status": "error", "message": "Swarm core is not available"},
                status_code=503,
            )

        checkpoint_info = engine.get_checkpoint_info(task_id) if hasattr(engine, "get_checkpoint_info") else None
        if checkpoint_info is None:
            task_obj = engine.get_task(task_id)
            if task_obj is not None and task_obj.status != "paused":
                return JSONResponse(
                    {
                        "status": "error",
                        "message": f"Task '{task_id}' is not paused (status: {task_obj.status.value})",
                    },
                    status_code=409,
                )
            return JSONResponse(
                {"status": "error", "message": f"Task '{task_id}' not found"},
                status_code=404,
            )

        result = await engine.reject_checkpoint(task_id)
        if result is None:
            return JSONResponse(
                {"status": "error", "message": f"Failed to reject checkpoint for task '{task_id}'"},
                status_code=500,
            )

        return JSONResponse({
            "status": result.status,
            "message": "Checkpoint rejected, pipeline aborted",
            "task_id": result.task_id,
            "worker_results": [item.to_dict() for item in result.worker_results],
            "aggregated_output": result.aggregated_output,
            "error": result.error,
            "metadata": result.metadata,
        })

    @router.post("/api/swarm/tasks/{task_id}/cancel")
    async def swarm_cancel_task(task_id: str) -> JSONResponse:
        """Cancel a running task."""
        engine = _current_engine()
        if engine is None:
            return JSONResponse(
                {"status": "error", "message": "Swarm engine not available"},
                status_code=503,
            )
        # Check the task is active
        active = engine.get_active_task(task_id) if hasattr(engine, "get_active_task") else getattr(engine, "_active_tasks", {}).get(task_id)
        if active is None:
            return JSONResponse(
                {"status": "error", "message": f"Task '{task_id}' is not active (already completed or not found)"},
                status_code=404,
            )
        cancelled = await engine.cancel_task(task_id)
        if cancelled:
            return JSONResponse(
                {"status": "ok", "message": f"Task '{task_id}' cancelled", "task_id": task_id}
            )
        return JSONResponse(
            {"status": "error", "message": f"Failed to cancel task '{task_id}'"},
            status_code=500,
        )

    @router.post("/api/swarm/tasks/{task_id}/retry")
    async def swarm_retry_task(task_id: str) -> JSONResponse:
        """Retry a failed/timeout/cancelled task by re-dispatching."""
        engine = _current_engine()
        if engine is None:
            return JSONResponse(
                {"status": "error", "message": "Swarm engine not available"},
                status_code=503,
            )
        new_task = await engine.retry_task(task_id)
        if new_task is None:
            return JSONResponse(
                {"status": "error", "message": f"Task '{task_id}' not found"},
                status_code=404,
            )
        # Dispatch in the background (non-blocking)
        handle = asyncio.create_task(_run_and_route_task(engine, new_task, is_broadcast=False))
        svc = get_swarm_service()
        svc.register_task_handle(new_task.id, handle)
        handle.add_done_callback(
            lambda h, tid=new_task.id: svc.unregister_task_handle(tid)
        )
        return JSONResponse(
            {
                "status": "ok",
                "message": f"Retrying task '{task_id}' as '{new_task.id}'",
                "old_task_id": task_id,
                "new_task_id": new_task.id,
            }
        )
