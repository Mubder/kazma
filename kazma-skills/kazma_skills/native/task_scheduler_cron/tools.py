"""Task Scheduler Cron Native Skill — tools for scheduling reminders and agent tasks.

Scheduled audit 2026-10-13: every tool answers on a single-operator install
only (SCH-S03, decision D1), reads and changes the caller's tenant's jobs only,
never hands the model a chat address (SCH-S10), and resolves the operator's
zone and the delivery address once, off the event loop (SCH-E10).
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _out(payload: dict[str, Any]) -> str:
    return _json.dumps(payload, ensure_ascii=False, indent=2)


async def _refused() -> str | None:
    from kazma_core.cron.single_operator import REFUSAL_CODE, scheduled_refusal

    refusal = await asyncio.to_thread(scheduled_refusal)
    if refusal:
        return _out({"ok": False, "code": REFUSAL_CODE, "error": refusal})
    return None


def _context() -> dict[str, Any]:
    """The caller's tenant rule, zone, actor and source. Blocking (settings,
    the RBAC store): called through ``asyncio.to_thread``."""
    from kazma_core.cron.scheduler import get_cron_parent, resolve_cron_zone, scheduled_tenant_filter
    from kazma_core.safety.hitl import get_current_thread_id
    from kazma_core.tools.send_message import get_current_platform

    zone, zone_name = resolve_cron_zone()
    parent = get_cron_parent()
    thread = get_current_thread_id() or ""
    platform = get_current_platform() or "chat"
    if parent:
        source, actor = f"cron:{parent.get('job_id', '')}", f"agent:{parent.get('job_id', '')}"
    else:
        source, actor = "chat", f"agent:{platform}:{thread[:24]}"
    return {"tenant": scheduled_tenant_filter(), "zone": zone, "zone_name": zone_name,
            "thread": thread, "actor": actor, "source": source}


def _public(job: dict[str, Any]) -> dict[str, Any]:
    """A job as the model sees it: never the delivery address (SCH-S10)."""
    job = dict(job)
    job.pop("delivery_target", None)
    job.pop("tenant_id", None)
    return job


async def schedule_task(timing: str, prompt: str, kind: str = "reminder", tools: str = "read",
                        catchup_minutes: int = 0) -> str:
    """Schedule a reminder or an agent task for a future time.

    Args:
        timing: When it fires. One-off: '30m', '2h', '3d', or a date and time
            ('2026-12-01T09:00' is read in the operator's time zone; an offset
            or 'Z' is honored). Recurring: 'daily at 9am', 'daily at 21:30',
            'weekdays at 08:30', 'every mon,wed at 9:15pm', optionally followed
            by 'until 2026-12-31' or 'count 5'. Must be in the future.
        prompt: For a reminder, the EXACT message to deliver (do NOT wrap it in
            'send via Telegram' -- the scheduler delivers it). For a task, the
            goal the agent works on when it fires.
        kind: 'reminder' (default) delivers the prompt as written and runs no
            model; 'task' runs the agent at fire time and delivers its answer.
        tools: For a task, what it may use while nobody is watching: 'read'
            (default, read-only tools) or 'read_write'. Approval-gated tools
            are never available to a fired task.
        catchup_minutes: How late it may still run after downtime (default:
            the operator's setting, 60 minutes). Later, it is recorded as
            missed and the chat gets one notice instead.

    Returns:
        JSON with the job id, when it fires (UTC and the operator's zone), and
        what it will do -- or an error saying why it was not booked.
    """
    refused = await _refused()
    if refused:
        return refused
    from kazma_core.cron.scheduler import CronLimitError, get_cron_scheduler
    from kazma_core.tools.send_message import resolve_delivery_target

    scheduler = get_cron_scheduler()
    if scheduler is None:
        return "Error: Cron scheduler not initialized."

    # Capture the chat this reminder was booked from so the result routes back
    # to the right conversation at fire time (AGENTS §16B). Web SSE/WS never
    # used to stamp `_gateway.delivery_target`, so every job was born
    # `target=(none)` and UNDELIVERABLE at fire time (2026-09-02).
    delivery_target = await asyncio.to_thread(resolve_delivery_target)
    if not delivery_target:
        return _out({
            "ok": False,
            "error": (
                "Cannot schedule: no delivery address. Book from Telegram, or "
                "set connectors.telegram.swarm_chat_id / allowed_users so a "
                "Web-booked reminder can alert you."
            ),
        })
    platform = delivery_target.split(":", 1)[0] if ":" in delivery_target else "telegram"
    ctx = await asyncio.to_thread(_context)
    try:
        result = await scheduler.schedule(
            timing=timing,
            prompt=prompt,
            platform=platform,
            # The booking thread: the cancel_job resolver verifies against
            # this thread's pending jobs (incident 2026-08-16).
            thread_id=ctx["thread"],
            delivery_target=delivery_target,
            kind=kind,
            capabilities=tools,
            catchup_minutes=catchup_minutes or None,
            tenant_id=ctx["tenant"] or None,
            actor=ctx["actor"],
            source=ctx["source"],
            zone=ctx["zone"],
            zone_name=ctx["zone_name"],
        )
        return _out({"ok": True, **_public(result)})
    except CronLimitError as e:
        return _out({"ok": False, "code": e.code, "error": str(e)})
    except ValueError as e:
        return _out({"ok": False, "code": "invalid_timing", "error": str(e)})
    except Exception as e:  # noqa: BLE001 — the tool answers; the log has the traceback
        logger.error("Error scheduling task: %s", e, exc_info=True)
        return f"Error scheduling task: {e}"


async def list_scheduled() -> str:
    """List scheduled reminders and tasks: status, timing, what each does,
    and how its last run and delivery went.

    Returns:
        JSON list of jobs.
    """
    refused = await _refused()
    if refused:
        return refused
    from kazma_core.cron.scheduler import get_cron_scheduler

    scheduler = get_cron_scheduler()
    if scheduler is None:
        return "Error: Cron scheduler not initialized."
    try:
        ctx = await asyncio.to_thread(_context)
        jobs = await scheduler.list_jobs(tenant_id=ctx["tenant"], scoped=ctx["tenant"] is not None,
                                         zone=ctx["zone"], zone_name=ctx["zone_name"])
        return _out([_public(j) for j in jobs])
    except Exception as e:  # noqa: BLE001
        logger.error("Error listing scheduled tasks: %s", e, exc_info=True)
        return f"Error listing scheduled tasks: {e}"


async def cancel_scheduled(job_id: str) -> str:
    """Cancel a scheduled reminder or task by its job ID.

    A job running right now is cancelled when this run ends.

    Args:
        job_id: The unique identifier of the job to cancel.

    Returns:
        JSON with the outcome: cancelled, cancel_pending, not_found or not_cancellable.
    """
    refused = await _refused()
    if refused:
        return refused
    from kazma_core.cron.scheduler import get_cron_scheduler

    scheduler = get_cron_scheduler()
    if scheduler is None:
        return "Error: Cron scheduler not initialized."
    try:
        ctx = await asyncio.to_thread(_context)
        result = await scheduler.cancel(job_id, tenant_id=ctx["tenant"], actor=ctx["actor"], source=ctx["source"])
        return _out(result)
    except Exception as e:  # noqa: BLE001
        logger.error("Error cancelling scheduled task: %s", e, exc_info=True)
        return f"Error cancelling scheduled task: {e}"


async def edit_scheduled(job_id: str, timing: str = "", prompt: str = "") -> str:
    """Edit a scheduled job's timing and/or prompt.

    Provide at least one of timing or prompt. A job that already finished is
    not brought back by an edit (book it again instead); a job running right
    now takes the change when this run ends.

    Args:
        job_id: The unique identifier of the job to edit.
        timing: New timing, in the forms schedule_task accepts (optional).
        prompt: New prompt (optional).

    Returns:
        JSON with the outcome: rescheduled, edit_pending, not_found,
        not_editable or error.
    """
    refused = await _refused()
    if refused:
        return refused
    from kazma_core.cron.scheduler import get_cron_scheduler

    scheduler = get_cron_scheduler()
    if scheduler is None:
        return "Error: Cron scheduler not initialized."

    if not (timing or "").strip() and not (prompt or "").strip():
        return "Error: provide at least one of timing or prompt to edit."

    try:
        ctx = await asyncio.to_thread(_context)
        result = await scheduler.reschedule(
            job_id,
            timing=(timing or "").strip() or None,
            prompt=(prompt or "").strip() or None,
            tenant_id=ctx["tenant"],
            actor=ctx["actor"],
            source=ctx["source"],
            zone=ctx["zone"],
            zone_name=ctx["zone_name"],
        )
        return _out(result)
    except Exception as e:  # noqa: BLE001
        logger.error("Error editing scheduled task: %s", e, exc_info=True)
        return f"Error editing scheduled task: {e}"
