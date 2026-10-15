"""Selective quality-routed OCR and deterministic native/OCR merging."""

from __future__ import annotations

import re
import time
from dataclasses import replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..config import DocumentConfig
from ..errors import (
    DocumentFormatError,
    DocumentLimitError,
    DocumentOcrError,
    DocumentOcrUnavailableError,
)
from ..models import DocumentBlock, DocumentIR, DocumentPage
from ..quality import PageQuality, assess_document_quality
from .base import OcrComponentHealth, OcrEngineReport, OcrHealth, OcrReadiness
from .raster import (
    get_image_parser_health,
    get_pdf_rasterizer,
    render_image_page,
)
from .tesseract import TesseractProvider, select_language

__all__ = [
    "apply_ocr",
    "combine_health",
    "effective_engine",
    "get_ocr_health",
    "merge_page_content",
    "select_ocr_provider",
]

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


# Native layers that are known-bad for Arabic/CID dumps — prefer OCR when
# confidence is acceptable rather than keeping visual-order garbage.
_REPLACEABLE_NATIVE_REASONS = frozenset(
    {
        "high_presentation_forms",
        "cid_mangled_text",
    }
)


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

    # Tier-3 path for electronic PDFs with a usable text layer that is still
    # wrong for RTL (presentation forms / cid placeholders). OCR is slower but
    # Tesseract returns logical-order Arabic when ``ara`` is installed.
    if (
        _REPLACEABLE_NATIVE_REASONS.intersection(quality.reasons)
        and (ocr_confidence or 0.0) >= 0.55
        and ocr_text.strip()
    ):
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


def _engine_report(capability: Any) -> OcrEngineReport:
    return OcrEngineReport(
        OcrComponentHealth(capability.readiness, capability.engine, capability.engine_version, capability.reason),
        tuple(capability.languages),
    )


def effective_engine(setting: str, neural_ready: bool) -> str:
    """The engine that reads pages under ``documents.ocr.engine``."""
    if setting == "tesseract":
        return "tesseract"
    return "neural" if neural_ready else "tesseract"


def _neural_engine_health(model_dir: Path | None = None) -> OcrEngineReport:
    """The neural engine as this process has it (packages and models)."""
    from .neural import NeuralOcrProvider

    return _engine_report(NeuralOcrProvider(model_dir).probe())


def select_ocr_provider(
    config: DocumentConfig, *, model_dir: Path | None = None,
) -> tuple[Any, str | None]:
    """The provider for ``documents.ocr.engine`` and, when the neural engine
    was asked for by name and cannot run here, why Tesseract reads instead."""
    if config.ocr_engine == "tesseract":
        return TesseractProvider(), None
    from .neural import NeuralOcrProvider

    neural = NeuralOcrProvider(model_dir)
    capability = neural.probe()
    if capability.available:
        return neural, None
    if config.ocr_engine == "neural":
        return TesseractProvider(), capability.reason or "The neural OCR engine is unavailable"
    return TesseractProvider(), None


def get_ocr_health(
    requested_languages: tuple[str, ...] = (),
    *,
    engine: str = "auto",
    model_dir: Path | None = None,
) -> OcrHealth:
    from ..worker_container import image_ocr_health, uses_worker_image

    if uses_worker_image():
        # OCR runs inside the worker image: its engines and languages count.
        return image_ocr_health(requested_languages, engine=engine)
    tesseract = _engine_report(TesseractProvider().probe(requested_languages))
    neural = _neural_engine_health(model_dir)
    if neural.health.readiness is OcrReadiness.READY:
        missing = sorted(set(requested_languages) - set(neural.languages))
        if missing:
            neural = OcrEngineReport(OcrComponentHealth(
                OcrReadiness.UNAVAILABLE, neural.health.name, neural.health.version,
                f"The neural OCR engine does not read: {', '.join(missing)}"), neural.languages)
    selected = effective_engine(engine, neural.health.readiness is OcrReadiness.READY)
    chosen = neural if selected == "neural" else tesseract
    fallback_reason = (
        f"The neural OCR engine is unavailable ({neural.health.reason}); Tesseract reads pages"
        if engine == "neural" and selected != "neural" else None
    )
    return _assemble_health(
        chosen, requested_languages, engine_setting=engine, selected=selected,
        engines=(("tesseract", tesseract), ("neural", neural)), fallback_reason=fallback_reason,
    )


