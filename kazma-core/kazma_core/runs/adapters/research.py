"""Research sessions and crawls (and research pipelines) as runs.

Sources of truth: ``research_sessions.db`` for sessions and the crawl journal
(``web_acquire.crawl_jobs``) for crawls and pipelines. Progress is the
acquisition monitor's: its ledger records, per run, when the stage, counts or
effects last changed (``since``) -- the meaningful-progress rule this whole
supervisor generalizes. The monitor already announces these runs' stalls and
outcomes (``kinds.py``), so the supervisor records them without a second
message. A crawl or pipeline an agent ran inside its turn reports to that
agent (``monitor._AGENT_RUN_KINDS``) and is announced by it.

A running session's owner is the task registered for it in this process; a
crawl's is its journal owner (``crawl_jobs._owner_gone``, KB-E07). Cancel is
the Research page's (``research_session.cancel_session``); a crawl is resumed
from its page (an explicit resume, never automatic).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

from kazma_core.runs.adapters._common import short_title
from kazma_core.runs.contract import ActionResult, Run, RunState

__all__ = ["CRAWLS", "SESSIONS"]

logger = logging.getLogger(__name__)


def _monitor_since() -> dict[str, float]:
    """``key -> since`` from the monitor's ledger (``session:<id>``, ``job:<id>``)."""
    from kazma_core.web_acquire import monitor

    try:
        jobs = monitor._read().get("jobs", {})
    except (ValueError, TypeError, AttributeError):
        logger.warning("[runs] the acquisition monitor's ledger is unreadable; using row times",
                       exc_info=True)
        return {}
    return {key: float(row.get("since") or 0.0) for key, row in jobs.items() if isinstance(row, dict)}


class _Sessions:
    STATE_MAP: dict[str, str] = {
        "pending": RunState.QUEUED,
        "running": RunState.RUNNING,
        "cancelling": RunState.RUNNING,
        "cancelled": RunState.CANCELLED,
        "done": RunState.DONE,
        "partial": RunState.DONE,
        "error": RunState.FAILED,
        "interrupted": RunState.FAILED,
    }

    async def list_runs(self, now: float, *, window_s: float) -> list[Run]:
        return await asyncio.to_thread(self._read, now, window_s)

    def _read(self, now: float, window_s: float) -> list[Run]:
        from kazma_core.tools import research_session

        connection = research_session._conn()
        try:
            rows = connection.execute(
                "SELECT id, topic, status, stage, sources, created_at, updated_at, error "
                "FROM research_sessions WHERE depth != 'chat' AND "
                "(status IN ('pending', 'running', 'cancelling') OR updated_at >= ?) "
                "ORDER BY updated_at DESC LIMIT 200",
                (now - window_s,),
            ).fetchall()
        finally:
            connection.close()
        since = _monitor_since()
        return [self._run(row, since, research_session._running_task(row["id"]) is not None) for row in rows]

    def _run(self, row: Any, since: dict[str, float], owned: bool) -> Run:
        native = str(row["status"])
        state = RunState(self.STATE_MAP.get(native, RunState.UNKNOWN))
        updated = float(row["updated_at"] or 0.0) or None
        progress_at = since.get("session:" + row["id"]) or updated
        terminal = state in (RunState.DONE, RunState.FAILED, RunState.CANCELLED)
        return Run(
            run_id=f"research_session:{row['id']}", kind="research_session", native_id=row["id"],
            title=short_title(row["topic"]), state=state, native_state=native,
            started_at=float(row["created_at"] or 0.0) or None, progress_at=progress_at,
            progress_note=f"{row['stage']} · {int(row['sources'] or 0)} sources",
            owner_alive=owned if native in ("running", "cancelling") else None,
            ended_at=updated if terminal else None,
            version=str(updated or ""),
            actions=("cancel",) if native in ("pending", "running") else (),
            link="/research?session=" + quote(row["id"], safe=""),
            detail=short_title(row["error"] or "", 200) if state == RunState.FAILED else "",
        )

    async def act(self, native_id: str, action: str, *, actor: str) -> ActionResult:
        if action != "cancel":
            return ActionResult(False, "not_offered", f"Research sessions do not take {action}.")
        from kazma_core.tools.research_session import cancel_session

        session = await asyncio.to_thread(cancel_session, native_id)
        if session is None:
            return ActionResult(False, "not_found", "No such research session.")
        return ActionResult(True, "cancelling", f"The session is {session.status}.")


class _Crawls:
    STATE_MAP: dict[str, str] = {
        "running": RunState.RUNNING,
        "completed": RunState.DONE,
        "partial": RunState.DONE,
        "failed": RunState.FAILED,
        "interrupted": RunState.FAILED,
    }

    async def list_runs(self, now: float, *, window_s: float) -> list[Run]:
        return await asyncio.to_thread(self._read, now, window_s)

    def _read(self, now: float, window_s: float) -> list[Run]:
        from kazma_core.web_acquire import crawl_jobs
        from kazma_core.web_acquire.monitor import _AGENT_RUN_KINDS
        from kazma_core.web_acquire.result import AcquisitionError

        jobs = crawl_jobs._read()[1]
        since = _monitor_since()
        runs: list[Run] = []
        for row in jobs.values():
            if row["status"] != "running" and float(row.get("updated_at") or 0.0) < now - window_s:
                continue
            owner_gone = None
            if row["status"] == "running":
                try:
                    owner_gone = crawl_jobs._owner_gone(row, now)
                except AcquisitionError:
                    owner_gone = None
            runs.append(self._run(row, since, owner_gone, row.get("kind") in _AGENT_RUN_KINDS))
        return runs

    def _run(self, row: dict[str, Any], since: dict[str, float], owner_gone: bool | None,
             agent_run: bool) -> Run:
        from kazma_core.web_acquire.crawl_jobs import _summary_counts

        native = str(row["status"])
        state = RunState(self.STATE_MAP.get(native, RunState.UNKNOWN))
        research = row.get("kind") in ("research_pipeline", "research_crawl")
        counts = _summary_counts(row)
        updated = float(row.get("updated_at") or 0.0) or None
        phase = (row.get("progress") or {}).get("phase", "starting")
        return Run(
            run_id=f"crawl:{row['job_id']}", kind="crawl", native_id=row["job_id"],
            title=short_title(row.get("seed") or row.get("library_id") or row["job_id"]),
            state=state, native_state=native,
            started_at=float(row.get("created_at") or 0.0) or None,
            progress_at=since.get("job:" + row["job_id"]) or updated,
            progress_note=f"{phase} · {counts['fetched']} fetched, {counts['failed']} failed",
            owner_alive=None if owner_gone is None else not owner_gone,
            ended_at=updated if native != "running" else None,
            version=str(updated or ""),
            link=("/research?job=" if research else "/knowledge?job=") + quote(row["job_id"], safe=""),
            detail=f"{counts['failed']} failed, {counts['ambiguous']} uncertain" if state == RunState.FAILED else "",
            announced_by="the agent turn that ran it" if agent_run else "",
        )

    async def act(self, native_id: str, action: str, *, actor: str) -> ActionResult:
        return ActionResult(False, "not_offered", "A crawl is resumed or removed from its page.")


SESSIONS = _Sessions()
CRAWLS = _Crawls()
