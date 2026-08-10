"""Phase 8 native document-platform tool delegation tests."""

from __future__ import annotations

import asyncio

import pytest

from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import (
    DocumentIngestionService,
    set_ingestion_service,
)
from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id


@pytest.fixture
def wired_service(tmp_path):
    config = DocumentConfig(
        storage_root=tmp_path / "store",
        worker_concurrency=1,
        worker_lease_seconds=5,
        worker_heartbeat_seconds=1,
    )
    svc = DocumentIngestionService(config=config)
    set_ingestion_service(svc)
    yield svc
    set_ingestion_service(None)
    svc.close()


@pytest.mark.asyncio
async def test_document_import_read_delegates(wired_service, tmp_path, monkeypatch):
    from kazma_skills.native.document_platform import tools

    # Point the workspace root at a temp dir containing a real file.
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "note.txt").write_text("Native tool delegation for Phase 8.\n", encoding="utf-8")
    monkeypatch.setattr(
        "kazma_core.workspace.binding.resolve_active_root", lambda: ws
    )
    await wired_service.start_workers()
    try:
        out = await tools.document_import("note.txt")
        assert "document_id" in out
        assert "state: ready" in out
        doc_id = out.split("document_id:")[1].split()[0].strip()

        read = await tools.document_read(doc_id)
        assert "Native tool delegation" in read
    finally:
        await wired_service.stop_workers()


@pytest.mark.asyncio
async def test_document_import_rejects_outside_workspace(wired_service, tmp_path, monkeypatch):
    from kazma_skills.native.document_platform import tools

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(
        "kazma_core.workspace.binding.resolve_active_root", lambda: ws
    )
    out = await tools.document_import("../../etc/passwd")
    assert out.startswith("Error")
    assert "outside" in out or "not found" in out


@pytest.mark.asyncio
async def test_document_status_uses_tenant_context(wired_service, tmp_path, monkeypatch):
    from kazma_skills.native.document_platform import tools

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "n.txt").write_text("scoped", encoding="utf-8")
    monkeypatch.setattr(
        "kazma_core.workspace.binding.resolve_active_root", lambda: ws
    )
    token = set_current_tenant_id("tenant-x")
    await wired_service.start_workers()
    try:
        out = await tools.document_import("n.txt")
        doc_id = out.split("document_id:")[1].split()[0].strip()
        # Same tenant sees it.
        status = await tools.document_status(document_id=doc_id)
        assert "state:" in status
    finally:
        await wired_service.stop_workers()
        reset_current_tenant_id(token)

    # A different tenant must NOT see it.
    token2 = set_current_tenant_id("tenant-y")
    try:
        status2 = await tools.document_status(document_id=doc_id)
        assert status2.startswith("Error")
    finally:
        reset_current_tenant_id(token2)