def _assemble_health(
    chosen: OcrEngineReport,
    requested_languages: tuple[str, ...],
    *,
    engine_setting: str,
    selected: str,
    engines: tuple[tuple[str, OcrEngineReport], ...],
    fallback_reason: str | None,
) -> OcrHealth:
    try:
        _, rasterizer = get_pdf_rasterizer()
    except Exception as exc:
        rasterizer = OcrComponentHealth(
            OcrReadiness.UNAVAILABLE,
            None,
            reason=f"PDF rasterizer health probe failed ({type(exc).__name__})",
        )
    image_parser = get_image_parser_health()
    return combine_health(
        chosen, rasterizer, image_parser, requested_languages, engine_setting=engine_setting,
        selected=selected, engines=engines, fallback_reason=fallback_reason,
    )


def combine_health(
    chosen: OcrEngineReport,
    rasterizer: OcrComponentHealth,
    image_parser: OcrComponentHealth,
    requested_languages: tuple[str, ...],
    *,
    engine_setting: str,
    selected: str,
    engines: tuple[tuple[str, OcrEngineReport], ...],
    fallback_reason: str | None,
) -> OcrHealth:
    """OCR readiness from the selected engine and the page sources: shared
    by this process's health and the worker image's report."""
    capability = chosen.health
    if capability.readiness is OcrReadiness.UNAVAILABLE:
        readiness = OcrReadiness.UNAVAILABLE
        reason = capability.reason
    elif image_parser.readiness is OcrReadiness.UNAVAILABLE:
        readiness = OcrReadiness.UNAVAILABLE
        reason = image_parser.reason
    elif rasterizer.readiness is OcrReadiness.UNAVAILABLE:
        readiness = OcrReadiness.DEGRADED
        reason = "Image OCR is ready; PDF OCR has no healthy rasterizer"
    elif fallback_reason:
        readiness = OcrReadiness.DEGRADED
        reason = fallback_reason
    else:
        readiness = OcrReadiness.READY
        reason = None
    return OcrHealth(
        readiness=readiness,
        engine=capability,
        rasterizer=rasterizer,
        image_parser=image_parser,
        languages=chosen.languages,
        requested_languages=requested_languages,
        reason=reason,
        engine_setting=engine_setting,
        selected_engine=selected,
        engines=engines,
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


#: Cells read one at a time per page, and the time they may take: past
#: either, the tables not yet read keep the page's own reading.
_TABLE_MAX_CELLS = 600
_TABLE_SECONDS = 120.0


def _with_page_tables(
    provider: Any,
    image: Path,
    result: Any,
    config: DocumentConfig,
    *,
    language: str,
    installed: tuple[str, ...],
    work_dir: Path,
) -> tuple[tuple[DocumentBlock, ...], str | None]:
    """The page's OCR lines with its ruled tables read cell by cell."""
    tables, note = _read_page_tables(provider, image, config, language=language, installed=installed,
                                     work_dir=work_dir)
    return _page_with_tables(result, tables), note


def _read_page_tables(
    provider: Any,
    image: Path,
    config: DocumentConfig,
    *,
    language: str,
    installed: tuple[str, ...],
    work_dir: Path,
) -> tuple[list[Any], str | None]:
    """The page's ruled tables read cell by cell, and why the pass stopped early."""
    if not hasattr(provider, "recognize_region"):
        return [], None
    from .tables import read_tables

    try:
        return read_tables(
            image,
            provider=provider,
            language=language,
            digits="eng" in installed and bool(getattr(provider, "digit_pass", True)),
            dpi=config.ocr_dpi,
            timeout_seconds=config.ocr_subprocess_timeout_seconds,
            output_limit_bytes=config.ocr_output_limit_bytes,
            work_dir=work_dir,
            max_cells=_TABLE_MAX_CELLS,
            deadline=time.monotonic() + _TABLE_SECONDS,
        )
    except (DocumentOcrError, DocumentOcrUnavailableError, DocumentLimitError, OSError) as exc:
        # A cell that cannot be read costs the cell pass, never the page.
        return [], f"table_read_failed: {type(exc).__name__}"


def _page_with_tables(result: Any, tables: list[Any]) -> tuple[DocumentBlock, ...]:
    if not tables:
        return result.blocks
    from .tables import with_tables

    return with_tables(
        result.blocks, tables, page_number=result.page_number, language=result.language,
        engine_version=result.engine_version, dpi=result.dpi, engine=result.engine,
    )


def _switch_to_tesseract(capability: Any) -> tuple[Any, Any] | None:
    """Tesseract and its capability, to finish a document the neural engine
    would not finish in time; None when Tesseract already reads it or cannot
    run here."""
    if capability.engine == "tesseract":
        return None
    tesseract = TesseractProvider()
    capability = tesseract.probe()
    return (tesseract, capability) if capability.available else None


def _requested_pages(document: DocumentIR, pages: tuple[int, ...] | None) -> tuple[int, ...]:
    """The pages a caller asked for, each one the document has."""
    if pages is None:
        return ()
    available_pages = {page.page_number for page in document.pages}
    requested_pages = tuple(dict.fromkeys(pages))
    if (
        not requested_pages
        or any(isinstance(item, bool) or item < 1 for item in requested_pages)
        or not set(requested_pages) <= available_pages
    ):
        raise DocumentFormatError("Requested OCR page does not exist")
    return requested_pages


def _selected_pages(
    document: DocumentIR,
    qualities: tuple[PageQuality, ...],
    requested_pages: tuple[int, ...],
    *,
    force: bool,
) -> set[int]:
    """The pages to read: every requested (or every) page when forced, else
    the pages whose quality needs OCR, within the requested ones."""
    if force:
        return set(requested_pages or sorted(page.page_number for page in document.pages))
    selected = {quality.page_number for quality in qualities if quality.needs_ocr}
    if requested_pages:
        selected &= set(requested_pages)
    return selected


def _read_page(
    provider: Any,
    capability: Any,
    page: DocumentPage,
    quality: PageQuality,
    config: DocumentConfig,
    *,
    source: Path,
    source_mime: str,
    rasterizer: Any,
    language: str | None,
    work_dir: Path,
) -> tuple[DocumentPage, list[str]]:
    """One selected page read by ``provider``: the page with the OCR text
    merged in, and the page's warnings. Raises the OCR errors ``apply_ocr``
    records per page."""
    notes: list[str] = []
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
        recognize_page = dict(
            page_number=page.page_number,
            language=selected_language,
            dpi=config.ocr_dpi,
            max_pixels=config.max_pixels_per_image,
            timeout_seconds=config.ocr_subprocess_timeout_seconds,
            output_limit_bytes=config.ocr_output_limit_bytes,
            work_dir=work_dir,
        )
        # A provider that reads line by line (the neural engine) skips the
        # lines inside ruled tables: the table pass reads them cell by
        # cell and its blocks replace them (tables.with_tables).
        skipped: tuple[tuple[float, float, float, float], ...] = ()
        if config.ocr_tables and getattr(provider, "skips_table_regions", False):
            from .tables import table_regions

            skipped = tuple(table_regions(temporary_image))
        result = provider.recognize(
            temporary_image, **recognize_page, **({"skip_regions": skipped} if skipped else {}))
        ocr_blocks = result.blocks
        if config.ocr_tables:
            tables, table_note = _read_page_tables(
                provider, temporary_image, config,
                language=selected_language, installed=capability.languages, work_dir=work_dir,
            )
            if skipped and (table_note or len(tables) < len(skipped)):
                # A table skipped in the page's reading was not read cell
                # by cell: the page is read again in full, so no text is lost.
                result = provider.recognize(temporary_image, **recognize_page)
            ocr_blocks = _page_with_tables(result, tables)
            if table_note:
                notes.append(f"Tables on page {page.page_number} were not all read cell by cell ({table_note})")
        blocks = merge_page_content(
            page,
            ocr_blocks,
            quality=quality,
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
        if result.confidence is None:
            notes.append(f"OCR found no text on page {page.page_number}")
        elif result.confidence < config.ocr_min_confidence:
            notes.append(
                f"OCR confidence is low on page {page.page_number} "
                f"({result.confidence:.2f})"
            )
        return replace(page, blocks=blocks, metadata=page_metadata), notes
    finally:
        if temporary_image is not None:
            temporary_image.unlink(missing_ok=True)


def apply_ocr(
    source: Path,
    document: DocumentIR,
    config: DocumentConfig,
    *,
    force: bool = False,
    language: str | None = None,
    pages: tuple[int, ...] | None = None,
    work_dir: Path,
    provider: Any = None,
    model_dir: Path | None = None,
    deadline: float | None = None,
) -> DocumentIR:
    """Assess every page, then OCR only selected pages in this worker.

    ``provider`` overrides ``documents.ocr.engine`` (tests, measurements);
    ``model_dir`` is where the neural engine's models are (the worker request
    names it). ``deadline`` (``time.monotonic()``) is when the worker must be
    done: when the neural engine's slowest page so far would not fit before
    it, Tesseract reads the remaining pages and the document says so -- a
    document finished by a faster engine, never a worker killed at its time
    limit with nothing read."""

    if not 72 <= config.ocr_dpi <= 600:
        raise DocumentOcrError("OCR DPI must be between 72 and 600")
    qualities = assess_document_quality(
        document,
        min_text_chars=config.ocr_min_text_chars_per_page,
    )
    document = _annotate_quality(document, qualities)
    quality_by_page = {item.page_number: item for item in qualities}
    requested_pages = _requested_pages(document, pages)

    source_mime = str(document.metadata.get("source_mime", ""))
    if source_mime != "application/pdf" and source_mime not in _IMAGE_MIMES:
        if force:
            raise DocumentFormatError("Explicit OCR supports PDF and image documents")
        return document
    selected = _selected_pages(document, qualities, requested_pages, force=force)
    if not selected:
        return replace(
            document,
            metadata={
                **dict(document.metadata),
                "ocr": {"status": "not_needed", "selected_pages": []},
            },
        )

    fallback_reason = None
    if provider is None:
        provider, fallback_reason = select_ocr_provider(config, model_dir=model_dir)
    capability = provider.probe()
    if not capability.available:
        return _unavailable(
            document,
            capability.reason or "Tesseract OCR is unavailable",
            force=force,
        )
    if fallback_reason:
        # The neural engine was asked for by name and cannot run here.
        document = _with_warning(document, f"The neural OCR engine is unavailable ({fallback_reason}); "
                                           "Tesseract read the pages")
    first_engine, first_provider = capability.engine, capability.provider
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
    page_seconds: list[float] = []
    switched_at: int | None = None
    for page in document.pages:
        if page.page_number not in selected:
            output_pages.append(page)
            continue
        if (deadline is not None and switched_at is None and page_seconds
                and time.monotonic() + max(page_seconds) > deadline):
            switch = _switch_to_tesseract(capability)
            if switch is not None:
                provider, capability = switch
                switched_at = page.page_number
                warnings.append(
                    f"Pages {page.page_number} onward were read by Tesseract: the neural OCR engine "
                    "would not have finished them within the document worker's time limit")
        page_started = time.monotonic()
        try:
            read, notes = _read_page(
                provider, capability, page, quality_by_page[page.page_number], config,
                source=source, source_mime=source_mime, rasterizer=rasterizer, language=language,
                work_dir=work_dir,
            )
            output_pages.append(read)
            succeeded.append(page.page_number)
            warnings.extend(notes)
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
            page_seconds.append(time.monotonic() - page_started)

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
                "provider": first_provider,
                "engine": first_engine,
                "engine_setting": config.ocr_engine,
                **({"tesseract_from_page": switched_at} if switched_at is not None else {}),
            },
        },
    )
