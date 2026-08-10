"""Selective quality-routed OCR and deterministic native/OCR merging."""

from __future__ import annotations

import re
from dataclasses import replace
from difflib import SequenceMatcher
from pathlib import Path

from ..config import DocumentConfig
from ..errors import (
    DocumentFormatError,
    DocumentLimitError,
    DocumentOcrError,
    DocumentOcrUnavailableError,
)
from ..models import DocumentBlock, DocumentIR, DocumentPage
from ..quality import PageQuality, assess_document_quality
from .base import OcrComponentHealth, OcrHealth, OcrReadiness
from .raster import (
    get_image_parser_health,
    get_pdf_rasterizer,
    render_image_page,
)
from .tesseract import TesseractProvider, select_language

__all__ = ["apply_ocr", "get_ocr_health", "merge_page_content"]

_IMAGE_MIMES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/bmp",
        "image/webp",
    }
)


def _normalize_similarity(text: str) -> str:
    return re.sub(r"\W+", "", text, flags=re.UNICODE).casefold()


def merge_page_content(
    page: DocumentPage,
    ocr_blocks: tuple[DocumentBlock, ...],
    *,
    quality: PageQuality,
    ocr_confidence: float | None,
) -> tuple[DocumentBlock, ...]:
    """Replace or augment native text by stable similarity rules."""

    if not ocr_blocks:
        return page.blocks
    native_text = "\n".join(block.text for block in page.blocks if block.text)
    ocr_text = "\n".join(block.text for block in ocr_blocks if block.text)
    if not native_text.strip():
        return ocr_blocks
    native_normalized = _normalize_similarity(native_text)
    ocr_normalized = _normalize_similarity(ocr_text)
    similarity = SequenceMatcher(None, native_normalized, ocr_normalized).ratio()
    if similarity >= 0.82:
        if (ocr_confidence or 0.0) >= 0.8 and (
            quality.character_quality < 0.9
            or len(ocr_normalized) > len(native_normalized) * 1.15
        ):
            return ocr_blocks
        return page.blocks

    native_parts = {
        _normalize_similarity(block.text)
        for block in page.blocks
        if _normalize_similarity(block.text)
    }
    additions = tuple(
        block
        for block in ocr_blocks
        if _normalize_similarity(block.text)
        and not any(
            SequenceMatcher(None, _normalize_similarity(block.text), native).ratio()
            >= 0.9
            for native in native_parts
        )
    )
    return (*page.blocks, *additions)


def get_ocr_health(
    requested_languages: tuple[str, ...] = (),
) -> OcrHealth:
    provider = TesseractProvider()
    capability = provider.probe(requested_languages)
    engine = OcrComponentHealth(
        capability.readiness,
        capability.engine,
        capability.engine_version,
        capability.reason,
    )
    try:
        _, rasterizer = get_pdf_rasterizer()
    except Exception as exc:
        rasterizer = OcrComponentHealth(
            OcrReadiness.UNAVAILABLE,
            None,
            reason=f"PDF rasterizer health probe failed ({type(exc).__name__})",
        )
    image_parser = get_image_parser_health()
    if capability.readiness is OcrReadiness.UNAVAILABLE:
        readiness = OcrReadiness.UNAVAILABLE
        reason = capability.reason
    elif image_parser.readiness is OcrReadiness.UNAVAILABLE:
        readiness = OcrReadiness.UNAVAILABLE
        reason = image_parser.reason
    elif rasterizer.readiness is OcrReadiness.UNAVAILABLE:
        readiness = OcrReadiness.DEGRADED
        reason = "Image OCR is ready; PDF OCR has no healthy rasterizer"
    else:
        readiness = OcrReadiness.READY
        reason = None
    return OcrHealth(
        readiness=readiness,
        engine=engine,
        rasterizer=rasterizer,
        image_parser=image_parser,
        languages=capability.languages,
        requested_languages=requested_languages,
        reason=reason,
    )


def _annotate_quality(
    document: DocumentIR,
    qualities: tuple[PageQuality, ...],
) -> DocumentIR:
    by_page = {item.page_number: item for item in qualities}
    pages = tuple(
        replace(
            page,
            metadata={
                **dict(page.metadata),
                "extraction_quality": by_page[page.page_number].to_dict(),
            },
        )
        for page in document.pages
    )
    return replace(document, pages=pages)


def _with_warning(document: DocumentIR, warning: str) -> DocumentIR:
    warnings = [
        str(item)
        for item in document.metadata.get("warnings", [])
        if "OCR was not run" not in str(item)
    ]
    warnings.append(warning)
    return replace(
        document,
        metadata={
            **dict(document.metadata),
            "warnings": list(dict.fromkeys(warnings)),
        },
    )


def _unavailable(
    document: DocumentIR,
    message: str,
    *,
    force: bool,
) -> DocumentIR:
    if force:
        raise DocumentOcrUnavailableError(message)
    warned = _with_warning(document, f"OCR unavailable: {message}")
    return replace(
        warned,
        metadata={
            **dict(warned.metadata),
            "ocr": {"status": "unavailable", "reason": message},
        },
    )


