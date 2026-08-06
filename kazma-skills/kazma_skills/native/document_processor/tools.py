"""Document Processor Native Skill — read, merge, split, and inspect documents.

Each tool lazily imports its heavy dependency and returns a friendly
install-hint string when the library is missing, so the skill always loads.
Output is written to ``kazma-data/documents/`` for generated/manipulated files.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DOC_DIR = Path("kazma-data/documents")

# Supported document suffixes (used by file_read delegation + attachment parsing)
DOCUMENT_SUFFIXES = frozenset({
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv",
    ".pptx", ".ppt", ".json", ".txt", ".md", ".log", ".rtf",
})

_MAX_OUTPUT_CHARS = 20_000  # generous cap (vs the old 8_000)


# ── Helpers ─────────────────────────────────────────────────────────────


def _slugify(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9\s-]", "", text.lower().strip())
    slug = re.sub(r"[\s-]+", "-", slug).strip("-")
    return (slug or "document")[:max_len]


def _filename(name: str, ext: str) -> Path:
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    return DOC_DIR / f"{ts}_{_slugify(name)}.{ext}"


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n[... truncated at {limit} chars ...]"
    return text


# ── read_document ───────────────────────────────────────────────────────


async def read_document(path: str) -> str:
    """Read and extract text from a document file.

    Auto-detects format by suffix and extracts text + tables + metadata.
    Supported: PDF, DOCX, XLSX, PPTX, CSV, JSON, TXT, MD.

    Args:
        path: Path to the document file.

    Returns:
        Extracted text content with page/sheet markers, or an error message.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Error: Document not found: {path}"
    if not p.is_file():
        return f"Error: Path is not a file: {path}"

    suffix = p.suffix.lower()

    try:
        if suffix == ".pdf":
            return _read_pdf(p)
        elif suffix == ".docx":
            return _read_docx(p)
        elif suffix in (".xlsx", ".xls"):
            return _read_excel(p)
        elif suffix == ".pptx":
            return _read_pptx(p)
        elif suffix == ".csv":
            return _read_csv(p)
        elif suffix == ".json":
            return _read_json(p)
        elif suffix in (".txt", ".md", ".log"):
            return _read_text(p)
        elif suffix == ".rtf":
            return _read_rtf(p)
        else:
            # Last resort: try as text
            return _read_text(p)
    except Exception as exc:
        logger.error("[document_processor] Error reading %s: %s", path, exc)
        return f"Error reading document: {exc}"


def _read_pdf(p: Path) -> str:
    """Extract text + tables from a PDF using pdfplumber (primary) or pypdf (fallback)."""
    # Primary: pdfplumber (better quality + table extraction)
    try:
        import pdfplumber

        lines: list[str] = []
        with pdfplumber.open(p) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if text.strip():
                    lines.append(f"--- Page {i + 1} ---")
                    lines.append(text.strip())
                    lines.append("")

                # Extract tables if present
                try:
                    tables = page.extract_tables()
                    for j, table in enumerate(tables):
                        if table and len(table) > 1:
                            lines.append(f"  [Table {j + 1} on page {i + 1}]")
                            for row in table[:20]:  # cap at 20 rows per table
                                cells = [str(c) if c else "" for c in row]
                                lines.append("  | " + " | ".join(cells) + " |")
                            if len(table) > 20:
                                lines.append(f"  [... {len(table) - 20} more rows ...]")
                            lines.append("")
                except Exception:
                    pass  # table extraction is best-effort

        result = "\n".join(lines)
        if result.strip():
            return _truncate(result)

        # No text extracted — might be a scanned PDF
        return (
            "No text layer found in this PDF. It may be a scanned/image-only document. "
            "Install OCR support: pip install 'kazma[ocr]'"
        )
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("[document_processor] pdfplumber failed: %s — trying pypdf", exc)

    # Fallback: pypdf
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(p))
        lines = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                lines.append(f"--- Page {i + 1} ---")
                lines.append(text.strip())
                lines.append("")
        result = "\n".join(lines)
        if result.strip():
            return _truncate(result)
        return (
            "No text layer found in this PDF. It may be a scanned/image-only document. "
            "Install OCR support: pip install 'kazma[ocr]'"
        )
    except ImportError:
        return "Error: No PDF library installed. Run: pip install pdfplumber pypdf"
    except Exception as exc:
        return f"Error parsing PDF: {exc}"


