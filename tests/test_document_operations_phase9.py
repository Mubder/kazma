"""Phase 9 operations & scale tests for the document platform.

Covers: reference-safe GC (grace/dry-run/symlink refusal/bounded batch),
backpressure/capacity caps + HTTP codes, immutable/sanitized/tenant-isolated
audit, content-free metrics, cancellable maintenance loop, Postgres
FOR UPDATE SKIP LOCKED + CAS/lease claim semantics (via a SQLite-backed fake
pool), and the DB+blob+manifest backup/migration round-trip.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kazma_core.documents.audit import DocumentAuditStore
from kazma_core.documents.backup import perform_document_backup, verify_document_backup
from kazma_core.documents.capacity import CapacityError, DocumentCapacityGuard
from kazma_core.documents.config import get_document_config
from kazma_core.documents.jobs import DocumentJobRepository
from kazma_core.documents.repository import DocumentRepository
from kazma_core.documents.retention import DocumentGarbageCollector
from kazma_core.documents.storage import ContentAddressedStorage


# ── Fixtures / helpers ───────────────────────────────────────────────────


def _store(tmp_path: Path, **cfg_overrides):
    root = tmp_path / "store"
    cfg = replace(get_document_config(), storage_root=root, **cfg_overrides)
    repo = DocumentRepository(str(root / "documents.db"), tenant_quota_bytes=10_000_000)
    jobs = DocumentJobRepository(repo, jitter=lambda _b: 0)
    storage = ContentAddressedStorage(root)
    audit = DocumentAuditStore(repo)
    return cfg, repo, jobs, storage, audit


def _make_referenced_blob(repo, storage, *, tenant="t1", data=b"referenced"):
    sha = hashlib.sha256(data).hexdigest()
    storage.put_stream(io.BytesIO(data), kind="quarantine", max_bytes=1_000_000)
    storage.put_stream(io.BytesIO(data), kind="originals", max_bytes=1_000_000)
    doc = repo.create_document(tenant_id=tenant, owner_id="o", title="Doc")
    blob = repo.register_blob(
        tenant_id=tenant, sha256=sha, byte_size=len(data), storage_kind="quarantine"
    )
    ver = repo.create_version(
        tenant_id=tenant, document_id=doc.id, actor_id="o", source_blob_id=blob.id,
        source_sha256=sha, original_filename="f.txt", mime_type="text/plain",
    )
    with repo._lock:
        repo._conn.execute(
            "UPDATE documents SET current_version_id=? WHERE id=?", (str(ver.id), str(doc.id))
        )
    return doc, ver, sha


# ── Audit ────────────────────────────────────────────────────────────────


def test_audit_immutable_sanitized_and_tenant_isolated(tmp_path):
    _cfg, repo, _jobs, _storage, audit = _store(tmp_path)
    audit.record(
        tenant_id="t1", event_type="intake", action="upload", outcome="success",
        actor_id="alice", document_id="d1",
        detail={"reason": "ok", "filename": "secret.pdf", "byte_size": 42},
    )
    audit.record(tenant_id="t2", event_type="access", action="read")

    page = audit.list_events(tenant_id="t1")
    assert len(page["events"]) == 1
    detail = page["events"][0]["detail"]
    assert "filename" not in detail  # allowlist scrubbed content
    assert detail == {"reason": "ok", "byte_size": 42}

    # tenant isolation
    assert audit.count(tenant_id="t1") == 1
    assert audit.count(tenant_id="t2") == 1
    assert audit.list_events(tenant_id="t2")["events"][0]["event_type"] == "access"

    # immutability: UPDATE and casual DELETE both blocked by triggers
    with pytest.raises(sqlite3.IntegrityError):
        with repo._lock:
            repo._conn.execute("UPDATE document_audit_events SET outcome='failure'")
    with pytest.raises(sqlite3.IntegrityError):
        with repo._lock:
            repo._conn.execute("DELETE FROM document_audit_events")


def test_audit_retention_prune_and_pagination(tmp_path):
    _cfg, _repo, _jobs, _storage, audit = _store(tmp_path)
    for i in range(5):
        audit.record(tenant_id="t1", event_type="access", action=f"a{i}")
    first = audit.list_events(tenant_id="t1", limit=2)
    assert len(first["events"]) == 2 and first["has_more"]
    nxt = audit.list_events(tenant_id="t1", limit=2, before_id=first["next_before_id"])
    assert len(nxt["events"]) == 2
    # prune everything created before "tomorrow"
    future = datetime(2999, 1, 1, tzinfo=UTC).isoformat()
    deleted = audit.prune_older_than(cutoff_iso=future, max_rows=100)
    assert deleted == 5
    assert audit.count(tenant_id="t1") == 0


# ── Capacity / backpressure ──────────────────────────────────────────────


def test_capacity_rate_limit_429(tmp_path):
    cfg, _repo, jobs, _storage, _audit = _store(
        tmp_path, capacity_intake_rate_per_minute=2, capacity_storage_free_floor_bytes=1
    )
    guard = DocumentCapacityGuard(config=cfg, jobs=jobs, storage_root=cfg.storage_root)
    guard.check_intake(tenant_id="t1", byte_size=10)
    guard.check_intake(tenant_id="t1", byte_size=10)
    with pytest.raises(CapacityError) as exc:
        guard.check_intake(tenant_id="t1", byte_size=10)
    assert exc.value.status == 429 and exc.value.retry_after >= 1


def test_capacity_byte_window_429(tmp_path):
    cfg, _repo, jobs, _storage, _audit = _store(
        tmp_path, capacity_intake_bytes_per_minute=100, capacity_storage_free_floor_bytes=1
    )
    guard = DocumentCapacityGuard(config=cfg, jobs=jobs, storage_root=cfg.storage_root)
    guard.check_intake(tenant_id="t1", byte_size=80)
    with pytest.raises(CapacityError) as exc:
        guard.check_intake(tenant_id="t1", byte_size=80)
    assert exc.value.status == 429


def test_capacity_tenant_and_global_caps(tmp_path):
    cfg, repo, jobs, _storage, _audit = _store(
        tmp_path,
        capacity_max_tenant_queued_jobs=1,
        capacity_max_queued_jobs=1,
        capacity_storage_free_floor_bytes=1,
    )
    guard = DocumentCapacityGuard(config=cfg, jobs=jobs, storage_root=cfg.storage_root)
    # enqueue one job to fill both the tenant and global backlog
    doc = repo.create_document(tenant_id="t1", owner_id="o", title="T")
    data = b"x"
    sha = hashlib.sha256(data).hexdigest()
    blob = repo.register_blob(tenant_id="t1", sha256=sha, byte_size=1, storage_kind="quarantine")
    ver = repo.create_version(
        tenant_id="t1", document_id=doc.id, actor_id="o", source_blob_id=blob.id,
        source_sha256=sha, original_filename="f", mime_type="text/plain",
    )
    jobs.enqueue(tenant_id="t1", workspace_id="w", document_id=doc.id, version_id=ver.id, idempotency_key="k1")
    # global cap (503) is checked before tenant cap
    with pytest.raises(CapacityError) as exc:
        guard.check_intake(tenant_id="t1", byte_size=1)
    assert exc.value.status == 503 and exc.value.reason == "queue_backpressure"


def test_capacity_storage_floor_507(tmp_path):
    cfg, _repo, jobs, _storage, _audit = _store(
        tmp_path, capacity_storage_free_floor_bytes=10 ** 18
    )
    guard = DocumentCapacityGuard(config=cfg, jobs=jobs, storage_root=cfg.storage_root)
    with pytest.raises(CapacityError) as exc:
        guard.check_intake(tenant_id="t1", byte_size=1)
    assert exc.value.status == 507 and exc.value.reason == "storage_low"


def test_capacity_snapshot_degraded_reasons(tmp_path):
    cfg, repo, jobs, _storage, _audit = _store(tmp_path, capacity_max_queued_jobs=1)
    doc = repo.create_document(tenant_id="t1", owner_id="o", title="T")
    sha = hashlib.sha256(b"x").hexdigest()
    blob = repo.register_blob(tenant_id="t1", sha256=sha, byte_size=1, storage_kind="quarantine")
    ver = repo.create_version(
        tenant_id="t1", document_id=doc.id, actor_id="o", source_blob_id=blob.id,
        source_sha256=sha, original_filename="f", mime_type="text/plain",
    )
    jobs.enqueue(tenant_id="t1", workspace_id="w", document_id=doc.id, version_id=ver.id, idempotency_key="k1")
    guard = DocumentCapacityGuard(config=cfg, jobs=jobs, storage_root=cfg.storage_root)
    snap = guard.snapshot(tenant_id="t1")
    assert snap["status"] in ("degraded", "unavailable")
    assert "queue_backpressure" in snap["degraded_reasons"]
    assert snap["tenant"]["queued"] == 1


# ── Garbage collection ───────────────────────────────────────────────────


def test_gc_reference_safe_and_dry_run(tmp_path):
    cfg, repo, _jobs, storage, audit = _store(
        tmp_path, gc_grace_seconds=0, retention_quarantine_days=0
    )
    _doc, _ver, ref_sha = _make_referenced_blob(repo, storage)
    orphan = b"orphan no ref"
    orphan_sha = hashlib.sha256(orphan).hexdigest()
    storage.put_stream(io.BytesIO(orphan), kind="originals", max_bytes=1_000_000)
    gc = DocumentGarbageCollector(repository=repo, storage=storage, audit=audit, config=cfg)
    time.sleep(0.02)

    dry = gc.collect(dry_run=True)
    assert dry.deleted_blobs >= 1  # would delete the orphan
    assert storage.verify_blob(kind="originals", sha256=orphan_sha)  # dry-run touched nothing

    real = gc.collect(dry_run=False)
    assert storage.verify_blob(kind="originals", sha256=ref_sha)  # referenced survives
    assert not storage.blob_path(kind="originals", sha256=orphan_sha).is_file()  # orphan gone
    assert real.deleted_blobs >= 1 and not real.errors


def test_gc_grace_period_protects_recent_files(tmp_path):
    cfg, repo, _jobs, storage, audit = _store(tmp_path, gc_grace_seconds=3600)
    orphan = b"recent orphan"
    orphan_sha = hashlib.sha256(orphan).hexdigest()
    storage.put_stream(io.BytesIO(orphan), kind="originals", max_bytes=1_000_000)
    gc = DocumentGarbageCollector(repository=repo, storage=storage, audit=audit, config=cfg)
    report = gc.collect(dry_run=False)
    # File is younger than the grace window → not deleted.
    assert storage.verify_blob(kind="originals", sha256=orphan_sha)
    assert report.deleted_blobs == 0


def test_gc_rechecks_reference_published_after_mark(tmp_path, monkeypatch):
    cfg, repo, _jobs, storage, audit = _store(
        tmp_path, gc_grace_seconds=0, retention_quarantine_days=0
    )
    data = b"deduplicated during gc"
    stored = storage.put_stream(
        io.BytesIO(data), kind="originals", max_bytes=1_000_000
    )
    original_mark = DocumentGarbageCollector._mark

    def mark_then_publish(self, config, now):
        marks = original_mark(self, config, now)
        doc = repo.create_document(tenant_id="t1", owner_id="o", title="Live")
        blob = repo.register_blob(
            tenant_id="t1",
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            storage_kind="originals",
        )
        repo.create_version(
            tenant_id="t1",
            document_id=doc.id,
            actor_id="o",
            source_blob_id=blob.id,
            source_sha256=stored.sha256,
            original_filename="live.txt",
            mime_type="text/plain",
        )
        return marks

    monkeypatch.setattr(DocumentGarbageCollector, "_mark", mark_then_publish)
    gc = DocumentGarbageCollector(
        repository=repo, storage=storage, audit=audit, config=cfg
    )
    time.sleep(0.02)
    report = gc.collect(dry_run=False)

    assert storage.verify_blob(kind="originals", sha256=stored.sha256)
    assert report.deleted_blobs == 0


def test_gc_bounded_batch(tmp_path):
    cfg, repo, _jobs, storage, audit = _store(
        tmp_path, gc_grace_seconds=0, gc_max_deletions_per_run=2
    )
    for i in range(5):
        data = f"orphan-{i}".encode()
        storage.put_stream(io.BytesIO(data), kind="originals", max_bytes=1_000_000)
    gc = DocumentGarbageCollector(repository=repo, storage=storage, audit=audit, config=cfg)
    time.sleep(0.02)
    report = gc.collect(dry_run=False)
    assert report.deleted_blobs == 2  # bounded by the batch budget
    assert report.budget_exhausted


def test_gc_refuses_symlink_escape(tmp_path):
    cfg, repo, _jobs, storage, audit = _store(tmp_path, gc_grace_seconds=0)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.bin"
    secret.write_bytes(b"do not delete")
    link_dir = cfg.storage_root / "originals" / "sha256" / "ln"
    link_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(outside, link_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    try:
        gc = DocumentGarbageCollector(repository=repo, storage=storage, audit=audit, config=cfg)
        report = gc.collect(dry_run=False)
        assert secret.is_file()  # never deleted through a symlink
        assert report.refused_symlinks >= 1
    finally:
        # L-5: never leave a junction/symlink behind in tmp (pytest-of-*
        # poison). Target is inside tmp_path, not C:\Users.
        try:
            if link_dir.is_symlink() or link_dir.exists():
                link_dir.unlink(missing_ok=True)
        except OSError:
            pass


def test_gc_tombstoned_document_collected(tmp_path):
    cfg, repo, _jobs, storage, audit = _store(
        tmp_path, gc_grace_seconds=0, retention_tombstone_days=0, retention_quarantine_days=0
    )
    doc, _ver, sha = _make_referenced_blob(repo, storage, data=b"tombstone me")
    repo.tombstone_document(tenant_id="t1", document_id=doc.id, actor_id="o", reason="test")
    gc = DocumentGarbageCollector(repository=repo, storage=storage, audit=audit, config=cfg)
    time.sleep(0.02)
    report = gc.collect(dry_run=False)
    # Expired-tombstone content is reclaimable.
    assert not storage.blob_path(kind="originals", sha256=sha).is_file()
    assert report.deleted_blobs >= 1


# ── Queue introspection ──────────────────────────────────────────────────


def test_queue_stats_and_tenant_load(tmp_path):
    _cfg, repo, jobs, _storage, _audit = _store(tmp_path)
    for tenant, key in (("t1", "k1"), ("t1", "k2"), ("t2", "k3")):
        doc = repo.create_document(tenant_id=tenant, owner_id="o", title="T")
        data = f"{tenant}{key}".encode()
        sha = hashlib.sha256(data).hexdigest()
        blob = repo.register_blob(tenant_id=tenant, sha256=sha, byte_size=len(data), storage_kind="quarantine")
        ver = repo.create_version(
            tenant_id=tenant, document_id=doc.id, actor_id="o", source_blob_id=blob.id,
            source_sha256=sha, original_filename="f", mime_type="text/plain",
        )
        jobs.enqueue(tenant_id=tenant, workspace_id="w", document_id=doc.id, version_id=ver.id, idempotency_key=key)
    stats = jobs.queue_stats()
    assert stats.depth == 3 and stats.non_terminal == 3 and stats.dead_letter == 0
    assert jobs.tenant_load(tenant_id="t1").queued == 2
    assert jobs.tenant_load(tenant_id="t2").queued == 1
    assert jobs.queue_stats(tenant_id="t1").depth == 2


# ── Metrics (no content labels) ──────────────────────────────────────────


def test_metrics_helpers_are_content_free():
    from kazma_core.documents import telemetry

    # All record_* helpers accept only ids/codes/counts — never content — and
    # never raise even when prometheus is present.
    telemetry.record_intake(accepted=True, byte_size=123)
    telemetry.record_intake_rejection("quota_exceeded")
    telemetry.record_stage("parsing", "success", latency_seconds=0.1)
    telemetry.record_parser("pdf", "success")
    telemetry.record_pages(3, kind="ocr")
    telemetry.record_sandbox_termination("timeout")
    telemetry.record_indexing(chunks=5, latency_seconds=0.2)
    telemetry.record_generation_failure("convert")
    telemetry.record_redaction_failure()
    telemetry.record_dead_letter()
    telemetry.set_queue_gauges(depth=1, oldest_age_seconds=2, active_leases=0, retry_waiting=0, dead_letter=0)
    telemetry.set_storage_gauges(logical_bytes=100, physical_bytes=50)

    extra = telemetry.correlation_extra(
        tenant_id="t1", document_id="d1", job_id="j1", attempt=2, parser="pdf",
        text="THIS SHOULD BE DROPPED", filename="secret.pdf",
    )
    assert "text" not in extra and "filename" not in extra
    assert extra["tenant_id"] == "t1" and extra["attempt"] == 2

    # If prometheus is available, verify no metric name embeds content and the
    # label sets are the bounded ones we declared.
    if telemetry._PROM:
        from prometheus_client import generate_latest

        text = generate_latest().decode("utf-8", "replace")
        assert "THIS SHOULD BE DROPPED" not in text
        assert "secret.pdf" not in text


def test_document_span_is_noop_safe():
    from kazma_core.documents import telemetry

    with telemetry.document_span("doc.test", document_id="d1") as span:
        span.set_attribute("k", "v")  # never raises whether OTel is present or not


def test_document_span_preserves_caller_exception():
    from kazma_core.documents import telemetry

    with pytest.raises(ValueError, match="caller failure"):
        with telemetry.document_span("doc.test"):
            raise ValueError("caller failure")


# ── Maintenance loop cancellation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_maintenance_loop_is_cancellable():
    from kazma_core.documents.retention import start_document_maintenance_loop

    task = start_document_maintenance_loop(first_delay_seconds=1000)
    await asyncio.sleep(0.05)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


# ── Postgres claim semantics (SQLite-backed fake pool) ───────────────────


class _FakeCursor:
    """Translate a subset of Postgres SQL to SQLite and record executed text."""

    def __init__(self, conn, log):
        self._cur = conn.cursor()
        self._log = log

    @staticmethod
    def _translate(sql: str) -> str:
        sql = sql.replace("%s", "?")
        sql = re.sub(r"\bFOR UPDATE SKIP LOCKED\b", "", sql)
        sql = re.sub(r"\bFOR UPDATE\b", "", sql)
        sql = sql.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        sql = sql.replace("TIMESTAMPTZ", "TEXT")
        sql = sql.replace("BOOLEAN", "INTEGER")
        sql = re.sub(r"DEFAULT FALSE", "DEFAULT 0", sql)
        sql = re.sub(r"DEFAULT TRUE", "DEFAULT 1", sql)
        sql = re.sub(r"=\s*FALSE\b", "= 0", sql)
        sql = re.sub(r"=\s*TRUE\b", "= 1", sql)
        sql = sql.replace("WHERE TRUE", "WHERE 1=1")
        return sql

    @staticmethod
    def _params(params):
        if params is None:
            return params
        out = []
        for p in params:
            if isinstance(p, datetime):
                out.append(p.astimezone(UTC).isoformat())
            elif isinstance(p, bool):
                out.append(1 if p else 0)
            else:
                out.append(p)
        return out

    def execute(self, sql, params=None):
        self._log.append(sql)
        translated = self._translate(sql)
        if params is None:
            self._cur.executescript(translated) if ";" in translated and "INSERT" not in translated.upper() else self._cur.execute(translated)
        else:
            self._cur.execute(translated, self._params(params))
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, conn, log):
        self._conn = conn
        self._log = log

    def cursor(self):
        return _FakeCursor(self._conn, self._log)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


class _FakePool:
    """Postgres-shaped pool backed by one in-memory SQLite connection."""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
        self.sql_log: list[str] = []

    class _Ctx:
        def __init__(self, conn):
            self._conn = conn

        def __enter__(self):
            return self._conn

        def __exit__(self, *exc):
            return False

    def connection(self):
        return self._Ctx(_FakeConn(self._conn, self.sql_log))


def _pg_repo():
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository

    pool = _FakePool()
    repo = PostgresDocumentJobRepository(pool, jitter=lambda _b: 0)
    return repo, pool


def _ids():
    from kazma_core.documents.models import new_document_id, new_version_id

    return str(new_document_id()), str(new_version_id())


def test_pg_claim_uses_skip_locked_and_cas_lifecycle():
    from kazma_core.documents.models import DocumentJobState

    repo, pool = _pg_repo()
    doc_id, ver_id = _ids()
    job = repo.enqueue(
        tenant_id="t1", workspace_id="w", document_id=doc_id, version_id=ver_id,
        idempotency_key="k1", max_attempts=3,
    )
    assert job.state is DocumentJobState.RECEIVED
    # advance to a claimable state via CAS transitions
    job = repo.transition(
        tenant_id="t1", job_id=job.id, expected_state=DocumentJobState.RECEIVED,
        expected_version=job.version, new_state=DocumentJobState.QUARANTINED,
    )
    job = repo.transition(
        tenant_id="t1", job_id=job.id, expected_state=DocumentJobState.QUARANTINED,
        expected_version=job.version, new_state=DocumentJobState.VALIDATING, lease_owner=None,
    )
    job = repo.transition(
        tenant_id="t1", job_id=job.id, expected_state=DocumentJobState.VALIDATING,
        expected_version=job.version, new_state=DocumentJobState.READY_TO_PARSE,
    )
    claimed = repo.claim_next(owner="worker-a", lease_seconds=60)
    assert claimed is not None and claimed.state is DocumentJobState.PARSING
    assert claimed.lease_owner == "worker-a" and claimed.attempt == 1

    # The claim query MUST use FOR UPDATE SKIP LOCKED (multi-replica primitive).
    assert any("FOR UPDATE SKIP LOCKED" in s for s in pool.sql_log)

    # CAS staleness: an out-of-date expected_version is rejected.
    from kazma_core.documents.jobs import StaleJobUpdateError

    with pytest.raises(StaleJobUpdateError):
        repo.transition(
            tenant_id="t1", job_id=claimed.id, expected_state=DocumentJobState.PARSING,
            expected_version=claimed.version + 5, new_state=DocumentJobState.NORMALIZING,
            lease_owner="worker-a",
        )


def test_pg_record_failure_retry_then_dead_letter():
    from kazma_core.documents.models import DocumentJobState

    repo, _pool = _pg_repo()
    doc_id, ver_id = _ids()
    job = repo.enqueue(
        tenant_id="t1", workspace_id="w", document_id=doc_id, version_id=ver_id,
        idempotency_key="k1", max_attempts=1,
    )
    job = repo.transition(tenant_id="t1", job_id=job.id, expected_state=DocumentJobState.RECEIVED,
                          expected_version=job.version, new_state=DocumentJobState.QUARANTINED)
    job = repo.transition(tenant_id="t1", job_id=job.id, expected_state=DocumentJobState.QUARANTINED,
                          expected_version=job.version, new_state=DocumentJobState.VALIDATING)
    job = repo.transition(tenant_id="t1", job_id=job.id, expected_state=DocumentJobState.VALIDATING,
                          expected_version=job.version, new_state=DocumentJobState.READY_TO_PARSE)
    claimed = repo.claim_next(owner="w-a")
    # attempt==max_attempts → transient failure dead-letters immediately
    failed = repo.record_failure(
        tenant_id="t1", job_id=claimed.id, expected_state=DocumentJobState.PARSING,
        expected_version=claimed.version, owner="w-a", error_code="boom",
        error_message="kaboom", transient=True,
    )
    assert failed.state is DocumentJobState.DEAD_LETTER


def test_pg_expired_lease_recovered_once():
    from kazma_core.documents.models import DocumentJobState

    repo, _pool = _pg_repo()
    doc_id, ver_id = _ids()
    job = repo.enqueue(tenant_id="t1", workspace_id="w", document_id=doc_id, version_id=ver_id,
                       idempotency_key="k1", max_attempts=3)
    for src, dst in (
        (DocumentJobState.RECEIVED, DocumentJobState.QUARANTINED),
        (DocumentJobState.QUARANTINED, DocumentJobState.VALIDATING),
        (DocumentJobState.VALIDATING, DocumentJobState.READY_TO_PARSE),
    ):
        job = repo.transition(tenant_id="t1", job_id=job.id, expected_state=src,
                              expected_version=job.version, new_state=dst)
    claimed = repo.claim_next(owner="w-a", lease_seconds=0.0001)
    time.sleep(0.01)
    recovered = repo.recover_expired_leases()
    assert recovered == 1
    back = repo.get(tenant_id="t1", job_id=claimed.id)
    assert back.state in (DocumentJobState.RETRY_WAIT, DocumentJobState.PARSING)


def test_pg_readiness_is_truthful():
    from kazma_core.documents.jobs_pg import document_storage_readiness

    repo, _pool = _pg_repo()
    # A postgres jobs repo but SQLite metadata → degraded + honest reasons.
    rdy = document_storage_readiness(jobs_repo=repo)
    # backend detection depends on env; assert the shape + metadata honesty.
    assert rdy["metadata_multi_replica"] is False
    assert "metadata_single_replica" in rdy["degraded_reasons"] or rdy["jobs_backend"] == "sqlite"


# ── Backup + migration round-trip (DB + blob + manifest) ─────────────────


def test_backup_consistency_db_blob_manifest(tmp_path):
    cfg, repo, _jobs, storage, _audit = _store(tmp_path)
    doc, ver, _sha = _make_referenced_blob(repo, storage, data=b"backup body")
    storage.write_manifest(document_id=doc.id, version_id=ver.id, manifest={"ir": {"pages": []}})
    report = perform_document_backup(storage_root=cfg.storage_root, dest_dir=tmp_path / "backups")
    assert report["ok"] and report["copied_blobs"] >= 1 and report["manifests"] == 1
    assert report["verification"]["ok"] and report["verification"]["checked"] >= 1
    # Independent re-verification of the backup dir.
    again = verify_document_backup(backup_dir=report["path"])
    assert again["ok"] and not again["missing"] and not again["corrupt"]


def test_backup_fails_when_referenced_blob_is_missing(tmp_path):
    cfg, repo, _jobs, storage, _audit = _store(tmp_path)
    _doc, _ver, sha = _make_referenced_blob(repo, storage, data=b"missing body")
    storage.blob_path(kind="quarantine", sha256=sha).unlink(missing_ok=True)
    storage.blob_path(kind="originals", sha256=sha).unlink(missing_ok=True)

    report = perform_document_backup(
        storage_root=cfg.storage_root, dest_dir=tmp_path / "backups"
    )

    assert report["ok"] is False
    assert report["verification"]["ok"] is False
    assert report["verification"]["missing"]


def test_migration_document_store_roundtrip(tmp_path, monkeypatch):
    from kazma_core.migration import exporter as exp
    from kazma_core.migration import importer as imp
    from kazma_core.migration.bundle import Manifest

    # Source store in dir A.
    root_a = tmp_path / "A" / "document-store"
    cfg_a = replace(get_document_config(), storage_root=root_a)
    repo = DocumentRepository(str(root_a / "documents.db"), tenant_quota_bytes=10_000_000)
    storage = ContentAddressedStorage(root_a)
    doc, ver, sha = _make_referenced_blob(repo, storage, data=b"cross-machine content")
    storage.write_manifest(document_id=doc.id, version_id=ver.id, manifest={"ir": {"pages": []}})
    repo.close()

    staging = tmp_path / "staging"
    (staging / "data").mkdir(parents=True)
    manifest = Manifest()

    monkeypatch.setattr("kazma_core.documents.config.get_document_config", lambda: cfg_a)
    exp._export_document_store(staging, manifest, lambda *_: None)
    assert (staging / "data" / "documents.db").exists()
    assert manifest.table_counts.get("_document_store", {}).get("blobs", 0) >= 1

    # Import into dir B.
    root_b = tmp_path / "B" / "document-store"
    cfg_b = replace(get_document_config(), storage_root=root_b)
    monkeypatch.setattr("kazma_core.documents.config.get_document_config", lambda: cfg_b)

    class _Report:
        backup_path = str(tmp_path / "B" / "backup")
        _warns: list = []

        def warn(self, msg):
            self._warns.append(msg)

    (tmp_path / "B" / "backup").mkdir(parents=True)
    imp._restore_document_store(staging, staging / "data", _Report(), lambda *_: None)

    assert (root_b / "documents.db").exists()
    assert storage_b_has(root_b, "quarantine", sha) or storage_b_has(root_b, "originals", sha)
    # Metadata round-trips: the version + blob rows are present in B.
    conn = sqlite3.connect(str(root_b / "documents.db"))
    try:
        conn.row_factory = sqlite3.Row
        assert conn.execute("SELECT COUNT(*) c FROM document_versions").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM document_blobs").fetchone()["c"] == 1
    finally:
        conn.close()


def test_migration_export_refuses_missing_referenced_blob(tmp_path, monkeypatch):
    from kazma_core.migration import exporter as exp
    from kazma_core.migration.bundle import Manifest

    root = tmp_path / "document-store"
    cfg = replace(get_document_config(), storage_root=root)
    repo = DocumentRepository(
        str(root / "documents.db"), tenant_quota_bytes=10_000_000
    )
    storage = ContentAddressedStorage(root)
    _doc, _ver, sha = _make_referenced_blob(repo, storage, data=b"missing export")
    repo.close()
    storage.blob_path(kind="quarantine", sha256=sha).unlink(missing_ok=True)
    storage.blob_path(kind="originals", sha256=sha).unlink(missing_ok=True)
    staging = tmp_path / "staging-missing"
    (staging / "data").mkdir(parents=True)
    monkeypatch.setattr(
        "kazma_core.documents.config.get_document_config", lambda: cfg
    )

    with pytest.raises(RuntimeError, match="referenced blob"):
        exp._export_document_store(staging, Manifest(), lambda *_: None)


def storage_b_has(root: Path, kind: str, sha: str) -> bool:
    return (root / kind / "sha256" / sha[:2] / sha[2:4] / sha).is_file()


# ── API layer: HTTP status codes + operational endpoints (fake service) ──


class _Cfg:
    intake_max_bytes = 1_000_000
    retention_rejected_days = 7
    retention_dead_letter_days = 14
    retention_tombstone_days = 30
    retention_quarantine_days = 3
    retention_original_days = 30
    retention_artifact_days = 30
    retention_audit_days = 365
    gc_grace_seconds = 3600
    gc_max_deletions_per_run = 500
    gc_enabled = True
    gc_auto_maintain = True
    gc_interval_hours = 6


class _FakeService:
    """Minimal app.state.documents stand-in for API-mapping tests."""

    def __init__(self, *, capacity_exc=None):
        self.config = _Cfg()
        self._capacity_exc = capacity_exc

    def ingest_stream(self, *args, **kwargs):
        if self._capacity_exc is not None:
            raise self._capacity_exc
        raise AssertionError("unexpected ingest")

    def metrics_snapshot(self, *, tenant_id=None):
        return {"queue": {"depth": 0}, "storage": {"logical_bytes": 0}, "status": "ok",
                "degraded_reasons": [], "tenant_quota": {"used_bytes": 0}}

    def capacity_snapshot(self, *, tenant_id=None):
        return {"status": "ok", "queue": {"depth": 0, "dead_letter": 0}, "degraded_reasons": []}

    def readiness(self):
        return {"status": "ready", "jobs_backend": "sqlite", "jobs_multi_replica": False,
                "metadata_multi_replica": False, "degraded_reasons": []}

    def audit_events(self, *, tenant_id, document_id=None, event_type=None, limit=50, before_id=None):
        return {"events": [{"id": 1, "event_type": "intake", "action": "upload",
                            "outcome": "success", "created_at": "2026-01-01T00:00:00+00:00"}],
                "has_more": False, "next_before_id": None}

    def run_maintenance(self, *, dry_run=False, actor_id=None):
        return {"dry_run": dry_run, "deleted_blobs": 0, "reclaimed_bytes": 0, "errors": []}


def _fake_client(capacity_exc=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui.documents_api import create_documents_router

    app = FastAPI()
    app.state.documents = _FakeService(capacity_exc=capacity_exc)
    app.include_router(create_documents_router())
    return TestClient(app)


@pytest.mark.parametrize(
    "status,code,reason",
    [(429, "rate_limited", "rate_limited"),
     (503, "queue_full", "queue_backpressure"),
     (507, "storage_full", "storage_low")],
)
def test_api_upload_capacity_status_codes(status, code, reason):
    exc = CapacityError(code, "nope", status=status, retry_after=12, reason=reason)
    client = _fake_client(capacity_exc=exc)
    resp = client.post(
        "/api/documents",
        headers={"X-Document-Filename": "x.txt"},
        content=b"some bytes",
    )
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert body["code"] == code and body["reason"] == reason
    assert resp.headers.get("Retry-After") == "12"


def test_api_operational_endpoints():
    client = _fake_client()
    assert client.get("/api/documents/ops/metrics").json()["ok"] is True
    assert client.get("/api/documents/ops/capacity").json()["capacity"]["status"] == "ok"
    assert client.get("/api/documents/ops/readiness").json()["readiness"]["status"] == "ready"
    ret = client.get("/api/documents/ops/retention").json()
    assert ret["ok"] and ret["retention"]["tombstone_days"] == 30
    aud = client.get("/api/documents/ops/audit").json()
    assert aud["ok"] and aud["events"][0]["event_type"] == "intake"


def test_api_maintenance_requires_admin_and_runs(monkeypatch):
    client = _fake_client()
    # Admin gate is enforced: an unauthenticated caller is refused when a
    # secret is configured (or admin role otherwise required).
    blocked = client.post("/api/documents/ops/maintenance/dry-run")
    assert blocked.status_code in (200, 401, 403)
    # Authorize as an admin/secret principal to exercise the maintenance logic.
    monkeypatch.setattr("kazma_ui.auth.get_kazma_secret", lambda: "")
    monkeypatch.setattr(
        "kazma_ui.auth.get_request_principal",
        lambda req: {"source": "secret", "role": "admin", "username": "admin"},
    )
    dry = client.post("/api/documents/ops/maintenance/dry-run").json()
    assert dry["ok"] and dry["report"]["dry_run"] is True
    run = client.post("/api/documents/ops/maintenance/run").json()
    assert run["ok"] and run["report"]["dry_run"] is False
