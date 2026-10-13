"""One tenant's backlog cannot make every other tenant wait behind all of it.

2026-10-13 (F08). Workers claimed the oldest eligible job in the whole
queue: a tenant that queued 500 documents (its admission cap) held both
worker slots until every one was done, and a retried job kept its original
``created_at`` and returned to the front. Now the next job comes from the
tenant with the fewest jobs running, then by when it became ready. Same
rule on SQLite and on real Postgres (a private database on the server, so
the shared test database's other jobs never decide the order). The old
order is the negative control.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from kazma_core.documents.jobs import DocumentJobRepository, fair_claim_order
from kazma_core.documents.models import DocumentId, DocumentJobState, VersionId
from kazma_core.documents.repository import DocumentRepository


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def tick(self, seconds: float = 1.0) -> None:
        self.value += timedelta(seconds=seconds)


def _sqlite_jobs(tmp_path, clock):
    from tests.test_document_jobs import _enqueue

    meta = DocumentRepository(tmp_path / "fair.db", tenant_quota_bytes=10_000_000)
    jobs = DocumentJobRepository(meta, clock=clock, jitter=lambda _b: 0, retry_base_seconds=5)
    counter = {"n": 0}

    def enqueue(tenant: str):
        counter["n"] += 1
        job = _enqueue(meta, jobs, tenant=tenant, key=f"k{counter['n']}")
        _to_ready(jobs, job)
        clock.tick()
        return job

    return jobs, enqueue, meta.close


def _to_ready(jobs, job):
    for target in (DocumentJobState.QUARANTINED, DocumentJobState.VALIDATING, DocumentJobState.READY_TO_PARSE):
        job = jobs.transition(
            tenant_id=job.tenant_id, job_id=job.id, expected_state=job.state,
            expected_version=job.version, new_state=target,
        )
    return job


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def queue(request, tmp_path):
    clock = _Clock()
    if request.param == "sqlite":
        jobs, enqueue, close = _sqlite_jobs(tmp_path, clock)
        yield jobs, enqueue, clock
        close()
        return
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("KAZMA_DATABASE_URL", "")
    if not dsn or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("Postgres not configured")
    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository

    name = f"kazma_fair_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    base, _, _ = dsn.rpartition("/")
    pool = PostgresPool(f"{base}/{name}", min_size=1, max_size=2)
    try:
        jobs = PostgresDocumentJobRepository(pool, clock=clock, jitter=lambda _b: 0, retry_base_seconds=5)
        counter = {"n": 0}

        def enqueue(tenant: str):
            counter["n"] += 1
            job = jobs.enqueue(
                tenant_id=tenant, workspace_id="w", document_id=DocumentId(str(uuid.uuid4())),
                version_id=VersionId(str(uuid.uuid4())), idempotency_key=f"k{counter['n']}",
            )
            job = _to_ready(jobs, job)
            clock.tick()
            return job

        yield jobs, enqueue, clock
    finally:
        pool._pool.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _claim(jobs) -> str:
    job = jobs.claim_next(owner=f"w-{uuid.uuid4().hex[:6]}", lease_seconds=600)
    assert job is not None
    return job.tenant_id


def test_a_quiet_tenant_is_served_before_a_busy_tenants_backlog(queue):
    jobs, enqueue, _clock = queue
    for _ in range(5):
        enqueue("busy")
    enqueue("quiet")  # arrives last
    # First: both tenants run nothing, the oldest wins (busy). Second: busy
    # now runs one, quiet runs none -> quiet, though its job is the newest.
    assert [_claim(jobs) for _ in range(3)] == ["busy", "quiet", "busy"]


def test_a_retried_job_waits_for_its_turn_by_when_it_became_ready(queue):
    jobs, enqueue, clock = queue
    first = enqueue("solo")
    claimed = jobs.claim_next(owner="w1", lease_seconds=600)
    assert claimed.id == first.id
    failed = jobs.record_failure(
        tenant_id="solo", job_id=first.id, expected_state=claimed.state,
        expected_version=claimed.version, owner="w1", error_code="parser_timeout",
        error_message="timed out", transient=True,
    )
    assert failed.state is DocumentJobState.RETRY_WAIT  # due again at t+5 s
    second = enqueue("solo")  # created at t+0 s: newer, but ready sooner
    clock.tick(10)
    order = [jobs.claim_next(owner=f"w{i}", lease_seconds=600).id for i in range(2)]
    # By readiness the new job goes first; by created_at (the old order) the
    # retried job would have jumped back to the front.
    assert order == [second.id, first.id]


def test_the_old_order_would_starve_the_quiet_tenant(tmp_path):
    # Negative control: the same queue in the old order serves busy, busy.
    clock = _Clock()
    jobs, enqueue, close = _sqlite_jobs(tmp_path, clock)
    try:
        for _ in range(5):
            enqueue("busy")
        enqueue("quiet")
        jobs.claim_next(owner="w1", lease_seconds=600)
        rows = jobs._conn.execute(
            "SELECT tenant_id FROM document_jobs WHERE state = 'ready_to_parse' ORDER BY created_at, id"
        ).fetchall()
        assert rows[0]["tenant_id"] == "busy"  # what the old query claimed next
        running, order = fair_claim_order()
        fair = jobs._conn.execute(
            f"{running} SELECT j.tenant_id FROM document_jobs j LEFT JOIN running r "
            f"ON r.tenant_id = j.tenant_id WHERE j.state = 'ready_to_parse' {order} LIMIT 1"
        ).fetchone()
        assert fair["tenant_id"] == "quiet"
    finally:
        close()


def test_one_sqlite_claim_reads_a_bounded_window(tmp_path):
    clock = _Clock()
    jobs, enqueue, close = _sqlite_jobs(tmp_path, clock)
    try:
        for _ in range(70):
            enqueue("t")
        seen: list[int] = []
        conn = jobs._conn
        conn.set_trace_callback(lambda sql: seen.append(sql.count("LIMIT")) if "WITH running" in sql else None)
        try:
            assert jobs.claim_next(owner="w", lease_seconds=60) is not None
        finally:
            conn.set_trace_callback(None)
        assert seen == [1]  # one candidate read, bounded
    finally:
        close()
