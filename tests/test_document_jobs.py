from __future__ import annotations

import hashlib
import io
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.jobs import (
    DocumentJobRepository,
    InvalidJobTransitionError,
    JobConflictError,
    JobLeaseConflictError,
    StaleJobUpdateError,
)
from kazma_core.documents.models import DocumentJobState
from kazma_core.documents.repository import DocumentRepository


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self.value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.value += timedelta(seconds=seconds)


def _repository(
    path, *, clock: MutableClock | None = None
) -> tuple[DocumentRepository, DocumentJobRepository]:
    metadata = DocumentRepository(path, tenant_quota_bytes=100_000)
    jobs = DocumentJobRepository(
        metadata,
        clock=clock or MutableClock(),
        jitter=lambda _base: 0,
        retry_base_seconds=5,
    )
    return metadata, jobs


def _enqueue(
    metadata: DocumentRepository,
    jobs: DocumentJobRepository,
    *,
    tenant: str = "tenant-a",
    key: str = "upload-1",
    max_attempts: int = 3,
):
    document = metadata.create_document(
        tenant_id=tenant, owner_id="owner", title="Document"
    )
    payload = f"{tenant}-{key}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    blob = metadata.register_blob(
        tenant_id=tenant,
        sha256=digest,
        byte_size=len(payload),
        storage_kind="originals",
    )
    version = metadata.create_version(
        tenant_id=tenant,
        document_id=document.id,
        actor_id="owner",
        source_blob_id=blob.id,
        source_sha256=digest,
        original_filename="document.pdf",
        mime_type="application/pdf",
    )
    return jobs.enqueue(
        tenant_id=tenant,
        workspace_id="workspace-1",
        document_id=document.id,
        version_id=version.id,
        idempotency_key=key,
        max_attempts=max_attempts,
    )


def _ready(jobs: DocumentJobRepository, job):
    job = jobs.transition(
        tenant_id=job.tenant_id,
        job_id=job.id,
        expected_state=job.state,
        expected_version=job.version,
        new_state=DocumentJobState.QUARANTINED,
    )
    job = jobs.transition(
        tenant_id=job.tenant_id,
        job_id=job.id,
        expected_state=job.state,
        expected_version=job.version,
        new_state=DocumentJobState.VALIDATING,
    )
    return jobs.transition(
        tenant_id=job.tenant_id,
        job_id=job.id,
        expected_state=job.state,
        expected_version=job.version,
        new_state=DocumentJobState.READY_TO_PARSE,
    )


def test_job_schema_uses_document_connection_and_required_pragmas(tmp_path) -> None:
    metadata, jobs = _repository(tmp_path / "documents.db")
    try:
        assert jobs._conn is metadata._conn
        assert metadata._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert metadata._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        tables = {
            row[0]
            for row in metadata._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"document_jobs", "document_job_events"} <= tables
    finally:
        metadata.close()


def test_enqueue_is_idempotent_per_tenant_and_appends_initial_event(tmp_path) -> None:
    metadata, jobs = _repository(tmp_path / "documents.db")
    try:
        first = _enqueue(metadata, jobs)
        replay = jobs.enqueue(
            tenant_id=first.tenant_id,
            workspace_id=first.workspace_id,
            document_id=first.document_id,
            version_id=first.version_id,
            idempotency_key=first.idempotency_key,
        )
        assert replay.id == first.id
        events = jobs.events(tenant_id=first.tenant_id, job_id=first.id)
        assert [(event.event_type, event.to_state) for event in events] == [
            ("enqueued", DocumentJobState.RECEIVED)
        ]
    finally:
        metadata.close()


def test_idempotency_key_rejects_a_different_request(tmp_path) -> None:
    metadata, jobs = _repository(tmp_path / "documents.db")
    try:
        first = _enqueue(metadata, jobs, key="same-key")
        other = _enqueue(metadata, jobs, key="other-key")
        with pytest.raises(JobConflictError, match="different request"):
            jobs.enqueue(
                tenant_id=first.tenant_id,
                workspace_id=first.workspace_id,
                document_id=other.document_id,
                version_id=other.version_id,
                idempotency_key=first.idempotency_key,
            )
    finally:
        metadata.close()


