from __future__ import annotations

import asyncio
import hashlib
import threading
import time

from kazma_core.documents.jobs import DocumentJobRepository
from kazma_core.documents.models import DocumentJobState
from kazma_core.documents.repository import DocumentRepository
from kazma_core.documents.worker import (
    DocumentWorker,
    StageResult,
    ValidationPolicyError,
)


def _ready_job(tmp_path, *, key: str = "worker-job", max_attempts: int = 3):
    metadata = DocumentRepository(
        tmp_path / f"{key}.db", tenant_quota_bytes=100_000
    )
    jobs = DocumentJobRepository(metadata, jitter=lambda _base: 0)
    document = metadata.create_document(
        tenant_id="tenant", owner_id="owner", title="Worker document"
    )
    digest = hashlib.sha256(key.encode()).hexdigest()
    blob = metadata.register_blob(
        tenant_id="tenant",
        sha256=digest,
        byte_size=len(key),
        storage_kind="originals",
    )
    version = metadata.create_version(
        tenant_id="tenant",
        document_id=document.id,
        actor_id="owner",
        source_blob_id=blob.id,
        source_sha256=digest,
        original_filename="worker.pdf",
        mime_type="application/pdf",
    )
    job = jobs.enqueue(
        tenant_id="tenant",
        workspace_id="workspace",
        document_id=document.id,
        version_id=version.id,
        idempotency_key=key,
        max_attempts=max_attempts,
    )
    for state in (
        DocumentJobState.QUARANTINED,
        DocumentJobState.VALIDATING,
        DocumentJobState.READY_TO_PARSE,
    ):
        job = jobs.transition(
            tenant_id=job.tenant_id,
            job_id=job.id,
            expected_state=job.state,
            expected_version=job.version,
            new_state=state,
        )
    return metadata, jobs, job


async def _wait_state(
    jobs: DocumentJobRepository,
    job,
    expected: DocumentJobState,
    *,
    timeout: float = 2,
):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        current = await asyncio.to_thread(
            jobs.get, tenant_id=job.tenant_id, job_id=job.id
        )
        if current is not None and current.state is expected:
            return current
        await asyncio.sleep(0.01)
    raise AssertionError(f"job did not reach {expected.value}")


async def test_worker_runs_handlers_off_event_loop_and_reaches_ready(tmp_path) -> None:
    metadata, jobs, job = _ready_job(tmp_path)
    loop_thread = threading.get_ident()
    handler_threads: list[int] = []

    def handler(next_state):
        def run(_job, _context):
            handler_threads.append(threading.get_ident())
            return StageResult(next_state)

        return run

    worker = DocumentWorker(
        jobs,
        {
            DocumentJobState.PARSING: handler(DocumentJobState.NORMALIZING),
            DocumentJobState.NORMALIZING: handler(DocumentJobState.INDEXING),
            DocumentJobState.INDEXING: handler(DocumentJobState.VERIFYING),
            DocumentJobState.VERIFYING: handler(DocumentJobState.READY),
        },
        concurrency=2,
        poll_interval=0.01,
        lease_seconds=1,
        heartbeat_interval=0.05,
    )
    try:
        await worker.start()
        current = await _wait_state(jobs, job, DocumentJobState.READY)
        assert current.attempt == 1
        assert handler_threads
        assert all(thread_id != loop_thread for thread_id in handler_threads)
    finally:
        await worker.stop()
        metadata.close()