def _read_docx(p: Path) -> str:
    """Extract text from a DOCX file using python-docx."""
    try:
        from docx import Document
    except ImportError:
        return "Error: python-docx not installed. Run: pip install python-docx"

    doc = Document(str(p))
    lines: list[str] = []

    # Paragraphs (including headings)
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            # Detect heading style for structure markers
            style_name = (para.style.name or "").lower()
            if "heading" in style_name:
                level = re.search(r"\d+", style_name)
                prefix = "#" * int(level.group()) if level else "#"
                lines.append(f"{prefix} {text}")
            else:
                lines.append(text)

    # Tables
    for i, table in enumerate(doc.tables):
        lines.append(f"\n[Table {i + 1}]")
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            lines.append("| " + " | ".join(cells) + " |")

    result = "\n".join(lines)
    return _truncate(result) if result else "Document appears to be empty."


def _read_excel(p: Path) -> str:
    """Extract data from XLSX/XLS using openpyxl."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return "Error: openpyxl not installed. Run: pip install openpyxl"

    wb = load_workbook(p, read_only=True, data_only=True)
    lines: list[str] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        lines.append(f"--- Sheet: {sheet_name} ---")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 200:
                lines.append("[... truncated after 200 rows ...]")
                break
            cells = [str(c) if c is not None else "" for c in row]
            if any(c.strip() for c in cells):
                lines.append(" | ".join(cells))
        lines.append("")
    wb.close()
    return _truncate("\n".join(lines))


def _read_pptx(p: Path) -> str:
    """Extract text from PPTX slides using python-pptx."""
    try:
        from pptx import Presentation
    except ImportError:
        return "Error: python-pptx not installed. Run: pip install python-pptx"

    prs = Presentation(str(p))
    lines: list[str] = []
    for i, slide in enumerate(prs.slides):
        lines.append(f"--- Slide {i + 1} ---")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        lines.append(text)
            if shape.has_table:
                table = shape.table
                lines.append(f"  [Table on slide {i + 1}]")
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    lines.append("  | " + " | ".join(cells) + " |")
        lines.append("")
    return _truncate("\n".join(lines))


def _read_csv(p: Path) -> str:
    """Extract data from a CSV file."""
    lines: list[str] = []
    with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i >= 200:
                lines.append("[... truncated after 200 rows ...]")
                break
            lines.append(", ".join(row))
    return _truncate("\n".join(lines))


def _read_json(p: Path) -> str:
    """Pretty-print a JSON file."""
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    return _truncate(json.dumps(data, indent=2, ensure_ascii=False))


def _read_text(p: Path) -> str:
    """Read a plain-text file."""
    content = p.read_text(encoding="utf-8", errors="replace")
    return _truncate(content)


def _read_rtf(p: Path) -> str:
    """Strip RTF formatting to get plain text (best-effort)."""
    import re as _re

    raw = p.read_text(encoding="utf-8", errors="replace")
    # Remove RTF control words and groups
    text = _re.sub(r"\\[a-z]+-?\d+ ?|[@{}]|\\'[0-9a-f]{2}", "", raw, flags=_re.IGNORECASE)
    text = _re.sub(r"[{}]", "", text)
    text = _re.sub(r"\s+", " ", text).strip()
    return _truncate(text) if text else "RTF file appears to be empty."


# ── pdf_merge ───────────────────────────────────────────────────────────


async def pdf_merge(file_paths: list[str], output_name: str = "merged") -> str:
    """Merge multiple PDF files into a single PDF.

    Args:
        file_paths: List of PDF file paths to merge (in order).
        output_name: Name for the output file (without extension).

    Returns:
        Success message with the output path, or error.
    """
    try:
        from pypdf import PdfWriter
    except ImportError:
        return "Error: pypdf not installed. Run: pip install pypdf"

    if not file_paths:
        return "Error: No file paths provided."

    writer = PdfWriter()
    for fp in file_paths:
        p = Path(fp).expanduser().resolve()
        if not p.exists():
            return f"Error: File not found: {fp}"
        if p.suffix.lower() != ".pdf":
            return f"Error: Not a PDF file: {fp}"
        try:
            writer.append(str(p))
        except Exception as exc:
            return f"Error: Failed to append {fp}: {exc}"

    dest = _filename(output_name, "pdf")
    with open(dest, "wb") as f:
        writer.write(f)
    writer.close()

    return f"PDF merged successfully.\n  Files: {len(file_paths)}\n  Saved to: {dest}"


# ── pdf_split ───────────────────────────────────────────────────────────


async def pdf_split(
    file_path: str,
    start_page: int = 1,
    end_page: int = 0,
    output_name: str = "",
) -> str:
    """Extract specific pages from a PDF into a new file.

    Args:
        file_path: Path to the source PDF.
        start_page: First page to extract (1-indexed).
        end_page: Last page to extract (1-indexed, 0 = to end).
        output_name: Name for the output file (without extension).

    Returns:
        Success message with the output path, or error.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return "Error: pypdf not installed. Run: pip install pypdf"

    p = Path(file_path).expanduser().resolve()
    if not p.exists():
        return f"Error: File not found: {file_path}"

    reader = PdfReader(str(p))
    total = len(reader.pages)

    if start_page < 1:
        start_page = 1
    if end_page <= 0 or end_page > total:
        end_page = total
    if start_page > total:
        return f"Error: start_page ({start_page}) exceeds total pages ({total})."

    writer = PdfWriter()
    for i in range(start_page - 1, min(end_page, total)):
        writer.add_page(reader.pages[i])

    name = output_name or f"{p.stem}_pages_{start_page}-{end_page}"
    dest = _filename(name, "pdf")
    with open(dest, "wb") as f:
        writer.write(f)
    writer.close()

    return (
        f"PDF split successfully.\n"
        f"  Source: {p.name} ({total} pages)\n"
        f"  Extracted: pages {start_page}-{end_page}\n"
        f"  Saved to: {dest}"
    )


