"""Phase 4 document parser consolidation and compatibility regression tests."""

from __future__ import annotations

import importlib
import logging
import shutil
import stat
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.errors import (
    DocumentEncryptedError,
    DocumentFormatError,
    DocumentLimitError,
    DocumentSandboxError,
    DocumentSecurityError,
)
from kazma_core.documents.parsers.common import ParseContext, sha256_path
from kazma_core.documents.registry import (
    ParserPlugin,
    ParserReadiness,
    ParserRegistry,
)
from kazma_core.documents.sandbox import SandboxResult
from kazma_core.documents.service import DocumentService
from kazma_core.documents.sniff import sniff_document


class _NoopParser:
    def parse(self, path: Path, context: ParseContext):  # pragma: no cover - probe only
        raise AssertionError


class _ToolResult:
    def __init__(self, value: str) -> None:
        self.value = value

    def as_tool_output(self) -> str:
        return self.value


@pytest.fixture
def document_config(tmp_path: Path) -> DocumentConfig:
    return DocumentConfig(
        storage_root=tmp_path / "document-store",
        worker_timeout_seconds=30,
    )


def _context(path: Path, config: DocumentConfig, parser_id: str, mime: str) -> ParseContext:
    return ParseContext(
        config=config,
        source_sha256=sha256_path(path),
        mime_type=mime,
        extension=path.suffix.lower(),
        parser_id=parser_id,
        parser_version="1",
    )


def test_registry_marks_healthy_native_ready_and_missing_dependency_unavailable() -> None:
    plugins = (
        ParserPlugin(
            "native",
            "1",
            ("text/plain",),
            (".txt",),
            ("text",),
            ("max_output_chars_total",),
            True,
            _NoopParser,
        ),
        ParserPlugin(
            "missing",
            "1",
            ("application/x-missing",),
            (".missing",),
            (),
            (),
            True,
            _NoopParser,
            dependencies=("kazma_dependency_that_does_not_exist",),
        ),
    )
    registry = ParserRegistry(plugins)
    assert registry.capability("native").readiness is ParserReadiness.READY
    assert registry.capability("missing").readiness is ParserReadiness.UNAVAILABLE


def test_legacy_office_readiness_tracks_libreoffice_probe() -> None:
    registry = ParserRegistry()
    expected = bool(shutil.which("soffice") or shutil.which("libreoffice"))
    for parser_id in ("legacy-doc", "legacy-xls", "legacy-ppt"):
        assert registry.capability(parser_id).available is expected


def test_mime_mismatch_and_unknown_format_reject(
    tmp_path: Path, document_config: DocumentConfig
) -> None:
    mismatch = tmp_path / "wrong.pdf"
    mismatch.write_text("plain text", encoding="utf-8")
    with pytest.raises(DocumentFormatError, match="does not match"):
        sniff_document(mismatch, document_config)

    unknown = tmp_path / "sample.bin"
    unknown.write_bytes(b"\x00\x01\x02")
    with pytest.raises(DocumentFormatError, match="Unsupported document extension"):
        sniff_document(unknown, document_config)


def _write_ooxml(
    path: Path,
    members: dict[str, bytes],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
) -> None:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        for name, value in members.items():
            archive.writestr(name, value)


