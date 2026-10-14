"""An outage is retried within the queue's budget; only content is rejected.

2026-10-13 (F07/Q01). Real queue, real stage handlers, faults injected where
they happen in production. Each fault had its own wrong ending before:

* a required malware scanner that stopped running between intake and parse
  REJECTED the document permanently, as if its content were unsafe;
* a Postgres error during intake validation reached the generic handler and
  REJECTED it (only SQLite errors were treated as transient);
* a full disk while writing the manifest was recorded as ``worker_crash``;
* a store error while RECORDING a stage's outcome ended the worker loop for
  good -- one database blip removed a worker until the next restart.

Negative controls: infected content and a ``ValueError`` in validation still
reject permanently, and ``_process_claimed`` itself still raises the store
error the loop now survives.
"""

from __future__ import annotations

import asyncio
import errno
import io
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from kazma_core.documents import malware
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.errors import DocumentSecurityError, infrastructure_errors, storage_full
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.jobs import DocumentJobState
from kazma_core.documents.malware import MalwareScannerUnavailable, MalwareScanResult
from kazma_core.documents.worker import DocumentWorker


def _service(root, **changes) -> DocumentIngestionService:
    settings = dict(
        storage_root=root, security_malware_scan="off", security_local_salvage=False, ocr_enabled=False,
    )
    settings.update(changes)
    svc = DocumentIngestionService(config=DocumentConfig(**settings))
    svc.jobs._jitter = lambda _base: 0.0
    return svc


def _upload(svc, body: bytes = b"fault body\n", key: str | None = None):
    return svc.ingest_stream(
        io.BytesIO(body), filename="note.txt", tenant_id="t", actor_id="alice",
        workspace_id="w", idempotency_key=key,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self) -> None:
        self.now += timedelta(hours=1)


async def _process_one(svc, owner: str = "fault-worker") -> None:
    claimed = svc.jobs.claim_next(owner=owner, lease_seconds=60)
    assert claimed is not None
    await DocumentWorker(svc.jobs, svc.stage_handlers())._process_claimed(claimed, owner)


def _job(svc, result):
    return svc.jobs.get(tenant_id="t", job_id=result.job_id)


def _scanner(monkeypatch, status: str) -> None:
    detail = {"clean": "ok", "skipped": "no clamscan/clamdscan on PATH", "infected": "threat detected"}[status]
    signature = "Test.Signature" if status == "infected" else None
    monkeypatch.setattr(
        malware, "scan_document_path",
        lambda path: MalwareScanResult(status, "clamscan", detail, signature=signature),
    )


def test_the_error_classes_say_what_is_an_outage():
    assert MalwareScannerUnavailable("no scanner").retryable
    assert not DocumentSecurityError("bad bytes").retryable  # control: content stays permanent
    assert storage_full(OSError(errno.ENOSPC, "No space left on device"))
    assert not storage_full(OSError(errno.ENOENT, "missing"))
    assert sqlite3.OperationalError in infrastructure_errors() or sqlite3.Error in infrastructure_errors()
    assert ValueError not in infrastructure_errors()