# ── pdf_info ────────────────────────────────────────────────────────────


async def pdf_info(file_path: str) -> str:
    """Extract metadata from a PDF file.

    Args:
        file_path: Path to the PDF file.

    Returns:
        Formatted metadata string (page count, title, author, dates, etc.).
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return "Error: pypdf not installed. Run: pip install pypdf"

    p = Path(file_path).expanduser().resolve()
    if not p.exists():
        return f"Error: File not found: {file_path}"

    reader = PdfReader(str(p))
    total = len(reader.pages)
    meta = reader.metadata or {}

    # Check for text layer
    has_text = False
    for page in reader.pages[:5]:  # sample first 5 pages
        if (page.extract_text() or "").strip():
            has_text = True
            break

    lines = [
        f"PDF Metadata: {p.name}",
        f"  Pages: {total}",
        f"  Title: {meta.get('/Title', '(none)')}",
        f"  Author: {meta.get('/Author', '(none)')}",
        f"  Subject: {meta.get('/Subject', '(none)')}",
        f"  Creator: {meta.get('/Creator', '(none)')}",
        f"  Created: {meta.get('/CreationDate', '(unknown)')}",
        f"  Modified: {meta.get('/ModDate', '(unknown)')}",
        f"  Text layer: {'Yes' if has_text else 'No (likely scanned/image-only)'}",
    ]
    if not has_text:
        lines.append("  ⚠ No extractable text. Install OCR: pip install 'kazma[ocr]'")

    return "\n".join(lines)