def test_retry_delay_is_capped() -> None:
    repository = object.__new__(DocumentJobRepository)
    repository._retry_base_seconds = 5
    repository._retry_max_seconds = 30
    repository._jitter = lambda base: base

    assert repository._retry_delay(20) == 30


def test_valid_invalid_and_stale_cas_transitions(tmp_path) -> None:
    metadata, jobs = _repository(tmp_path / "documents.db")
    try:
        received = _enqueue(metadata, jobs)
        quarantined = jobs.transition(
            tenant_id=received.tenant_id,
            job_id=received.id,
            expected_state=received.state,
            expected_version=received.version,
            new_state=DocumentJobState.QUARANTINED,
        )
        assert quarantined.version == received.version + 1
        with pytest.raises(InvalidJobTransitionError):
            jobs.transition(
                tenant_id=quarantined.tenant_id,
                job_id=quarantined.id,
                expected_state=quarantined.state,
                expected_version=quarantined.version,
                new_state=DocumentJobState.READY,
            )
        with pytest.raises(StaleJobUpdateError):
            jobs.transition(
                tenant_id=received.tenant_id,
                job_id=received.id,
                expected_state=received.state,
                expected_version=received.version,
                new_state=DocumentJobState.QUARANTINED,
            )
    finally:
        metadata.close()


def test_concurrent_repository_connections_claim_exactly_once(tmp_path) -> None:
    path = tmp_path / "documents.db"
    metadata_a, jobs_a = _repository(path)
    job = _ready(jobs_a, _enqueue(metadata_a, jobs_a))
    metadata_b, jobs_b = _repository(path)
    barrier = threading.Barrier(2)

    def claim(repository: DocumentJobRepository, owner: str):
        barrier.wait(timeout=2)
        return repository.claim_next(owner=owner, lease_seconds=30)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda args: claim(*args),
                    ((jobs_a, "worker-a"), (jobs_b, "worker-b")),
                )
            )
        claimed = [result for result in results if result is not None]
        assert len(claimed) == 1
        assert claimed[0].id == job.id
        assert claimed[0].attempt == 1
    finally:
        metadata_b.close()
        metadata_a.close()


def test_lease_renewal_requires_owner_and_expiry_recovers_once(tmp_path) -> None:
    clock = MutableClock()
    metadata, jobs = _repository(tmp_path / "documents.db", clock=clock)
    try:
        ready = _ready(jobs, _enqueue(metadata, jobs))
        claimed = jobs.claim_next(owner="worker-a", lease_seconds=5)
        assert claimed is not None and claimed.id == ready.id
        renewed = jobs.renew_lease(
            tenant_id=claimed.tenant_id,
            job_id=claimed.id,
            owner="worker-a",
            lease_seconds=5,
        )
        assert renewed.lease_owner == "worker-a"
        with pytest.raises(JobLeaseConflictError):
            jobs.renew_lease(
                tenant_id=claimed.tenant_id,
                job_id=claimed.id,
                owner="worker-b",
                lease_seconds=5,
            )
        clock.advance(6)
        assert jobs.recover_expired_leases() == 1
        assert jobs.recover_expired_leases() == 0
        recovered = jobs.get(tenant_id=claimed.tenant_id, job_id=claimed.id)
        assert recovered is not None
        assert recovered.state is DocumentJobState.RETRY_WAIT
        assert recovered.lease_owner is None
        assert [
            event.event_type
            for event in jobs.events(tenant_id=claimed.tenant_id, job_id=claimed.id)
        ].count("lease_expired") == 1
    finally:
        metadata.close()


