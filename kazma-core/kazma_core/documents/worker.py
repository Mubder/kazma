"""Bounded asynchronous orchestration for durable document jobs."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from . import telemetry
from .jobs import (
    DocumentJobRecord,
    DocumentJobRepository,
    JobConflictError,
)
from .models import DocumentJobState

logger = logging.getLogger(__name__)

__all__ = [
    "DocumentProcessingError",
    "DocumentWorker",
    "DocumentWorkerManager",
    "PermanentDocumentError",
    "StageContext",
    "StageHandler",
    "StageResult",
    "TransientDocumentError",
    "ValidationPolicyError",
    "start_document_workers",
    "stop_document_workers",
]

_PROCESSING_STATES = frozenset(
    {
        DocumentJobState.VALIDATING,
        DocumentJobState.PARSING,
        DocumentJobState.OCR_RUNNING,
        DocumentJobState.NORMALIZING,
        DocumentJobState.INDEXING,
        DocumentJobState.VERIFYING,
    }
)


class DocumentProcessingError(RuntimeError):
    """Base error carrying a safe durable error code and message."""

    transient = False

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class PermanentDocumentError(DocumentProcessingError):
    """A permanent parser or policy failure."""


class ValidationPolicyError(PermanentDocumentError):
    """A permanent document-validation policy rejection."""


class TransientDocumentError(DocumentProcessingError):
    """A retryable worker, parser, or infrastructure failure."""

    transient = True


@dataclass(frozen=True, slots=True)
class StageResult:
    """The canonical state reached by a successful stage handler."""

    next_state: DocumentJobState
    stage: str | None = None


class StageContext:
    """Thread-safe cooperative cancellation signal for a stage handler."""

    __slots__ = ("_cancelled",)

    def __init__(self, cancelled: threading.Event) -> None:
        self._cancelled = cancelled

    def cancellation_requested(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancellation_requested():
            raise TransientDocumentError(
                "cooperative_cancel", "Document processing was cancelled"
            )


class StageHandler(Protocol):
    """Synchronous handler executed off the API event-loop thread."""

    def __call__(
        self, job: DocumentJobRecord, context: StageContext
    ) -> StageResult: ...


class DocumentWorker:
    """Poll and execute document stages with bounded concurrency and leases."""

    def __init__(
        self,
        repository: DocumentJobRepository,
        handlers: Mapping[DocumentJobState, StageHandler],
        *,
        concurrency: int = 2,
        poll_interval: float = 0.25,
        lease_seconds: float = 60.0,
        heartbeat_interval: float | None = None,
        owner: str | None = None,
        tenant_id: str | None = None,
        shutdown_grace_seconds: float = 30.0,
    ) -> None:
        if isinstance(concurrency, bool) or int(concurrency) <= 0:
            raise ValueError("concurrency must be a positive integer")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        heartbeat = heartbeat_interval or max(0.1, lease_seconds / 3)
        if heartbeat <= 0 or heartbeat >= lease_seconds:
            raise ValueError("heartbeat_interval must be positive and below lease_seconds")
        if shutdown_grace_seconds <= 0:
            raise ValueError("shutdown_grace_seconds must be positive")
        self._repository = repository
        self._handlers = {
            DocumentJobState(state): handler for state, handler in handlers.items()
        }
        self._concurrency = int(concurrency)
        self._poll_interval = float(poll_interval)
        self._lease_seconds = float(lease_seconds)
        self._heartbeat_interval = float(heartbeat)
        self._owner = owner or f"document-worker-{uuid.uuid4()}"
        self._tenant_id = tenant_id
        self._shutdown_grace_seconds = float(shutdown_grace_seconds)
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def running(self) -> bool:
        return bool(self._tasks) and any(not task.done() for task in self._tasks)

    @property
    def owner(self) -> str:
        return self._owner

    async def start(self) -> None:
        """Run startup recovery and begin the bounded poll loops."""
        if self.running:
            raise RuntimeError("document worker is already running")
        self._stop_event.clear()
        recovered = await asyncio.to_thread(
            self._repository.recover_expired_leases, tenant_id=self._tenant_id
        )
        if recovered:
            logger.info("Recovered %d expired document jobs", recovered)
        self._tasks = [
            asyncio.create_task(
                self._worker_loop(index),
                name=f"document-worker-{index}",
            )
            for index in range(self._concurrency)
        ]

    async def stop(self) -> None:
        """Stop new claims, await active work, then cancel and await stragglers."""
        self._stop_event.set()
        tasks = list(self._tasks)
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self._shutdown_grace_seconds,
            )
        except TimeoutError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._tasks.clear()

    async def _worker_loop(self, index: int) -> None:
        owner = f"{self._owner}:{index}"
        while not self._stop_event.is_set():
            try:
                job = await asyncio.to_thread(
                    self._repository.claim_next,
                    owner=owner,
                    lease_seconds=self._lease_seconds,
                    tenant_id=self._tenant_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Document claim failed worker=%s error_type=%s",
                    owner,
                    type(exc).__name__,
                )
                await self._wait_for_poll()
                continue
            if job is None:
                await self._wait_for_poll()
                continue
            await self._process_claimed(job, owner)

    async def _wait_for_poll(self) -> None:
        try:
            await asyncio.wait_for(
                self._stop_event.wait(), timeout=self._poll_interval
            )
        except TimeoutError:
            pass

    async def _process_claimed(self, job: DocumentJobRecord, owner: str) -> None:
        current = job
        while current.state in _PROCESSING_STATES:
            cancellation = threading.Event()
            heartbeat = asyncio.create_task(
                self._heartbeat(current, owner, cancellation),
                name=f"document-heartbeat-{current.id}",
            )
            failure: Exception | None = None
            result: StageResult | None = None
            stage_label = current.state.value
            stage_started = time.monotonic()
            try:
                handler = self._handlers.get(current.state)
                if handler is None:
                    raise PermanentDocumentError(
                        "handler_missing",
                        f"No document handler is configured for stage {current.state.value}",
                    )
                result = await asyncio.to_thread(
                    handler, current, StageContext(cancellation)
                )
                if not isinstance(result, StageResult):
                    raise PermanentDocumentError(
                        "invalid_handler_result",
                        "Document stage handler returned an invalid result",
                    )
            except asyncio.CancelledError:
                cancellation.set()
                raise
            except Exception as exc:
                failure = exc
            finally:
                cancellation.set()
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            telemetry.record_stage(
                stage_label,
                "failure" if failure is not None else "success",
                latency_seconds=time.monotonic() - stage_started,
            )

            refreshed = await asyncio.to_thread(
                self._repository.get,
                tenant_id=current.tenant_id,
                job_id=current.id,
            )
            if (
                refreshed is None
                or refreshed.lease_owner != owner
                or refreshed.state not in _PROCESSING_STATES
            ):
                return
            if refreshed.cancel_requested:
                await self._persist_cancellation(refreshed, owner)
                return
            if failure is not None:
                await self._persist_failure(refreshed, owner, failure)
                return
            if result is None:
                await self._persist_failure(
                    refreshed,
                    owner,
                    TransientDocumentError(
                        "worker_crash", "Document worker ended without a stage result"
                    ),
                )
                return
            try:
                current = await asyncio.to_thread(
                    self._repository.transition,
                    tenant_id=refreshed.tenant_id,
                    job_id=refreshed.id,
                    expected_state=refreshed.state,
                    expected_version=refreshed.version,
                    new_state=result.next_state,
                    stage=result.stage,
                    lease_owner=owner,
                    event_type="stage_completed",
                )
            except JobConflictError as exc:
                logger.warning(
                    "Document stage transition conflicted job=%s error_type=%s",
                    refreshed.id,
                    type(exc).__name__,
                )
                latest = await asyncio.to_thread(
                    self._repository.get,
                    tenant_id=refreshed.tenant_id,
                    job_id=refreshed.id,
                )
                if (
                    latest is not None
                    and latest.lease_owner == owner
                    and latest.cancel_requested
                    and latest.state in _PROCESSING_STATES
                ):
                    await self._persist_cancellation(latest, owner)
                return

    async def _heartbeat(
        self,
        job: DocumentJobRecord,
        owner: str,
        cancellation: threading.Event,
    ) -> None:
        while not cancellation.is_set():
            try:
                await asyncio.sleep(self._heartbeat_interval)
                if cancellation.is_set():
                    return
                refreshed = await asyncio.to_thread(
                    self._repository.renew_lease,
                    tenant_id=job.tenant_id,
                    job_id=job.id,
                    owner=owner,
                    lease_seconds=self._lease_seconds,
                )
                if refreshed.cancel_requested:
                    cancellation.set()
                    return
            except asyncio.CancelledError:
                raise
            except JobConflictError:
                cancellation.set()
                return
            except Exception as exc:
                logger.error(
                    "Document heartbeat failed job=%s error_type=%s",
                    job.id,
                    type(exc).__name__,
                )
                cancellation.set()
                return

    async def _persist_cancellation(
        self, job: DocumentJobRecord, owner: str
    ) -> None:
        try:
            await asyncio.to_thread(
                self._repository.cancel_claimed,
                tenant_id=job.tenant_id,
                job_id=job.id,
                owner=owner,
            )
        except JobConflictError as exc:
            logger.warning(
                "Document cancellation conflicted job=%s error_type=%s",
                job.id,
                type(exc).__name__,
            )

    async def _persist_failure(
        self,
        job: DocumentJobRecord,
        owner: str,
        failure: Exception,
    ) -> None:
        code, message, transient = _classify_failure(failure)
        try:
            updated = await asyncio.to_thread(
                self._repository.record_failure,
                tenant_id=job.tenant_id,
                job_id=job.id,
                expected_state=job.state,
                expected_version=job.version,
                owner=owner,
                error_code=code,
                error_message=message,
                transient=transient,
            )
        except JobConflictError as exc:
            logger.warning(
                "Document failure persistence conflicted job=%s error_type=%s",
                job.id,
                type(exc).__name__,
            )
            return
        # Sandbox containment + dead-letter telemetry (no content, safe codes).
        if code in ("parser_timeout", "parser_oom", "parser_output_limit", "sandbox_terminated"):
            _reason = {
                "parser_timeout": "timeout",
                "parser_oom": "oom",
                "parser_output_limit": "output",
                "sandbox_terminated": "degraded",
            }[code]
            telemetry.record_sandbox_termination(_reason)
        if updated is not None and updated.state is DocumentJobState.DEAD_LETTER:
            telemetry.record_dead_letter()
        logger.warning(
            "Document stage failed job=%s stage=%s code=%s transient=%s",
            job.id,
            job.state.value,
            code,
            transient,
        )


def _classify_failure(failure: Exception) -> tuple[str, str, bool]:
    if isinstance(failure, DocumentProcessingError):
        return failure.code, failure.safe_message, failure.transient
    if isinstance(failure, (TimeoutError, subprocess.TimeoutExpired)):
        return "parser_timeout", "Document parser exceeded its time limit", True
    return (
        "worker_crash",
        f"Document stage failed unexpectedly ({type(failure).__name__})",
        True,
    )


class DocumentWorkerManager:
    """Explicit lifecycle owner for future application wiring."""

    def __init__(self, worker: DocumentWorker) -> None:
        self.worker = worker

    async def start(self) -> None:
        await self.worker.start()

    async def stop(self) -> None:
        await self.worker.stop()


_default_manager: DocumentWorkerManager | None = None


async def start_document_workers(
    repository: DocumentJobRepository,
    handlers: Mapping[DocumentJobState, StageHandler],
    **kwargs: object,
) -> DocumentWorkerManager:
    """Explicitly construct and start the process-local document worker manager."""
    global _default_manager
    if _default_manager is not None and _default_manager.worker.running:
        raise RuntimeError("document workers are already running")
    worker = DocumentWorker(repository, handlers, **kwargs)
    manager = DocumentWorkerManager(worker)
    await manager.start()
    _default_manager = manager
    return manager


async def stop_document_workers() -> None:
    """Stop the explicitly started process-local document worker manager."""
    global _default_manager
    manager = _default_manager
    _default_manager = None
    if manager is not None:
        await manager.stop()
