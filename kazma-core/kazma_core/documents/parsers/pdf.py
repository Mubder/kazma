"""Native PDF text and table parser.

Multi-engine extraction (electronic / text-layer PDFs):

1. **PyMuPDF** — logical-order Arabic + English; layout-aware multi-column;
   native tables
2. **pypdfium2** (optional) — PDFium peer for bake-off scoring
3. **pdfplumber** — strong tables; Arabic may be visually reversed
4. **pypdf** / PyPDF2 — text only

Engines are scored with :func:`score_document_extraction`. The best score wins;
when the primary score is already strong we short-circuit (fast path). Ties
prefer PyMuPDF via a small rank bonus inside the scorer.

OCR for scanned / empty / presentation-form pages is intentionally *not* done
here. The isolated ``parser_worker`` routes those pages through ``apply_ocr``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..errors import DocumentEncryptedError, DocumentLimitError, DocumentUnavailableError
from ..models import BlockType, DocumentIR
from ..quality import score_document_extraction
from .common import IRBuilder, ParseContext
from .pdf_layout import extract_pymupdf_page_text

__all__ = ["PdfParser"]

logger = logging.getLogger(__name__)

# Content score at/above which we skip remaining engines (fast path).
_GOOD_ENOUGH_SCORE = 0.82


def _pdf_metadata(
    raw: object,
    page_count: int,
    *,
    extractor: str,
    extractor_version: str | None = None,
) -> dict[str, object]:
    source = raw if isinstance(raw, dict) else {}
    aliases = {
        "title": ("Title", "/Title", "title"),
        "author": ("Author", "/Author", "author"),
        "subject": ("Subject", "/Subject", "subject"),
        "creator": ("Creator", "/Creator", "creator"),
        "created": ("CreationDate", "/CreationDate", "creationDate"),
        "modified": ("ModDate", "/ModDate", "modDate"),
    }
    metadata: dict[str, object] = {
        "page_count": page_count,
        "encrypted": False,
        "extractor": extractor,
    }
    if extractor_version:
        metadata["extractor_version"] = extractor_version
    for target, keys in aliases.items():
        value = next((source[key] for key in keys if source.get(key) is not None), None)
        if value is not None:
            metadata[target] = str(value)[:4096]
    return metadata


def _table_rows_from_matrix(
    table: list[list[Any] | None] | None,
    *,
    max_rows: int,
    builder: IRBuilder,
) -> list[str]:
    """Flatten a 2D cell matrix into ``a | b | c`` rows (shared IR shape)."""

    rows: list[str] = []
    if not table:
        return rows
    for row in table[:max_rows]:
        if row is None:
            continue
        cells = [str(cell) if cell is not None else "" for cell in row]
        builder.count_cells(len(cells))
        rows.append(" | ".join(cells))
    return rows


class PdfParser:
    """Extract PDF text without OCR or rendering."""

    def parse(self, path: Path, context: ParseContext) -> DocumentIR:
        """Run available extractors and keep the highest-scoring IR."""

        engines: tuple[tuple[str, Callable[[Path, ParseContext], DocumentIR]], ...] = (
            ("pymupdf", self._parse_pymupdf),
            ("pypdfium2", self._parse_pypdfium2),
            ("pdfplumber", self._parse_pdfplumber),
            ("pypdf", self._parse_pypdf),
        )
        best: tuple[float, DocumentIR, str] | None = None
        tried: list[str] = []
        last_error: BaseException | None = None

        for name, runner in engines:
            try:
                candidate = runner(path, context)
            except ImportError:
                logger.debug("PDF extractor %s unavailable", name)
                continue
            except DocumentEncryptedError:
                raise
            except DocumentLimitError:
                raise
            except DocumentUnavailableError as exc:
                # pypdf raises this when nothing is installed — keep looking.
                last_error = exc
                logger.debug("PDF extractor %s unavailable: %s", name, exc)
                continue
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "PDF extractor %s failed for %s",
                    name,
                    path.name,
                    exc_info=True,
                )
                continue

            score = score_document_extraction(candidate)
            tried.append(f"{name}:{score:.3f}")
            if best is None or score > best[0]:
                best = (score, candidate, name)

            # Fast path: strong extract from an early engine — no need to pay
            # for additional backends on clean electronic PDFs.
            if score >= _GOOD_ENOUGH_SCORE and any(
                block.text.strip()
                for page in candidate.pages
                for block in page.blocks
            ):
                break

        if best is None:
            if last_error is not None and isinstance(last_error, DocumentUnavailableError):
                raise last_error
            raise DocumentUnavailableError("No healthy PDF parser is installed")

        score, document, winner = best
        meta = {
            **dict(document.metadata),
            "extractor": winner,
            "extraction_score": score,
            "extractors_tried": tried,
        }
        # Preserve version if the winning backend set it under another name.
        if document.metadata.get("extractor") and document.metadata.get("extractor") != winner:
            meta["extractor_backend"] = document.metadata.get("extractor")
        return replace(document, metadata=meta)

    @staticmethod
    def _parse_pymupdf(path: Path, context: ParseContext) -> DocumentIR:
        import fitz  # PyMuPDF

        builder = IRBuilder(path, context)
        try:
            version = getattr(fitz, "VersionBind", None) or (
                fitz.version[0] if getattr(fitz, "version", None) else None
            )
        except Exception:
            version = None

        with fitz.open(path) as document:
            if bool(getattr(document, "is_encrypted", False)):
                # Empty password unlocks some "encrypted but openable" files.
                try:
                    unlocked = bool(document.authenticate(""))
                except Exception:
                    unlocked = False
                if not unlocked and getattr(document, "needs_pass", True):
                    raise DocumentEncryptedError("Encrypted PDFs are not supported")

            if document.page_count > context.config.max_pages:
                raise DocumentLimitError("PDF page count exceeds the configured limit")

            raw_meta: dict[str, object] = {}
            try:
                raw_meta = dict(document.metadata or {})
            except Exception:
                raw_meta = {}

            for page_index in range(document.page_count):
                page = document.load_page(page_index)
                blocks: list[tuple[BlockType, str, dict[str, object] | None]] = []

                text, layout_meta = extract_pymupdf_page_text(page)
                if text:
                    blocks.append(
                        (
                            BlockType.TEXT,
                            text,
                            {
                                "layout_method": layout_meta.get("method"),
                                "column_count": layout_meta.get("column_count"),
                            },
                        )
                    )

                try:
                    finder = page.find_tables()
                    tables = list(getattr(finder, "tables", None) or [])
                    for table_index, table in enumerate(tables, start=1):
                        try:
                            matrix = table.extract()
                        except Exception:
                            continue
                        rows = _table_rows_from_matrix(
                            matrix,
                            max_rows=context.config.max_rows_per_sheet,
                            builder=builder,
                        )
                        if rows:
                            blocks.append(
                                (
                                    BlockType.TABLE,
                                    "\n".join(rows),
                                    {
                                        "table_index": table_index,
                                        "rows": len(rows),
                                        "extractor": "pymupdf",
                                    },
                                )
                            )
                except DocumentLimitError:
                    raise
                except Exception:
                    builder.warnings.append(
                        f"Table extraction failed on PDF page {page_index + 1}"
                    )

                image_count = 0
                try:
                    image_count = len(page.get_images(full=True) or ())
                except Exception:
                    image_count = 0
                builder.count_images(image_count)

                rect = page.rect
                builder.add_page(
                    blocks,
                    width=float(rect.width) if rect is not None else None,
                    height=float(rect.height) if rect is not None else None,
                    rotation=int(getattr(page, "rotation", 0) or 0),
                    metadata={
                        "kind": "page",
                        "image_count": image_count,
                        "extractor": "pymupdf",
                        "layout": {
                            "method": layout_meta.get("method"),
                            "column_count": layout_meta.get("column_count"),
                            "rtl_columns": layout_meta.get("rtl_columns"),
                        },
                    },
                )

        if not any(page.blocks for page in builder.pages):
            builder.warnings.append("No text layer found; OCR was not run")
        return builder.build(
            metadata=_pdf_metadata(
                raw_meta,
                len(builder.pages),
                extractor="pymupdf",
                extractor_version=str(version) if version else None,
            )
        )

    @staticmethod
    def _parse_pypdfium2(path: Path, context: ParseContext) -> DocumentIR:
        """Optional PDFium backend (install ``pypdfium2``). Text only."""

        import pypdfium2 as pdfium

        builder = IRBuilder(path, context)
        try:
            import importlib.metadata

            version = importlib.metadata.version("pypdfium2")
        except Exception:
            version = None

        document = pdfium.PdfDocument(str(path))
        try:
            # Encrypted / password-protected files raise or report zero pages.
            page_count = len(document)
            if page_count <= 0:
                raise DocumentUnavailableError("pypdfium2 could not open the PDF")
            if page_count > context.config.max_pages:
                raise DocumentLimitError("PDF page count exceeds the configured limit")

            raw_meta: dict[str, object] = {}
            try:
                # pypdfium2 metadata is a mapping-like object when present.
                meta_obj = getattr(document, "get_metadata_dict", None)
                if callable(meta_obj):
                    raw_meta = dict(meta_obj() or {})
            except Exception:
                raw_meta = {}

            for page_index in range(page_count):
                page = document[page_index]
                text = ""
                width = None
                height = None
                try:
                    width = float(page.get_width())
                    height = float(page.get_height())
                except Exception:
                    pass
                try:
                    textpage = page.get_textpage()
                    try:
                        text = (textpage.get_text_bounded() or "").strip()
                    finally:
                        textpage.close()
                except Exception:
                    text = ""
                blocks = (
                    [(BlockType.TEXT, text, {"extractor": "pypdfium2"})]
                    if text
                    else []
                )
                builder.add_page(
                    blocks,
                    width=width if width and width > 0 else None,
                    height=height if height and height > 0 else None,
                    metadata={
                        "kind": "page",
                        "image_count": 0,
                        "extractor": "pypdfium2",
                    },
                )
        finally:
            document.close()

        if not any(page.blocks for page in builder.pages):
            builder.warnings.append("No text layer found; OCR was not run")
        return builder.build(
            metadata=_pdf_metadata(
                raw_meta,
                len(builder.pages),
                extractor="pypdfium2",
                extractor_version=version,
            )
        )

    @staticmethod
    def _parse_pdfplumber(path: Path, context: ParseContext) -> DocumentIR:
        import pdfplumber

        builder = IRBuilder(path, context)
        try:
            import importlib.metadata

            plumber_version = importlib.metadata.version("pdfplumber")
        except Exception:
            plumber_version = None

        with pdfplumber.open(path) as pdf:
            plumber_meta = pdf.metadata
            if len(pdf.pages) > context.config.max_pages:
                raise DocumentLimitError("PDF page count exceeds the configured limit")
            for page_index, page in enumerate(pdf.pages, start=1):
                blocks: list[tuple[BlockType, str, dict[str, object] | None]] = []
                text = page.extract_text() or ""
                if text.strip():
                    blocks.append((BlockType.TEXT, text.strip(), None))
                try:
                    for table_index, table in enumerate(page.extract_tables(), start=1):
                        rows = _table_rows_from_matrix(
                            table,
                            max_rows=context.config.max_rows_per_sheet,
                            builder=builder,
                        )
                        if rows:
                            blocks.append(
                                (
                                    BlockType.TABLE,
                                    "\n".join(rows),
                                    {
                                        "table_index": table_index,
                                        "rows": len(rows),
                                        "extractor": "pdfplumber",
                                    },
                                )
                            )
                except DocumentLimitError:
                    raise
                except Exception:
                    builder.warnings.append(
                        f"Table extraction failed on PDF page {page_index}"
                    )
                image_count = len(getattr(page, "images", ()) or ())
                builder.count_images(image_count)
                builder.add_page(
                    blocks,
                    width=float(page.width) if page.width else None,
                    height=float(page.height) if page.height else None,
                    metadata={
                        "kind": "page",
                        "image_count": image_count,
                        "extractor": "pdfplumber",
                    },
                )
        if not any(page.blocks for page in builder.pages):
            builder.warnings.append("No text layer found; OCR was not run")
        return builder.build(
            metadata=_pdf_metadata(
                plumber_meta,
                len(builder.pages),
                extractor="pdfplumber",
                extractor_version=plumber_version,
            )
        )

    @staticmethod
    def _parse_pypdf(path: Path, context: ParseContext) -> DocumentIR:
        try:
            from pypdf import PdfReader
            backend = "pypdf"
        except ImportError:
            try:
                from PyPDF2 import PdfReader  # type: ignore[no-redef]
                backend = "PyPDF2"
            except ImportError as exc:
                raise DocumentUnavailableError(
                    "No healthy PDF parser is installed"
                ) from exc

        try:
            import importlib.metadata

            version = importlib.metadata.version(
                "pypdf" if backend == "pypdf" else "PyPDF2"
            )
        except Exception:
            version = None

        reader = PdfReader(str(path))
        if getattr(reader, "is_encrypted", False):
            raise DocumentEncryptedError("Encrypted PDFs are not supported")
        if len(reader.pages) > context.config.max_pages:
            raise DocumentLimitError("PDF page count exceeds the configured limit")
        builder = IRBuilder(path, context)
        for page in reader.pages:
            text = page.extract_text() or ""
            box = getattr(page, "mediabox", None)
            width = float(box.width) if box is not None else None
            height = float(box.height) if box is not None else None
            blocks = [(BlockType.TEXT, text.strip(), None)] if text.strip() else []
            image_count = 0
            try:
                resources = page.get("/Resources") or {}
                xobjects = resources.get("/XObject") or {}
                image_count = sum(
                    1
                    for value in xobjects.values()
                    if str(value.get("/Subtype")) == "/Image"
                )
            except Exception:
                image_count = 0
            builder.count_images(image_count)
            builder.add_page(
                blocks,
                width=width if width and width > 0 else None,
                height=height if height and height > 0 else None,
                metadata={
                    "kind": "page",
                    "image_count": image_count,
                    "extractor": backend,
                },
            )
        if not any(page.blocks for page in builder.pages):
            builder.warnings.append("No text layer found; OCR was not run")
        return builder.build(
            metadata=_pdf_metadata(
                reader.metadata,
                len(builder.pages),
                extractor=backend,
                extractor_version=version,
            )
        )