def test_transient_retry_backoff_then_dead_letter(tmp_path) -> None:
    clock = MutableClock()
    metadata, jobs = _repository(tmp_path / "documents.db", clock=clock)
    try:
        ready = _ready(jobs, _enqueue(metadata, jobs, max_attempts=2))
        first = jobs.claim_next(owner="worker", lease_seconds=30)
        assert first is not None and first.id == ready.id
        retry = jobs.record_failure(
            tenant_id=first.tenant_id,
            job_id=first.id,
            expected_state=first.state,
            expected_version=first.version,
            owner="worker",
            error_code="Parser Timeout!",
            error_message=" timed\nout ",
            transient=True,
        )
        assert retry.state is DocumentJobState.RETRY_WAIT
        assert retry.error_code == "parser_timeout"
        assert retry.error_message == "timed out"
        assert jobs.claim_next(owner="worker", lease_seconds=30) is None
        clock.advance(5)
        second = jobs.claim_next(owner="worker", lease_seconds=30)
        assert second is not None and second.attempt == 2
        dead = jobs.record_failure(
            tenant_id=second.tenant_id,
            job_id=second.id,
            expected_state=second.state,
            expected_version=second.version,
            owner="worker",
            error_code="parser_timeout",
            error_message="Parser timed out",
            transient=True,
        )
        assert dead.state is DocumentJobState.DEAD_LETTER
        with pytest.raises(InvalidJobTransitionError):
            jobs.request_cancel(tenant_id=dead.tenant_id, job_id=dead.id)
    finally:
        metadata.close()


def test_pending_and_cooperative_processing_cancellation(tmp_path) -> None:
    metadata, jobs = _repository(tmp_path / "documents.db")
    try:
        pending = _enqueue(metadata, jobs, key="pending")
        cancelled = jobs.request_cancel(tenant_id=pending.tenant_id, job_id=pending.id)
        assert cancelled.state is DocumentJobState.CANCELLED

        ready = _ready(jobs, _enqueue(metadata, jobs, key="active"))
        active = jobs.claim_next(owner="worker", lease_seconds=30)
        assert active is not None and active.id == ready.id
        requested = jobs.request_cancel(tenant_id=active.tenant_id, job_id=active.id)
        assert requested.state is DocumentJobState.PARSING
        assert requested.cancel_requested
        cancelled = jobs.cancel_claimed(
            tenant_id=active.tenant_id, job_id=active.id, owner="worker"
        )
        assert cancelled.state is DocumentJobState.CANCELLED
    finally:
        metadata.close()


