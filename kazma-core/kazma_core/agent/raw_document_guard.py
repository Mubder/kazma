"""Code that builds a document by hand is sent to Kazma's generators.

A model asked for a PDF, a Word file, a workbook or a deck sometimes writes
the file itself in ``python_exec`` (reportlab, python-docx, openpyxl,
python-pptx...). The result skips everything the generators do -- the house
face in every font slot, Arabic shaping, direction per block, the verified
render -- and the attempt burns iterations (audit 2026-08-14: a 100-iteration
rabbit hole on an Arabic calendar). The tool worker refuses such a call
before it runs and names the generator for that format.

The deck the owner reported on 2026-11-10 ("set to the Kazma font but shown
as Arial") was python-pptx code run this way: the guard listed the PDF, Word
and Excel libraries but not PowerPoint, and always named ``generate_pdf``.
Gate: ``tests/test_raw_document_guard.py``.
"""

from __future__ import annotations

__all__ = ["RAW_DOCUMENT_MARKERS", "raw_document_refusal"]

#: What, in lower-cased code, says it builds a file of the generator's format.
#: Reading a file (``load_workbook(``, ``openpyxl.load``) is not building one.
RAW_DOCUMENT_MARKERS: dict[str, tuple[str, ...]] = {
    "generate_pdf": ("reportlab", "fpdf", "simpledoctemplate", "platypus", "canvas("),
    "generate_docx": ("docx.document", "from docx"),
    "generate_xlsx": ("openpyxl.workbook(", "workbook()", "xlsxwriter"),
    "generate_pptx": ("from pptx", "import pptx", "pptx.presentation", "pptx.util"),
}

#: The tools whose call this guard reads.
CODE_TOOLS = frozenset({"python_exec", "code_exec", "shell_exec"})

_HOW = {
    "generate_pdf": "generate_pdf(title='...', markdown_path='output.md') after writing "
                    "the content to a .md file with file_write",
    "generate_docx": "generate_docx(title='...', markdown_path='output.md') after writing "
                     "the content to a .md file with file_write",
    "generate_xlsx": "generate_xlsx(sheets=[{'name': '...', 'rows': [[...], ...]}], "
                     "filename='...')",
    "generate_pptx": "generate_pptx(title='...', slides=[{'heading': '...', "
                     "'bullets': ['...']}, ...])",
}


def raw_document_refusal(tool: str, args: dict) -> str:
    """The refusal for a call that builds a document by hand, or ``""``."""
    if str(tool).lower() not in CODE_TOOLS:
        return ""
    code = str((args or {}).get("code") or (args or {}).get("command") or "").lower()
    if not code or any(name in code for name in RAW_DOCUMENT_MARKERS):
        return ""
    wanted = [name for name, marks in RAW_DOCUMENT_MARKERS.items()
              if any(mark in code for mark in marks)]
    if not wanted:
        return ""
    tools = "\n".join(f"- {_HOW[name]}" for name in wanted)
    return (
        "SYSTEM OVERRIDE: You are trying to build a document via raw Python. "
        "STOP -- use Kazma's document generator for that format instead:\n"
        f"{tools}\n"
        "The generators use Kazma's house font in every font slot, shape Arabic, "
        "lay out each block in its own direction and check the result -- your "
        "manual code cannot. Do NOT retry with Python."
    )
