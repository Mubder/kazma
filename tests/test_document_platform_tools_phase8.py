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


@pytest.mark.asyncio
async def test_document_status_no_id_is_platform_overview(wired_service):
    """Bare document_status is a health/catalog probe, not a missing-arg error."""
    from kazma_skills.native.document_platform import tools

    token = set_current_tenant_id("tenant-overview")
    try:
        out = await tools.document_status()
    finally:
        reset_current_tenant_id(token)

    assert not out.startswith("Error"), out
    assert "Document platform overview" in out
    assert "enabled:" in out
    assert "documents: 0" in out
    assert "catalog: empty" in out


@pytest.mark.asyncio
async def test_document_status_overview_includes_imported_doc(
    wired_service, tmp_path, monkeypatch
):
    from kazma_skills.native.document_platform import tools

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "n.txt").write_text("overview", encoding="utf-8")
    monkeypatch.setattr(
        "kazma_core.workspace.binding.resolve_active_root", lambda: ws
    )
    token = set_current_tenant_id("tenant-overview-2")
    await wired_service.start_workers()
    try:
        imported = await tools.document_import("n.txt")
        doc_id = imported.split("document_id:")[1].split()[0].strip()
        overview = await tools.document_status()
        assert "Document platform overview" in overview
        assert doc_id in overview
        assert "documents: 1" in overview
    finally:
        await wired_service.stop_workers()
        reset_current_tenant_id(token)


@pytest.mark.asyncio
async def test_document_import_says_when_it_is_still_processing(wired_service, tmp_path, monkeypatch):
    # DOC-A5 (2026-10-13): the tool said it returned the final state; it
    # waits 30 s. A job not finished by then is reported as still processing.
    from kazma_skills.native.document_platform import tools

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "slow.txt").write_text("Not processed before the wait ends.\n", encoding="utf-8")
    monkeypatch.setattr("kazma_core.workspace.binding.resolve_active_root", lambda: ws)

    async def no_workers():
        return wired_service

    monkeypatch.setattr(tools, "_ensure_service", no_workers)
    monkeypatch.setattr(tools, "_INGEST_WAIT_SECONDS", 0.5)
    out = await tools.document_import("slow.txt")
    assert "Still processing" in out and "document_status" in out
    assert "state: ready\n" not in out + "\n"  # a state short of ready (e.g. ready_to_parse)


@pytest.mark.asyncio
async def test_a_finished_import_carries_no_still_processing_note(wired_service, tmp_path, monkeypatch):
    # Contrast: with workers running the job finishes within the wait.
    from kazma_skills.native.document_platform import tools

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "quick.txt").write_text("Processed within the wait.\n", encoding="utf-8")
    monkeypatch.setattr("kazma_core.workspace.binding.resolve_active_root", lambda: ws)
    await wired_service.start_workers()
    try:
        out = await tools.document_import("quick.txt")
        assert "state: ready" in out and "Still processing" not in out
    finally:
        await wired_service.stop_workers()