def test_event_history_is_database_immutable(tmp_path) -> None:
    metadata, jobs = _repository(tmp_path / "documents.db")
    try:
        job = _enqueue(metadata, jobs)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            metadata._conn.execute(
                "UPDATE document_job_events SET event_type = 'changed' WHERE job_id = ?",
                (str(job.id),),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            metadata._conn.execute(
                "DELETE FROM document_job_events WHERE job_id = ?", (str(job.id),)
            )
        assert len(jobs.events(tenant_id=job.tenant_id, job_id=job.id)) == 1
    finally:
        metadata.close()


def test_terminal_job_row_is_database_immutable(tmp_path) -> None:
    metadata, jobs = _repository(tmp_path / "documents.db")
    try:
        job = _enqueue(metadata, jobs)
        cancelled = jobs.request_cancel(tenant_id=job.tenant_id, job_id=job.id)
        with pytest.raises(sqlite3.IntegrityError, match="terminal"):
            metadata._conn.execute(
                "UPDATE document_jobs SET state = 'received' WHERE id = ?",
                (str(cancelled.id),),
            )
    finally:
        metadata.close()


def test_validating_to_ocr_required_transition_and_claim(tmp_path) -> None:
    metadata, jobs = _repository(tmp_path / "documents.db")
    try:
        job = _enqueue(metadata, jobs)
        job = jobs.transition(
            tenant_id=job.tenant_id,
            job_id=job.id,
            expected_state=job.state,
            expected_version=job.version,
            new_state=DocumentJobState.QUARANTINED,
        )
        job = jobs.transition(
            tenant_id=job.tenant_id,
            job_id=job.id,
            expected_state=job.state,
            expected_version=job.version,
            new_state=DocumentJobState.VALIDATING,
        )
        forced = jobs.transition(
            tenant_id=job.tenant_id,
            job_id=job.id,
            expected_state=job.state,
            expected_version=job.version,
            new_state=DocumentJobState.OCR_REQUIRED,
            event_type="validated",
        )
        assert forced.state is DocumentJobState.OCR_REQUIRED
        # The OCR leg of the claim path must be able to pick the row up.
        claimed = jobs.claim_next(owner="ocr-worker", lease_seconds=30)
        assert claimed is not None and claimed.id == forced.id
        assert claimed.state is DocumentJobState.OCR_RUNNING
    finally:
        metadata.close()


def test_claim_next_dead_letters_exhausted_rows_and_claims_healthy_work(
    tmp_path,
) -> None:
    clock = MutableClock()
    metadata, jobs = _repository(tmp_path / "documents.db", clock=clock)
    try:
        # Exhausted ocr_required row via the realistic path: claim the only
        # attempt, then the parser escalates PARSING -> OCR_REQUIRED.
        escalated = _ready(
            jobs, _enqueue(metadata, jobs, key="escalated", max_attempts=1)
        )
        claimed = jobs.claim_next(owner="worker", lease_seconds=30)
        assert claimed is not None and claimed.id == escalated.id
        escalated = jobs.transition(
            tenant_id=claimed.tenant_id,
            job_id=claimed.id,
            expected_state=claimed.state,
            expected_version=claimed.version,
            new_state=DocumentJobState.OCR_REQUIRED,
            lease_owner="worker",
        )
        # Exhausted ready_to_parse row (defense in depth for any producer).
        stalled = _ready(jobs, _enqueue(metadata, jobs, key="stalled", max_attempts=1))
        metadata._conn.execute(
            "UPDATE document_jobs SET attempt = max_attempts WHERE id = ?",
            (str(stalled.id),),
        )
        metadata._conn.commit()
        clock.advance(1)
        healthy = _ready(jobs, _enqueue(metadata, jobs, key="healthy"))
        # A single exhausted claimable row used to raise
        # InvalidJobTransitionError here, rolling back the whole claim
        # transaction and wedging the queue forever.
        survivor = jobs.claim_next(owner="worker", lease_seconds=30)
        assert survivor is not None and survivor.id == healthy.id
        for poisoned in (escalated, stalled):
            dead = jobs.get(tenant_id=poisoned.tenant_id, job_id=poisoned.id)
            assert dead is not None
            assert dead.state is DocumentJobState.DEAD_LETTER
            assert dead.error_code == "max_attempts"
    finally:
        metadata.close()


def test_transient_intake_failure_lands_in_claimable_retry_wait(
    tmp_path, monkeypatch
) -> None:
    clock = MutableClock()
    config = DocumentConfig(storage_root=tmp_path / "store")
    metadata = DocumentRepository(
        config.storage_root / "documents.db", tenant_quota_bytes=100_000
    )
    jobs = DocumentJobRepository(
        metadata, clock=clock, jitter=lambda _base: 0, retry_base_seconds=5
    )
    service = DocumentIngestionService(
        config=config, repository=metadata, jobs=jobs
    )

    def _transient_fault(**_kwargs) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(service, "_validate_and_promote", _transient_fault)
    try:
        result = service.ingest_stream(
            io.BytesIO(b"Kazma transient intake retry probe.\n"),
            filename="note.txt",
            tenant_id="tenant-a",
            workspace_id="ws-1",
            actor_id="alice",
        )
        assert result.state is DocumentJobState.RETRY_WAIT
        job = jobs.get(tenant_id="tenant-a", job_id=result.job_id)
        assert job is not None
        assert job.stage == "validating"  # ACTIVE stage preserved -> claimable
        assert job.retry_at is not None  # reclaim gate stamped with backoff
        assert job.error_code == "validation_transient"
        assert jobs.claim_next(owner="worker", lease_seconds=30) is None
        clock.advance(5)  # retry_base_seconds with zero jitter
        claimed = jobs.claim_next(owner="worker", lease_seconds=30)
        assert claimed is not None and claimed.id == job.id
        assert claimed.state is DocumentJobState.VALIDATING
    finally:
        service.close()
