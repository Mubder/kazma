from __future__ import annotations

import hashlib

import pytest
from kazma_core.documents.repository import DocumentAccessError, DocumentRepository


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_repository_initializes_required_sqlite_pragmas_and_schema(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "documents.db", tenant_quota_bytes=10_000)
    try:
        assert repository._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert repository._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert repository._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        tables = {
            row[0]
            for row in repository._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "documents",
            "document_versions",
            "document_blobs",
            "document_artifacts",
            "document_acl",
            "document_tombstones",
        } <= tables
    finally:
        repository.close()


def test_stable_document_immutable_version_and_source_dedup(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "documents.db", tenant_quota_bytes=10_000)
    try:
        document = repository.create_document(
            tenant_id="tenant-a", owner_id="alice", title="Contract"
        )
        digest = _digest(b"same source")
        blob = repository.register_blob(
            tenant_id="tenant-a",
            sha256=digest,
            byte_size=11,
            storage_kind="originals",
        )
        duplicate_blob = repository.register_blob(
            tenant_id="tenant-a",
            sha256=digest,
            byte_size=11,
            storage_kind="originals",
        )
        first = repository.create_version(
            tenant_id="tenant-a",
            document_id=document.id,
            actor_id="alice",
            source_blob_id=blob.id,
            source_sha256=digest,
            original_filename="contract.pdf",
            mime_type="application/pdf",
        )
        duplicate = repository.create_version(
            tenant_id="tenant-a",
            document_id=document.id,
            actor_id="alice",
            source_blob_id=blob.id,
            source_sha256=digest,
            original_filename="renamed.pdf",
            mime_type="application/pdf",
        )

        assert duplicate_blob.id == blob.id
        assert duplicate.id == first.id
        assert repository.list_versions(
            tenant_id="tenant-a", document_id=document.id, actor_id="alice"
        ) == [first]
        updated = repository.get_document(
            tenant_id="tenant-a", document_id=document.id, actor_id="alice"
        )
        assert updated is not None and updated.current_version_id == first.id
    finally:
        repository.close()


def test_all_reads_are_tenant_scoped_and_cross_tenant_mutations_denied(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "documents.db", tenant_quota_bytes=10_000)
    try:
        document = repository.create_document(
            tenant_id="tenant-a", owner_id="alice", title="Private"
        )
        digest = _digest(b"private")
        blob = repository.register_blob(
            tenant_id="tenant-a",
            sha256=digest,
            byte_size=7,
            storage_kind="originals",
        )

        assert repository.get_document(
            tenant_id="tenant-b", document_id=document.id
        ) is None
        assert repository.get_blob(tenant_id="tenant-b", blob_id=blob.id) is None
        assert repository.list_documents(tenant_id="tenant-b") == []
        with pytest.raises(DocumentAccessError):
            repository.create_version(
                tenant_id="tenant-b",
                document_id=document.id,
                actor_id="alice",
                source_blob_id=blob.id,
                source_sha256=digest,
                original_filename="private.txt",
                mime_type="text/plain",
            )
    finally:
        repository.close()


def test_ownership_and_acl_checks(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "documents.db", tenant_quota_bytes=10_000)
    try:
        document = repository.create_document(
            tenant_id="tenant-a", owner_id="alice", title="Shared"
        )
        assert not repository.has_access(
            tenant_id="tenant-a", document_id=document.id, actor_id="bob"
        )
        with pytest.raises(DocumentAccessError):
            repository.grant_access(
                tenant_id="tenant-a",
                document_id=document.id,
                actor_id="bob",
                principal_id="bob",
                permission="write",
            )
        repository.grant_access(
            tenant_id="tenant-a",
            document_id=document.id,
            actor_id="alice",
            principal_id="bob",
            permission="read",
        )
        assert repository.has_access(
            tenant_id="tenant-a", document_id=document.id, actor_id="bob"
        )
        assert not repository.has_access(
            tenant_id="tenant-a",
            document_id=document.id,
            actor_id="bob",
            permission="write",
        )
        repository.assert_owner(
            tenant_id="tenant-a", document_id=document.id, actor_id="alice"
        )
        with pytest.raises(DocumentAccessError):
            repository.assert_owner(
                tenant_id="tenant-a", document_id=document.id, actor_id="bob"
            )
    finally:
        repository.close()


def test_quota_accounting_deduplicates_same_tenant_references(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "documents.db", tenant_quota_bytes=10_000)
    try:
        digest = _digest(b"shared")
        blob = repository.register_blob(
            tenant_id="tenant-a",
            sha256=digest,
            byte_size=6,
            storage_kind="originals",
        )
        first_version = None
        first_document = None
        for owner, title in (("alice", "One"), ("bob", "Two")):
            document = repository.create_document(
                tenant_id="tenant-a", owner_id=owner, title=title
            )
            version = repository.create_version(
                tenant_id="tenant-a",
                document_id=document.id,
                actor_id=owner,
                source_blob_id=blob.id,
                source_sha256=digest,
                original_filename=f"{title}.txt",
                mime_type="text/plain",
            )
            if first_version is None:
                first_document = document
                first_version = version

        artifact_blob = repository.register_blob(
            tenant_id="tenant-a",
            sha256=digest,
            byte_size=6,
            storage_kind="artifacts",
        )
        assert first_document is not None and first_version is not None
        repository.create_artifact(
            tenant_id="tenant-a",
            document_id=first_document.id,
            version_id=first_version.id,
            actor_id="alice",
            blob_id=artifact_blob.id,
            artifact_type="normalized",
        )

        tenant_b_blob = repository.register_blob(
            tenant_id="tenant-b",
            sha256=digest,
            byte_size=6,
            storage_kind="originals",
        )

        assert tenant_b_blob.id != blob.id
        assert repository.tenant_referenced_blob_bytes(tenant_id="tenant-a") == 12
        assert repository.tenant_referenced_blob_bytes(tenant_id="tenant-b") == 0
        assert repository.tenant_references_sha256(
            tenant_id="tenant-a", sha256=digest
        )
        assert not repository.tenant_references_sha256(
            tenant_id="tenant-b", sha256=digest
        )
    finally:
        repository.close()


def test_quota_is_enforced_atomically_when_versions_are_published(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "documents.db", tenant_quota_bytes=10)
    try:
        records = []
        for index in range(2):
            payload = f"value-{index}".encode()
            digest = _digest(payload)
            blob = repository.register_blob(
                tenant_id="tenant-a",
                sha256=digest,
                byte_size=len(payload),
                storage_kind="originals",
            )
            document = repository.create_document(
                tenant_id="tenant-a",
                owner_id="alice",
                title=f"Document {index}",
            )
            records.append((document, blob, digest))

        first_document, first_blob, first_digest = records[0]
        repository.create_version(
            tenant_id="tenant-a",
            document_id=first_document.id,
            actor_id="alice",
            source_blob_id=first_blob.id,
            source_sha256=first_digest,
            original_filename="first.txt",
            mime_type="text/plain",
        )
        second_document, second_blob, second_digest = records[1]
        with pytest.raises(ValueError, match="quota exceeded"):
            repository.create_version(
                tenant_id="tenant-a",
                document_id=second_document.id,
                actor_id="alice",
                source_blob_id=second_blob.id,
                source_sha256=second_digest,
                original_filename="second.txt",
                mime_type="text/plain",
            )

        assert repository.tenant_referenced_blob_bytes(tenant_id="tenant-a") == 7
        assert repository.list_versions(
            tenant_id="tenant-a",
            document_id=second_document.id,
            actor_id="alice",
        ) == []
    finally:
        repository.close()
