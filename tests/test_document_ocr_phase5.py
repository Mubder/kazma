"""Phase 5 multilingual OCR quality-routing and isolation tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.errors import (
    DocumentFormatError,
    DocumentLimitError,
    DocumentOcrError,
    DocumentOcrUnavailableError,
)
from kazma_core.documents.models import (
    BlockType,
    DocumentBlock,
    DocumentIR,
    DocumentPage,
    Provenance,
    new_document_id,
    new_version_id,
)
from kazma_core.documents.ocr import (
    OcrCapability,
    OcrPageResult,
    OcrReadiness,
    TesseractProvider,
    apply_ocr,
    get_ocr_health,
    merge_page_content,
    select_language,
)
from kazma_core.documents.ocr.tesseract import _parse_tsv
from kazma_core.documents.parsers.common import ParseContext, sha256_path
from kazma_core.documents.parsers.image import ImageParser
from kazma_core.documents.quality import assess_document_quality, assess_page_quality
from kazma_core.documents.sandbox import SandboxResult
from kazma_core.documents.service import DocumentService
from kazma_core.documents.sniff import sniff_document


@pytest.fixture
def config(tmp_path: Path) -> DocumentConfig:
    return DocumentConfig(
        storage_root=tmp_path / "documents",
        ocr_languages=("eng", "ara"),
        max_pixels_per_image=1_000_000,
        ocr_min_text_chars_per_page=40,
    )


def _block(page: int, text: str, *, confidence: float | None = None) -> DocumentBlock:
    return DocumentBlock(
        block_id=f"p{page}-b1",
        block_type=BlockType.TEXT,
        text=text,
        confidence=confidence,
    )


def _document(
    pages: tuple[DocumentPage, ...],
    *,
    mime: str = "application/pdf",
) -> DocumentIR:
    return DocumentIR(
        document_id=new_document_id(),
        version_id=new_version_id(),
        pages=pages,
        provenance=Provenance(source="fixture", parser="fixture"),
        metadata={"source_mime": mime, "warnings": []},
    )


def test_quality_routes_healthy_scanned_and_mixed_pages() -> None:
    healthy = DocumentPage(
        1,
        (_block(1, "Healthy native text " * 8),),
        width=612,
        height=792,
    )
    scanned = DocumentPage(
        2,
        (),
        width=612,
        height=792,
        metadata={"image_count": 1},
    )
    qualities = assess_document_quality(_document((healthy, scanned)))
    assert qualities[0].needs_ocr is False
    assert qualities[1].needs_ocr is True
    assert "image_or_scanned_page" in qualities[1].reasons


def test_language_selection_override_auto_and_missing() -> None:
    installed = ("eng", "ara", "osd")
    assert select_language(
        "eng+ara", configured=("eng", "ara"), installed=installed
    ) == "eng+ara"
    assert select_language(
        "auto",
        configured=("eng", "ara"),
        installed=installed,
        native_text="Hello مرحبا",
    ) == "eng+ara"
    assert select_language(
        None,
        configured=("eng", "ara"),
        installed=installed,
        native_text="مرحبا",
    ) == "ara"
    with pytest.raises(DocumentOcrUnavailableError, match="missing"):
        select_language("ara", configured=("eng",), installed=("eng",))


def test_tsv_coordinates_confidence_and_mixed_unicode_preserved() -> None:
    payload = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth"
        "\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t20\t30\t12\t90\tHello\n"
        "5\t1\t1\t1\t1\t2\t45\t20\t35\t12\t80\tمرحبا\n"
    ).encode()
    result = _parse_tsv(
        payload,
        page_number=3,
        language="eng+ara",
        engine_version="5.4",
        dpi=200,
    )
    assert result.blocks[0].text == "Hello مرحبا"
    assert result.blocks[0].bounding_box.to_dict() == {
        "x0": 10.0,
        "x1": 80.0,
        "y0": 20.0,
        "y1": 32.0,
    }
    assert result.blocks[0].confidence == 0.85
    assert result.blocks[0].metadata["direction"] == "mixed"
    assert result.page_number == 3


def test_image_pixel_and_page_limits(tmp_path: Path, config: DocumentConfig) -> None:
    Image = pytest.importorskip("PIL.Image")
    oversized = tmp_path / "oversized.png"
    Image.new("RGB", (11, 11), "white").save(oversized)
    context = ParseContext(
        replace(config, max_pixels_per_image=100),
        sha256_path(oversized),
        "image/png",
        ".png",
        "image",
        "1",
    )
    with pytest.raises(DocumentLimitError, match="pixel"):
        ImageParser().parse(oversized, context)

    multipage = tmp_path / "multipage.tiff"
    first = Image.new("RGB", (5, 5), "white")
    second = Image.new("RGB", (5, 5), "black")
    first.save(multipage, save_all=True, append_images=[second])
    context = replace(
        context,
        config=replace(config, max_pages=1),
        source_sha256=sha256_path(multipage),
        mime_type="image/tiff",
        extension=".tiff",
    )
    with pytest.raises(DocumentLimitError, match="frame count"):
        ImageParser().parse(multipage, context)


class _FakeProvider:
    def __init__(
        self,
        *,
        confidence: float = 0.95,
        readiness: OcrReadiness = OcrReadiness.READY,
    ) -> None:
        self.confidence = confidence
        self.readiness = readiness
        self.pages: list[int] = []

    def probe(self, requested_languages: tuple[str, ...] = ()) -> OcrCapability:
        del requested_languages
        return OcrCapability(
            "fake",
            "fake",
            "1",
            ("eng", "ara"),
            ("tsv",),
            self.readiness,
            "fake unavailable" if self.readiness is OcrReadiness.UNAVAILABLE else None,
        )

    def recognize(self, image_path: Path, **kwargs: object) -> OcrPageResult:
        assert image_path.is_file()
        page_number = int(kwargs["page_number"])
        self.pages.append(page_number)
        language = str(kwargs["language"])
        return OcrPageResult(
            page_number,
            (
                DocumentBlock(
                    block_id=f"p{page_number}-ocr-1",
                    block_type=BlockType.TEXT,
                    text=f"OCR page {page_number}",
                    confidence=self.confidence,
                    metadata={
                        "ocr": True,
                        "language": language,
                        "direction": "ltr",
                    },
                ),
            ),
            language,
            "fake",
            "1",
            int(kwargs["dpi"]),
            self.confidence,
        )


class _FakeRasterizer:
    name = "fake"
    version = "1"

    def __init__(self) -> None:
        self.pages: list[int] = []

    def render_page(self, source: Path, page: DocumentPage, **kwargs: object) -> Path:
        del source
        Image = pytest.importorskip("PIL.Image")
        self.pages.append(page.page_number)
        output = Path(kwargs["work_dir"]) / f"page-{page.page_number}.png"
        Image.new("RGB", (20, 20), "white").save(output)
        return output


def test_mixed_pdf_rasterizes_only_selected_page(
    tmp_path: Path,
    config: DocumentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.documents.ocr import pipeline
    from kazma_core.documents.ocr.base import OcrComponentHealth

    rasterizer = _FakeRasterizer()
    monkeypatch.setattr(
        pipeline,
        "get_pdf_rasterizer",
        lambda: (
            rasterizer,
            OcrComponentHealth(OcrReadiness.READY, "fake", "1"),
        ),
    )
    provider = _FakeProvider()
    document = _document(
        (
            DocumentPage(1, (_block(1, "native text " * 10),), width=612, height=792),
            DocumentPage(
                2,
                (),
                width=612,
                height=792,
                metadata={"image_count": 1},
            ),
        )
    )
    source = tmp_path / "mixed.pdf"
    source.write_bytes(b"%PDF-fixture")
    result = apply_ocr(
        source,
        document,
        config,
        work_dir=tmp_path,
        provider=provider,
    )
    assert rasterizer.pages == [2]
    assert provider.pages == [2]
    assert result.pages[0].blocks[0].text.startswith("native")
    assert result.pages[1].blocks[0].metadata["ocr"] is True


def test_merge_deduplicates_and_augments_deterministically() -> None:
    page = DocumentPage(1, (_block(1, "Invoice total 42"),))
    quality = assess_page_quality(page, min_text_chars=40)
    duplicate = replace(_block(1, "Invoice total 42", confidence=0.9), block_id="ocr-1")
    assert merge_page_content(
        page, (duplicate,), quality=quality, ocr_confidence=0.9
    ) == page.blocks
    addition = replace(_block(1, "Different footer", confidence=0.9), block_id="ocr-2")
    merged = merge_page_content(
        page, (addition,), quality=quality, ocr_confidence=0.9
    )
    assert [block.text for block in merged] == ["Invoice total 42", "Different footer"]


def test_low_confidence_is_warning_not_false_success(
    tmp_path: Path,
    config: DocumentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Image = pytest.importorskip("PIL.Image")
    source = tmp_path / "scan.png"
    Image.new("RGB", (20, 20), "white").save(source)
    provider = _FakeProvider(confidence=0.4)
    result = apply_ocr(
        source,
        _document(
            (DocumentPage(1, (), metadata={"kind": "image", "image_only": True}),),
            mime="image/png",
        ),
        config,
        force=True,
        work_dir=tmp_path,
        provider=provider,
    )
    assert result.metadata["ocr"]["status"] == "completed"
    assert any("confidence is low" in warning for warning in result.metadata["warnings"])
    assert result.pages[0].blocks[0].confidence == 0.4


def test_unavailable_auto_falls_back_but_force_is_typed(
    tmp_path: Path,
    config: DocumentConfig,
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-fixture")
    document = _document((DocumentPage(1, (), metadata={"image_count": 1}),))
    provider = _FakeProvider(readiness=OcrReadiness.UNAVAILABLE)
    fallback = apply_ocr(
        source,
        document,
        config,
        work_dir=tmp_path,
        provider=provider,
    )
    assert fallback.pages[0].blocks == ()
    assert any("OCR unavailable" in item for item in fallback.metadata["warnings"])
    with pytest.raises(DocumentOcrUnavailableError, match="fake unavailable"):
        apply_ocr(
            source,
            document,
            config,
            force=True,
            work_dir=tmp_path,
            provider=provider,
        )


def test_page_limit_auto_falls_back_but_force_fails(
    tmp_path: Path,
    config: DocumentConfig,
) -> None:
    Image = pytest.importorskip("PIL.Image")
    source = tmp_path / "oversized.png"
    Image.new("RGB", (20, 20), "white").save(source)
    document = _document(
        (DocumentPage(1, (), metadata={"kind": "image", "image_only": True}),),
        mime="image/png",
    )
    limited = replace(config, max_pixels_per_image=100)

    fallback = apply_ocr(
        source,
        document,
        limited,
        work_dir=tmp_path,
        provider=_FakeProvider(),
    )
    assert fallback.pages[0].blocks == ()
    assert any("pixel limit" in item for item in fallback.metadata["warnings"])

    with pytest.raises(DocumentLimitError, match="pixel limit"):
        apply_ocr(
            source,
            document,
            limited,
            force=True,
            work_dir=tmp_path,
            provider=_FakeProvider(),
        )


def test_service_maps_explicit_ocr_unavailable_to_typed_error() -> None:
    with pytest.raises(DocumentOcrUnavailableError, match="Install ara"):
        DocumentService._validate_response(
            {
                "protocol_version": 1,
                "ok": False,
                "code": "ocr_unavailable",
                "message": "Install ara traineddata",
            },
            "unused",
        )


def test_image_magic_extension_mismatch_rejected(
    tmp_path: Path,
    config: DocumentConfig,
) -> None:
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "wrong.jpg"
    Image.new("RGB", (4, 4), "white").save(path, format="PNG")
    with pytest.raises(DocumentFormatError, match="does not match"):
        sniff_document(path, config)


@pytest.mark.asyncio
async def test_compatibility_ocr_tool_delegates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_skills.native.document_processor import tools

    path = tmp_path / "scan.png"
    path.write_bytes(b"image")
    document = _document(
        (DocumentPage(1, (_block(1, "delegated OCR"),)),),
        mime="image/png",
    )
    tool_result = Mock()
    tool_result.as_tool_output.return_value = "fenced delegated"
    service = Mock()
    service.ocr_transient = AsyncMock(return_value=document)
    service.read_ir.return_value = tool_result
    monkeypatch.setattr(tools, "DocumentService", Mock(return_value=service))
    monkeypatch.setattr(tools, "_workspace_scope_error", Mock(return_value=None))
    assert await tools.ocr_document(str(path), "eng", [1]) == "fenced delegated"
    service.ocr_transient.assert_awaited_once_with(
        path.resolve(),
        approved_path=path.resolve(),
        language="eng",
        pages=(1,),
    )


def test_tesseract_subprocess_timeout_is_typed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.documents.ocr import tesseract

    monkeypatch.setattr(
        tesseract,
        "run_isolated_subprocess",
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
    with pytest.raises(DocumentOcrError, match="time limit") as error:
        TesseractProvider("fake-tesseract")._run(
            ("--version",),
            work_dir=tmp_path,
            timeout=1,
            stdout_limit=100,
        )
    assert error.value.code == "ocr_timeout"


def test_ocr_health_contract_has_all_components(config: DocumentConfig) -> None:
    value = get_ocr_health(config.ocr_languages).to_dict()
    assert {"engine", "languages", "rasterizer", "image_parser", "readiness"} <= set(value)
    assert value["engine"]["readiness"] in {"ready", "unavailable"}


def test_real_tesseract_smoke_when_ready(tmp_path: Path) -> None:
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    provider = TesseractProvider()
    capability = provider.probe(("eng",))
    if not capability.available:
        pytest.skip(capability.reason or "Tesseract eng OCR unavailable")
    path = tmp_path / "smoke.png"
    image = Image.new("RGB", (400, 100), "white")
    ImageDraw.Draw(image).text((10, 30), "Kazma OCR smoke 123", fill="black")
    image.save(path)
    result = provider.recognize(
        path,
        page_number=1,
        language="eng",
        dpi=200,
        max_pixels=1_000_000,
        timeout_seconds=15,
        output_limit_bytes=1_000_000,
        work_dir=tmp_path,
    )
    assert result.page_number == 1
    assert result.language == "eng"
