from __future__ import annotations

import hashlib
import io
import json
import os

import pytest
from kazma_core.documents.models import DocumentId, VersionId
from kazma_core.documents.repository import DocumentRepository
from kazma_core.documents.storage import (
    BlobChecksumError,
    BlobTooLargeError,
    ContentAddressedStorage,
    StorageQuotaExceeded,
)


def test_stream_write_is_atomic_verified_and_deduplicated(tmp_path, monkeypatch) -> None:
    storage = ContentAddressedStorage(tmp_path / "storage")
    payload = b"abc" * 10_000
    expected = hashlib.sha256(payload).hexdigest()
    replace_calls: list[tuple[object, object]] = []
    real_replace = os.replace

    def tracked_replace(source, destination) -> None:
        replace_calls.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", tracked_replace)

    first = storage.put_stream(
        (payload[index : index + 701] for index in range(0, len(payload), 701)),
        kind="originals",
        max_bytes=len(payload),
        expected_sha256=expected,
    )
    second = storage.put_stream(
        io.BytesIO(payload),
        kind="originals",
        max_bytes=len(payload),
    )

    assert first.path == (
        tmp_path
        / "storage"
        / "originals"
        / "sha256"
        / expected[:2]
        / expected[2:4]
        / expected
    )
    assert first.path.read_bytes() == payload
    assert storage.verify_blob(kind="originals", sha256=expected)
    assert replace_calls
    assert first.reused is False
    assert second.reused is True


def test_oversized_or_bad_checksum_never_promotes_or_leaves_temp(tmp_path) -> None:
    storage = ContentAddressedStorage(tmp_path / "storage")

    with pytest.raises(BlobTooLargeError):
        storage.put_stream([b"1234", b"5678"], kind="quarantine", max_bytes=7)
    with pytest.raises(BlobChecksumError):
        storage.put_stream(
            [b"content"],
            kind="originals",
            max_bytes=100,
            expected_sha256="0" * 64,
        )

    files = [path for path in storage.root.rglob("*") if path.is_file()]
    assert files == []
    assert list(storage.root.rglob(".incoming-*")) == []


def test_existing_corrupt_blob_is_never_overwritten(tmp_path) -> None:
    storage = ContentAddressedStorage(tmp_path / "storage")
    payload = b"correct"
    digest = hashlib.sha256(payload).hexdigest()
    target = storage.blob_path(kind="artifacts", sha256=digest)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt")

    with pytest.raises(BlobChecksumError):
        storage.put_stream([payload], kind="artifacts", max_bytes=100)

    assert target.read_bytes() == b"corrupt"


def test_manifest_is_atomic_deterministic_and_validates_ids(tmp_path) -> None:
    storage = ContentAddressedStorage(tmp_path / "storage")
    document_id = DocumentId("11111111-1111-4111-8111-111111111111")
    version_id = VersionId("22222222-2222-4222-8222-222222222222")

    path = storage.write_manifest(
        document_id=document_id,
        version_id=version_id,
        manifest={"z": 1, "a": {"name": "مرحبا"}},
    )

    assert path == (
        tmp_path / "storage" / "manifests" / str(document_id) / f"{version_id}.json"
    )
    assert path.read_text(encoding="utf-8") == '{"a":{"name":"مرحبا"},"z":1}'
    assert storage.read_manifest(
        document_id=document_id, version_id=version_id
    ) == {"a": {"name": "مرحبا"}, "z": 1}
    assert json.loads(path.read_text(encoding="utf-8"))["z"] == 1
    assert list(path.parent.glob(".manifest-*")) == []
    with pytest.raises(ValueError):
        storage.write_manifest(
            document_id="../escape",  # type: ignore[arg-type]
            version_id=version_id,
            manifest={},
        )


@pytest.mark.parametrize(
    ("kind", "digest"),
    [
        ("../originals", "a" * 64),
        ("originals/x", "a" * 64),
        ("originals", "../" + "a" * 61),
        ("originals", "A" * 64),
        ("originals", "a" * 63),
    ],
)
def test_storage_rejects_path_traversal(kind: str, digest: str, tmp_path) -> None:
    storage = ContentAddressedStorage(tmp_path / "storage")
    with pytest.raises(ValueError):
        storage.blob_path(kind=kind, sha256=digest)


def test_storage_quota_uses_unique_tenant_referenced_bytes(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "documents.db", tenant_quota_bytes=3)
    storage = ContentAddressedStorage(tmp_path / "storage")
    try:
        payload = b"abc"
        first = storage.put_stream(
            [payload],
            kind="originals",
            max_bytes=10,
            tenant_id="tenant-a",
            repository=repository,
            tenant_quota_bytes=3,
        )
        blob = repository.register_blob(
            tenant_id="tenant-a",
            sha256=first.sha256,
            byte_size=first.byte_size,
            storage_kind="originals",
        )
        for owner in ("alice", "bob"):
            document = repository.create_document(
                tenant_id="tenant-a", owner_id=owner, title=owner
            )
            repository.create_version(
                tenant_id="tenant-a",
                document_id=document.id,
                actor_id=owner,
                source_blob_id=blob.id,
                source_sha256=blob.sha256,
                original_filename="same.txt",
                mime_type="text/plain",
            )

        reused = storage.put_stream(
            [payload],
            kind="originals",
            max_bytes=10,
            tenant_id="tenant-a",
            repository=repository,
            tenant_quota_bytes=3,
        )
        assert reused.reused is True
        assert repository.tenant_referenced_blob_bytes(tenant_id="tenant-a") == 3

        with pytest.raises(StorageQuotaExceeded):
            storage.put_stream(
                [b"x"],
                kind="originals",
                max_bytes=10,
                tenant_id="tenant-a",
                repository=repository,
                tenant_quota_bytes=3,
            )
        assert list(storage.root.rglob(".incoming-*")) == []
    finally:
        repository.close()
