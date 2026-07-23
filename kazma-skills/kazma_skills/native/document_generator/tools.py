"""Document Generator Native Skill — create PDF/DOCX/XLSX/Markdown files.

Each tool lazily imports its heavy dependency and returns a friendly
install-hint string when the library is missing, so the skill always loads.
Output is written to ``kazma-data/documents/``.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DOC_DIR = Path("kazma-data/documents")


def _slugify(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9\s-]", "", text.lower().strip())
    slug = re.sub(r"[\s-]+", "-", slug).strip("-")
    return (slug or "document")[:max_len]


def _filename(title: str, ext: str) -> Path:
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    return DOC_DIR / f"{ts}_{_slugify(title)}.{ext}"


def _normalize_sections(sections: list[Any]) -> list[dict[str, str]]:
    """Coerce a sections list into [{heading, body}] dicts."""
    out: list[dict[str, str]] = []
    for s in sections or []:
        if isinstance(s, dict):
            out.append({
                "heading": str(s.get("heading", "")),
                "body": str(s.get("body", "")),
            })
        elif isinstance(s, (list, tuple)) and len(s) >= 2:
            out.append({"heading": str(s[0]), "body": str(s[1])})
    return out


async def generate_pdf(
    title: str,
    sections: list[dict[str, str]],
) -> str:
    """Generate a PDF from a title and sections (each {heading, body}).

    Requires the ``reportlab`` package (``pip install reportlab``).
    """
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
        )
    except ImportError:
        return "Error: reportlab not installed. Run: pip install reportlab"

    secs = _normalize_sections(sections)
    dest = _filename(title, "pdf")
    try:
        doc = SimpleDocTemplate(str(dest), pagesize=LETTER)
        styles = getSampleStyleSheet()
        title_style = styles["Title"]
        head_style = ParagraphStyle(
            "SectionHead", parent=styles["Heading2"], spaceBefore=14, spaceAfter=6
        )
        body_style = styles["BodyText"]
        flow: list[Any] = [Paragraph(title, title_style), Spacer(1, 12)]
        for s in secs:
            if s["heading"]:
                flow.append(Paragraph(s["heading"], head_style))
            if s["body"]:
                flow.append(Paragraph(s["body"].replace("\n", "<br/>"), body_style))
            flow.append(Spacer(1, 8))
        doc.build(flow)
    except Exception as exc:  # noqa: BLE001
        return f"Error: PDF generation failed — {exc}"
    return f"PDF generated successfully.\n  Title: {title}\n  Sections: {len(secs)}\n  Saved to: {dest}"


async def generate_docx(
    title: str,
    sections: list[dict[str, str]],
) -> str:
    """Generate a Word .docx from a title and sections (each {heading, body}).

    Requires ``python-docx`` (``pip install python-docx``).
    """
    try:
        from docx import Document
    except ImportError:
        return "Error: python-docx not installed. Run: pip install python-docx"

    secs = _normalize_sections(sections)
    dest = _filename(title, "docx")
    try:
        doc = Document()
        doc.add_heading(title, level=0)
        for s in secs:
            if s["heading"]:
                doc.add_heading(s["heading"], level=1)
            if s["body"]:
                doc.add_paragraph(s["body"])
        doc.save(str(dest))
    except Exception as exc:  # noqa: BLE001
        return f"Error: DOCX generation failed — {exc}"
    return f"DOCX generated successfully.\n  Title: {title}\n  Sections: {len(secs)}\n  Saved to: {dest}"


async def generate_xlsx(
    sheets: list[dict[str, Any]],
    filename: str = "workbook",
) -> str:
    """Generate an Excel .xlsx from sheets (each {name, rows}).

    ``rows`` is a list of row-lists; the first row is treated as the header.
    Requires ``openpyxl`` (``pip install openpyxl``).
    """
    try:
        from openpyxl import Workbook
    except ImportError:
        return "Error: openpyxl not installed. Run: pip install openpyxl"

    dest = _filename(filename, "xlsx")
    try:
        wb = Workbook()
        # Remove the default sheet; we add named ones below.
        wb.remove(wb.active)
        for sh in sheets or []:
            name = str(sh.get("name", "Sheet"))[:31]
            rows = sh.get("rows") or []
            ws = wb.create_sheet(title=name)
            for row in rows:
                ws.append([str(c) if c is not None else "" for c in row])
        if not wb.sheetnames:
            wb.create_sheet("Sheet")
        wb.save(str(dest))
    except Exception as exc:  # noqa: BLE001
        return f"Error: XLSX generation failed — {exc}"
    return (
        f"XLSX generated successfully.\n"
        f"  Sheets: {len(sheets or [])}\n"
        f"  Saved to: {dest}"
    )


async def generate_markdown_doc(
    title: str,
    sections: list[dict[str, str]],
) -> str:
    """Generate a Markdown (.md) document from a title and sections.

    No external dependency required.
    """
    secs = _normalize_sections(sections)
    dest = _filename(title, "md")
    try:
        lines: list[str] = [f"# {title}", ""]
        for s in secs:
            if s["heading"]:
                lines.append(f"## {s['heading']}")
                lines.append("")
            if s["body"]:
                lines.append(s["body"])
                lines.append("")
        dest.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        return f"Error: could not write markdown file — {exc}"
    return f"Markdown generated successfully.\n  Title: {title}\n  Sections: {len(secs)}\n  Saved to: {dest}"
