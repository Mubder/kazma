"""Remote-parse egress on real PostgreSQL metadata, jobs, audit and settings.

F03/F04 (2026-10-13): the egress rules in ``tests/test_document_egress_hardening.py``
were qualified on SQLite only. These run the same rules with every store the
durable path uses on Postgres -- document metadata, the job queue, the audit
table and (when ``KAZMA_DB_BACKEND=postgres``) the settings store -- and
assert each service really is on Postgres before trusting a pass. Nothing is
uploaded: the provider functions are replaced and inert bytes are used.

Requires a Postgres DSN in the environment (``KAZMA_DATABASE_URL`` with
``KAZMA_TEST_ALLOW_REAL_DB=1``); skips otherwise -- a skip is not evidence.
"""

from __future__ import annotations

import io
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.documents import extract_salvage as salvage
from kazma_core.documents.config import DocumentConfig

from tests.test_extract_salvage import _ir

pytestmark = pytest.mark.postgres


@pytest.fixture
def pg_backend(monkeypatch):
    try:
        from kazma_core.db.postgres_pool import get_postgres_pool

        # Never reset the shared pool here: the settings store already holds it.
        pool = get_postgres_pool()
    except Exception:  # noqa: BLE001 - no configured pool means skip
        pool = None
    if pool is None:
        pytest.skip("Postgres pool not configured")
    monkeypatch.setenv("KAZMA_DOCUMENTS_METADATA_BACKEND", "postgres")
    monkeypatch.setenv("KAZMA_DOCUMENTS_JOBS_BACKEND", "postgres")
    # A fresh Postgres settings store has no rollout row (the SQLite test
    # store is seeded from kazma.yaml): turn durable writes on for the test.
    store = get_config_store()
    previous = store.get("documents.enabled")
    store.set("documents.enabled", True, category="documents")
    yield pool
    if previous is None:
        store.delete("documents.enabled")
    else:
        store.set("documents.enabled", previous, category="documents")


def _service(root):
    from kazma_core.documents.ingestion import DocumentIngestionService

    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=root, security_malware_scan="off", security_local_salvage=False,
    ))
    assert getattr(svc.repository, "backend_name", None) == "postgres", "metadata is not on Postgres"
    assert "Postgres" in type(svc.jobs).__name__, f"jobs are on {type(svc.jobs).__name__}"
    return svc


def _job(svc, tenant, actor):
    result = svc.ingest_stream(io.BytesIO(b"a private document " + uuid.uuid4().hex.encode()),
                               filename="note.txt", tenant_id=tenant, actor_id=actor,
                               workspace_id=tenant + "-workspace")
    return svc.jobs.get(tenant_id=tenant, job_id=result.job_id)


def _egress_events(svc, tenant):
    return [e for e in svc.audit.list_events(tenant_id=tenant)["events"] if e["event_type"] == "egress"]


def test_concurrent_services_keep_each_jobs_identity_on_postgres(pg_backend, tmp_path, monkeypatch):
    first, second = _service(tmp_path / "one"), _service(tmp_path / "two")
    tenants = (f"pg-a-{uuid.uuid4().hex[:8]}", f"pg-b-{uuid.uuid4().hex[:8]}")
    a, b = _job(first, tenants[0], "alice"), _job(second, tenants[1], "bob")
    barrier = threading.Barrier(2)

    def parse(job, *, force_ocr):
        barrier.wait(timeout=10)
        salvage._record_egress("mock-no-upload", tmp_path, 21)
        barrier.wait(timeout=10)
        salvage._record_egress("mock-no-upload", tmp_path, 21, "failure")

    monkeypatch.setattr(first, "_parse_to_manifest_impl", parse)
    monkeypatch.setattr(second, "_parse_to_manifest_impl", parse)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(svc._parse_to_manifest, job, force_ocr=False)
                       for svc, job in ((first, a), (second, b))]
            for future in futures:
                future.result(timeout=30)
        for svc, job, actor in ((first, a, "alice"), (second, b, "bob")):
            events = _egress_events(svc, job.tenant_id)
            assert {e["action"] for e in events} == {"remote_parse_attempt", "remote_parse_failure"}
            for event in events:
                assert event["actor_id"] == actor and event["workspace_id"] == job.workspace_id
                assert event["document_id"] == str(job.document_id)
                assert event["version_id"] == str(job.version_id) and event["job_id"] == str(job.id)
        # Neither tenant sees the other's egress.
        assert all(e["actor_id"] == "alice" for e in _egress_events(first, tenants[0]))
        assert _egress_events(first, tenants[1]) == _egress_events(second, tenants[1])
    finally:
        first.close()
        second.close()


