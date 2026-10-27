"""A generated document's transient artifact is left to garbage collection.

Audit 2026-10-26 AUD-041. ``generate_document`` renders into the shared
content-addressed store and then ingests the result. Its cleanup asked only
the CALLING tenant whether anything referenced the artifact's bytes, and
unlinked the file when nothing did -- but the CAS path is shared by every
tenant, so it deleted a blob another tenant's artifact row still named.

Here tenant B holds an artifact on the exact bytes tenant A then generates.
The old cleanup (the negative control) deletes B's file; now it stays, and
garbage collection -- which asks every tenant, after its grace period --
still collects an artifact nobody references.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import time
from types import SimpleNamespace

import pytest
from kazma_core.documents.artifacts import ArtifactManifest, DocumentArtifact
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.models import DocumentResult, new_artifact_id

PAYLOAD = b"# Shared bytes\n\nThe same rendering for two tenants.\n"


def _service(tmp_path, **changes) -> DocumentIngestionService:
    settings = dict(
        storage_root=tmp_path / "store", security_malware_scan="off",
        security_local_salvage=False, ocr_enabled=False,
    )
    settings.update(changes)
    return DocumentIngestionService(config=DocumentConfig(**settings))


def _put_artifact(svc: DocumentIngestionService, data: bytes):
    return svc.storage.put_stream(io.BytesIO(data), kind="artifacts", max_bytes=1 << 20)


def _fake_generation(svc: DocumentIngestionService, stored, monkeypatch) -> None:
    manifest = ArtifactManifest(
        artifact_id=new_artifact_id(), operation="generate:markdown",
        input_sha256=(), renderer="markdown", renderer_version="1",
        output_mime_type="text/markdown", output_extension=".md",
        output_size=stored.byte_size, output_sha256=stored.sha256,
        created_at="2026-10-26T00:00:00+00:00", warnings=(),
    )
    artifact = DocumentArtifact(manifest, stored.path, stored.path)

    async def generate(*args, **kwargs):
        return DocumentResult(ok=True, code="artifact_ready", message="ok", data=artifact)

    ingested = SimpleNamespace(
        document_id="doc-a", version_id="ver-a", job_id="job-a",
        to_dict=lambda: {"document_id": "doc-a", "version_id": "ver-a", "job_id": "job-a"},
    )
    monkeypatch.setattr(svc.service, "generate", generate)
    # Tenant A's own intake is not under test: it would read the file, which
    # is all the generation path needs from it.
    monkeypatch.setattr(svc, "ingest_stream", lambda source, **kwargs: (source.read(), ingested)[1])


def test_generating_bytes_another_tenant_holds_leaves_its_blob(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    try:
        # Tenant B: a document and an artifact row on the shared bytes.
        mine = svc.ingest_stream(io.BytesIO(b"tenant b's document\n"), filename="b.txt",
                                 tenant_id="tenant-b", actor_id="bob", workspace_id="w")
        stored = _put_artifact(svc, PAYLOAD)
        blob = svc.repository.register_blob(
            tenant_id="tenant-b", sha256=stored.sha256, byte_size=stored.byte_size,
            storage_kind="artifacts",
        )
        svc.repository.create_artifact(
            tenant_id="tenant-b", document_id=mine.document_id, version_id=mine.version_id,
            actor_id="bob", blob_id=blob.id, artifact_type="export",
        )
        assert svc.repository.gc_is_live_reference(kind="artifacts", sha=stored.sha256)

        # Tenant A generates the very same bytes.
        _fake_generation(svc, stored, monkeypatch)
        out = asyncio.run(svc.generate_document(
            tenant_id="tenant-a", actor_id="alice", workspace_id="w",
            target_format="markdown", payload={"title": "x"},
        ))
        assert out["document_id"] == "doc-a"
        assert stored.path.is_file(), "tenant B's artifact blob was deleted by tenant A's cleanup"
        assert hashlib.sha256(stored.path.read_bytes()).hexdigest() == stored.sha256
    finally:
        svc.close()


def test_an_unreferenced_transient_artifact_is_collected_by_gc(tmp_path, monkeypatch):
    """What the cleanup did, GC does -- across every tenant, after its grace."""
    svc = _service(tmp_path, gc_grace_seconds=60)
    try:
        stored = _put_artifact(svc, b"nobody's rendering\n")
        _fake_generation(svc, stored, monkeypatch)
        asyncio.run(svc.generate_document(
            tenant_id="tenant-a", actor_id="alice", workspace_id="w",
            target_format="markdown", payload={"title": "x"},
        ))
        assert stored.path.is_file()  # within the grace period, kept
        old = time.time() - 3600
        os.utime(stored.path, (old, old))
        report = svc.run_maintenance(dry_run=False)
        assert not stored.path.exists(), report
    finally:
        svc.close()


@pytest.mark.parametrize("name", ["_cleanup_transient_artifact"])
def test_no_per_tenant_cleanup_of_a_shared_blob(name):
    assert not hasattr(DocumentIngestionService, name)
