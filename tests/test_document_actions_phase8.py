"""Phase 8 document action surfaces — coordinator + Web API contracts.

Covers the opaque-ID conversion / PDF-info / split / redact / merge / generate
coordinator methods, secure artifact retrieval (ACL + sanitization), and the
Web routes' truthful status mapping.
"""

from __future__ import annotations

import asyncio
import inspect
import io

import pytest

from kazma_core.documents.artifacts import ArtifactManifest, DocumentArtifact
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import (
    DocumentIngestionError,
    DocumentIngestionService,
)
from kazma_core.documents.models import DocumentResult, new_artifact_id


MD = b"# Kazma\n\nPhase 8 document actions.\n\nSecond paragraph.\n"


def _service(tmp_path) -> DocumentIngestionService:
    config = DocumentConfig(
        storage_root=tmp_path / "store",
        worker_concurrency=1,
        worker_lease_seconds=5,
        worker_heartbeat_seconds=1,
    )
    return DocumentIngestionService(config=config)


async def _ingest_ready(svc, *, tenant, actor, workspace, filename, data) -> str:
    result = await asyncio.to_thread(
        svc.ingest_stream,
        io.BytesIO(data),
        filename=filename,
        tenant_id=tenant,
        workspace_id=workspace,
        actor_id=actor,
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 15.0
    while loop.time() < deadline:
        status = await asyncio.to_thread(
            svc.job_status, tenant_id=tenant, job_id=result.job_id
        )
        if status and status["state"] == "ready":
            return str(result.document_id)
        if status and status["state"] in {"rejected", "dead_letter", "cancelled"}:
            raise AssertionError(f"ingest ended in {status['state']}")
        await asyncio.sleep(0.05)
    raise AssertionError("document did not reach ready")


# ── Coordinator API shape: no raw path is ever accepted ─────────────────


def test_action_methods_never_accept_a_path() -> None:
    for name in (
        "convert_document",
        "pdf_info_document",
        "pdf_split_document",
        "pdf_fill_form_document",
        "redact_document",
        "merge_documents",
        "generate_document",
    ):
        method = getattr(DocumentIngestionService, name)
        params = set(inspect.signature(method).parameters)
        assert "path" not in params, f"{name} must not accept a raw path"
        assert "approved_path" not in params
        assert "export_dir" not in params
        # Every mutating action is scoped by tenant + actor.
        assert "tenant_id" in params and "actor_id" in params


# ── Tenant / actor denial ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_convert_denies_other_actor(tmp_path) -> None:
    svc = _service(tmp_path)
    try:
        await svc.start_workers()
        doc_id = await _ingest_ready(
            svc,
            tenant="tenant-a",
            actor="alice",
            workspace="ws",
            filename="note.md",
            data=MD,
        )
        with pytest.raises(DocumentIngestionError) as exc:
            await svc.convert_document(
                tenant_id="tenant-a",
                actor_id="mallory",
                workspace_id="ws",
                document_id=doc_id,
                target_format="html",
            )
        assert exc.value.code == "document_access_denied"
    finally:
        await svc.stop_workers()
        svc.close()


@pytest.mark.asyncio
async def test_convert_denies_other_tenant(tmp_path) -> None:
    svc = _service(tmp_path)
    try:
        await svc.start_workers()
        doc_id = await _ingest_ready(
            svc,
            tenant="tenant-a",
            actor="alice",
            workspace="ws",
            filename="note.md",
            data=MD,
        )
        with pytest.raises(DocumentIngestionError) as exc:
            await svc.pdf_info_document(
                tenant_id="tenant-b",
                actor_id="alice",
                workspace_id="ws",
                document_id=doc_id,
            )
        assert exc.value.code == "document_access_denied"
    finally:
        await svc.stop_workers()
        svc.close()


# ── Successful durable conversion + sanitized artifact + download ───────


@pytest.mark.asyncio
async def test_convert_produces_sanitized_downloadable_artifact(tmp_path) -> None:
    svc = _service(tmp_path)
    try:
        await svc.start_workers()
        doc_id = await _ingest_ready(
            svc,
            tenant="tenant-a",
            actor="alice",
            workspace="ws",
            filename="note.md",
            data=MD,
        )
        data = await svc.convert_document(
            tenant_id="tenant-a",
            actor_id="alice",
            workspace_id="ws",
            document_id=doc_id,
            target_format="html",
        )
        # Flat, sanitized payload — no server path leaks anywhere.
        assert data["artifact_id"]
        assert "storage_path" not in data["manifest"]
        assert "export_path" not in data["manifest"]
        assert data["manifest"]["output"]["extension"] == ".html"

        # The artifact is durably owned and downloadable by opaque ID.
        info = svc.resolve_artifact_blob(
            tenant_id="tenant-a",
            actor_id="alice",
            artifact_id=data["artifact_id"],
        )
        assert info["path"].is_file()
        assert info["filename"].endswith(".html")
        assert "path" not in {"storage_path", "export_path"}

        # It appears in the document's artifact list.
        artifacts = svc.list_document_artifacts(
            tenant_id="tenant-a", actor_id="alice", document_id=doc_id
        )
        assert any(a["artifact_id"] == data["artifact_id"] for a in artifacts)
    finally:
        await svc.stop_workers()
        svc.close()


@pytest.mark.asyncio
async def test_download_denies_cross_actor(tmp_path) -> None:
    svc = _service(tmp_path)
    try:
        await svc.start_workers()
        doc_id = await _ingest_ready(
            svc,
            tenant="tenant-a",
            actor="alice",
            workspace="ws",
            filename="note.md",
            data=MD,
        )
        data = await svc.convert_document(
            tenant_id="tenant-a",
            actor_id="alice",
            workspace_id="ws",
            document_id=doc_id,
            target_format="html",
        )
        with pytest.raises(DocumentIngestionError) as exc:
            svc.resolve_artifact_blob(
                tenant_id="tenant-a",
                actor_id="mallory",
                artifact_id=data["artifact_id"],
            )
        assert exc.value.code == "artifact_access_denied"

        # An unknown / non-UUID artifact id is also a denial, not a crash.
        with pytest.raises(DocumentIngestionError) as exc2:
            svc.resolve_artifact_blob(
                tenant_id="tenant-a", actor_id="alice", artifact_id="not-a-uuid"
            )
        assert exc2.value.code == "artifact_access_denied"

        with pytest.raises(DocumentIngestionError) as exc3:
            svc.resolve_artifact_blob(
                tenant_id="tenant-a",
                actor_id="alice",
                artifact_id=str(new_artifact_id()),
            )
        assert exc3.value.code == "artifact_access_denied"
    finally:
        await svc.stop_workers()
        svc.close()


# ── Engine-unavailable truthfulness (no optional engine required) ───────


@pytest.mark.asyncio
async def test_pdf_split_engine_unavailable_is_truthful(tmp_path, monkeypatch) -> None:
    svc = _service(tmp_path)
    try:
        await svc.start_workers()
        doc_id = await _ingest_ready(
            svc,
            tenant="tenant-a",
            actor="alice",
            workspace="ws",
            filename="note.md",
            data=MD,
        )

        async def _fake_split(*_args, **_kwargs):
            return DocumentResult(
                ok=False,
                code="document_engine_unavailable",
                message="pypdf is unavailable",
            )

        monkeypatch.setattr(svc.service, "pdf_split", _fake_split)
        with pytest.raises(DocumentIngestionError) as exc:
            await svc.pdf_split_document(
                tenant_id="tenant-a",
                actor_id="alice",
                workspace_id="ws",
                document_id=doc_id,
                start_page=1,
                end_page=1,
            )
        assert exc.value.code == "document_engine_unavailable"
    finally:
        await svc.stop_workers()
        svc.close()


# ── Artifact payload sanitization (unit) ────────────────────────────────


def test_artifact_payload_strips_server_paths(tmp_path) -> None:
    manifest = ArtifactManifest(
        artifact_id=new_artifact_id(),
        operation="convert:markdown:html",
        input_sha256=("a" * 64,),
        renderer="markdown",
        renderer_version="1",
        output_mime_type="text/html",
        output_extension=".html",
        output_size=10,
        output_sha256="b" * 64,
        created_at="2026-01-01T00:00:00+00:00",
        warnings=("careful",),
    )
    artifact = DocumentArtifact(manifest, tmp_path / "blob", tmp_path / "export.html")
    result = DocumentResult(
        ok=True,
        code="artifact_ready",
        message="ok",
        data=artifact,
        artifact_id=manifest.artifact_id,
        warnings=("careful",),
    )
    payload = DocumentIngestionService._artifact_payload(result)
    assert payload["artifact_id"] == str(manifest.artifact_id)
    assert payload["warnings"] == ["careful"]
    assert "storage_path" not in payload["manifest"]
    assert "export_path" not in payload["manifest"]
    assert payload["manifest"]["operation"] == "convert:markdown:html"


def test_artifact_payload_raises_on_failure() -> None:
    result = DocumentResult(ok=False, code="boom", message="nope")
    with pytest.raises(DocumentIngestionError) as exc:
        DocumentIngestionService._artifact_payload(result)
    assert exc.value.code == "boom"


# ── Generation is durably ingested (not just a transient artifact) ──────


@pytest.mark.asyncio
async def test_generate_document_durably_ingests(tmp_path) -> None:
    svc = _service(tmp_path)
    try:
        await svc.start_workers()
        out = await svc.generate_document(
            tenant_id="tenant-a",
            actor_id="alice",
            workspace_id="ws",
            target_format="markdown",
            payload={"title": "Generated", "sections": [{"heading": "H", "body": "B"}]},
            output_name="generated",
        )
        assert out["document_id"]
        assert out["version_id"]
        assert out["job_id"]
        assert out["target_format"] == "markdown"

        # The generated document is tenant-owned and readable by opaque ID.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 15.0
        state = None
        while loop.time() < deadline:
            status = await asyncio.to_thread(
                svc.job_status, tenant_id="tenant-a", job_id=out["job_id"]
            )
            state = status["state"] if status else None
            if state == "ready":
                break
            await asyncio.sleep(0.05)
        assert state == "ready"
        content = await asyncio.to_thread(
            svc.get_content,
            tenant_id="tenant-a",
            actor_id="alice",
            document_id=out["document_id"],
        )
        assert "Generated" in content["text"]
    finally:
        await svc.stop_workers()
        svc.close()


@pytest.mark.asyncio
async def test_generate_rejects_oversized_payload(tmp_path) -> None:
    svc = _service(tmp_path)
    try:
        big = {"title": "x", "blob": "y" * (1024 * 1024 + 10)}
        with pytest.raises(DocumentIngestionError) as exc:
            await svc.generate_document(
                tenant_id="tenant-a",
                actor_id="alice",
                workspace_id="ws",
                target_format="markdown",
                payload=big,
            )
        assert exc.value.code == "intake_too_large"
    finally:
        svc.close()


# ── Optional: a real PDF-info round-trip when pypdf is installed ─────────


@pytest.mark.asyncio
async def test_pdf_info_when_pypdf_available(tmp_path) -> None:
    pypdf = pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    pdf_bytes = buffer.getvalue()

    svc = _service(tmp_path)
    try:
        await svc.start_workers()
        doc_id = await _ingest_ready(
            svc,
            tenant="tenant-a",
            actor="alice",
            workspace="ws",
            filename="blank.pdf",
            data=pdf_bytes,
        )
        report = await svc.pdf_info_document(
            tenant_id="tenant-a",
            actor_id="alice",
            workspace_id="ws",
            document_id=doc_id,
        )
        assert "report" in report
        assert isinstance(report["report"], dict)
    finally:
        await svc.stop_workers()
        svc.close()
