"""Every key a document tool's record names reaches the document (2026-11-12).

The document tools took ``list[dict]``: the model read no key names and
guessed, and the engines dropped what they did not read. Live, a deck's every
slide title was lost -- the model sent ``"title"``, the engine reads
``"heading"``. The tools now type these parameters with the records in
``kazma_core.documents.payloads``; here each record's every key is set to its
own marker, rendered through the real engine, and found in the file. A key
added to a record without being read fails here, as does a test payload that
leaves a key out.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pptx")
pytest.importorskip("openpyxl")
pytest.importorskip("docx")

from kazma_core.documents import renderer_worker as rw  # noqa: E402
from kazma_core.documents.payloads import Chart, Section, Sheet, Slide, Table  # noqa: E402


def _keys(record: Any) -> set[str]:
    return set(record.__annotations__)


def _xml_text(path: Path, pattern: str) -> str:
    with zipfile.ZipFile(path) as z:
        return "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if re.search(pattern, n))


def _pptx_text(path: Path) -> str:
    from pptx import Presentation

    deck = Presentation(str(path))
    parts: list[str] = []
    for slide in deck.slides:
        parts.extend(shape.text_frame.text for shape in slide.shapes if shape.has_text_frame)
        if slide.has_notes_slide:
            parts.append(slide.notes_slide.notes_text_frame.text)
    return "\n".join(parts)


def test_every_slide_key_reaches_the_deck(tmp_path):
    slides = [
        {"heading": "zqheading", "bullets": ["zqbullet"], "notes": "zqnotes"},
        {"heading": "zqheadingtwo", "body": "zqbody"},
    ]
    assert set().union(*map(set, slides)) == _keys(Slide), "the payload covers every key of Slide"
    path = tmp_path / "deck.pptx"
    rw._generate_pptx(path, {"title": "Deck", "slides": slides})
    text = _pptx_text(path)
    for marker in ("zqheading", "zqbullet", "zqnotes", "zqheadingtwo", "zqbody"):
        assert marker in text, f"{marker} never reached the deck"


def test_a_slide_sent_with_title_keeps_its_title(tmp_path):
    """The live failure: every slide as {"title": ..., "bullets": [...]}."""
    path = tmp_path / "deck.pptx"
    rw._generate_pptx(path, {"title": "Deck", "slides": [
        {"title": "عرض تحليلي للمفهوم", "bullets": ["نقطة أولى"]}]})
    assert "عرض تحليلي للمفهوم" in _pptx_text(path)


def test_negative_control_the_engine_that_read_only_heading_lost_it(tmp_path, monkeypatch):
    from kazma_core.documents.engines import pptx as engine

    monkeypatch.setattr(engine, "slide_heading", lambda value: str(value.get("heading", "")))
    path = tmp_path / "deck.pptx"
    rw._generate_pptx(path, {"title": "Deck", "slides": [
        {"title": "عرض تحليلي للمفهوم", "bullets": ["نقطة أولى"]}]})
    assert "عرض تحليلي للمفهوم" not in _pptx_text(path)


def test_every_sheet_and_chart_key_reaches_the_workbook(tmp_path):
    chart = {"type": "line", "category_col": 1, "value_col": [2, 3], "title": "zqcharttitle",
             "x_axis": "zqxaxis", "y_axis": "zqyaxis"}
    sheet = {"name": "zqname", "title": "zqtitle",
             "rows": [["zqhead", "a", "b"], ["zqrow", 1, 2], ["two", 3, 4]], "chart": chart}
    assert set(sheet) == _keys(Sheet) and set(chart) == _keys(Chart)
    path = tmp_path / "book.xlsx"
    rw._generate_xlsx(path, {"title": "Book", "sheets": [sheet]})

    from openpyxl import load_workbook

    ws = load_workbook(str(path))["zqname"]
    cells = {str(c.value) for row in ws.iter_rows() for c in row if c.value is not None}
    assert {"zqtitle", "zqhead", "zqrow"} <= cells
    charts = _xml_text(path, r"xl/charts/chart\d+\.xml")
    assert re.search(r"<(\w+:)?lineChart>", charts), "type"
    for marker in ("zqcharttitle", "zqxaxis", "zqyaxis"):
        assert marker in charts, marker
    assert "'zqname'!$A$3:$A$4" in charts, "category_col"
    assert "'zqname'!$B$3:$B$4" in charts and "'zqname'!$C$3:$C$4" in charts, "value_col, two series"


def test_every_section_and_table_key_reaches_the_document(tmp_path):
    section = {"heading": "zqsectionheading", "body": "zqsectionbody"}
    table = {"heading": "zqtableheading", "headers": ["zqheader", "b"], "rows": [["zqcell", 2]]}
    assert set(section) == _keys(Section) and set(table) == _keys(Table)
    path = tmp_path / "doc.docx"
    rw._generate_docx(path, {"title": "Doc", "sections": [section], "tables": [table]})
    text = re.sub(r"<[^>]+>", "", _xml_text(path, r"word/document\.xml"))
    for marker in ("zqsectionheading", "zqsectionbody", "zqtableheading", "zqheader", "zqcell"):
        assert marker in text, f"{marker} never reached the document"


def test_the_tools_take_the_records():
    """The tools' parameters are the records, so their schemas name the keys."""
    from kazma_core.agent.tool_schema import _generate_schema
    from kazma_skills.native.document_generator import tools as generator
    from kazma_skills.native.document_processor import tools as processor

    pptx = _generate_schema(processor.generate_pptx)["properties"]["slides"]["items"]
    assert set(pptx["properties"]) == _keys(Slide)
    for fn, param, record in ((generator.generate_pdf, "sections", Section),
                              (generator.generate_pdf, "tables", Table),
                              (generator.generate_docx, "sections", Section),
                              (generator.generate_docx, "tables", Table),
                              (generator.generate_markdown_doc, "sections", Section),
                              (generator.generate_xlsx, "sheets", Sheet)):
        items = _generate_schema(fn)["properties"][param]["items"]
        assert set(items["properties"]) == _keys(record), (fn.__name__, param)
    # The images generate_pdf took were emptied before rendering, from the
    # first commit: a control that did nothing is not offered.
    assert "images" not in _generate_schema(generator.generate_pdf)["properties"]
