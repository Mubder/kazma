"""Universal Scheduled Tasks page + CRUD API.

Aggregates every user-facing scheduled task Kazma owns -- cron jobs
(``cron.db``) and scheduled X posts (their publication operations) -- into
one list, so the Web UI and chat manage the SAME underlying stores.

This module is a PRESENTATION + CRUD layer only. Scheduled audit
(2026-10-13) rules it keeps:

* **One state machine** (SCH-E12, S07): each row carries the actions its
  state allows (``actions``), and a change names the version it was made
  from; a change during a fire is recorded and applied when the fire ends
  (202), a stale or illegal one answers 409 with a reason -- never 404.
  Bringing a finished job back is its own action (``/restore``).
* **The prompt is never round-tripped through the list** (SCH-S02): the list
  shows a display summary; the edit dialog reads the whole prompt from
  ``GET /api/scheduled/cron/{id}``; a timing-only edit sends no prompt.
* **X rows are the operation truth** (SCH-S04): they come from the X
  publication queue, and the page changes them through X Studio's
  revision-bound queue actions (``/api/x/queue/{op}/reschedule|cancel``),
  which hold a booking whose account changed. The page's own reschedule and
  delete of X rows (``PUT/DELETE /api/scheduled/x/{id}``), which skipped
  that binding, are gone.
* **Single-operator only** (SCH-S03, decision D1): with platform users every
  route answers 409 ``scheduled_single_operator`` and the page says why.
* **Bounded, off the loop, audited** (SCH-E14, E10, S11, S12): body limits, a
  rate bucket, the job caps; the tenant rule, the zone and the delivery
  address are read once per request in a thread; every change records its
  actor and source.

Mutating endpoints use the same ``X-Requested-With`` + origin pair as the
X/email APIs (:mod:`kazma_ui.same_origin`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates
from kazma_core.errors import safe_error, validation_error
from pydantic import BaseModel, Field

from kazma_ui.rate_limit import rate_limit
from kazma_ui.same_origin import verify_same_origin

logger = logging.getLogger(__name__)

#: The page's status classes: every cron status and every X operation state
#: maps to one, and an unknown status needs attention -- never "Completed"
#: (SCH-S04: everything that was not pending or running counted as done).
#: ``static/js/scheduled.js`` STATUS_CLASS mirrors this map
#: (tests/test_scheduled_presentation.py holds them equal).
SCHEDULED_STATUS_CLASS: dict[str, str] = {
    # cron jobs
    "pending": "upcoming",
    "running": "upcoming",
    "delivering": "upcoming",
    "done": "done",
    "failed": "failed",
    "cancelled": "cancelled",
    "undeliverable": "failed",
    "missed": "attention",
    "delivery_unknown": "attention",
    # X publication operations
    "scheduled": "upcoming",
    "deferred": "upcoming",
    "sending": "attention",
    "awaiting_approval": "attention",
    "outcome_unknown": "attention",
    "published": "done",
    "failed_permanent": "failed",
    "expired": "failed",
}

#: Statuses whose X row needs the operator (shown under "Needs attention").
_X_STATES = frozenset({"scheduled", "deferred", "sending", "awaiting_approval", "outcome_unknown",
                       "published", "failed_permanent", "cancelled", "expired"})


def _status_class(status: str) -> str:
    return SCHEDULED_STATUS_CLASS.get(str(status or ""), "attention")


def _cron_actions(status: str) -> list[str]:
    """What the page may offer for a cron job in *status*: the store's
    state machine (:class:`kazma_core.cron.store.SQLiteCronStore`)."""
    if status in ("pending", "running"):
        return ["edit", "cancel"]
    if status == "delivering":
        return ["cancel"]
    if status in ("done", "failed", "cancelled", "undeliverable", "missed", "delivery_unknown", "completed"):
        return ["restore"]
    return []


# ── Request bodies (module-level so FastAPI resolves them as JSON bodies) ──
# Bounds (SCH-E14/S06): a timing is a short expression; a prompt is a
# reminder's message or a task's instruction (kazma_core.cron.settings).

_TIMING_MAX = 100
_PROMPT_MAX = 8000


class CronCreateBody(BaseModel):
    timing: str = Field(..., min_length=1, max_length=_TIMING_MAX)
    prompt: str = Field(..., min_length=1, max_length=_PROMPT_MAX)
    kind: str = Field(default="task", max_length=20)
    capabilities: str = Field(default="read", max_length=20)


class CronEditBody(BaseModel):
    timing: str = Field(default="", max_length=_TIMING_MAX)
    # None = unchanged. The page sends a prompt only when the operator
    # edited it (SCH-S02).
    prompt: str | None = Field(default=None, max_length=_PROMPT_MAX)
    expected_version: int | None = None


class _CronRestoreBody(BaseModel):
    timing: str = Field(..., min_length=1, max_length=_TIMING_MAX)
    expected_version: int | None = None


class XScheduleBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    when: str = Field(..., min_length=1, max_length=_TIMING_MAX)
    reply_to_id: str = Field(default="", max_length=40)
    proposal_id: str = Field(default="", max_length=200)
    idempotency_key: str = Field(default="", max_length=200)


class _ScheduledRoute(APIRoute):
    """An APIRoute that refuses on an install with platform users (D1)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        path = self.path

        async def checked(request: Request) -> Response:
            from kazma_core.cron.single_operator import REFUSAL_CODE, scheduled_refusal

            refusal = await asyncio.to_thread(scheduled_refusal)
            if refusal:
                if path == "/scheduled":  # the page says it in the page's language
                    request.state.scheduled_refusal = refusal
                else:
                    return JSONResponse({"ok": False, "code": REFUSAL_CODE, "error": refusal}, status_code=409)
            return await handler(request)

        return checked


