"""A PDF is parsed only inside the bounded document worker.

Found 2026-10-13 (F01): the document intent handler opened the user's source
PDF with fitz in the server process before trying the documents service, and
the document pipeline fell back to fitz exactly when the worker had refused
the file -- both outside the worker sandbox and the PDF active-content
policy (``documents/pdf_policy.py``). This gate reads every product module
and fails on a PDF-library opener anywhere but the worker-side modules, or a
declared check of a PDF Kazma itself just generated.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Modules that run inside the document worker (or a container job).
WORKER_SIDE = frozenset({
    "kazma-core/kazma_core/documents/sniff.py",
    "kazma-core/kazma_core/documents/mutation_worker.py",
    "kazma-core/kazma_core/documents/renderer_worker.py",
    "kazma-core/kazma_core/documents/parsers/pdf.py",
    "kazma-core/kazma_core/documents/ocr/raster.py",
})
#: In-process openers of a PDF that Kazma's own renderer just produced.
OWN_OUTPUT_CHECKS = {
    "kazma-skills/kazma_skills/native/document_generator/tools.py": (
        "verifies that the PDF the generator itself just wrote holds the section text"
    ),
    "kazma-core/kazma_core/agent/intent/handlers/document.py": (
        "the quality gate counts pages of the document the generate tool just produced; "
        "the user's source PDF goes through the documents service"
    ),
    "kazma-core/kazma_core/documents/fidelity.py": (
        "the fidelity corpus measures (page count, alignment, rasterizing a page for OCR) "
        "PDFs Kazma's own PDF engine just rendered from the fixed corpus text; the "
        "extraction under test goes through the documents service's isolated parser"
    ),
}


# The PDF libraries, and how each opens a document. Matched through the
# module's own imports so an alias (``import pypdfium2 as pdfium``) or an
# attribute call (``pypdf.PdfReader``, ``fitz.Document``) is seen, not only the
# canonical ``module.attr`` spellings (Q04 OBS-03, 2026-10-15).
_MODULE_OPENERS = {
    "fitz": {"open", "Document"},
    "pymupdf": {"open", "Document"},
    "pdfplumber": {"open", "PDF"},
    "pdfium": {"PdfDocument"},
    "pypdfium2": {"PdfDocument"},
    "pypdf": {"PdfReader", "PdfDocument"},
}
#: Class names distinctive enough to be a PDF opener on any receiver or bare.
_BARE_OPENERS = frozenset({"PdfReader", "PdfDocument"})


def _import_map(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """Local name -> the PDF module it refers to, and the bare opener names a
    ``from <pdf module> import ...`` brought in (alias included)."""
    modules: dict[str, str] = {}
    bare: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _MODULE_OPENERS:
                    modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in _MODULE_OPENERS:
            openers = _MODULE_OPENERS[node.module] | _BARE_OPENERS
            for alias in node.names:
                if alias.name in openers:
                    bare.add(alias.asname or alias.name)
    return modules, bare


def _openers(tree: ast.AST) -> list[tuple[int, str]]:
    modules, bare = _import_map(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            receiver, attr = func.value.id, func.attr
            canonical = modules.get(receiver) or (receiver if receiver in _MODULE_OPENERS else None)
            if (canonical and attr in _MODULE_OPENERS[canonical]) or attr in _BARE_OPENERS:
                found.append((node.lineno, f"{receiver}.{attr}"))
        elif isinstance(func, ast.Name) and (func.id in _BARE_OPENERS or func.id in bare):
            found.append((node.lineno, func.id))
    return found


def _product_files() -> list[str]:
    listed = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return [
        rel for rel in listed
        if rel.startswith(("kazma-core/kazma_core/", "kazma-skills/kazma_skills/", "kazma-ui/kazma_ui/",
                           "kazma-gateway/kazma_gateway/", "kazma-cli/kazma_cli/", "kazma-tui/kazma_tui/"))
    ]


def test_pdf_libraries_open_files_only_inside_the_worker() -> None:
    offenders = []
    seen_declared = set()
    for rel in _product_files():
        path = REPO / rel
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        hits = _openers(tree)
        if not hits:
            continue
        if rel in WORKER_SIDE:
            continue
        if rel in OWN_OUTPUT_CHECKS:
            seen_declared.add(rel)
            continue
        offenders += [f"{rel}:{line} {name}()" for line, name in hits]
    assert not offenders, (
        "A PDF is opened outside the document worker -- no sandbox, no active-content "
        "policy. Read it through DocumentService (read_transient_sync / parse_path):\n  "
        + "\n  ".join(offenders)
    )
    stale = set(OWN_OUTPUT_CHECKS) - seen_declared
    assert not stale, f"declared own-output checks that no longer open a PDF: {sorted(stale)}"


def test_the_gate_sees_the_old_in_process_reader() -> None:
    # Negative control: the intent handler's _read_pdf_raw before 2026-10-13.
    old = (
        "def _read_pdf_raw(path):\n"
        "    import fitz\n"
        "    doc = fitz.open(str(path))\n"
        "    return '\\n'.join(page.get_text() for page in doc)\n"
    )
    assert _openers(ast.parse(old)) == [(3, "fitz.open")]
    assert _openers(ast.parse("from pypdf import PdfReader\nr = PdfReader(p)\n")) == [(2, "PdfReader")]


def test_the_gate_sees_alias_and_attribute_openers() -> None:
    # Q04 OBS-03: shapes the old pair-list missed -- an aliased import, an
    # attribute call, and a bare opener imported under another name.
    cases = {
        "import pypdfium2 as pdfium\npdfium.PdfDocument(p)\n": "pdfium.PdfDocument",
        "import fitz\nfitz.Document(p)\n": "fitz.Document",
        "import pypdf\npypdf.PdfReader(p)\n": "pypdf.PdfReader",
        "import pymupdf as fitz\nfitz.open(p)\n": "fitz.open",
        "from pypdf import PdfReader as PR\nPR(p)\n": "PR",
    }
    for source, label in cases.items():
        hits = _openers(ast.parse(source))
        assert [name for _line, name in hits] == [label], source
    # Not a PDF opener: python-docx's Document, a plain file open.
    assert _openers(ast.parse("import docx\ndocx.Document(p)\n")) == []
    assert _openers(ast.parse("import zipfile\nzipfile.ZipFile(p).open(n)\n")) == []


def test_the_source_readers_use_the_documents_service() -> None:
    # The declared own-output exemption above covers the intent handler's
    # quality gate only; its reader of the USER's PDF opens nothing itself.
    # The document pipeline's ``_read_pdf`` was here too; the pipeline had no
    # caller and was removed (audit 2026-10-26 AUD-075).
    for rel, name in (
        ("kazma-core/kazma_core/agent/intent/handlers/document.py", "_read_pdf_raw"),
    ):
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        assert _openers(function) == [], rel
        calls = {
            node.func.attr for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "read_transient_sync" in calls, rel
