"""Code that builds a document by hand is sent to Kazma's generator for that
format (``kazma_core.agent.raw_document_guard``).

The owner's deck of 2026-11-10 -- the Kazma font named, Arial shown -- was
python-pptx code run in ``python_exec``: the guard knew reportlab, fpdf,
python-docx and openpyxl, not python-pptx, and always named generate_pdf.
The old marker list is the negative control.
"""

from __future__ import annotations

import pytest

from kazma_core.agent.raw_document_guard import (
    CODE_TOOLS,
    RAW_DOCUMENT_MARKERS,
    raw_document_refusal,
)

_DECK = (
    "from pptx import Presentation\n"
    "from pptx.util import Pt\n"
    "prs = Presentation()\n"
    "s = prs.slides.add_slide(prs.slide_layouts[1])\n"
    "s.shapes.title.text = 'Kazma'\n"
    "prs.save('Kazma-Intro-AR.pptx')\n"
)

# The guard's markers before 2026-11-11: the negative control.
_OLD_MARKERS = (
    "reportlab", "fpdf", "from fpdf", "simpledoctemplate",
    "canvas(", "platypus", "openpyxl.workbook", "docx.document", "from docx",
)


@pytest.mark.parametrize("code, tool", [
    (_DECK, "generate_pptx"),
    ("import pptx\np = pptx.Presentation()", "generate_pptx"),
    ("from reportlab.platypus import SimpleDocTemplate", "generate_pdf"),
    ("from fpdf import FPDF", "generate_pdf"),
    ("from docx import Document\nd = Document()", "generate_docx"),
    ("import openpyxl\nwb = openpyxl.Workbook()", "generate_xlsx"),
    ("from openpyxl import Workbook\nwb = Workbook()", "generate_xlsx"),
    ("import xlsxwriter\nxlsxwriter.Workbook('a.xlsx')", "generate_xlsx"),
])
def test_each_format_is_sent_to_its_own_generator(code, tool):
    refusal = raw_document_refusal("python_exec", {"code": code})
    assert refusal.startswith("SYSTEM OVERRIDE")
    assert f"- {tool}(" in refusal
    others = set(RAW_DOCUMENT_MARKERS) - {tool}
    assert not any(f"- {other}(" in refusal for other in others)


def test_the_old_markers_missed_the_deck():
    """The negative control: the deck passed the guard as it was."""
    assert not any(marker in _DECK.lower() for marker in _OLD_MARKERS)
    assert raw_document_refusal("python_exec", {"code": _DECK})


@pytest.mark.parametrize("code", [
    "import openpyxl\nwb = openpyxl.load_workbook('sales.xlsx')\nprint(wb.sheetnames)",
    "import json\nprint(json.dumps({'a': 1}))",
    "print(generate_pptx)",          # names the generator: left alone, as before
])
def test_reading_a_file_or_ordinary_code_runs(code):
    assert raw_document_refusal("python_exec", {"code": code}) == ""


def test_shell_commands_are_read_and_other_tools_are_not():
    assert raw_document_refusal("shell_exec", {"command": "python -c \"from pptx import Presentation\""})
    assert raw_document_refusal("file_write", {"content": _DECK}) == ""
    assert {"python_exec", "code_exec", "shell_exec"} == set(CODE_TOOLS)


def test_the_tool_worker_asks_the_guard():
    """The one decision is the module's: the tool worker holds no marker list."""
    from pathlib import Path

    import kazma_core.agent.graph_tool_worker as worker

    source = Path(worker.__file__).read_text(encoding="utf-8")
    assert "raw_document_refusal(" in source
    assert "simpledoctemplate" not in source.lower()
