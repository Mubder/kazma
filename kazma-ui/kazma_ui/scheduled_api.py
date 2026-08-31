"""Universal Scheduled Tasks page + CRUD API.

Aggregates every user-facing scheduled task Kazma owns — cron jobs
(``cron.db``) and scheduled X posts (``x_scheduled.db``) — into one list and
exposes create/edit/delete for both, so the Web UI and chat manage the SAME
underlying stores (full parity: whatever one side does, the other sees).

This module is a PRESENTATION + CRUD layer only. The ToU/safety policy for
scheduled X posts (caps, dedupe, enabled checks) lives in
:func:`kazma_core.x_api.booking.book_x_post`; HITL approval applies on the
chat/agent path, and the human authoring a draft directly in this UI is the
approval for the Web path.

Mutating endpoints use the same Origin + ``X-Requested-With`` CSRF pair as the
X/email APIs.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from kazma_core.errors import safe_error, validation_error

logger = logging.getLogger(__name__)


# ── Request bodies (module-level so FastAPI resolves them as JSON bodies) ──


class CronCreateBody(BaseModel):
    timing: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)


class CronEditBody(BaseModel):
    timing: str = ""
    prompt: str = ""


class XScheduleBody(BaseModel):
    text: str = Field(..., min_length=1)
    when: str = Field(..., min_length=1)
    reply_to_id: str = ""
    proposal_id: str = ""


class XRescheduleBody(BaseModel):
    when: str = Field(..., min_length=1)


# ── CSRF guard (mirrors kazma_ui.x_api) ──────────────────────────────


async def _verify_same_origin(request: Request) -> None:
    xrw = request.headers.get("x-requested-with", "").lower()
    if xrw != "xmlhttprequest":
        raise HTTPException(status_code=403, detail="missing custom request header")
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if origin:
        own_host = request.headers.get("host") or ""
        try:
            from urllib.parse import urlparse

            origin_host = urlparse(origin).netloc
        except Exception:  # noqa: BLE001
            origin_host = ""
        if own_host and origin_host and origin_host != own_host:
            raise HTTPException(status_code=403, detail="cross-origin request denied")


def _tenant_id() -> str:
    try:
        from kazma_core.tenant_isolation import require_tenant_id

        return require_tenant_id() or "default"
    except Exception:  # noqa: BLE001
        return "default"


def _tenant_filter() -> str | None:
    """Mirror CronScheduler.list_jobs: scope only in multi-user/production."""
    try:
        from kazma_core.tenant_isolation import multi_user_or_production, require_tenant_id

        if multi_user_or_production():
            return require_tenant_id()
    except Exception:  # noqa: BLE001
        pass
    return None


def _iso(epoch: float | None) -> str:
    if not epoch:
        return ""
    try:
        return datetime.fromtimestamp(float(epoch)).astimezone().isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return ""


def create_scheduled_router(agent: Any, templates: Jinja2Templates) -> APIRouter:
    """Build the /scheduled page + /api/scheduled/* CRUD router."""
    router = APIRouter(tags=["scheduled"])
    protected = APIRouter(tags=["scheduled"])

    # ── Page ──────────────────────────────────────────────────────────

    @router.get("/scheduled", response_class=HTMLResponse)
    async def scheduled_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "scheduled.html",
            {"config": getattr(agent, "config", {}), "active_page": "scheduled"},
        )

    # ── Aggregated list ───────────────────────────────────────────────

    @router.get("/api/scheduled/tasks")
    async def list_tasks() -> JSONResponse:
        items: list[dict[str, Any]] = []

        # Cron jobs (tenant-scoped like CronScheduler.list_jobs).
        try:
            from kazma_core.cron.scheduler import get_cron_scheduler
            from kazma_core.text_display import (
                display_kicker,
                extract_post_body,
                shorten_outcome,
                text_dir,
            )

            sched = get_cron_scheduler()
            if sched is not None:
                for job in await sched.list_jobs():
                    prompt = str(job.get("prompt") or "")
                    body = extract_post_body(prompt)
                    last = str(job.get("last_result") or "")
                    shown = body or prompt
                    items.append({
                        "source": "cron",
                        "id": job.get("job_id", ""),
                        "kind": "task",
                        "summary": shown[:500],
                        "kicker": display_kicker(prompt, body),
                        "when": job.get("next_run", ""),
                        "timing": job.get("timing", ""),
                        "status": job.get("status", ""),
                        "last_result": last[:500],
                        "outcome": shorten_outcome(last),
                        "dir": text_dir(shown),
                        "platform": job.get("platform", ""),
                    })
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scheduled] cron list failed: %s", exc)

        # Scheduled X posts.
        try:
            from kazma_core.x_api.schedule import get_x_scheduled_store

            store = get_x_scheduled_store()
            tenant = _tenant_filter()
            from kazma_core.text_display import extract_post_body, text_dir

            for p in store.list_all(tenant_id=tenant, limit=200):
                body = extract_post_body(p.text or "")
                items.append({
                    "source": "x",
                    "id": p.id,
                    "kind": "post",
                    "summary": (body or p.text)[:500],
                    "kicker": "",
                    "when": _iso(p.fire_at),
                    "timing": "",
                    "status": p.status,
                    "tweet_id": p.tweet_id,
                    "error": p.error,
                    "outcome": "",
                    "dir": text_dir(body or p.text or ""),
                    "reply_to_id": p.reply_to_id,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scheduled] x list failed: %s", exc)

        # Soonest first among pending/active, then newest.
        items.sort(key=lambda it: (it.get("status") not in ("pending", "running"), it.get("when") or ""))
        return JSONResponse({"ok": True, "count": len(items), "tasks": items})

    # ── Cron CRUD ─────────────────────────────────────────────────────

    @protected.post("/api/scheduled/cron", dependencies=[Depends(_verify_same_origin)])
    async def create_cron(body: CronCreateBody) -> JSONResponse:
        from kazma_core.cron.scheduler import get_cron_scheduler

        sched = get_cron_scheduler()
        if sched is None:
            return JSONResponse({"ok": False, "error": "Cron scheduler not initialized."}, status_code=503)
        try:
            result = await sched.schedule(
                timing=body.timing.strip(),
                prompt=body.prompt.strip(),
                platform="web",
                thread_id="",
                delivery_target="",
            )
            return JSONResponse({"ok": True, **result})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": validation_error(exc)}, status_code=400)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[scheduled] cron create failed")
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    @protected.put("/api/scheduled/cron/{job_id}", dependencies=[Depends(_verify_same_origin)])
    async def edit_cron(job_id: str, body: CronEditBody) -> JSONResponse:
        from kazma_core.cron.scheduler import get_cron_scheduler

        sched = get_cron_scheduler()
        if sched is None:
            return JSONResponse({"ok": False, "error": "Cron scheduler not initialized."}, status_code=503)
        if not (body.timing or "").strip() and not (body.prompt or "").strip():
            return JSONResponse({"ok": False, "error": "Provide timing and/or prompt."}, status_code=400)
        try:
            result = await sched.reschedule(
                job_id,
                timing=(body.timing or "").strip() or None,
                prompt=(body.prompt or "").strip() or None,
            )
            status_code = 200 if result.get("status") in ("rescheduled",) else 404
            return JSONResponse({"ok": result.get("status") == "rescheduled", **result}, status_code=status_code)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[scheduled] cron edit failed")
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    @protected.delete("/api/scheduled/cron/{job_id}", dependencies=[Depends(_verify_same_origin)])
    async def delete_cron(job_id: str) -> JSONResponse:
        from kazma_core.cron.scheduler import get_cron_scheduler

        sched = get_cron_scheduler()
        if sched is None:
            return JSONResponse({"ok": False, "error": "Cron scheduler not initialized."}, status_code=503)
        try:
            result = await sched.cancel(job_id)
            ok = result.get("status") == "cancelled"
            return JSONResponse({"ok": ok, **result}, status_code=200 if ok else 404)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[scheduled] cron delete failed")
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    # ── Scheduled X post CRUD ─────────────────────────────────────────

    @protected.post("/api/scheduled/x", dependencies=[Depends(_verify_same_origin)])
    async def create_x(body: XScheduleBody) -> JSONResponse:
        from kazma_core.x_api.booking import book_x_post

        tenant = _tenant_id()
        text = body.text
        proposal_ref = (body.proposal_id or "").strip()
        if proposal_ref:
            try:
                from kazma_core.agent.artifacts import get_artifact_store

                stored = get_artifact_store().stored_text_for(
                    proposal_ref, tenant_id=tenant
                )
            except Exception:
                stored = None
            if not stored:
                return JSONResponse(
                    {"ok": False, "error": "proposal_id did not resolve"},
                    status_code=400,
                )
            text = stored
        ok, payload = book_x_post(
            text=text,
            when=body.when,
            reply_to_id=body.reply_to_id or "",
            tenant_id=tenant,
            thread_id="",
            delivery_target="",
        )
        if ok and proposal_ref:
            try:
                from kazma_core.agent.artifacts import get_artifact_store

                get_artifact_store().proposal_posted(proposal_ref, tenant_id=tenant)
            except Exception:
                logger.debug("[scheduled] proposal_posted failed", exc_info=True)
            payload["proposal_id"] = proposal_ref
        return JSONResponse({"ok": ok, **payload}, status_code=200 if ok else 400)

    @protected.put("/api/scheduled/x/{post_id}", dependencies=[Depends(_verify_same_origin)])
    async def edit_x(post_id: int, body: XRescheduleBody) -> JSONResponse:
        from kazma_core.cron.scheduler import parse_timing
        from kazma_core.x_api.schedule import get_x_scheduled_store
        import time as _time

        store = get_x_scheduled_store()
        post = store.get(post_id)
        if post is None:
            return JSONResponse({"ok": False, "error": f"No scheduled post {post_id}."}, status_code=404)
        if post.status != "pending":
            return JSONResponse(
                {"ok": False, "error": f"Post {post_id} is '{post.status}' and cannot be edited."},
                status_code=400,
            )
        try:
            new_fire = parse_timing(body.when.strip()).timestamp()
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": validation_error(exc)}, status_code=400)
        if new_fire <= _time.time():
            return JSONResponse({"ok": False, "error": "New time is in the past."}, status_code=400)
        store.set_fire_time(post_id, new_fire)
        return JSONResponse({"ok": True, "id": post_id, "fire_at": _iso(new_fire)})

    @protected.delete("/api/scheduled/x/{post_id}", dependencies=[Depends(_verify_same_origin)])
    async def delete_x(post_id: int) -> JSONResponse:
        from kazma_core.x_api.schedule import get_x_scheduled_store

        store = get_x_scheduled_store()
        cancelled = store.cancel(post_id)
        if cancelled:
            return JSONResponse({"ok": True, "cancelled": True, "id": post_id})
        post = store.get(post_id)
        if post is None:
            return JSONResponse({"ok": False, "error": f"No scheduled post {post_id}."}, status_code=404)
        return JSONResponse(
            {"ok": False, "error": f"Post {post_id} is already '{post.status}'."},
            status_code=400,
        )

    router.include_router(protected)
    return router