def _tenant_id() -> str:
    from kazma_core.x_api.ownership import x_tenant_id

    return x_tenant_id()


def _iso(epoch: float | None) -> str:
    if not epoch:
        return ""
    try:
        return datetime.fromtimestamp(float(epoch), tz=UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _request_context(request: Request) -> dict[str, Any]:
    """Everything a Scheduled request reads from settings, resolved once.

    Blocking (the RBAC store, the settings store, the session): routes call
    it through ``asyncio.to_thread`` (SCH-E10/S11 -- it was read on the loop,
    once per job for the zone).
    """
    from kazma_core.cron.scheduler import resolve_cron_zone, scheduled_tenant_filter

    from kazma_ui.web_actor import web_actor

    zone, zone_name = resolve_cron_zone()
    return {"tenant": scheduled_tenant_filter(), "zone": zone, "zone_name": zone_name,
            "actor": web_actor(request)}


_CRON_STATUS_HTTP = {
    "rescheduled": 200, "cancelled": 200, "restored": 200,
    "edit_pending": 202, "cancel_pending": 202,
    "not_found": 404, "conflict": 409, "not_editable": 409, "not_cancellable": 409, "not_finished": 409,
    "unchanged": 400, "error": 400,
}

_CRON_REASONS = {
    "edit_pending": "The task is running now; the change is recorded and applies when this run ends.",
    "cancel_pending": "The task is running now; it is cancelled when this run ends.",
    "conflict": "The task changed since you opened it. Refresh and review it again.",
    "not_editable": "The task has finished. Use Run again to schedule it again.",
    "not_cancellable": "The task has already finished.",
    "not_finished": "The task still has work to do; edit it instead.",
    "not_found": "No such scheduled task.",
    "unchanged": "Provide a new time and/or prompt.",
}


def _cron_reply(result: dict[str, Any]) -> JSONResponse:
    status = str(result.get("status") or "error")
    code = _CRON_STATUS_HTTP.get(status, 400)
    body = {"ok": code < 300, **result}
    if "error" not in body and status in _CRON_REASONS:
        body["error" if code >= 300 else "message"] = _CRON_REASONS[status]
    if code >= 300:
        body["code"] = status
    return JSONResponse(body, status_code=code)


def _cron_item(job: dict[str, Any]) -> dict[str, Any]:
    from kazma_core.cron.capabilities import describe_capabilities
    from kazma_core.cron.timing import looks_recurring
    from kazma_core.text_display import display_kicker, extract_post_body, shorten_outcome, text_dir

    prompt = str(job.get("prompt") or "")
    body = extract_post_body(prompt)
    shown = (body or prompt)[:500]
    last = str(job.get("last_result") or "")
    status = str(job.get("status") or "")
    when = job.get("next_run") or ""
    if status not in ("pending", "running", "delivering"):
        when = job.get("finished_at") or job.get("last_fire_at") or when
    return {
        "source": "cron",
        "id": job.get("job_id", ""),
        "version": job.get("version", 1),
        "kind": job.get("kind", "task"),
        "capabilities": job.get("capabilities", "read"),
        "what_it_does": describe_capabilities(str(job.get("kind", "task")), str(job.get("capabilities", "read"))),
        # A display summary, never the prompt: editing re-reads the prompt.
        "summary": shown,
        "kicker": display_kicker(prompt, body),
        "when": when,
        "timing": job.get("timing", ""),
        "recurring": looks_recurring(str(job.get("timing") or "")),
        "status": status,
        "class": _status_class(status),
        "actions": _cron_actions(status),
        "failure_count": job.get("failure_count", 0),
        "last_outcome": job.get("last_outcome", ""),
        "delivery_state": job.get("delivery_state", ""),
        "delivery_error": str(job.get("delivery_error") or "")[:300],
        "destination": job.get("destination", ""),
        "edit_pending": job.get("edit_pending", False),
        "cancel_pending": job.get("cancel_pending", False),
        "last_result": last[:500],
        "outcome": shorten_outcome(last),
        "dir": text_dir(shown),
        "platform": job.get("platform", ""),
        "timezone": job.get("timezone", ""),
    }


def _x_items() -> list[dict[str, Any]]:
    """Scheduled X posts from the publication queue (operation truth).

    Blocking: the publication store, the X settings, the tenant rule.
    """
    from kazma_core.text_display import extract_post_body, text_dir
    from kazma_core.x_api.publication_service import queue_inventory

    items: list[dict[str, Any]] = []
    for row in queue_inventory(limit=200, include_finished=True).get("items", []):
        text = str(row.get("text") or "")
        body = extract_post_body(text)
        state = str(row.get("state") or "")
        actions = []
        if row.get("can_reschedule"):
            actions.append("edit")
        if row.get("can_cancel"):
            actions.append("cancel")
        items.append({
            "source": "x",
            "id": row.get("id"),
            "operation_id": row.get("id"),
            "version": row.get("version"),
            "kind": "post",
            "summary": (body or text)[:500],
            "kicker": "",
            "when": _iso(row.get("due_at")),
            "timing": "",
            "status": state,
            "class": _status_class(state),
            "actions": actions,
            "tweet_id": row.get("tweet_id") or "",
            "error": str(row.get("reason") or "") if state not in ("scheduled", "published") else "",
            "outcome": "",
            "dir": text_dir(body or text),
            "reply_to_id": row.get("reply_to_id") or "",
            "needs_reconciliation": bool(row.get("needs_reconciliation")),
        })
    return items


def create_scheduled_router(agent: Any, templates: Jinja2Templates) -> APIRouter:
    """Build the /scheduled page + /api/scheduled/* CRUD router."""
    router = APIRouter(tags=["scheduled"], route_class=_ScheduledRoute)
    protected = APIRouter(tags=["scheduled"], route_class=_ScheduledRoute)
    mutating = [Depends(verify_same_origin), Depends(rate_limit("scheduled", 30))]

    # ── Page ──────────────────────────────────────────────────────────

    @router.get("/scheduled", response_class=HTMLResponse)
    def scheduled_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "scheduled.html",
            {"config": getattr(agent, "config", {}), "active_page": "scheduled",
             "single_operator_refusal": bool(getattr(request.state, "scheduled_refusal", None))},
        )

    # ── Aggregated list ───────────────────────────────────────────────

    @router.get("/api/scheduled/tasks")
    async def list_tasks(request: Request) -> JSONResponse:
        items: list[dict[str, Any]] = []
        problems: list[str] = []
        ctx = await asyncio.to_thread(_request_context, request)
        try:
            from kazma_core.cron.scheduler import get_cron_scheduler

            sched = get_cron_scheduler()
            if sched is not None:
                jobs = await sched.list_jobs(tenant_id=ctx["tenant"], scoped=ctx["tenant"] is not None,
                                             zone=ctx["zone"], zone_name=ctx["zone_name"])
                items += [_cron_item(job) for job in jobs]
        except Exception as exc:  # noqa: BLE001 — one source failing leaves the other listed, and says so
            logger.warning("[scheduled] cron list failed: %s", exc)
            problems.append("cron")
        try:
            items += await asyncio.to_thread(_x_items)
        except PermissionError:
            pass  # no tenant for X here: X lists nothing rather than another tenant's
        except Exception as exc:  # noqa: BLE001
            logger.warning("[scheduled] x list failed: %s", exc)
            problems.append("x")
        order = {"attention": 0, "upcoming": 1}
        items.sort(key=lambda it: (order.get(it.get("class", ""), 2), it.get("when") or ""))
        return JSONResponse({"ok": True, "count": len(items), "tasks": items,
                             "timezone": ctx["zone_name"], "unavailable": problems})

    # ── Cron CRUD ─────────────────────────────────────────────────────

    @router.get("/api/scheduled/cron/{job_id}")
    async def get_cron(job_id: str, request: Request) -> JSONResponse:
        from kazma_core.cron.scheduler import get_cron_scheduler

        sched = get_cron_scheduler()
        if sched is None:
            return JSONResponse({"ok": False, "error": "Cron scheduler not initialized."}, status_code=503)
        ctx = await asyncio.to_thread(_request_context, request)
        job = await sched.get_job(job_id, tenant_id=ctx["tenant"])
        if job is None:
            return JSONResponse({"ok": False, "code": "not_found", "error": "No such scheduled task."},
                                status_code=404)
        job.pop("tenant_id", None)
        return JSONResponse({"ok": True, "job": {**job, "actions": _cron_actions(job.get("status", ""))}})

    @protected.post("/api/scheduled/cron", dependencies=mutating)
    async def create_cron(body: CronCreateBody, request: Request) -> JSONResponse:
        from kazma_core.cron.scheduler import CronLimitError, get_cron_scheduler

        sched = get_cron_scheduler()
        if sched is None:
            return JSONResponse({"ok": False, "error": "Cron scheduler not initialized."}, status_code=503)
        try:
            from kazma_core.tools.send_message import resolve_delivery_target

            delivery_target = await asyncio.to_thread(resolve_delivery_target)
            if not delivery_target:
                return JSONResponse(
                    {
                        "ok": False,
                        "code": "no_delivery_address",
                        "error": (
                            "Cannot schedule: no delivery address. Set "
                            "connectors.telegram.swarm_chat_id or allowed_users "
                            "so a Web-booked reminder can alert you on Telegram."
                        ),
                    },
                    status_code=400,
                )
            ctx = await asyncio.to_thread(_request_context, request)
            platform = delivery_target.split(":", 1)[0] if ":" in delivery_target else "telegram"
            result = await sched.schedule(
                timing=body.timing.strip(),
                prompt=body.prompt.strip(),
                platform=platform,
                thread_id="",
                delivery_target=delivery_target,
                kind=body.kind,
                capabilities=body.capabilities,
                tenant_id=ctx["tenant"] or "default",
                actor=ctx["actor"],
                source="web",
                zone=ctx["zone"],
                zone_name=ctx["zone_name"],
            )
            result.pop("tenant_id", None)
            return JSONResponse({"ok": True, **result})
        except CronLimitError as exc:
            return JSONResponse({"ok": False, "code": exc.code, "error": str(exc)}, status_code=409)
        except ValueError as exc:
            return JSONResponse({"ok": False, "code": "invalid", "error": validation_error(exc)}, status_code=400)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[scheduled] cron create failed")
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    @protected.put("/api/scheduled/cron/{job_id}", dependencies=mutating)
    async def edit_cron(job_id: str, body: CronEditBody, request: Request) -> JSONResponse:
        from kazma_core.cron.scheduler import get_cron_scheduler

        sched = get_cron_scheduler()
        if sched is None:
            return JSONResponse({"ok": False, "error": "Cron scheduler not initialized."}, status_code=503)
        timing = (body.timing or "").strip() or None
        prompt = body.prompt.strip() if body.prompt is not None and body.prompt.strip() else None
        if timing is None and prompt is None:
            return JSONResponse({"ok": False, "code": "unchanged", "error": "Provide timing and/or prompt."},
                                status_code=400)
        try:
            ctx = await asyncio.to_thread(_request_context, request)
            result = await sched.reschedule(
                job_id, timing=timing, prompt=prompt, tenant_id=ctx["tenant"],
                expected_version=body.expected_version, actor=ctx["actor"], source="web",
                zone=ctx["zone"], zone_name=ctx["zone_name"])
            return _cron_reply(result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[scheduled] cron edit failed")
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    @protected.delete("/api/scheduled/cron/{job_id}", dependencies=mutating)
    async def delete_cron(job_id: str, request: Request, expected_version: int | None = None) -> JSONResponse:
        from kazma_core.cron.scheduler import get_cron_scheduler

        sched = get_cron_scheduler()
        if sched is None:
            return JSONResponse({"ok": False, "error": "Cron scheduler not initialized."}, status_code=503)
        try:
            ctx = await asyncio.to_thread(_request_context, request)
            result = await sched.cancel(job_id, tenant_id=ctx["tenant"], expected_version=expected_version,
                                        actor=ctx["actor"], source="web")
            return _cron_reply(result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[scheduled] cron delete failed")
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    @protected.post("/api/scheduled/cron/{job_id}/restore", dependencies=mutating)
    async def restore_cron(job_id: str, body: _CronRestoreBody, request: Request) -> JSONResponse:
        from kazma_core.cron.scheduler import get_cron_scheduler

        sched = get_cron_scheduler()
        if sched is None:
            return JSONResponse({"ok": False, "error": "Cron scheduler not initialized."}, status_code=503)
        try:
            ctx = await asyncio.to_thread(_request_context, request)
            result = await sched.restore(job_id, timing=body.timing.strip(), tenant_id=ctx["tenant"],
                                         expected_version=body.expected_version, actor=ctx["actor"],
                                         source="web", zone=ctx["zone"], zone_name=ctx["zone_name"])
            return _cron_reply(result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[scheduled] cron restore failed")
            return JSONResponse({"ok": False, "error": safe_error(exc)}, status_code=500)

    # ── Scheduled X post booking ──────────────────────────────────────
    # Plain ``def``: every call below is synchronous SQLite (the publication
    # store, the saved-drafts store, the post ledger via booking) and none
    # awaits anything; FastAPI runs a sync handler in its thread pool
    # (AGENTS §35). Rescheduling and cancelling an X booking are X Studio's
    # revision-bound queue actions (SCH-S04).

    @protected.post("/api/scheduled/x", dependencies=mutating)
    def create_x(body: XScheduleBody) -> JSONResponse:
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
            except Exception:  # noqa: BLE001
                stored = None
            if not stored:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "proposal_id did not resolve to a single saved draft",
                    },
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
            idempotency_key="proposal:" + proposal_ref if proposal_ref else body.idempotency_key,
            proposal_id=proposal_ref,
        )
        if ok and proposal_ref:
            try:
                from kazma_core.agent.artifacts import get_artifact_store

                get_artifact_store().proposal_posted(
                    proposal_ref,
                    tenant_id=tenant,
                    via="x_studio_schedule",
                    used_ref=str(payload.get("id") or ""),
                )
            except Exception:  # noqa: BLE001
                logger.debug("[scheduled] proposal_posted failed", exc_info=True)
            payload["proposal_id"] = proposal_ref
        status = 200 if ok else (409 if payload.get("code") == "booking_conflict" else 400)
        return JSONResponse({"ok": ok, **payload}, status_code=status)

    router.include_router(protected)
    return router
