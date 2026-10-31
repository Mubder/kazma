"""A PDF table's cells wrap inside the page and keep their formatting (2026-10-31).

Live: a Kuwait history paper's chronology came out of generate_pdf with its
event cells running past the page's right edge, a short date running into
the next column ("c. 3000 BCE onwardDilmunite") and "**1752**" printed with
its asterisks. ReportLab draws a plain-string cell on one line at its
natural width. The agent read the page back, took it for broken, and
rewrote its table as a list.
"""
from __future__ import annotations

import pytest

from kazma_core.documents.content_model import BodyBlock, ContentModel, TableBlock, TitleBlock
from kazma_core.documents.profile import DocProfile

pymupdf = pytest.importorskip("pymupdf")

ROWS = [
    ("c. 5500–5000 BCE", "Ubaid-related settlement at As-Sabiyah (H3); earliest evidence of boat building in the Gulf"),
    ("c. 3000 BCE onward", "Dilmunite outpost on Failaka; Bronze Age trade with Mesopotamia and the Indus"),
    ("c. 600 BCE", "Neo-Babylonian control of the bay; palace and temple of Nebuchadnezzar II on Failaka; "
                   "a governor under Nabonidus and a long inscription naming the island"),
    ("**1752**", "**Election of Sabah I ibn Jaber — conventional founding of the emirate**"),
    ("1760s / 1814 / 1920", "The three walls of Kuwait Town"),
]


def _render(tmp_path, monkeypatch):
    from kazma_core.documents.engines import pdf as pdfmod

    monkeypatch.setattr(pdfmod.PdfEngine, "_render_via_docx", lambda self, *a, **k: False)
    table_md = "| Date | Event |\n|---|---|\n" + "\n".join(f"| {d} | {e} |" for d, e in ROWS)
    model = ContentModel()
    model.add(TitleBlock(text="A History of Kuwait", level=0, fill="3b82f6"))
    model.add(BodyBlock(text="## Chronology\n\n" + table_md + "\n"))
    model.add(TableBlock(headers=["Date", "Event"], rows=[[d.strip("*"), e.strip("*")] for d, e in ROWS]))
    out = tmp_path / "paper.pdf"
    pdfmod.PdfEngine(DocProfile.for_content("A History of Kuwait Chronology"), []).render(model, out)
    return out


def _spans(path):
    with pymupdf.open(path) as doc:
        return [(page.rect.width, span) for page in doc
                for block in page.get_text("dict")["blocks"]
                for line in block.get("lines", [])
                for span in line["spans"] if span["text"].strip()]


def test_every_cell_stays_inside_the_page(tmp_path, monkeypatch):
    spans = _spans(_render(tmp_path, monkeypatch))
    width = spans[0][0]
    over = [s["text"] for w, s in spans if s["bbox"][2] > width - 30]
    assert not over, over


def test_inline_formatting_reaches_the_cells(tmp_path, monkeypatch):
    path = _render(tmp_path, monkeypatch)
    with pymupdf.open(path) as doc:
        text = "".join(page.get_text() for page in doc)
    assert "**" not in text
    spans = _spans(path)
    assert not [s["text"] for _, s in spans if "onward" in s["text"] and "Dilmunite" in s["text"]]
    date = next(s for _, s in spans if s["text"].strip() == "c. 3000 BCE onward")
    event = next(s for _, s in spans if s["text"].startswith("Dilmunite"))
    assert date["bbox"][2] <= event["bbox"][0]  # the date ends before its event begins
    bold = "".join(s["text"] for _, s in spans if "Bold" in s["font"])
    assert "1752" in bold and "Election of Sabah I" in bold


def test_negative_control_plain_cells_run_off_the_page(tmp_path, monkeypatch):
    from kazma_core.documents import rich_render

    monkeypatch.setattr(rich_render, "ltr_table_cells",
                        lambda headers, rows, **k: ([list(headers)] + [list(r) for r in rows], None))
    path = _render(tmp_path, monkeypatch)
    spans = _spans(path)
    width = spans[0][0]
    assert [s["text"] for w, s in spans if s["bbox"][2] > width - 30]
    with pymupdf.open(path) as doc:
        text = "".join(page.get_text() for page in doc)
    # Wider than the page and centred, the table lost its date column off the
    # left edge as well.
    assert "c. 5500" not in text
