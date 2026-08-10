"""Native PDF text and table parser."""

from __future__ import annotations

from pathlib import Path

from ..errors import DocumentEncryptedError, DocumentLimitError, DocumentUnavailableError
from ..models import BlockType, DocumentIR
from .common import IRBuilder, ParseContext

__all__ = ["PdfParser"]


def _pdf_metadata(raw: object, page_count: int) -> dict[str, object]:
    source = raw if isinstance(raw, dict) else {}
    aliases = {
        "title": ("Title", "/Title"),
        "author": ("Author", "/Author"),
        "subject": ("Subject", "/Subject"),
        "creator": ("Creator", "/Creator"),
        "created": ("CreationDate", "/CreationDate"),
        "modified": ("ModDate", "/ModDate"),
    }
    metadata: dict[str, object] = {"page_count": page_count, "encrypted": False}
    for target, keys in aliases.items():
        value = next((source[key] for key in keys if source.get(key) is not None), None)
        if value is not None:
            metadata[target] = str(value)[:4096]
    return metadata


class PdfParser:
    """Extract PDF text without OCR or rendering."""

    def parse(self, path: Path, context: ParseContext) -> DocumentIR:
        try:
            return self._parse_pdfplumber(path, context)
        except ImportError:
            return self._parse_pypdf(path, context)

    @staticmethod
    def _parse_pdfplumber(path: Path, context: ParseContext) -> DocumentIR:
        import pdfplumber

        builder = IRBuilder(path, context)
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) > context.config.max_pages:
                raise DocumentLimitError("PDF page count exceeds the configured limit")
            for page_index, page in enumerate(pdf.pages, start=1):
                blocks: list[tuple[BlockType, str, dict[str, object] | None]] = []
                text = page.extract_text() or ""
                if text.strip():
                    blocks.append((BlockType.TEXT, text.strip(), None))
                try:
                    for table_index, table in enumerate(page.extract_tables(), start=1):
                        rows: list[str] = []
                        for row in table[: context.config.max_rows_per_sheet]:
                            cells = [str(cell) if cell is not None else "" for cell in row]
                            builder.count_cells(len(cells))
                            rows.append(" | ".join(cells))
                        if rows:
                            blocks.append(
                                (
                                    BlockType.TABLE,
                                    "\n".join(rows),
                                    {"table_index": table_index, "rows": len(rows)},
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
                    },
                )
        if not any(page.blocks for page in builder.pages):
            builder.warnings.append("No text layer found; OCR was not run")
        return builder.build(metadata=_pdf_metadata(pdf.metadata, len(builder.pages)))

    @staticmethod
    def _parse_pypdf(path: Path, context: ParseContext) -> DocumentIR:
        try:
            from pypdf import PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader  # type: ignore[no-redef]
            except ImportError as exc:
                raise DocumentUnavailableError("No healthy PDF parser is installed") from exc

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
                metadata={"kind": "page", "image_count": image_count},
            )
        if not any(page.blocks for page in builder.pages):
            builder.warnings.append("No text layer found; OCR was not run")
        return builder.build(metadata=_pdf_metadata(reader.metadata, len(builder.pages)))
