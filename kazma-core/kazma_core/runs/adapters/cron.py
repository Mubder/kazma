"""Scheduled jobs' fires as runs.

A scheduled job is a run while it fires (``running``) or delivers
(``delivering``), and afterwards when the fire ended badly (failed,
undeliverable, missed, delivery unknown) within the window. A job waiting for
its next time is a plan, not a run, and is not listed (the Scheduled page
shows it).

Source of truth: ``cron.db`` through the live scheduler's own store (an
aiosqlite connection on the server loop, so this adapter reads it there). A
fire's owner is alive while the scheduler's fire task runs
(``CronScheduler.fire_owner_alive``); a fire another owner holds, while its
lease runs (SCH-E04). Failures are already
announced by the scheduled jobs' alert outbox (§16 E2), so the supervisor
sends no second message about them. Cancel is the Scheduled page's
(``CronScheduler.cancel``, compare-and-set on the job's version).
"""

from __future__ import annotations

from typing import Any

from kazma_core.runs.adapters._common import epoch, origin_from, short_title
from kazma_core.runs.contract import ActionResult, Run, RunState

__all__ = ["ADAPTER"]


class _CronAdapter:
    STATE_MAP: dict[str, str] = {
        "pending": RunState.QUEUED,
        "running": RunState.RUNNING,
        "delivering": RunState.RUNNING,
        "done": RunState.DONE,
        "completed": RunState.DONE,
        "failed": RunState.FAILED,
        "cancelled": RunState.CANCELLED,
        "undeliverable": RunState.FAILED,
        "missed": RunState.FAILED,
        "delivery_unknown": RunState.UNKNOWN,
    }

    @staticmethod
    def _scheduler() -> Any:
        from kazma_core.cron.scheduler import get_cron_scheduler

        return get_cron_scheduler()

    async def list_runs(self, now: float, *, window_s: float) -> list[Run]:
        scheduler = self._scheduler()
        if scheduler is None:
            return []
        jobs = await scheduler._store.list_all(limit=500)
        runs: list[Run] = []
        for job in jobs:
            native = str(getattr(job.status, "value", job.status))
            if native == "pending":
                continue
            ended = epoch(job.finished_at)
            if native not in ("running", "delivering") and (ended or 0.0) < now - window_s:
                continue
            runs.append(self._run(job, native, now, scheduler))
        return runs

    def _run(self, job: Any, native: str, now: float, scheduler: Any) -> Run:
        state = RunState(self.STATE_MAP.get(native, RunState.UNKNOWN))
        owner = None
        if native == "running":
            # A running fire is judged from its claim: the occurrence it
            # serves, never ``last_fire_at`` (a recurring job's PREVIOUS
            # fire). Its owner is alive while the scheduler's fire task runs;
            # the lease decides only for a fire this scheduler does not own
            # (audit 2026-10-26 AUD-019: a lease that lapsed under a healthy
            # 15-minute fire made it "stuck: nothing is running it").
            fired = epoch(job.fire_due) or epoch(job.last_fire_at)
            owner = scheduler.fire_owner_alive(job)
            if owner is None:
                owner = bool(job.lease_until and job.lease_until > now)
        else:
            fired = epoch(job.last_fire_at) or epoch(job.fire_due)
        actions: tuple[str, ...] = ("cancel",) if native in ("running", "delivering") else ()
        detail = ""
        if state in (RunState.FAILED, RunState.UNKNOWN):
            detail = short_title(job.delivery_error or job.last_outcome or native, 200)
        return Run(
            run_id=f"scheduled_job:{job.job_id}", kind="scheduled_job", native_id=job.job_id,
            title=short_title(job.prompt), state=state, native_state=native,
            started_at=fired, progress_at=fired, owner_alive=owner,
            deadline_at=float(job.lease_until) if native == "running" and job.lease_until else None,
            ended_at=epoch(job.finished_at), effects="unknown" if native == "delivery_unknown" else "none",
            version=str(job.version), actions=actions, link="/scheduled",
            detail=detail, origin=origin_from(job.delivery_target, job.thread_id),
        )

    async def act(self, native_id: str, action: str, *, actor: str) -> ActionResult:
        if action != "cancel":
            return ActionResult(False, "not_offered", f"Scheduled jobs do not take {action}.")
        scheduler = self._scheduler()
        if scheduler is None:
            return ActionResult(False, "scheduler_unavailable", "The scheduler is not running.")
        job = await scheduler._store.get(native_id)
        if job is None:
            return ActionResult(False, "not_found", "No such scheduled job.")
        outcome = await scheduler.cancel(native_id, expected_version=job.version, actor=actor, source="runs")
        status = str(outcome.get("status") or "")
        ok = status in ("cancelled", "cancel_pending")
        message = {"cancelled": "Cancelled.",
                   "cancel_pending": "It is firing; the cancel is applied when the fire ends."}.get(
            status, f"Not cancelled ({status}).")
        return ActionResult(ok, status, message)


ADAPTER = _CronAdapter()
