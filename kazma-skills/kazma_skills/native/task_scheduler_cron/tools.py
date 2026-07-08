"""Task Scheduler Cron Native Skill — tools for scheduling autonomous tasks."""

from __future__ import annotations

import logging
import json as _json

logger = logging.getLogger(__name__)


async def schedule_task(timing: str, prompt: str) -> str:
    """Schedule a task to run autonomously at a future time.

    Args:
        timing: Timing expression, e.g. '5m', '1h', 'daily at 9am'.
        prompt: The goal or prompt the agent should execute.

    Returns:
        JSON response with the scheduled job info.
    """
    from kazma_core.cron.scheduler import get_cron_scheduler

    scheduler = get_cron_scheduler()
    if scheduler is None:
        return "Error: Cron scheduler not initialized."

    try:
        result = await scheduler.schedule(timing=timing, prompt=prompt)
        return _json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Error scheduling task: %s", e)
        return f"Error scheduling task: {e}"


async def list_scheduled() -> str:
    """List all scheduled background tasks and their current status.

    Returns:
        JSON response with lists of scheduled jobs.
    """
    from kazma_core.cron.scheduler import get_cron_scheduler

    scheduler = get_cron_scheduler()
    if scheduler is None:
        return "Error: Cron scheduler not initialized."

    try:
        jobs = await scheduler.list_jobs()
        return _json.dumps(jobs, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Error listing scheduled tasks: %s", e)
        return f"Error listing scheduled tasks: {e}"


async def cancel_scheduled(job_id: str) -> str:
    """Cancel a scheduled background task using its job ID.

    Args:
        job_id: The unique identifier of the job to cancel.

    Returns:
        JSON response indicating cancellation status.
    """
    from kazma_core.cron.scheduler import get_cron_scheduler

    scheduler = get_cron_scheduler()
    if scheduler is None:
        return "Error: Cron scheduler not initialized."

    try:
        result = await scheduler.cancel(job_id)
        return _json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Error cancelling scheduled task: %s", e)
        return f"Error cancelling scheduled task: {e}"