async def test_worker_persists_permanent_and_transient_failures(tmp_path) -> None:
    metadata_a, jobs_a, permanent_job = _ready_job(tmp_path, key="permanent")

    def reject(_job, _context):
        raise ValidationPolicyError("mime_rejected", "MIME type is not allowed")

    permanent_worker = DocumentWorker(
        jobs_a,
        {DocumentJobState.PARSING: reject},
        concurrency=1,
        poll_interval=0.01,
        lease_seconds=1,
        heartbeat_interval=0.05,
    )
    try:
        await permanent_worker.start()
        rejected = await _wait_state(
            jobs_a, permanent_job, DocumentJobState.REJECTED
        )
        assert rejected.error_code == "mime_rejected"
    finally:
        await permanent_worker.stop()
        metadata_a.close()

    metadata_b, jobs_b, timeout_job = _ready_job(
        tmp_path, key="timeout", max_attempts=1
    )

    def timeout(_job, _context):
        raise TimeoutError("raw parser detail must not be persisted")

    transient_worker = DocumentWorker(
        jobs_b,
        {DocumentJobState.PARSING: timeout},
        concurrency=1,
        poll_interval=0.01,
        lease_seconds=1,
        heartbeat_interval=0.05,
    )
    try:
        await transient_worker.start()
        dead = await _wait_state(
            jobs_b, timeout_job, DocumentJobState.DEAD_LETTER
        )
        assert dead.error_code == "parser_timeout"
        assert "raw parser detail" not in (dead.error_message or "")
    finally:
        await transient_worker.stop()
        metadata_b.close()


async def test_worker_cooperatively_observes_durable_cancellation(tmp_path) -> None:
    metadata, jobs, job = _ready_job(tmp_path, key="cancel")
    entered = threading.Event()
    observed = threading.Event()

    def cancellable(_job, context):
        entered.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if context.cancellation_requested():
                observed.set()
                break
            time.sleep(0.005)
        return StageResult(DocumentJobState.NORMALIZING)

    worker = DocumentWorker(
        jobs,
        {DocumentJobState.PARSING: cancellable},
        concurrency=1,
        poll_interval=0.01,
        lease_seconds=0.3,
        heartbeat_interval=0.02,
    )
    try:
        await worker.start()
        assert await asyncio.to_thread(entered.wait, 1)
        active = await _wait_state(jobs, job, DocumentJobState.PARSING)
        jobs.request_cancel(tenant_id=active.tenant_id, job_id=active.id)
        await _wait_state(jobs, job, DocumentJobState.CANCELLED)
        assert observed.is_set()
    finally:
        await worker.stop()
        metadata.close()


async def test_stop_is_bounded_even_when_a_task_ignores_cancellation(tmp_path):
    """``stop()`` must return. It could previously hang the whole process.

    The worker loop parks in ``asyncio.to_thread(claim_next)``, and a thread
    is not cancellable: ``task.cancel()`` only marks the task, and the
    exception is delivered when the thread returns. ``stop()`` bounded its
    first wait and then did an UNBOUNDED ``gather(*tasks)`` after cancelling,
    so a claim stuck on a lock meant shutdown never finished.

    That is what CI had been failing on since 2026-09-19 — every run red,
    ``test_documents_api_phase8`` timing out in ``TestClient.__exit__`` ->
    ``wait_shutdown``, and **"0 failed"** in the totals every time, because
    the job exits 1 on a teardown timeout rather than an assertion.

    This test does not need a real stuck SQLite lock. It needs a task that
    does not honour cancellation, which is the property that mattered.
    """
    metadata = DocumentRepository(tmp_path / "bounded.db", tenant_quota_bytes=100_000)
    jobs = DocumentJobRepository(metadata, jitter=lambda _base: 0)
    worker = DocumentWorker(
        jobs, {}, concurrency=1, poll_interval=0.05, shutdown_grace_seconds=0.2
    )

    async def _uncancellable() -> None:
        # Shields itself the way a thread effectively does: the cancellation
        # is delivered but the coroutine does not finish promptly.
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await asyncio.sleep(30)  # "thread still running"
                raise

    worker._tasks = [asyncio.create_task(_uncancellable())]

    started = time.monotonic()
    await asyncio.wait_for(worker.stop(), timeout=20.0)
    elapsed = time.monotonic() - started

    # grace (0.2s) + cancel grace (2s) + slack. The point is that it RETURNS;
    # the bound is what makes the difference between a slow shutdown and a
    # process that never exits.
    assert elapsed < 10.0, f"stop() took {elapsed:.1f}s — the bound is gone"
    assert worker._tasks == [], "stop() must clear its task list even on timeout"

    for task in asyncio.all_tasks() - {asyncio.current_task()}:
        task.cancel()
