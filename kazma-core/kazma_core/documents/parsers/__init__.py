"""Built-in document parser plugin registrations."""

from __future__ import annotations

import importlib
import shutil

from ..registry import ParserPlugin, ParserReadiness
from .image import ImageParser
from .ooxml import DocxParser, LegacyOfficeParser, PptxParser, XlsxParser
from .pdf import PdfParser
from .text import CsvParser, HtmlDocumentParser, JsonParser, RtfParser, TextParser

__all__ = ["builtin_plugins"]

_LIMITS = (
    "max_pages",
    "max_sheets",
    "max_slides",
    "max_rows_per_sheet",
    "max_cells",
    "max_output_chars_per_page",
    "max_output_chars_total",
)


def _pdf_health() -> tuple[ParserReadiness, str | None]:
    """PDF readiness: PyMuPDF or pdfplumber = full; text-only engines = degraded.

    pypdfium2 participates in the multi-engine *bake-off* when installed, but it
    is text-only (no tables). Advertising READY + ``tables`` without PyMuPDF or
    pdfplumber would over-promise capability — keep it DEGRADED like pypdf.
    """

    # Full readiness requires an engine that can deliver declared features
    # (pages + text + tables): PyMuPDF or pdfplumber.
    for module in ("fitz", "pymupdf"):
        try:
            importlib.import_module(module)
            return ParserReadiness.READY, None
        except Exception:
            continue
    try:
        importlib.import_module("pdfplumber")
        return ParserReadiness.READY, None
    except Exception:
        pass
    # Text-only peers: still parse PDFs, but capability.features drops to
    # degraded_features ("pages", "text") — no tables advertised.
    for module, label in (
        ("pypdfium2", "pypdfium2"),
        ("pypdf", "pypdf"),
        ("PyPDF2", "PyPDF2"),
    ):
        try:
            importlib.import_module(module)
            return (
                ParserReadiness.DEGRADED,
                f"PyMuPDF/pdfplumber unavailable; text-only PDF via {label} "
                "(tables not advertised)",
            )
        except Exception:
            continue
    return ParserReadiness.UNAVAILABLE, "No healthy PDF parser dependency is installed"


def builtin_plugins() -> tuple[ParserPlugin, ...]:
    from kazma_core.documents.binaries import find_soffice

    libreoffice = find_soffice() or shutil.which("soffice") or shutil.which("libreoffice") or "soffice"
    return (
        ParserPlugin(
            "image",
            "1",
            ("image/png", "image/jpeg", "image/tiff", "image/bmp", "image/webp"),
            (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"),
            ("pages", "dimensions", "ocr_input"),
            ("max_pages", "max_pixels_per_image"),
            True,
            ImageParser,
            dependencies=("PIL:Pillow",),
        ),
        ParserPlugin(
            "pdf",
            "1",
            ("application/pdf",),
            (".pdf",),
            ("pages", "text", "tables"),
            _LIMITS,
            True,
            PdfParser,
            health_probe=_pdf_health,
            degraded_features=("pages", "text"),
        ),
        ParserPlugin(
            "docx",
            "1",
            ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",),
            (".docx",),
            ("paragraphs", "headings", "tables"),
            _LIMITS,
            True,
            DocxParser,
            dependencies=("docx:python-docx",),
        ),
        ParserPlugin(
            "xlsx",
            "1",
            ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
            (".xlsx",),
            ("sheets", "tables"),
            _LIMITS,
            True,
            XlsxParser,
            dependencies=("openpyxl",),
        ),
        ParserPlugin(
            "pptx",
            "1",
            ("application/vnd.openxmlformats-officedocument.presentationml.presentation",),
            (".pptx",),
            ("slides", "text", "tables"),
            _LIMITS,
            True,
            PptxParser,
            dependencies=("pptx:python-pptx",),
        ),
        ParserPlugin(
            "csv",
            "1",
            ("text/csv",),
            (".csv",),
            ("rows", "tables"),
            _LIMITS,
            True,
            lambda: CsvParser(","),
        ),
        ParserPlugin(
            "tsv",
            "1",
            ("text/tab-separated-values",),
            (".tsv",),
            ("rows", "tables"),
            _LIMITS,
            True,
            lambda: CsvParser("\t"),
        ),
        ParserPlugin(
            "json",
            "1",
            ("application/json",),
            (".json",),
            ("structured_text",),
            _LIMITS,
            True,
            JsonParser,
        ),
        ParserPlugin(
            "text",
            "1",
            ("text/plain", "text/markdown", "text/x-log"),
            (".txt", ".md", ".markdown", ".log"),
            ("text", "sections"),
            _LIMITS,
            True,
            TextParser,
        ),
        ParserPlugin(
            "html",
            "1",
            ("text/html",),
            (".html", ".htm"),
            ("text",),
            _LIMITS,
            True,
            HtmlDocumentParser,
        ),
        ParserPlugin(
            "rtf",
            "1",
            ("application/rtf",),
            (".rtf",),
            ("text",),
            _LIMITS,
            True,
            RtfParser,
        ),
        ParserPlugin(
            "legacy-doc",
            "1",
            ("application/msword",),
            (".doc",),
            ("converter", "paragraphs", "tables"),
            _LIMITS,
            True,
            lambda: LegacyOfficeParser(libreoffice),
            system_binaries=(libreoffice,),
        ),
        ParserPlugin(
            "legacy-xls",
            "1",
            ("application/vnd.ms-excel",),
            (".xls",),
            ("converter", "sheets", "tables"),
            _LIMITS,
            True,
            lambda: LegacyOfficeParser(libreoffice),
            system_binaries=(libreoffice,),
        ),
        ParserPlugin(
            "legacy-ppt",
            "1",
            ("application/vnd.ms-powerpoint",),
            (".ppt",),
            ("converter", "slides", "tables"),
            _LIMITS,
            True,
            lambda: LegacyOfficeParser(libreoffice),
            system_binaries=(libreoffice,),
        ),
    )
