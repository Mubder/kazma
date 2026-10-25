"""Document jobs as runs.

Source of truth: the document job queue (``document_jobs``, SQLite or
Postgres), read through the installed ingestion service (never one built for
the purpose: ``peek_ingestion_service``). A job in a processing stage has an
owner while its lease runs; the worker renews ``heartbeat_at`` as it works,
which is the job's progress. OCR progress ("page 12 of 54") is the note.

Cancel and Retry are the Documents page's (``cancel_job`` / ``retry_job``),
acting as the operator principal (§19 B4b) -- a single-operator surface.
Document jobs touch only Kazma's own document store: their effects are none.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from kazma_core.runs.adapters._common import epoch, short_title
from kazma_core.runs.contract import ActionResult, Run, RunState

__all__ = ["ADAPTER"]

_ACTIVE = frozenset({"validating", "parsing", "ocr_running", "normalizing", "indexing", "verifying"})


class _DocumentAdapter:
    STATE_MAP: dict[str, str] = {
        "received": RunState.QUEUED,
        "quarantined": RunState.QUEUED,
        "validating": RunState.RUNNING,
        "ready_to_parse": RunState.QUEUED,
        "parsing": RunState.RUNNING,
        "ocr_required": RunState.QUEUED,
        "ocr_running": RunState.RUNNING,
        "normalizing": RunState.RUNNING,
        "indexing": RunState.RUNNING,
        "verifying": RunState.RUNNING,
        "ready": RunState.DONE,
        "retry_wait": RunState.QUEUED,
        "rejected": RunState.FAILED,
        "cancelled": RunState.CANCELLED,
        "dead_letter": RunState.FAILED,
    }

    async def list_runs(self, now: float, *, window_s: float) -> list[Run]:
        from kazma_core.documents.ingestion import peek_ingestion_service

        service = peek_ingestion_service()
        if service is None:
            return []
        return await asyncio.to_thread(self._read, service, now, window_s)

    def _read(self, service: Any, now: float, window_s: float) -> list[Run]:
        from kazma_core.documents import ocr_progress

        since = datetime.fromtimestamp(now - window_s, UTC).isoformat()
        titles: dict[tuple[str, str], str] = {}
        runs: list[Run] = []
        for job in service.jobs.recent_jobs(since=since):
            key = (job.tenant_id, str(job.document_id))
            if key not in titles:
                record = service.repository.get_document(tenant_id=job.tenant_id, document_id=job.document_id,
                                                         include_deleted=True)
                titles[key] = record.title if record is not None else str(job.document_id)
            runs.append(self._run(job, titles[key], ocr_progress.get(str(job.id)), now))
        return runs

    def _run(self, job: Any, title: str, ocr: dict[str, Any] | None, now: float) -> Run:
        native = job.state.value
        state = RunState(self.STATE_MAP.get(native, RunState.UNKNOWN))
        owner = None
        if native in _ACTIVE:
            owner = bool(job.lease_owner) and (epoch(job.lease_expires_at) or 0.0) > now
        note = job.stage or native
        if ocr and ocr.get("ocr_pages_total"):
            note = f"OCR page {int(ocr.get('ocr_pages_done', 0))} of {int(ocr['ocr_pages_total'])}"
        if state in (RunState.QUEUED, RunState.RUNNING) and not job.cancel_requested:
            actions: tuple[str, ...] = ("cancel",)
        elif native in ("dead_letter", "rejected"):
            actions = ("retry",)
        else:
            actions = ()
        terminal = state in (RunState.DONE, RunState.FAILED, RunState.CANCELLED)
        return Run(
            run_id=f"document_job:{job.id}", kind="document_job", native_id=f"{job.tenant_id}/{job.id}",
            title=short_title(title), state=state, native_state=native,
            started_at=epoch(job.created_at),
            progress_at=epoch(job.heartbeat_at) or epoch(job.updated_at),
            progress_note=note, owner_alive=owner,
            deadline_at=epoch(job.retry_at) if native == "retry_wait" else None,
            ended_at=epoch(job.updated_at) if terminal else None,
            version=str(job.version), actions=actions,
            link="/documents?job=" + str(job.id),
            detail=short_title(job.error_message or job.error_code or "", 200) if state == RunState.FAILED else "",
            lineage=f"document:{job.tenant_id}/{job.document_id}",
        )

    async def act(self, native_id: str, action: str, *, actor: str) -> ActionResult:
        from kazma_core.documents.ingestion import peek_ingestion_service
        from kazma_core.documents.principal import operator_for

        tenant, _, job_id = native_id.partition("/")
        service = peek_ingestion_service()
        if service is None:
            return ActionResult(False, "documents_unavailable", "Documents are not running.")
        principal = await asyncio.to_thread(operator_for, service, tenant)
        if principal is None:
            return ActionResult(False, "single_operator", "Documents act for one operator only.")
        call = {"cancel": service.cancel_job, "retry": service.retry_job}.get(action)
        if call is None:
            return ActionResult(False, "not_offered", f"Document jobs do not take {action}.")
        from kazma_core.db.pg_helpers import store_errors
        from kazma_core.documents.ingestion import DocumentIngestionError
        from kazma_core.documents.jobs import JobConflictError
        from kazma_core.documents.repository import DocumentAccessError

        try:
            job = await asyncio.to_thread(call, tenant_id=tenant, job_id=job_id, actor_id=principal)
        except (JobConflictError, DocumentAccessError, DocumentIngestionError, ValueError,
                *store_errors()) as exc:
            return ActionResult(False, type(exc).__name__, short_title(str(exc), 200))
        return ActionResult(True, "cancel_requested" if action == "cancel" else "retried",
                            f"Job {job.get('job_id', job_id)} is {job.get('state', '')}.")


ADAPTER = _DocumentAdapter()