def test_an_unavailable_postgres_audit_refuses_the_upload(pg_backend, tmp_path, monkeypatch):
    import os

    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.documents.audit import DocumentAuditStore
    from kazma_core.documents.repository_pg import PostgresDocumentRepository

    svc = _service(tmp_path / "store")
    job = _job(svc, f"pg-t-{uuid.uuid4().hex[:8]}", "alice")
    path = tmp_path / "x.pdf"
    path.write_bytes(b"never uploaded")
    monkeypatch.setenv("LLAMAPARSE_API_KEY", "audit-fake-key")
    get_config_store().set("documents.security.remote_parse", True, category="documents")
    uploaded = []
    monkeypatch.setattr(salvage, "_llamaparse", lambda *a: uploaded.append(True) or "")
    # A real Postgres audit store on its own pool, then that pool closed: the
    # native failure the audit write meets when the database goes away.
    own_pool = PostgresPool(os.environ["KAZMA_DATABASE_URL"], min_size=1, max_size=1)
    svc.audit = DocumentAuditStore(PostgresDocumentRepository(own_pool, tenant_quota_bytes=1024 ** 3))
    own_pool.close()

    def parse(job, *, force_ocr):
        with pytest.raises(RuntimeError, match="audit could not be persisted"):
            salvage.try_remote_parse(path, _ir("", 0))

    monkeypatch.setattr(svc, "_parse_to_manifest_impl", parse)
    try:
        svc._parse_to_manifest(job, force_ocr=False)
        assert not uploaded
    finally:
        get_config_store().set("documents.security.remote_parse", False, category="documents")
        svc.close()


def test_live_revocation_through_the_settings_store_stops_the_next_upload(pg_backend, tmp_path, monkeypatch):
    store = get_config_store()
    store.set("documents.security.remote_parse", True, category="documents")
    svc = _service(tmp_path / "store")
    job = _job(svc, f"pg-r-{uuid.uuid4().hex[:8]}", "alice")
    monkeypatch.setenv("LLAMAPARSE_API_KEY", "audit-fake-key")
    monkeypatch.setenv("REDUCTO_API_KEY", "audit-fake-key")
    uploaded = []

    def first_provider(*args):
        uploaded.append("llama")
        store.set("documents.security.remote_parse", False, category="documents")
        raise RuntimeError("provider failed; the operator revoked consent meanwhile")

    monkeypatch.setattr(salvage, "_llamaparse", first_provider)
    monkeypatch.setattr(salvage, "_reducto", lambda *a: uploaded.append("reducto") or "x")
    path = tmp_path / "x.pdf"
    path.write_bytes(b"inert")

    def parse(job, *, force_ocr):
        assert salvage.try_remote_parse(path, _ir("", 0)) is None

    monkeypatch.setattr(svc, "_parse_to_manifest_impl", parse)
    try:
        svc._parse_to_manifest(job, force_ocr=False)
        assert uploaded == ["llama"]
        phases = sorted(e["action"] for e in _egress_events(svc, job.tenant_id))
        assert phases == ["remote_parse_attempt", "remote_parse_failure"]
        # The revocation travelled through the Postgres settings store itself
        # when the suite runs with KAZMA_DB_BACKEND=postgres (the CI job).
        print("settings store on postgres:", store._use_postgres())
    finally:
        store.set("documents.security.remote_parse", False, category="documents")
        svc.close()


def test_a_reused_version_keeps_each_new_jobs_actor_on_postgres(pg_backend, tmp_path, monkeypatch):
    svc = _service(tmp_path / "store")
    tenant = f"pg-v-{uuid.uuid4().hex[:8]}"
    body = b"a private document " + uuid.uuid4().hex.encode()
    try:
        first = svc.ingest_stream(io.BytesIO(body), filename="note.txt", tenant_id=tenant,
                                  actor_id="alice", workspace_id="alice-workspace")
        first_job = svc.jobs.get(tenant_id=tenant, job_id=first.job_id)
        svc.repository.grant_access(tenant_id=tenant, document_id=first_job.document_id,
                                    actor_id="alice", principal_id="bob", permission="write")
        second = svc.ingest_stream(io.BytesIO(body), filename="note.txt", tenant_id=tenant,
                                   actor_id="bob", workspace_id="bob-workspace",
                                   document_id=first_job.document_id, force_ocr=True,
                                   idempotency_key="bob-request")
        job = svc.jobs.get(tenant_id=tenant, job_id=second.job_id)
        assert job.version_id == first_job.version_id and job.actor_id == "bob"
        monkeypatch.setattr(svc, "_parse_to_manifest_impl", lambda *a, **k:
                            salvage._record_egress("mock-no-upload", tmp_path, 18))
        svc._parse_to_manifest(job, force_ocr=True)
        events = _egress_events(svc, tenant)
        assert len(events) == 1
        assert events[0]["actor_id"] == "bob" and events[0]["job_id"] == str(job.id)
    finally:
        svc.close()
