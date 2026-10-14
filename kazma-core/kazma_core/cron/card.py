"""What an approval card says about a scheduling call (Scheduled audit SCH-S05, S03).

``schedule_task`` books work that runs later with nobody watching, so its
card names what the job will do when it fires -- deliver a stored message, or
run the agent with read-only or read-and-write tools -- and how often.
``edit_scheduled`` replaces a stored prompt, so its card shows the prompt it
replaces next to the new one (it showed only the new one). The tool worker
adds these lines to the card's disclosure notes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["scheduling_card_notes"]

_PREVIEW = 400


def _args(call: dict[str, Any]) -> dict[str, Any]:
    raw = call.get("arguments") if "arguments" in call else call.get("args")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _clip(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= _PREVIEW else text[:_PREVIEW] + f"… ({len(text)} characters)"


async def scheduling_card_notes(calls: list[dict[str, Any]]) -> list[str]:
    """Disclosure lines for the scheduling calls in *calls* (never raises)."""
    notes: list[str] = []
    for call in calls:
        name = str(call.get("name") or "")
        try:
            if name == "schedule_task":
                notes.append(_booking_note(_args(call)))
            elif name == "edit_scheduled":
                note = await _edit_note(_args(call))
                if note:
                    notes.append(note)
        except Exception:  # noqa: BLE001 — a disclosure must not break the card
            logger.debug("[cron] card note for %s skipped", name, exc_info=True)
    return [n for n in notes if n]


def _booking_note(args: dict[str, Any]) -> str:
    from kazma_core.cron.capabilities import describe_capabilities, normalize_capabilities, normalize_kind
    from kazma_core.cron.timing import looks_recurring

    try:
        kind = normalize_kind(args.get("kind"), default="reminder")
        caps = normalize_capabilities(args.get("tools"))
    except ValueError as exc:
        return f"This booking will be refused: {exc}"
    timing = str(args.get("timing") or "")
    repeats = "It repeats (" + timing + ")." if looks_recurring(timing) else "It runs once."
    return f"When it fires, this scheduled job {describe_capabilities(kind, caps)}. {repeats}"


async def _edit_note(args: dict[str, Any]) -> str:
    from kazma_core.cron.scheduler import get_cron_scheduler, scheduled_tenant_filter

    job_id = str(args.get("job_id") or "")
    sched = get_cron_scheduler()
    if not job_id or sched is None:
        return ""
    tenant = await asyncio.to_thread(scheduled_tenant_filter)
    job = await sched.get_job(job_id, tenant_id=tenant)
    if job is None:
        return f"Job {job_id} is not one of this install's scheduled jobs; the edit will find nothing."
    lines = [f"Editing {job_id} ({job.get('status')}, {job.get('timing')})."]
    if str(args.get("prompt") or "").strip():
        lines.append("Prompt now: " + _clip(job.get("prompt", "")))
        lines.append("Prompt after: " + _clip(str(args.get("prompt"))))
    if str(args.get("timing") or "").strip():
        lines.append(f"Timing now: {job.get('timing')}; after: {args.get('timing')}.")
    return " ".join(lines)
