"""The Documents listing and the operator grant make a fixed number of queries.

Audit 2026-10-26 AUD-115. ``list_documents`` asked ``has_access`` once per
document, then every job id of the document and every job by id: a page of
N documents cost about 3N queries. ``grant_operator_access`` checked every
document with a query of its own, every minute. Now the readable documents
come from one joined query (documents x ACL) and the newest job of each
from one more, on both backends; the grant reads only the documents the
operator lacks.

The query count is measured on SQLite with the connection's own trace
callback, for 2 and for 8 documents. Negative control: on the old code the
count grows with the number of documents. The repository and queue queries
themselves run on SQLite here and on a private real Postgres database in the
Postgres job (``@pytest.mark.postgres``).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.models import VersionId


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 26, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def tick(self) -> None:
        self.now += timedelta(seconds=1)


# ── the repository and the queue, on both backends ────────────────────────


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def stores(request, tmp_path):
    clock = _Clock()
    if request.param == "sqlite":
        from kazma_core.documents.jobs import DocumentJobRepository
        from kazma_core.documents.repository import DocumentRepository

        repo = DocumentRepository(tmp_path / "documents.db", tenant_quota_bytes=10 ** 9)
        jobs = DocumentJobRepository(repo, clock=clock, jitter=lambda _b: 0)
        yield repo, jobs, clock
        repo.close()
        return
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("KAZMA_DATABASE_URL", "")
    if not dsn or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("Postgres not configured")
    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository
    from kazma_core.documents.repository_pg import PostgresDocumentRepository

    name = f"kazma_listing_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    base, _, _ = dsn.rpartition("/")
    pool = PostgresPool(f"{base}/{name}", min_size=1, max_size=2)
    try:
        repo = PostgresDocumentRepository(pool, tenant_quota_bytes=10 ** 9)
        jobs = PostgresDocumentJobRepository(pool, clock=clock, jitter=lambda _b: 0)
        yield repo, jobs, clock
    finally:
        pool._pool.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _ids(records) -> set[str]:
    return {str(r.id) for r in records}


def _version(repo, tenant: str, document, owner: str) -> VersionId:
    """A version of *document* (a job names one)."""
    payload = uuid.uuid4().bytes
    digest = hashlib.sha256(payload).hexdigest()
    blob = repo.register_blob(tenant_id=tenant, sha256=digest, byte_size=len(payload),
                              storage_kind="originals")
    return repo.create_version(
        tenant_id=tenant, document_id=document.id, actor_id=owner, source_blob_id=blob.id,
        source_sha256=digest, original_filename="d.txt", mime_type="text/plain",
    ).id


def test_the_joined_listing_follows_the_access_rule(stores):
    repo, _jobs, _clock = stores
    own = repo.create_document(tenant_id="t", owner_id="alice", title="own")
    shared = repo.create_document(tenant_id="t", owner_id="bob", title="shared")
    writable = repo.create_document(tenant_id="t", owner_id="bob", title="writable")
    co_owned = repo.create_document(tenant_id="t", owner_id="bob", title="co-owned")
    hidden = repo.create_document(tenant_id="t", owner_id="bob", title="hidden")
    gone = repo.create_document(tenant_id="t", owner_id="alice", title="gone")
    repo.create_document(tenant_id="other", owner_id="alice", title="other tenant")
    repo.grant_access(tenant_id="t", document_id=shared.id, actor_id="bob", principal_id="alice", permission="read")
    repo.grant_access(tenant_id="t", document_id=writable.id, actor_id="bob", principal_id="alice", permission="write")
    repo.grant_access(tenant_id="t", document_id=co_owned.id, actor_id="bob", principal_id="alice", permission="owner")
    repo.grant_access(tenant_id="t", document_id=hidden.id, actor_id="bob", principal_id="carol", permission="read")
    repo.tombstone_document(tenant_id="t", document_id=gone.id, actor_id="alice", reason="test")

    def listed(permission: str, granted: bool = True) -> set[str]:
        return _ids(repo.list_documents_by_access(
            tenant_id="t", actor_id="alice", permission=permission, granted=granted))

    assert listed("read") == _ids([own, shared, writable, co_owned])
    assert listed("write") == _ids([own, writable, co_owned])
    assert listed("owner") == _ids([own, co_owned])
    assert listed("owner", granted=False) == _ids([shared, writable, hidden])
    assert listed("read", granted=False) == _ids([hidden])
    # The per-document rule agrees, document by document, for read and write.
    for permission in ("read", "write"):
        for record in repo.list_documents(tenant_id="t"):
            assert repo.has_access(tenant_id="t", document_id=record.id, actor_id="alice",
                                   permission=permission) == (str(record.id) in listed(permission))
    with pytest.raises(ValueError):
        repo.list_documents_by_access(tenant_id="t", actor_id="alice", permission="admin")


def test_the_newest_job_of_each_document_in_one_query(stores):
    repo, jobs, clock = stores
    a = repo.create_document(tenant_id="t", owner_id="alice", title="a")
    b = repo.create_document(tenant_id="t", owner_id="alice", title="b")
    elsewhere = repo.create_document(tenant_id="other", owner_id="alice", title="c")
    made = {}
    for n, doc in enumerate((a, b, a, a, b)):
        made[n] = jobs.enqueue(tenant_id="t", workspace_id="w", document_id=doc.id,
                               version_id=_version(repo, "t", doc, "alice"), idempotency_key=f"k{n}")
        clock.tick()
    jobs.enqueue(tenant_id="other", workspace_id="w", document_id=elsewhere.id,
                 version_id=_version(repo, "other", elsewhere, "alice"), idempotency_key="elsewhere")
    newest = jobs.latest_jobs_by_document(tenant_id="t")
    assert set(newest) == {str(a.id), str(b.id)}
    assert newest[str(a.id)].id == made[3].id
    assert newest[str(b.id)].id == made[4].id
    for doc in (a, b):
        assert newest[str(doc.id)].id == jobs.document_job_ids(tenant_id="t", document_id=doc.id)[0]


# ── the service: a fixed number of queries, whatever the page holds ───────


@pytest.fixture
def sqlite_backends(monkeypatch):
    """The statement counts below are read off SQLite's trace callback: pin
    both document stores to SQLite whatever the environment names (a run
    with KAZMA_DATABASE_URL set would build them on Postgres)."""
    monkeypatch.setenv("KAZMA_DOCUMENTS_JOBS_BACKEND", "sqlite")
    monkeypatch.setenv("KAZMA_DOCUMENTS_METADATA_BACKEND", "sqlite")


def _service(tmp_path) -> DocumentIngestionService:
    return DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path, security_malware_scan="off",
        security_local_salvage=False, ocr_enabled=False,
    ))


def _seed(svc: DocumentIngestionService, count: int) -> None:
    """*count* documents, every other one the operator's own, two jobs each;
    alice owns the rest and may read the operator's."""
    from kazma_core.documents.principal import OPERATOR

    for n in range(count):
        owner = OPERATOR if n % 2 else "alice"
        record = svc.repository.create_document(tenant_id="t", owner_id=owner, title=f"d{n}")
        if owner != "alice":
            svc.repository.grant_access(tenant_id="t", document_id=record.id, actor_id=owner,
                                        principal_id="alice", permission="read")
        for k in range(2):
            svc.jobs.enqueue(tenant_id="t", workspace_id="w", document_id=record.id,
                             version_id=_version(svc.repository, "t", record, owner),
                             idempotency_key=f"{n}-{k}")


