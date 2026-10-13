"""The tenant blob quota holds on both metadata backends, also under a race.

2026-10-13 (F08): ``PostgresDocumentRepository`` stored ``tenant_quota_bytes``
and never applied it; a tenant on Postgres metadata could publish past its
bytes. The same rule now runs inside the publishing transaction, serialized
per tenant, and two concurrent publishers that would together pass the
quota cannot both succeed. SQLite runs the same cases as the reference.
"""

from __future__ import annotations

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest


def _sqlite(tmp_path, quota):
    from kazma_core.documents.repository import DocumentRepository

    return DocumentRepository(tmp_path / "quota.db", tenant_quota_bytes=quota)


def _postgres(_tmp_path, quota):
    try:
        from kazma_core.db.postgres_pool import get_postgres_pool

        pool = get_postgres_pool()  # never reset: the settings store holds it
    except Exception:  # noqa: BLE001 - no configured pool means skip
        pool = None
    if pool is None:
        pytest.skip("Postgres pool not configured")
    from kazma_core.documents.repository_pg import PostgresDocumentRepository

    return PostgresDocumentRepository(pool, tenant_quota_bytes=quota)


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def make_repo(request, tmp_path):
    builder = _sqlite if request.param == "sqlite" else _postgres
    made = []

    def make(quota):
        repo = builder(tmp_path, quota)
        made.append(repo)
        return repo

    yield make
    for repo in made:
        close = getattr(repo, "close", None)
        if callable(close) and repo.__class__.__name__ == "DocumentRepository":
            close()


def _publish(repo, tenant: str, doc, size: int, salt: str):
    digest = hashlib.sha256(f"{tenant}-{salt}".encode()).hexdigest()
    blob = repo.register_blob(tenant_id=tenant, sha256=digest, byte_size=size, storage_kind="originals")
    return repo.create_version(
        tenant_id=tenant, document_id=doc.id, actor_id="owner", source_blob_id=blob.id,
        source_sha256=digest, original_filename=f"{salt}.txt", mime_type="text/plain",
    )


def test_a_version_past_the_quota_is_refused(make_repo):
    repo = make_repo(1000)
    tenant = f"q-{uuid.uuid4().hex[:10]}"
    doc = repo.create_document(tenant_id=tenant, owner_id="owner", title="quota")
    _publish(repo, tenant, doc, 600, "first")
    with pytest.raises(ValueError, match="quota"):
        _publish(repo, tenant, doc, 600, "second")
    # The same bytes again are not counted twice.
    again = repo.register_blob(
        tenant_id=tenant, sha256=hashlib.sha256(f"{tenant}-first".encode()).hexdigest(),
        byte_size=600, storage_kind="originals",
    )
    assert again is not None
    # Another tenant has its own budget.
    other = f"q-{uuid.uuid4().hex[:10]}"
    other_doc = repo.create_document(tenant_id=other, owner_id="owner", title="quota")
    _publish(repo, other, other_doc, 600, "first")


def test_an_artifact_past_the_quota_is_refused(make_repo):
    repo = make_repo(1000)
    tenant = f"q-{uuid.uuid4().hex[:10]}"
    doc = repo.create_document(tenant_id=tenant, owner_id="owner", title="quota")
    version = _publish(repo, tenant, doc, 600, "source")
    digest = hashlib.sha256(f"{tenant}-artifact".encode()).hexdigest()
    blob = repo.register_blob(tenant_id=tenant, sha256=digest, byte_size=600, storage_kind="artifacts")
    with pytest.raises(ValueError, match="quota"):
        repo.create_artifact(
            tenant_id=tenant, document_id=doc.id, version_id=version.id, actor_id="owner",
            blob_id=blob.id, artifact_type="export",
        )


@pytest.mark.postgres
def test_two_concurrent_publishers_cannot_both_pass_the_quota(tmp_path):
    repo = _postgres(tmp_path, 1000)
    tenant = f"q-{uuid.uuid4().hex[:10]}"
    doc = repo.create_document(tenant_id=tenant, owner_id="owner", title="race")
    blobs = []
    for salt in ("left", "right"):
        digest = hashlib.sha256(f"{tenant}-{salt}".encode()).hexdigest()
        blobs.append((salt, digest, repo.register_blob(
            tenant_id=tenant, sha256=digest, byte_size=600, storage_kind="originals")))
    barrier = Barrier(2)

    def publish(item):
        salt, digest, blob = item
        barrier.wait(timeout=10)
        try:
            repo.create_version(
                tenant_id=tenant, document_id=doc.id, actor_id="owner", source_blob_id=blob.id,
                source_sha256=digest, original_filename=f"{salt}.txt", mime_type="text/plain",
            )
            return "published"
        except ValueError as exc:
            assert "quota" in str(exc)
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(publish, blobs))
    assert outcomes == ["published", "refused"]
    assert repo.tenant_referenced_blob_bytes(tenant_id=tenant) == 600


@pytest.mark.postgres
def test_without_the_check_both_publishers_pass(tmp_path, monkeypatch):
    # Negative control: Postgres metadata as it was -- the quota stored, never
    # applied -- publishes both halves of the race past the quota.
    from kazma_core.documents.repository_pg import PostgresDocumentRepository

    repo = _postgres(tmp_path, 1000)
    monkeypatch.setattr(PostgresDocumentRepository, "_require_blob_quota", lambda *args: None)
    tenant = f"q-{uuid.uuid4().hex[:10]}"
    doc = repo.create_document(tenant_id=tenant, owner_id="owner", title="race")
    for salt in ("left", "right"):
        _publish(repo, tenant, doc, 600, salt)
    assert repo.tenant_referenced_blob_bytes(tenant_id=tenant) == 1200