@pytest.mark.asyncio
async def test_a_scanner_outage_holds_a_queued_document_and_retries_it(tmp_path, monkeypatch):
    svc = _service(tmp_path, security_malware_scan="on", worker_max_retries=3)
    clock = _Clock()
    monkeypatch.setattr(svc.jobs, "_clock", clock)
    try:
        _scanner(monkeypatch, "clean")
        result = _upload(svc)
        assert result.state is DocumentJobState.READY_TO_PARSE
        _scanner(monkeypatch, "skipped")  # clamd stopped while the job queued
        await _process_one(svc)
        held = _job(svc, result)
        assert held.state is DocumentJobState.RETRY_WAIT, held.state
        assert held.error_code == "malware_scanner_unavailable"
        _scanner(monkeypatch, "clean")  # the scanner is back
        clock.advance()
        await _process_one(svc)
        assert _job(svc, result).state is DocumentJobState.READY
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_a_scanner_that_never_returns_dead_letters_instead_of_rejecting(tmp_path, monkeypatch):
    svc = _service(tmp_path, security_malware_scan="on", worker_max_retries=2)
    clock = _Clock()
    monkeypatch.setattr(svc.jobs, "_clock", clock)
    try:
        _scanner(monkeypatch, "clean")
        result = _upload(svc)
        _scanner(monkeypatch, "skipped")
        for _ in range(2):
            await _process_one(svc)
            clock.advance()
        job = _job(svc, result)
        assert job.state is DocumentJobState.DEAD_LETTER
        assert job.error_code == "malware_scanner_unavailable"
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_infected_content_is_still_rejected_permanently(tmp_path, monkeypatch):
    # Negative control: the content verdict is not softened by the change.
    svc = _service(tmp_path, security_malware_scan="on")
    try:
        _scanner(monkeypatch, "clean")
        result = _upload(svc)
        _scanner(monkeypatch, "infected")
        await _process_one(svc)
        job = _job(svc, result)
        assert job.state is DocumentJobState.REJECTED
        assert job.error_code == "malware_detected"
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_a_full_disk_while_writing_the_manifest_is_retried(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    clock = _Clock()
    monkeypatch.setattr(svc.jobs, "_clock", clock)
    try:
        result = _upload(svc)
        real = svc.storage.write_manifest
        calls = {"n": 0}

        def full_once(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(**kwargs)

        monkeypatch.setattr(svc.storage, "write_manifest", full_once)
        await _process_one(svc)
        held = _job(svc, result)
        assert held.state is DocumentJobState.RETRY_WAIT
        assert held.error_code == "storage_full"
        assert svc.storage.read_manifest(document_id=result.document_id, version_id=result.version_id) is None
        clock.advance()
        await _process_one(svc)
        assert _job(svc, result).state is DocumentJobState.READY
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_a_locked_metadata_store_inside_a_stage_is_retried(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    clock = _Clock()
    monkeypatch.setattr(svc.jobs, "_clock", clock)
    try:
        result = _upload(svc)
        real = svc.repository.get_version
        calls = {"n": 0}

        def locked_once(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return real(**kwargs)

        monkeypatch.setattr(svc.repository, "get_version", locked_once)
        await _process_one(svc)
        held = _job(svc, result)
        assert held.state is DocumentJobState.RETRY_WAIT
        assert held.error_code == "metadata_unavailable"
        clock.advance()
        await _process_one(svc)
        assert _job(svc, result).state is DocumentJobState.READY
    finally:
        svc.close()


def test_a_postgres_error_during_intake_validation_is_retried(tmp_path, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    svc = _service(tmp_path)

    def unreachable(**kwargs):
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(svc, "_validate_and_promote", unreachable)
    try:
        result = _upload(svc)
        assert result.state is DocumentJobState.RETRY_WAIT
        job = _job(svc, result)
        assert job.error_code == "validation_transient"
        assert job.stage == "validating"
    finally:
        svc.close()


@pytest.mark.postgres
def test_a_real_postgres_disconnect_is_an_outage_not_a_rejection():
    # The test above raises the driver's error by hand. Here a real server
    # ends a session (what a restarted database or a dropped connection
    # does) and the error the driver actually raises must classify as an
    # outage: retried as metadata_unavailable, never a content rejection.
    import os

    psycopg = pytest.importorskip("psycopg")
    from kazma_core.documents.worker import _classify_failure

    dsn = os.environ.get("KAZMA_DATABASE_URL", "")
    if not dsn or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("Postgres not configured")
    with psycopg.connect(dsn, autocommit=True) as victim, psycopg.connect(dsn, autocommit=True) as admin:
        pid = victim.execute("SELECT pg_backend_pid()").fetchone()[0]
        admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
        with pytest.raises(psycopg.Error) as caught:
            victim.execute("SELECT 1")
    failure = caught.value
    assert isinstance(failure, infrastructure_errors())
    code, _message, transient = _classify_failure(failure)
    assert (code, transient) == ("metadata_unavailable", True)


def test_a_value_error_during_intake_validation_still_rejects(tmp_path, monkeypatch):
    # Negative control: only infrastructure is retried, not an arbitrary error.
    svc = _service(tmp_path)

    def broken(**kwargs):
        raise ValueError("not an outage")

    monkeypatch.setattr(svc, "_validate_and_promote", broken)
    try:
        result = _upload(svc)
        assert result.state is DocumentJobState.REJECTED
        assert _job(svc, result).error_code == "validation_failed"
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_a_store_error_while_recording_an_outcome_does_not_end_the_worker(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    try:
        first = _upload(svc, b"first\n", key="first")
        real = svc.jobs.transition
        failed = {"n": 0}

        def unavailable_once(**kwargs):
            if kwargs.get("event_type") == "stage_completed" and failed["n"] == 0:
                failed["n"] += 1
                raise sqlite3.OperationalError("database is locked")
            return real(**kwargs)

        monkeypatch.setattr(svc.jobs, "transition", unavailable_once)
        worker = DocumentWorker(
            svc.jobs, svc.stage_handlers(), concurrency=1, poll_interval=0.05,
            lease_seconds=30, shutdown_grace_seconds=5,
        )
        await worker.start()
        try:
            deadline = time.monotonic() + 30
            while failed["n"] == 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert failed["n"] == 1
            second = _upload(svc, b"second\n", key="second")
            while _job(svc, second).state is not DocumentJobState.READY and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            # The same single loop is alive and went on to the next job.
            assert worker.running
            assert _job(svc, second).state is DocumentJobState.READY
            stuck = _job(svc, first)
            assert stuck.state in {DocumentJobState.PARSING, DocumentJobState.NORMALIZING}
            assert stuck.lease_owner and stuck.lease_owner.startswith(worker.owner)
        finally:
            await worker.stop()
        # The unrecorded job resumes once its lease expires.
        clock = _Clock()
        clock.now += timedelta(hours=1)
        monkeypatch.setattr(svc.jobs, "_clock", clock)
        assert svc.jobs.recover_expired_leases(tenant_id="t") == 1
        clock.advance()
        await _process_one(svc, owner="after")
        assert _job(svc, first).state is DocumentJobState.READY
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_process_claimed_still_raises_the_error_the_loop_survives(tmp_path, monkeypatch):
    # Negative control: the loop's survival is not the error disappearing.
    svc = _service(tmp_path)
    try:
        _upload(svc)

        def unavailable(**kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(svc.jobs, "transition", unavailable)
        claimed = svc.jobs.claim_next(owner="raw", lease_seconds=60)
        with pytest.raises(sqlite3.OperationalError):
            await DocumentWorker(svc.jobs, svc.stage_handlers())._process_claimed(claimed, "raw")
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_a_shutdown_during_a_stuck_stage_is_bounded_and_the_job_resumes(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    release = threading.Event()
    entered = threading.Event()
    try:
        result = _upload(svc)
        handlers = svc.stage_handlers()
        real_parse = handlers[DocumentJobState.PARSING]

        def stuck(job, context):
            entered.set()
            release.wait(60)  # a parser that ignores shutdown
            return real_parse(job, context)

        handlers[DocumentJobState.PARSING] = stuck
        worker = DocumentWorker(
            svc.jobs, handlers, concurrency=1, poll_interval=0.05,
            lease_seconds=30, shutdown_grace_seconds=0.5,
        )
        await worker.start()
        deadline = time.monotonic() + 30
        while not entered.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert entered.is_set()
        started = time.monotonic()
        await asyncio.wait_for(worker.stop(), timeout=20)
        assert time.monotonic() - started < 10  # grace 0.5 s + cancel grace + slack
        assert _job(svc, result).state is DocumentJobState.PARSING  # left leased, not lost
    finally:
        release.set()
        await asyncio.sleep(0.2)
    try:
        clock = _Clock()
        clock.now += timedelta(hours=1)
        monkeypatch.setattr(svc.jobs, "_clock", clock)
        svc.jobs.recover_expired_leases(tenant_id="t")
        clock.advance()
        for _ in range(3):
            if _job(svc, result).state is DocumentJobState.READY:
                break
            await _process_one(svc, owner="after-restart")
        assert _job(svc, result).state is DocumentJobState.READY
    finally:
        svc.close()
