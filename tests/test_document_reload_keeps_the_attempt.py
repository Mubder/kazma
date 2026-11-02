"""A graceful stop gives document jobs back on the attempt they had (FA-264).

The 2026-11 functional audit, three reloads during one long OCR job (a
six-page Arabic scan, ~110 s a page): each new process's lease recovery
re-queued it as the next attempt (``lease_expired``), and the third reload
dead-lettered it "Worker lease expired after maximum attempts" -- the ending
of a worker that crashed three times on a poisonous file, paged to the owner
as "Document job failed".

On a graceful stop the worker releases its leases (``released_for_shutdown``,
the claim's count returned) and stops its stages; the next claim runs on the
same attempt. The store's rule, and through the real worker; lease expiry
(what a reload was before) is the negative control.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from kazma_core.documents.models import DocumentJobState
from kazma_core.documents.worker import DocumentWorker, StageResult
from tests.test_document_worker import _ready_job, _wait_state


def test_three_graceful_stops_never_dead_letter_a_job(tmp_path):
    metadata, jobs, job = _ready_job(tmp_path, key="released", max_attempts=3)
    try:
        for _reload in range(3):
            claimed = jobs.claim_next(owner="w:0", lease_seconds=60)
            assert claimed is not None and claimed.id == job.id and claimed.attempt == 1
            assert jobs.release_leases(["w:0"]) == 1
            back = jobs.get(tenant_id=job.tenant_id, job_id=job.id)
            assert back.state is DocumentJobState.RETRY_WAIT and back.attempt == 0
            assert back.lease_owner is None
        events = [e.event_type for e in jobs.events(tenant_id=job.tenant_id, job_id=job.id)]
        assert events.count("released_for_shutdown") == 3 and "attempts_exhausted" not in events
        assert jobs.release_leases(["w:0"]) == 0  # nothing held
    finally:
        metadata.close()


def test_a_job_with_a_cancel_requested_ends_cancelled(tmp_path):
    metadata, jobs, job = _ready_job(tmp_path, key="cancel-on-stop")
    try:
        claimed = jobs.claim_next(owner="w:0", lease_seconds=60)
        jobs.request_cancel(tenant_id=claimed.tenant_id, job_id=claimed.id)
        assert jobs.release_leases(["w:0"]) == 1
        assert jobs.get(tenant_id=job.tenant_id, job_id=job.id).state is DocumentJobState.CANCELLED
    finally:
        metadata.close()


async def test_the_worker_gives_its_job_back_and_stops_quickly(tmp_path):
    metadata, jobs, job = _ready_job(tmp_path, key="worker-release")
    started = threading.Event()
    hold = threading.Event()

    def long_ocr(_job, _context):
        started.set()
        hold.wait(10)  # a stage in flight (a thread: cancellation cannot reach it)
        return StageResult(DocumentJobState.NORMALIZING)

    worker = DocumentWorker(jobs, {DocumentJobState.PARSING: long_ocr}, concurrency=1,
                            poll_interval=0.01, lease_seconds=60, heartbeat_interval=0.05,
                            shutdown_grace_seconds=30)
    try:
        await worker.start()
        await asyncio.to_thread(started.wait, 5)
        loop = asyncio.get_running_loop()
        began = loop.time()
        await worker.stop()
        assert loop.time() - began < 6, "the stop did not wait the 30 s grace for the stage"
        back = await asyncio.to_thread(jobs.get, tenant_id=job.tenant_id, job_id=job.id)
        assert back.state is DocumentJobState.RETRY_WAIT and back.attempt == 0
    finally:
        hold.set()
        metadata.close()


def test_lease_expiry_spent_the_attempts(tmp_path):
    """Negative control: a reload before FA-264 -- the lease expired and the
    next process recovered it -- three times dead-letters the job."""
    from datetime import UTC, datetime, timedelta

    now = [datetime.now(UTC)]
    metadata, jobs, job = _ready_job(tmp_path, key="expired", max_attempts=3)
    jobs._clock = lambda: now[0]
    try:
        for _reload in range(3):
            jobs.claim_next(owner="w:0", lease_seconds=5)
            now[0] += timedelta(seconds=10)
            jobs.recover_expired_leases()
        assert jobs.get(tenant_id=job.tenant_id, job_id=job.id).state is DocumentJobState.DEAD_LETTER
    finally:
        metadata.close()



@pytest.mark.postgres
def test_the_postgres_store_releases_on_the_same_attempt():
    """The same rule on the Postgres job store (the live install's)."""
    import os

    from kazma_core.db.postgres_pool import get_postgres_pool, reset_postgres_pool
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository
    from kazma_core.documents.models import new_document_id, new_version_id

    if not os.environ.get("KAZMA_DATABASE_URL") or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (scripts/postgres_suite.py)")
    reset_postgres_pool()
    pool = get_postgres_pool()
    if pool is None:
        pytest.skip("Postgres pool not configured")
    repo = PostgresDocumentJobRepository(pool)
    tenant = f"t-{os.urandom(4).hex()}"
    job = repo.enqueue(tenant_id=tenant, workspace_id="ws", document_id=new_document_id(),
                       version_id=new_version_id(), idempotency_key="fa264", max_attempts=3)
    for state in (DocumentJobState.QUARANTINED, DocumentJobState.VALIDATING, DocumentJobState.READY_TO_PARSE):
        job = repo.transition(tenant_id=tenant, job_id=job.id, expected_state=job.state,
                              expected_version=job.version, new_state=state)
    owner = f"fa264-{os.urandom(3).hex()}:0"
    for _reload in range(3):
        claimed = repo.claim_next(owner=owner, lease_seconds=60, tenant_id=tenant)
        assert claimed is not None and claimed.attempt == 1
        assert repo.release_leases([owner]) == 1
        back = repo.get(tenant_id=tenant, job_id=job.id)
        assert back.state is DocumentJobState.RETRY_WAIT and back.attempt == 0