def test_ooxml_preflight_rejects_traversal_bomb_and_macros(
    tmp_path: Path, document_config: DocumentConfig
) -> None:
    traversal = tmp_path / "traversal.docx"
    _write_ooxml(traversal, {"word/document.xml": b"<w/>", "../escape": b"x"})
    with pytest.raises(DocumentSecurityError, match="unsafe member path"):
        sniff_document(traversal, document_config)

    bomb = tmp_path / "bomb.docx"
    _write_ooxml(bomb, {"word/document.xml": b"x" * 100_000})
    with pytest.raises(DocumentLimitError, match="compression ratio"):
        sniff_document(bomb, replace(document_config, max_compression_ratio=2))

    members = tmp_path / "members.docx"
    _write_ooxml(
        members,
        {"word/document.xml": b"<w/>", "word/extra.xml": b"<w/>"},
    )
    with pytest.raises(DocumentLimitError, match="too many members"):
        sniff_document(members, replace(document_config, max_archive_members=2))

    macro = tmp_path / "macro.docx"
    _write_ooxml(
        macro,
        {"word/document.xml": b"<w/>", "word/vbaProject.bin": b"macro"},
    )
    with pytest.raises(DocumentSecurityError, match="Macro-enabled"):
        sniff_document(macro, document_config)

    unsafe_xml = tmp_path / "unsafe-xml.docx"
    _write_ooxml(
        unsafe_xml,
        {"word/document.xml": b"<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]>"},
    )
    with pytest.raises(DocumentSecurityError, match="unsafe XML"):
        sniff_document(unsafe_xml, document_config)

    symlink = tmp_path / "symlink.docx"
    with zipfile.ZipFile(symlink, "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", b"<w/>")
        info = zipfile.ZipInfo("word/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "document.xml")
    with pytest.raises(DocumentSecurityError, match="symbolic link"):
        sniff_document(symlink, document_config)


@pytest.mark.parametrize(
    "marker",
    (
        b"<!DOCTYPE unsafe>",
        "<!ENTITY unsafe 'value'>".encode("utf-16-le"),
    ),
)
def test_ooxml_stream_scans_padded_xml_across_chunks(
    tmp_path: Path,
    document_config: DocumentConfig,
    marker: bytes,
) -> None:
    path = tmp_path / "padded.docx"
    payload = b" " * (64 * 1024 - 5) + marker + b"<w/>"
    _write_ooxml(
        path,
        {"word/document.xml": payload},
        compression=zipfile.ZIP_STORED,
    )

    with pytest.raises(DocumentSecurityError, match="unsafe XML"):
        sniff_document(path, document_config)


def test_ooxml_stream_enforces_actual_expansion_and_crc(
    tmp_path: Path,
    document_config: DocumentConfig,
) -> None:
    oversized = tmp_path / "actual-size.docx"
    _write_ooxml(
        oversized,
        {"word/document.xml": b"<w>" + b"x" * 128 + b"</w>"},
        compression=zipfile.ZIP_STORED,
    )
    with pytest.raises(DocumentLimitError, match="expanded size"):
        sniff_document(
            oversized,
            replace(document_config, max_expanded_bytes=64),
        )

    corrupt = tmp_path / "bad-crc.docx"
    _write_ooxml(
        corrupt,
        {"word/document.xml": b"<w>integrity</w>"},
        compression=zipfile.ZIP_STORED,
    )
    with zipfile.ZipFile(corrupt) as archive:
        info = archive.getinfo("word/document.xml")
        data_offset = (
            info.header_offset
            + 30
            + len(info.filename.encode("utf-8"))
            + len(info.extra)
        )
    payload = bytearray(corrupt.read_bytes())
    payload[data_offset + 3] ^= 0x01
    corrupt.write_bytes(payload)
    with pytest.raises(DocumentFormatError, match="integrity validation"):
        sniff_document(corrupt, document_config)


def test_ooxml_rejects_falsified_central_directory_size(
    tmp_path: Path,
    document_config: DocumentConfig,
) -> None:
    path = tmp_path / "false-size.docx"
    _write_ooxml(
        path,
        {"word/document.xml": b"<w>directory mismatch</w>"},
        compression=zipfile.ZIP_STORED,
    )
    payload = bytearray(path.read_bytes())
    signature = b"PK\x01\x02"
    position = payload.find(signature)
    while position >= 0:
        name_length = int.from_bytes(payload[position + 28 : position + 30], "little")
        name = bytes(payload[position + 46 : position + 46 + name_length])
        if name == b"word/document.xml":
            payload[position + 24 : position + 28] = (1).to_bytes(4, "little")
            break
        position = payload.find(signature, position + 4)
    else:  # pragma: no cover - Python's ZIP writer always emits this record
        raise AssertionError("central directory member not found")
    path.write_bytes(payload)

    with pytest.raises(DocumentFormatError):
        sniff_document(path, document_config)


def test_degraded_pdf_capability_does_not_advertise_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.documents import parsers

    real_import = parsers.importlib.import_module

    def import_without_pdfplumber(name: str):
        if name == "pdfplumber":
            raise ImportError
        if name == "pypdf":
            return object()
        return real_import(name)

    monkeypatch.setattr(parsers.importlib, "import_module", import_without_pdfplumber)
    pdf_plugin = next(
        plugin for plugin in parsers.builtin_plugins() if plugin.parser_id == "pdf"
    )
    capability = ParserRegistry((pdf_plugin,)).capability("pdf")

    assert capability.readiness is ParserReadiness.DEGRADED
    assert capability.features == ("pages", "text")
    assert "tables" not in capability.features


def test_encrypted_pdf_rejected_before_parser(
    tmp_path: Path, document_config: DocumentConfig
) -> None:
    path = tmp_path / "encrypted.pdf"
    path.write_bytes(b"%PDF-1.7\n1 0 obj\n<< /Encrypt 2 0 R >>\n%%EOF")
    with pytest.raises(DocumentEncryptedError):
        sniff_document(path, document_config)


def test_pdf_zip_polyglot_rejected(
    tmp_path: Path, document_config: DocumentConfig
) -> None:
    path = tmp_path / "polyglot.pdf"
    path.write_bytes(b"%PDF-1.7\n%%EOF\nPK\x03\x04payload")
    with pytest.raises(DocumentSecurityError, match="polyglot"):
        sniff_document(path, document_config)


@pytest.mark.parametrize(
    ("name", "content", "parser_id", "mime"),
    [
        ("sample.txt", "alpha\n\nbeta", "text", "text/plain"),
        ("sample.md", "# Heading\n\nbody", "text", "text/markdown"),
        ("sample.log", "INFO safe", "text", "text/x-log"),
        ("sample.json", '{"b": 2, "a": 1}', "json", "application/json"),
        ("sample.csv", "a,b\n1,2\n", "csv", "text/csv"),
        ("sample.tsv", "a\tb\n1\t2\n", "tsv", "text/tab-separated-values"),
        ("sample.html", "<html><body><h1>Hello</h1><p>world</p></body></html>", "html", "text/html"),
        ("sample.rtf", r"{\rtf1\ansi Hello \b world\b0}", "rtf", "application/rtf"),
    ],
)
def test_native_text_parsers_emit_deterministic_ir(
    tmp_path: Path,
    document_config: DocumentConfig,
    name: str,
    content: str,
    parser_id: str,
    mime: str,
) -> None:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    plugin, _ = ParserRegistry().resolve(mime_type=mime, extension=path.suffix)
    first = plugin.factory().parse(path, _context(path, document_config, parser_id, mime))
    second = plugin.factory().parse(path, _context(path, document_config, parser_id, mime))
    assert first.to_json() == second.to_json()
    assert first.metadata["source_sha256"] == sha256_path(path)
    assert first.pages


def test_csv_bounds_are_enforced(tmp_path: Path, document_config: DocumentConfig) -> None:
    path = tmp_path / "rows.csv"
    path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    plugin, _ = ParserRegistry().resolve(mime_type="text/csv", extension=".csv")
    ir = plugin.factory().parse(
        path,
        _context(
            path,
            replace(document_config, max_rows_per_sheet=2),
            "csv",
            "text/csv",
        ),
    )
    assert "Rows truncated" in str(ir.metadata["warnings"])
    with pytest.raises(DocumentLimitError, match="cell count"):
        plugin.factory().parse(
            path,
            _context(
                path,
                replace(document_config, max_cells=2),
                "csv",
                "text/csv",
            ),
        )


@pytest.mark.asyncio
async def test_service_subprocess_paging_and_single_fence(
    tmp_path: Path, document_config: DocumentConfig
) -> None:
    path = tmp_path / "page.txt"
    path.write_text("abcdefghij\n\nklmnopqrst", encoding="utf-8")
    result = await DocumentService(config=document_config).read_transient(
        path,
        approved_path=path.resolve(),
        max_chars=12,
    )
    output = result.as_tool_output()
    assert result.continuation["has_more"] is True
    assert result.continuation["next_offset"] == 12
    assert output.count('<kazma:data source="document" untrusted="true">') == 1
    assert "next_offset=12" in output


def test_service_rejects_bad_worker_checksum(document_config: DocumentConfig) -> None:
    response = {
        "protocol_version": 1,
        "ok": True,
        "code": "ok",
        "message": "parsed",
        "source_sha256": "wrong",
        "ir_sha256": "wrong",
        "ir": {},
    }
    with pytest.raises(DocumentSandboxError, match="source checksum"):
        DocumentService(config=document_config)._validate_response(response, "expected")


def test_service_surfaces_sandbox_timeout(
    tmp_path: Path,
    document_config: DocumentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "timeout.txt"
    path.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(
        "kazma_core.documents.service.run_isolated_subprocess",
        lambda request: SandboxResult(
            command=request.command,
            returncode=1,
            stdout=b"",
            stderr=b"",
            duration_seconds=1,
            timed_out=True,
            output_limit_exceeded=False,
            resource_limits_enforced=False,
            resource_limit_degraded_reason=None,
        ),
    )
    with pytest.raises(DocumentSandboxError, match="time limit"):
        DocumentService(config=document_config).read_transient_sync(
            path, approved_path=path
        )


def test_service_warns_when_resource_limits_degrade(
    tmp_path: Path,
    document_config: DocumentConfig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "degraded.txt"
    path.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(
        "kazma_core.documents.service.run_isolated_subprocess",
        lambda request: SandboxResult(
            command=request.command,
            returncode=1,
            stdout=b"",
            stderr=b"",
            duration_seconds=0.1,
            timed_out=False,
            output_limit_exceeded=False,
            resource_limits_enforced=False,
            resource_limit_degraded_reason="CPU quota unavailable",
        ),
    )

    with caplog.at_level(logging.WARNING), pytest.raises(
        DocumentSandboxError, match="produced no result"
    ):
        DocumentService(config=document_config).read_transient_sync(
            path, approved_path=path
        )
    assert "resource limits were partially degraded" in caplog.text
    assert "CPU quota unavailable" in caplog.text


@pytest.mark.asyncio
async def test_document_processor_and_crawler_delegate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "delegate.txt"
    path.write_text("hello", encoding="utf-8")

    from kazma_skills.native.advanced_web_crawler import tools as crawler
    from kazma_skills.native.document_processor import tools as processor

    service = Mock()
    service.read_transient = AsyncMock(return_value=_ToolResult("delegated"))
    monkeypatch.setattr(processor, "DocumentService", Mock(return_value=service))
    monkeypatch.setattr(processor, "_workspace_scope_error", Mock(return_value=None))
    assert await processor.read_document(str(path)) == "delegated"
    service.read_transient.assert_awaited_once()

    service.read_transient.reset_mock()
    monkeypatch.setattr(crawler, "DocumentService", Mock(return_value=service))
    monkeypatch.setattr(crawler, "_workspace_scope_error", Mock(return_value=None))
    assert await crawler.parse_document(str(path)) == "delegated"
    service.read_transient.assert_awaited_once()


def test_attachment_excerpt_delegates_without_double_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kazma_gateway.agent_handler import attachments

    path = tmp_path / "delegate.txt"
    path.write_text("hello", encoding="utf-8")
    service = Mock()
    service.read_transient_sync.return_value = _ToolResult("delegated")
    monkeypatch.setattr(attachments, "DocumentService", Mock(return_value=service))
    assert attachments._try_parse_attachment(str(path)) == "delegated"
    assert service.read_transient_sync.call_args.kwargs["fence"] is False


def test_available_optional_ooxml_and_pdf_parsers(
    tmp_path: Path, document_config: DocumentConfig
) -> None:
    registry = ParserRegistry()

    if registry.capability("docx").available:
        from docx import Document

        path = tmp_path / "minimal.docx"
        doc = Document()
        doc.add_paragraph("docx text")
        doc.save(path)
        sniffed = sniff_document(path, document_config)
        plugin, _ = registry.resolve(
            mime_type=sniffed.mime_type, extension=sniffed.extension
        )
        assert "docx text" in plugin.factory().parse(
            path, _context(path, document_config, "docx", sniffed.mime_type)
        ).to_json()

    if registry.capability("xlsx").available:
        from openpyxl import Workbook

        path = tmp_path / "minimal.xlsx"
        workbook = Workbook()
        workbook.active.append(["xlsx text"])
        workbook.save(path)
        sniffed = sniff_document(path, document_config)
        plugin, _ = registry.resolve(
            mime_type=sniffed.mime_type, extension=sniffed.extension
        )
        assert "xlsx text" in plugin.factory().parse(
            path, _context(path, document_config, "xlsx", sniffed.mime_type)
        ).to_json()

    if registry.capability("pptx").available:
        from pptx import Presentation

        path = tmp_path / "minimal.pptx"
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = "pptx text"
        presentation.save(path)
        sniffed = sniff_document(path, document_config)
        plugin, _ = registry.resolve(
            mime_type=sniffed.mime_type, extension=sniffed.extension
        )
        assert "pptx text" in plugin.factory().parse(
            path, _context(path, document_config, "pptx", sniffed.mime_type)
        ).to_json()

    if registry.capability("pdf").available:
        from pypdf import PdfWriter

        path = tmp_path / "minimal.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with path.open("wb") as stream:
            writer.write(stream)
        sniffed = sniff_document(path, document_config)
        plugin, _ = registry.resolve(
            mime_type=sniffed.mime_type, extension=sniffed.extension
        )
        assert plugin.factory().parse(
            path, _context(path, document_config, "pdf", sniffed.mime_type)
        ).pages


def test_native_skill_loading_has_no_document_import_cycle() -> None:
    for module in (
        "kazma_core.documents",
        "kazma_skills.native.document_processor.tools",
        "kazma_skills.native.advanced_web_crawler.tools",
        "kazma_gateway.agent_handler.attachments",
    ):
        assert importlib.import_module(module)
