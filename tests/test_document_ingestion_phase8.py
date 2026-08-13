"""Phase 8 durable ingestion coordinator — end-to-end + isolation tests."""

from __future__ import annotations

import asyncio
import io
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import (
    DocumentIngestionError,
    DocumentIngestionService,
)
from kazma_core.documents.knowledge import DocumentIndexResult
from kazma_core.documents.models import DocumentJobState, DocumentResult
from kazma_core.documents.repository import DocumentAccessError


TEXT = b"Kazma document intelligence platform.\nPhase 8 durable ingestion.\n"


def _service(tmp_path) -> DocumentIngestionService:
    config = DocumentConfig(
        storage_root=tmp_path / "store",
        worker_concurrency=1,
        worker_lease_seconds=5,
        worker_heartbeat_seconds=1,
    )
    return DocumentIngestionService(config=config)


async def _wait_state(svc, tenant, job_id, expected, *, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last = None
    while loop.time() < deadline:
        status = await asyncio.to_thread(
            svc.job_status, tenant_id=tenant, job_id=job_id
        )
        last = status
        if status is not None and status["state"] == expected:
            return status
        await asyncio.sleep(0.05)
    raise AssertionError(f"job did not reach {expected}; last={last}")


@pytest.mark.asyncio
async def test_ingest_process_read_index(tmp_path):
    svc = _service(tmp_path)
    try:
        result = await asyncio.to_thread(
            svc.ingest_stream,
            io.BytesIO(TEXT),
            filename="note.txt",
            tenant_id="tenant-a",
            workspace_id="ws-1",
            actor_id="alice",
            title="Note",
        )
        assert result.state in {
            DocumentJobState.READY_TO_PARSE,
            DocumentJobState.OCR_REQUIRED,
        }
        await svc.start_workers()
        status = await _wait_state(svc, "tenant-a", result.job_id, "ready")
        assert status["state"] == "ready"

        content = await asyncio.to_thread(
            svc.get_content,
            tenant_id="tenant-a",
            actor_id="alice",
            document_id=result.document_id,
        )
        assert content["page_count"] >= 1
        assert "Phase 8 durable ingestion" in content["text"]
        assert content["fenced"] is True

        docs = await asyncio.to_thread(
            svc.list_documents, tenant_id="tenant-a", actor_id="alice"
        )
        assert any(d["document_id"] == str(result.document_id) for d in docs)
        assert docs[0]["state"] == "ready"

        events = await asyncio.to_thread(
            svc.job_events, tenant_id="tenant-a", job_id=result.job_id
        )
        states = [e["to_state"] for e in events]
        for expected in ("quarantined", "validating", "parsing", "verifying", "ready"):
            assert expected in states

        svc.service.index_document_ir = Mock(
            return_value=DocumentResult(
                ok=True,
                code="document_indexed",
                message="Indexed",
                data=DocumentIndexResult(
                    library_id="main",
                    chunk_count=3,
                    published=True,
                    source_url=f"document://{result.document_id}/{result.version_id}",
                ),
            )
        )
        indexed = await asyncio.to_thread(
            svc.index_document,
            tenant_id="tenant-a",
            actor_id="alice",
            document_id=result.document_id,
            library_id="main",
        )
        assert indexed["chunk_count"] == 3
        assert indexed["published"] is True

        svc.service.index_document_ir = Mock(
            return_value=DocumentResult(
                ok=False,
                code="document_access_denied",
                message="Document is unavailable",
            )
        )
        with pytest.raises(DocumentIngestionError) as excinfo:
            await asyncio.to_thread(
                svc.index_document,
                tenant_id="tenant-a",
                actor_id="alice",
                document_id=result.document_id,
                library_id="main",
            )
        assert excinfo.value.code == "document_access_denied"
    finally:
        await svc.stop_workers()
        svc.close()


@pytest.mark.asyncio
async def test_search_unwraps_results_and_surfaces_failures(tmp_path):
    svc = _service(tmp_path)
    try:
        svc.service.search_library = AsyncMock(
            return_value=DocumentResult(
                ok=True,
                code="document_search_complete",
                message="Search complete",
                data={"hits": [{"chunk_id": "chunk-1"}], "prompt_context": "fenced"},
            )
        )
        result = await svc.search_library(
            tenant_id="tenant-a",
            library_id="main",
            query="kazma",
        )
        assert result["prompt_context"] == "fenced"
        assert result["hits"][0]["chunk_id"] == "chunk-1"

        svc.service.search_library = AsyncMock(
            return_value=DocumentResult(
                ok=False,
                code="document_search_failed",
                message="Search failed safely",
            )
        )
        with pytest.raises(DocumentIngestionError) as excinfo:
            await svc.search_library(
                tenant_id="tenant-a",
                library_id="main",
                query="kazma",
            )
        assert excinfo.value.code == "document_search_failed"
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_idempotent_replay_returns_same_job(tmp_path):
    svc = _service(tmp_path)
    try:
        first = await asyncio.to_thread(
            svc.ingest_stream,
            io.BytesIO(TEXT),
            filename="note.txt",
            tenant_id="tenant-a",
            workspace_id="ws-1",
            actor_id="alice",
            idempotency_key="req-42",
        )
        second = await asyncio.to_thread(
            svc.ingest_stream,
            io.BytesIO(TEXT),
            filename="note.txt",
            tenant_id="tenant-a",
            workspace_id="ws-1",
            actor_id="alice",
            idempotency_key="req-42",
        )
        assert second.reused is True
        assert first.job_id == second.job_id
        assert first.document_id == second.document_id
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_tenant_isolation_blocks_cross_access(tmp_path):
    svc = _service(tmp_path)
    try:
        result = await asyncio.to_thread(
            svc.ingest_stream,
            io.BytesIO(TEXT),
            filename="note.txt",
            tenant_id="tenant-a",
            workspace_id="ws-1",
            actor_id="alice",
        )
        # Another tenant cannot see the document or its job.
        docs = await asyncio.to_thread(
            svc.list_documents, tenant_id="tenant-b", actor_id="mallory"
        )
        assert docs == []
        status = await asyncio.to_thread(
            svc.job_status, tenant_id="tenant-b", job_id=result.job_id
        )
        assert status is None
        with pytest.raises(DocumentIngestionError):
            await asyncio.to_thread(
                svc.get_content,
                tenant_id="tenant-b",
                actor_id="mallory",
                document_id=result.document_id,
            )
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_stream_over_limit_rejected(tmp_path):
    config = DocumentConfig(storage_root=tmp_path / "store", intake_max_bytes=16)
    svc = DocumentIngestionService(config=config)
    try:
        with pytest.raises(DocumentIngestionError) as excinfo:
            await asyncio.to_thread(
                svc.ingest_stream,
                io.BytesIO(b"x" * 4096),
                filename="big.txt",
                tenant_id="tenant-a",
                workspace_id="ws-1",
                actor_id="alice",
            )
        assert excinfo.value.code == "intake_too_large"
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_cancel_pending_job(tmp_path):
    svc = _service(tmp_path)
    try:
        result = await asyncio.to_thread(
            svc.ingest_stream,
            io.BytesIO(TEXT),
            filename="note.txt",
            tenant_id="tenant-a",
            workspace_id="ws-1",
            actor_id="alice",
        )
        # ACL: a non-reader principal cannot cancel the owner's job.
        with pytest.raises(DocumentAccessError):
            await asyncio.to_thread(
                svc.cancel_job, tenant_id="tenant-a", job_id=result.job_id,
                actor_id="bob",
            )
        # Backward-compatible: no actor → no per-principal gate.
        cancelled = await asyncio.to_thread(
            svc.cancel_job, tenant_id="tenant-a", job_id=result.job_id
        )
        assert cancelled["state"] == "cancelled"
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_restart_recovery_resumes_pending_job(tmp_path):
    config = DocumentConfig(
        storage_root=tmp_path / "store",
        worker_concurrency=1,
        worker_lease_seconds=5,
        worker_heartbeat_seconds=1,
    )
    # First coordinator: ingest but never start workers (job sits pending).
    svc1 = DocumentIngestionService(config=config)
    result = await asyncio.to_thread(
        svc1.ingest_stream,
        io.BytesIO(TEXT),
        filename="durable.txt",
        tenant_id="tenant-a",
        workspace_id="ws-1",
        actor_id="alice",
    )
    assert result.state == DocumentJobState.READY_TO_PARSE
    svc1.close()  # simulate process restart — DB persists under storage_root

    # Second coordinator on the SAME storage root picks up the durable job.
    svc2 = DocumentIngestionService(config=config)
    try:
        await svc2.start_workers()
        status = await _wait_state(svc2, "tenant-a", result.job_id, "ready")
        assert status["state"] == "ready"
        content = await asyncio.to_thread(
            svc2.get_content,
            tenant_id="tenant-a",
            actor_id="alice",
            document_id=result.document_id,
        )
        assert "durable ingestion" in content["text"]
    finally:
        await svc2.stop_workers()
        svc2.close()


@pytest.mark.asyncio
async def test_traversal_filename_is_sanitized(tmp_path):
    svc = _service(tmp_path)
    try:
        result = await asyncio.to_thread(
            svc.ingest_stream,
            io.BytesIO(TEXT),
            filename="../../etc/passwd.txt",
            tenant_id="tenant-a",
            workspace_id="ws-1",
            actor_id="alice",
        )
        detail = await asyncio.to_thread(
            svc.get_document_detail,
            tenant_id="tenant-a",
            actor_id="alice",
            document_id=result.document_id,
        )
        assert detail["versions"][0]["original_filename"] == "passwd.txt"
    finally:
        svc.close()
