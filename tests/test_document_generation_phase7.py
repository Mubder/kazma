"""Phase 7 generation, conversion, mutation, and secure-redaction contracts."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.models import DocumentResult
from kazma_core.documents.mutation import get_mutation_registry
from kazma_core.documents.renderers import (
    RendererPlugin,
    RendererReadiness,
    RendererRegistry,
)
from kazma_core.documents.repository import DocumentRepository
from kazma_core.documents.sandbox import SandboxResult
from kazma_core.documents.service import DocumentService
from kazma_core.documents.storage import ContentAddressedStorage


def _config(tmp_path: Path, **values: object) -> DocumentConfig:
    return replace(
        DocumentConfig(
            storage_root=tmp_path / "store",
            ocr_enabled=False,
            worker_timeout_seconds=30,
        ),
        **values,
    )


def test_renderer_registry_reports_present_and_missing_without_importing() -> None:
    registry = RendererRegistry(
        (
            RendererPlugin(
                "stdlib",
                "1",
                ("generate:markdown",),
                ("markdown",),
                ("unicode",),
                dependencies=("json",),
            ),
            RendererPlugin(
                "missing",
                "1",
                ("generate:missing",),
                ("missing",),
                (),
                dependencies=("kazma_phase7_dependency_does_not_exist",),
            ),
        )
    )
    capabilities = {item.renderer_id: item for item in registry.capabilities()}
    assert capabilities["stdlib"].readiness is RendererReadiness.READY
    assert capabilities["missing"].readiness is RendererReadiness.UNAVAILABLE
    assert capabilities["missing"].dependencies["kazma_phase7_dependency_does_not_exist"] is None


@pytest.mark.asyncio
async def test_generation_manifest_roundtrip_arabic_and_collision_safe_export(
    tmp_path: Path,
) -> None:
    service = DocumentService(config=_config(tmp_path))
    payload = {
        "title": "English العربية",
        "sections": [{"heading": "Intro مقدمة", "body": "Hello مرحبا"}],
        "citations": ["مرجع Reference"],
    }
    first = await service.generate(
        "markdown",
        payload,
        tenant_id="tenant",
        workspace_id="workspace",
        actor_id="actor",
        output_name="same",
        export_dir=tmp_path / "exports",
    )
    second = await service.generate(
        "markdown",
        payload,
        tenant_id="tenant",
        workspace_id="workspace",
        actor_id="actor",
        output_name="same",
        export_dir=tmp_path / "exports",
    )

    assert first.ok and second.ok
    assert first.data is not None and second.data is not None
    assert first.data.export_path != second.data.export_path
    assert "العربية" in first.data.export_path.read_text(encoding="utf-8")
    manifest = first.data.manifest.to_dict()
    assert manifest["renderer"] == "markdown"
    assert manifest["output"]["sha256"] == hashlib.sha256(
        first.data.storage_path.read_bytes()
    ).hexdigest()
    assert manifest["output"]["size"] > 0
    assert manifest["operation"] == "generate:markdown"
    assert manifest["provenance"]["workspace_id"] == "workspace"


@pytest.mark.asyncio
async def test_durable_scope_persists_immutable_artifact_manifest(tmp_path: Path) -> None:
    repository = DocumentRepository(tmp_path / "documents.db", tenant_quota_bytes=10_000_000)
    try:
        document = repository.create_document(
            tenant_id="tenant", owner_id="actor", title="Source"
        )
        source_hash = hashlib.sha256(b"source").hexdigest()
        source_blob = repository.register_blob(
            tenant_id="tenant",
            sha256=source_hash,
            byte_size=6,
            storage_kind="originals",
        )
        version = repository.create_version(
            tenant_id="tenant",
            document_id=document.id,
            actor_id="actor",
            source_blob_id=source_blob.id,
            source_sha256=source_hash,
            original_filename="source.md",
            mime_type="text/markdown",
        )
        config = _config(tmp_path)
        service = DocumentService(
            config=config,
            repository=repository,
            storage=ContentAddressedStorage(config.storage_root),
        )
        result = await service.generate(
            "markdown",
            {"title": "Durable", "sections": [{"heading": "", "body": "Body"}]},
            tenant_id="tenant",
            workspace_id="workspace",
            actor_id="actor",
            document_id=document.id,
            version_id=version.id,
        )
        assert result.ok and result.artifact_id is not None
        record = repository.get_artifact(
            tenant_id="tenant", artifact_id=result.artifact_id, actor_id="actor"
        )
        assert record is not None
        assert record.metadata["operation"] == "generate:markdown"
        assert record.metadata["output"]["sha256"] == result.data.manifest.output_sha256
        assert record.metadata["document_id"] == str(document.id)
        assert record.metadata["version_id"] == str(version.id)
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_external_render_resources_denied_before_optional_engine_probe(
    tmp_path: Path,
) -> None:
    source = tmp_path / "hostile.html"
    source.write_text(
        '<html><link href="https://example.invalid/x.css">'
        '<style>@import "file:///secret";</style></html>',
        encoding="utf-8",
    )
    result = await DocumentService(config=_config(tmp_path)).convert(
        source,
        "pdf",
        approved_path=source,
        tenant_id="tenant",
        workspace_id="workspace",
        actor_id="actor",
    )
    assert not result.ok
    assert result.code == "external_resource_denied"
    assert not list((tmp_path / "store" / "artifacts").glob("**/*"))


@pytest.mark.asyncio
async def test_worker_timeout_leaves_no_artifact_or_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timed_out(request):
        return SandboxResult(
            command=request.command,
            returncode=-1,
            stdout=b"",
            stderr=b"",
            duration_seconds=31,
            timed_out=True,
            output_limit_exceeded=False,
            resource_limits_enforced=True,
            resource_limit_degraded_reason=None,
        )

    monkeypatch.setattr(
        "kazma_core.documents.operations.run_isolated_subprocess", timed_out
    )
    result = await DocumentService(config=_config(tmp_path)).generate(
        "markdown",
        {"title": "Crash", "sections": []},
        tenant_id="tenant",
        workspace_id="workspace",
        actor_id="actor",
        export_dir=tmp_path / "exports",
    )
    assert not result.ok
    assert result.code == "document_worker_timeout"
    assert not (tmp_path / "exports").exists()
    assert not list((tmp_path / "store" / "artifacts").glob("**/*"))


@pytest.mark.asyncio
async def test_checksum_mismatch_is_never_promoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def corrupt(request):
        output = request.work_dir / "output.md"
        output.write_text("# valid bytes", encoding="utf-8")
        response = {
            "protocol_version": 1,
            "ok": True,
            "code": "ok",
            "message": "rendered",
            "renderer": "markdown",
            "renderer_version": "1",
            "source_sha256": None,
            "output_name": "output.md",
            "output_extension": "md",
            "output_mime_type": "text/markdown",
            "output_size": output.stat().st_size,
            "output_sha256": "0" * 64,
            "warnings": [],
        }
        (request.work_dir / "result.json").write_text(
            json.dumps(response), encoding="utf-8"
        )
        return SandboxResult(
            command=request.command,
            returncode=0,
            stdout=b"",
            stderr=b"",
            duration_seconds=0.1,
            timed_out=False,
            output_limit_exceeded=False,
            resource_limits_enforced=True,
            resource_limit_degraded_reason=None,
        )

    monkeypatch.setattr(
        "kazma_core.documents.operations.run_isolated_subprocess", corrupt
    )
    result = await DocumentService(config=_config(tmp_path)).generate(
        "markdown",
        {"title": "Mismatch", "sections": []},
        tenant_id="tenant",
        workspace_id="workspace",
        actor_id="actor",
        export_dir=tmp_path / "exports",
    )
    assert not result.ok
    assert result.code == "worker_checksum_mismatch"
    assert not (tmp_path / "exports").exists()


@pytest.mark.asyncio
async def test_pdf_merge_split_bounds_and_roundtrip(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    sources = []
    for index in range(3):
        path = tmp_path / f"{index}.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with path.open("wb") as handle:
            writer.write(handle)
        writer.close()
        sources.append(path)
    service = DocumentService(config=_config(tmp_path, max_pages=2))
    merged = await service.pdf_merge(
        tuple(sources[:2]),
        approved_paths=tuple(sources[:2]),
        tenant_id="tenant",
        workspace_id="workspace",
        actor_id="actor",
        export_dir=tmp_path / "exports",
    )
    assert merged.ok and merged.data is not None
    assert merged.data.manifest.provenance["report"]["page_count"] == 2
    over_limit = await service.pdf_merge(
        tuple(sources),
        approved_paths=tuple(sources),
        tenant_id="tenant",
        workspace_id="workspace",
        actor_id="actor",
    )
    assert not over_limit.ok
    assert over_limit.code == "document_limit_exceeded"
    invalid_split = await service.pdf_split(
        sources[0],
        approved_path=sources[0],
        tenant_id="tenant",
        workspace_id="workspace",
        actor_id="actor",
        start_page=2,
        end_page=3,
    )
    assert not invalid_split.ok
    assert invalid_split.code == "invalid_page_range"


@pytest.mark.asyncio
async def test_redaction_fails_closed_when_verified_engine_unavailable(
    tmp_path: Path,
) -> None:
    if get_mutation_registry().resolve("pdf:redact").available:
        pytest.skip("verified real redaction engine is available")
    source = tmp_path / "one.pdf"
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with source.open("wb") as handle:
        writer.write(handle)
    writer.close()
    result = await DocumentService(config=_config(tmp_path)).redact(
        source,
        ["do-not-log-this"],
        approved_path=source,
        tenant_id="tenant",
        workspace_id="workspace",
        actor_id="actor",
        export_dir=tmp_path / "exports",
    )
    assert not result.ok
    assert result.code == "document_engine_unavailable"
    assert "do-not-log-this" not in result.message
    assert not (tmp_path / "exports").exists()


@pytest.mark.asyncio
async def test_redaction_verification_failure_never_promotes_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    source = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with source.open("wb") as handle:
        writer.write(handle)
    writer.close()
    registry = RendererRegistry(
        (
            RendererPlugin(
                "fake-redactor",
                "1",
                ("pdf:redact",),
                ("pdf",),
                ("verification",),
            ),
        )
    )

    def verification_failed(request):
        (request.work_dir / "output.pdf").write_bytes(source.read_bytes())
        (request.work_dir / "result.json").write_text(
            json.dumps(
                {
                    "protocol_version": 1,
                    "ok": False,
                    "code": "redaction_verification_failed",
                    "message": "Secure redaction verification was unavailable",
                }
            ),
            encoding="utf-8",
        )
        return SandboxResult(
            command=request.command,
            returncode=0,
            stdout=b"",
            stderr=b"",
            duration_seconds=0.1,
            timed_out=False,
            output_limit_exceeded=False,
            resource_limits_enforced=True,
            resource_limit_degraded_reason=None,
        )

    monkeypatch.setattr(
        "kazma_core.documents.operations.run_isolated_subprocess",
        verification_failed,
    )
    result = await DocumentService(
        config=_config(tmp_path), mutation_registry=registry
    ).redact(
        source,
        ["secret"],
        approved_path=source,
        tenant_id="tenant",
        workspace_id="workspace",
        actor_id="actor",
        export_dir=tmp_path / "exports",
    )
    assert not result.ok
    assert result.code == "redaction_verification_failed"
    assert not (tmp_path / "exports").exists()
    assert not list((tmp_path / "store" / "artifacts").glob("**/*"))


@pytest.mark.asyncio
async def test_secure_redaction_removes_text_bytes_and_recoverable_structures(
    tmp_path: Path,
) -> None:
    if not get_mutation_registry().resolve("pdf:redact").available:
        pytest.skip("PyMuPDF/Pillow verified redaction engine is unavailable")
    import fitz

    secret = "KazmaUniqueSecretPhrase"
    source = tmp_path / "sensitive.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), f"Public text {secret}")
    document.set_metadata({"title": secret})
    document.save(source)
    document.close()
    result = await DocumentService(config=_config(tmp_path)).redact(
        source,
        [secret],
        approved_path=source,
        tenant_id="tenant",
        workspace_id="workspace",
        actor_id="actor",
        export_dir=tmp_path / "exports",
    )
    assert result.ok and result.data is not None
    report = result.data.manifest.provenance["report"]
    assert all(report["checks"].values())
    assert report["matches_removed"] >= 1
    payload = result.data.export_path.read_bytes()
    assert secret.lower().encode() not in payload.lower()
    rebuilt = fitz.open(result.data.export_path)
    assert secret.casefold() not in "".join(page.get_text() for page in rebuilt).casefold()
    assert not any(rebuilt.metadata.values())
    assert rebuilt.embfile_count() == 0
    assert all(page.first_annot is None and page.first_widget is None for page in rebuilt)
    rebuilt.close()


@pytest.mark.asyncio
async def test_redaction_refuses_mixed_text_and_image_content(
    tmp_path: Path,
) -> None:
    if not get_mutation_registry().resolve("pdf:redact").available:
        pytest.skip("PyMuPDF/Pillow verified redaction engine is unavailable")
    import fitz
    from PIL import Image

    secret = "KazmaMixedContentSecret"
    image_path = tmp_path / "content.png"
    Image.new("RGB", (40, 20), "white").save(image_path)
    source = tmp_path / "mixed.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), secret)
    page.insert_image(fitz.Rect(72, 100, 112, 120), filename=str(image_path))
    document.save(source)
    document.close()

    result = await DocumentService(config=_config(tmp_path)).redact(
        source,
        [secret],
        approved_path=source,
        tenant_id="tenant",
        workspace_id="workspace",
        actor_id="actor",
        export_dir=tmp_path / "exports",
    )

    assert not result.ok
    assert result.code == "redaction_source_not_verifiable"
    assert not (tmp_path / "exports").exists()


@pytest.mark.asyncio
async def test_legacy_generator_delegates_without_heavy_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kazma_skills.native.document_generator import tools
    from kazma_skills.native.document_processor import tools as processor_tools

    for module in (tools, processor_tools):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not imported & {
            "reportlab",
            "docx",
            "openpyxl",
            "pptx",
            "weasyprint",
            "pypdf",
            "fitz",
        }
    artifact = Mock(export_path=tmp_path / "result.pdf")
    service = Mock()
    service.generate = AsyncMock(
        return_value=DocumentResult(
            ok=True,
            code="artifact_ready",
            message="ready",
            data=artifact,
        )
    )
    monkeypatch.setattr(tools, "DocumentService", Mock(return_value=service))
    output = await tools.generate_pdf("Title", [{"heading": "H", "body": "B"}])
    assert "generated successfully" in output
    service.generate.assert_awaited_once()


def test_legacy_manifests_describe_isolation_and_verified_redaction() -> None:
    from kazma_skills.native.document_generator import tools as generator_tools
    from kazma_skills.native.document_processor import tools as processor_tools

    generator = Path(generator_tools.__file__).with_name("skill_manifest.yaml").read_text(
        encoding="utf-8"
    )
    processor = Path(processor_tools.__file__).with_name("skill_manifest.yaml").read_text(
        encoding="utf-8"
    )
    assert "isolated renderer" in generator
    assert "round-trip validate" in generator
    assert "Secure rasterize-redact-rebuild" in processor
    assert "temporarily unavailable" not in processor.lower()
    assert "visual masking" not in processor.lower()
