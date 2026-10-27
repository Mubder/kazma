"""Indexing, unindexing and the unindex inside delete need WRITE access.

Audit 2026-10-26 AUD-061. The knowledge adapter's index and unindex, and
the delete's unindex of every library, checked only that the actor could
READ the document (``get_document(actor_id=...)``). A read-only grantee could
publish a document into a library or take it out of every library; a delete
by one took the document out of every library and was refused only at the
tombstone, leaving it unsearchable and not deleted. Latent today (no
surface grants read-only access), so the grant is made directly here.

The negative control is the old code: each of the three goes through for
the read-only grantee.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.jobs import DocumentJobState
from kazma_core.documents.knowledge import DocumentKnowledgeAdapter
from kazma_core.documents.models import DocumentIR
from kazma_core.documents.worker import DocumentWorker

LIBRARY = "lib-1"


class _Store:
    """A knowledge store holding one library; it records what it was asked."""

    def __init__(self) -> None:
        self.held: set[str] = set()

    def get_library_for_tenant(self, library_id, tenant_id):
        return {"id": library_id} if library_id == LIBRARY else None

    def get_document_chunks(self, *, tenant_id, library_id, document_id):
        return []

    def list_document_libraries(self, *, tenant_id, document_id):
        return [LIBRARY] if document_id in self.held else []


class _Index:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self.calls: list[str] = []

    def publish_document_version(self, *, tenant_id, library_id, document_id, **kwargs):
        self.calls.append("publish")
        self.store.held.add(document_id)
        return {"published": True}

    def unindex_document(self, *, tenant_id, library_id, document_id):
        self.calls.append("unindex")
        self.store.held.discard(document_id)
        return 1


@pytest.fixture
def world(tmp_path):
    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=tmp_path, security_malware_scan="off",
        security_local_salvage=False, ocr_enabled=False,
    ))
    result = svc.ingest_stream(io.BytesIO(b"# Notes\n\nIndexed words.\n"), filename="n.md",
                               tenant_id="t", actor_id="alice", workspace_id="w")
    for _ in range(10):
        if svc.jobs.get(tenant_id="t", job_id=result.job_id).state is DocumentJobState.READY:
            break
        claimed = svc.jobs.claim_next(owner="w", lease_seconds=60)
        asyncio.run(DocumentWorker(svc.jobs, svc.stage_handlers())._process_claimed(claimed, "w"))
    assert svc.jobs.get(tenant_id="t", job_id=result.job_id).state is DocumentJobState.READY
    svc.repository.grant_access(tenant_id="t", document_id=result.document_id, actor_id="alice",
                                principal_id="rita", permission="read")
    store = _Store()
    index = _Index(store)
    adapter = DocumentKnowledgeAdapter(repository=svc.repository, knowledge_store=store,
                                       knowledge_index=index, config=svc.config)
    manifest = svc.storage.read_manifest(document_id=result.document_id, version_id=result.version_id)
    ir = DocumentIR.from_dict(manifest["ir"])
    try:
        yield svc, adapter, index, store, ir
    finally:
        svc.close()


def test_a_reader_cannot_publish_a_document_into_a_library(world):
    svc, adapter, index, store, ir = world
    assert svc.repository.has_access(tenant_id="t", document_id=ir.document_id,
                                     actor_id="rita", permission="read")
    denied = adapter.index_document_ir(ir, tenant_id="t", actor_id="rita", library_id=LIBRARY)
    assert denied.ok is False and denied.code == "document_access_denied"
    assert index.calls == []
    allowed = adapter.index_document_ir(ir, tenant_id="t", actor_id="alice", library_id=LIBRARY)
    assert allowed.ok is True, allowed
    assert index.calls == ["publish"]


def test_a_reader_cannot_take_a_document_out_of_a_library(world):
    svc, adapter, index, store, ir = world
    assert adapter.index_document_ir(ir, tenant_id="t", actor_id="alice", library_id=LIBRARY).ok
    denied = adapter.unindex_document(tenant_id="t", actor_id="rita", library_id=LIBRARY,
                                      document_id=ir.document_id)
    assert denied.ok is False and denied.code == "document_access_denied"
    assert index.calls == ["publish"]
    assert str(ir.document_id) in store.held
    allowed = adapter.unindex_document(tenant_id="t", actor_id="alice", library_id=LIBRARY,
                                       document_id=ir.document_id)
    assert allowed.ok is True
    assert index.calls == ["publish", "unindex"]


def test_a_reader_deleting_leaves_the_document_indexed_and_undeleted(world, monkeypatch):
    svc, adapter, index, store, ir = world
    assert adapter.index_document_ir(ir, tenant_id="t", actor_id="alice", library_id=LIBRARY).ok
    monkeypatch.setattr(svc.service, "knowledge_adapter", adapter)
    result = svc.service.delete_document(tenant_id="t", actor_id="rita",
                                         document_id=ir.document_id, reason="test")
    assert result.ok is False and result.code == "document_access_denied"
    assert index.calls == ["publish"], "the read-only delete took the document out of its library"
    assert str(ir.document_id) in store.held
    assert svc.repository.get_document(tenant_id="t", document_id=ir.document_id) is not None