def _statements(svc: DocumentIngestionService, call) -> tuple[object, int]:
    seen: list[str] = []
    conn = svc.repository._conn  # the jobs queue shares it on SQLite
    conn.set_trace_callback(seen.append)
    try:
        result = call()
    finally:
        conn.set_trace_callback(None)
    return result, len(seen)


@pytest.mark.parametrize("count", [2, 8])
def test_listing_costs_the_same_for_two_documents_and_for_eight(tmp_path, count, sqlite_backends):
    svc = _service(tmp_path)
    try:
        _seed(svc, count)
        rows, statements = _statements(svc, lambda: svc.list_documents(tenant_id="t", actor_id="alice"))
        assert len(rows) == count
        assert all(row["state"] and row["job_id"] for row in rows)
        # Two queries (documents x ACL, newest jobs), whatever the count;
        # the old listing made about three per document.
        assert statements <= 4, f"{statements} statements for {count} documents"
    finally:
        svc.close()


def test_the_operator_grant_reads_once_and_never_grants_twice(tmp_path, monkeypatch, sqlite_backends):
    from kazma_core.documents import principal

    svc = _service(tmp_path)
    try:
        _seed(svc, 6)
        first, _ = _statements(svc, lambda: svc.grant_operator_access(tenant_id="t", force=True))
        assert first == 3  # the three documents alice (not the operator) owns
        audited: list[str] = []
        monkeypatch.setattr(svc.audit, "record", lambda **kw: audited.append(kw["action"]))
        again, statements = _statements(svc, lambda: svc.grant_operator_access(tenant_id="t", force=True))
        assert again == 0 and audited == []
        assert statements <= 2, statements
        # The operator now reads every document.
        listed = svc.list_documents(tenant_id="t", actor_id=principal.OPERATOR)
        assert len(listed) == 6
    finally:
        svc.close()