def apply_ocr(
    source: Path,
    document: DocumentIR,
    config: DocumentConfig,
    *,
    force: bool = False,
    language: str | None = None,
    pages: tuple[int, ...] | None = None,
    work_dir: Path,
    provider: TesseractProvider | None = None,
) -> DocumentIR:
    """Assess every page, then OCR only selected pages in this worker."""

    if not 72 <= config.ocr_dpi <= 600:
        raise DocumentOcrError("OCR DPI must be between 72 and 600")
    qualities = assess_document_quality(
        document,
        min_text_chars=config.ocr_min_text_chars_per_page,
    )
    document = _annotate_quality(document, qualities)
    quality_by_page = {item.page_number: item for item in qualities}
    available_pages = {page.page_number for page in document.pages}
    if pages is not None:
        requested_pages = tuple(dict.fromkeys(pages))
        if (
            not requested_pages
            or any(isinstance(item, bool) or item < 1 for item in requested_pages)
            or not set(requested_pages) <= available_pages
        ):
            raise DocumentFormatError("Requested OCR page does not exist")
    else:
        requested_pages = ()

    source_mime = str(document.metadata.get("source_mime", ""))
    if source_mime != "application/pdf" and source_mime not in _IMAGE_MIMES:
        if force:
            raise DocumentFormatError("Explicit OCR supports PDF and image documents")
        return document
    if force:
        selected = set(requested_pages or sorted(available_pages))
    else:
        selected = {
            quality.page_number for quality in qualities if quality.needs_ocr
        }
        if requested_pages:
            selected &= set(requested_pages)
    if not selected:
        return replace(
            document,
            metadata={
                **dict(document.metadata),
                "ocr": {"status": "not_needed", "selected_pages": []},
            },
        )

    provider = provider or TesseractProvider()
    capability = provider.probe()
    if not capability.available:
        return _unavailable(
            document,
            capability.reason or "Tesseract OCR is unavailable",
            force=force,
        )
    rasterizer = None
    if source_mime == "application/pdf":
        try:
            rasterizer, raster_health = get_pdf_rasterizer()
        except Exception as exc:
            raster_health = OcrComponentHealth(
                OcrReadiness.UNAVAILABLE,
                None,
                reason=f"PDF rasterizer probe failed ({type(exc).__name__})",
            )
        if rasterizer is None:
            return _unavailable(
                document,
                raster_health.reason or "No healthy PDF rasterizer is installed",
                force=force,
            )
    elif get_image_parser_health().readiness is OcrReadiness.UNAVAILABLE:
        return _unavailable(document, "Pillow image support is unavailable", force=force)

    output_pages: list[DocumentPage] = []
    warnings = [
        str(item)
        for item in document.metadata.get("warnings", [])
        if "OCR was not run" not in str(item)
    ]
    succeeded: list[int] = []
    failed: list[int] = []
    for page in document.pages:
        if page.page_number not in selected:
            output_pages.append(page)
            continue
        temporary_image: Path | None = None
        try:
            native_text = "\n".join(block.text for block in page.blocks if block.text)
            selected_language = select_language(
                language,
                configured=config.ocr_languages,
                installed=capability.languages,
                native_text=native_text,
            )
            if source_mime == "application/pdf":
                assert rasterizer is not None
                temporary_image = rasterizer.render_page(
                    source,
                    page,
                    dpi=config.ocr_dpi,
                    max_pixels=config.max_pixels_per_image,
                    timeout_seconds=config.ocr_subprocess_timeout_seconds,
                    work_dir=work_dir,
                )
            else:
                temporary_image = render_image_page(
                    source,
                    page.page_number,
                    max_pixels=config.max_pixels_per_image,
                    work_dir=work_dir,
                )
            result = provider.recognize(
                temporary_image,
                page_number=page.page_number,
                language=selected_language,
                dpi=config.ocr_dpi,
                max_pixels=config.max_pixels_per_image,
                timeout_seconds=config.ocr_subprocess_timeout_seconds,
                output_limit_bytes=config.ocr_output_limit_bytes,
                work_dir=work_dir,
            )
            blocks = merge_page_content(
                page,
                result.blocks,
                quality=quality_by_page[page.page_number],
                ocr_confidence=result.confidence,
            )
            page_metadata = {
                **dict(page.metadata),
                "ocr": {
                    "status": "completed" if result.blocks else "no_text",
                    "language": result.language,
                    "engine": result.engine,
                    "engine_version": result.engine_version,
                    "dpi": result.dpi,
                    "confidence": result.confidence,
                },
            }
            output_pages.append(replace(page, blocks=blocks, metadata=page_metadata))
            succeeded.append(page.page_number)
            if result.confidence is None:
                warnings.append(f"OCR found no text on page {page.page_number}")
            elif result.confidence < config.ocr_min_confidence:
                warnings.append(
                    f"OCR confidence is low on page {page.page_number} "
                    f"({result.confidence:.2f})"
                )
        except DocumentOcrUnavailableError as exc:
            if force:
                raise
            failed.append(page.page_number)
            warnings.append(
                f"OCR unavailable on page {page.page_number}: {exc.safe_message}"
            )
            output_pages.append(page)
        except (DocumentOcrError, DocumentLimitError) as exc:
            if force:
                raise
            failed.append(page.page_number)
            warnings.append(f"OCR failed on page {page.page_number}: {exc.safe_message}")
            output_pages.append(page)
        finally:
            if temporary_image is not None:
                temporary_image.unlink(missing_ok=True)

    status = "completed" if succeeded and not failed else (
        "partial" if succeeded else "failed"
    )
    return replace(
        document,
        pages=tuple(output_pages),
        metadata={
            **dict(document.metadata),
            "warnings": list(dict.fromkeys(warnings)),
            "ocr": {
                "status": status,
                "selected_pages": sorted(selected),
                "completed_pages": succeeded,
                "failed_pages": failed,
                "provider": capability.provider,
            },
        },
    )
