"""Arabic shaping and direction live in ONE module, and shaping is cheap.

AGENTS §19H: ``documents/arabic.py`` is the only home for Arabic text policy
— one shaping pass, direction by bidi class, no codepoint-block regex, no
"any RTL character near the start" shortcut. On 2026-09-25 three copies
outside it were found:

* ``rich_render.shape_for_pdf`` built its own reshaper per call and gated on
  a block regex;
* ``parsers/pdf_layout.is_rtl_dominant`` kept the block regex AND the
  "first 200 characters" shortcut §19H had removed from the original — so
  PDF reading order flipped English pages to right-to-left;
* the document pipeline's fallback reader ran python-bidi's
  ``get_display(base_dir="R")`` on every page, reversing logical Arabic
  into visual order before the model read it.

And ``arabic._reshape`` itself built a fresh ``ArabicReshaper`` per call:
line wrapping shapes every candidate line, 130,123 constructions were 96%
of ``test_document_layout.py``'s 112s, and on CI one PDF test outlived its
120s timeout and took its chunk down.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOME = "kazma-core/kazma_core/documents/arabic.py"
AR_PARAGRAPH = (
    "هذا نص عربي طويل لاختبار التفاف الأسطر في محرك التقارير، وفيه كلمات كثيرة "
    "تكفي لملء عدة أسطر في عمود ضيق، مع رقم 2026 وكلمة Kazma في المنتصف. "
) * 6


# ── behaviour ─────────────────────────────────────────────────────────────


def test_wrapping_a_long_paragraph_builds_one_reshaper(monkeypatch):
    arabic_reshaper = pytest.importorskip("arabic_reshaper")
    from kazma_core.documents import arabic
    from kazma_core.documents.rich_render import inline_markdown_to_reportlab

    built = {"n": 0}
    real = arabic_reshaper.ArabicReshaper

    class Counting(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            built["n"] += 1
            super().__init__(*a, **k)

    monkeypatch.setattr(arabic_reshaper, "ArabicReshaper", Counting)
    arabic._shared_reshaper.cache_clear()
    try:
        out = inline_markdown_to_reportlab(
            AR_PARAGRAPH, col_width=180.0, font_name="Helvetica", font_size=11.0
        )
    finally:
        arabic._shared_reshaper.cache_clear()
    assert out.count("<br/>") >= 3, "the paragraph should have wrapped several times"
    assert built["n"] <= 1, f"built {built['n']} reshapers for one paragraph"


def test_shape_for_pdf_is_the_one_shaping_pass():
    pytest.importorskip("arabic_reshaper")
    from kazma_core.documents import arabic
    from kazma_core.documents.rich_render import shape_for_pdf

    text = "مرحبا بكم في كاظمه 2026"
    assert shape_for_pdf(text) == arabic.shape_text(text, base_dir="rtl")
    assert shape_for_pdf("plain English") == "plain English"


def test_pdf_reading_order_does_not_flip_english_for_one_arabic_word():
    from kazma_core.documents.parsers.pdf_layout import is_rtl_dominant

    # The Arabic word sits inside the first 200 characters — exactly where the
    # removed shortcut looked, and flipped the whole page.
    english = "كاظمه — " + "Quarterly report for the board. " * 8
    assert not is_rtl_dominant(english), "one Arabic word must not flip an English page"
    assert is_rtl_dominant("تقرير الربع الثالث لمجلس الإدارة " * 4)


def test_the_pdf_fallback_keeps_arabic_and_english_as_written(tmp_path, monkeypatch):
    """A correctly drawn PDF comes back in logical order; the fallback must
    not reorder it again (it ran get_display(base_dir="R") on every page)."""
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("arabic_reshaper")
    from kazma_core.documents import arabic

    font = ROOT / "kazma-core/kazma_core/documents/assets/fonts/IBMPlexSansArabic-Regular.ttf"
    if not font.exists():
        pytest.skip("bundled Arabic font missing")
    doc = pymupdf.open()
    page = doc.new_page()
    # Drawn the way Kazma's own PDF engine draws Arabic: shaped, visual order.
    page.insert_text((72, 100), arabic.shape_text("مرحبا بالعالم", base_dir="rtl"),
                     fontfile=str(font), fontname="plex")
    page.insert_text((72, 140), "Quarterly report, final.", fontname="helv")
    pdf = tmp_path / "mixed.pdf"
    doc.save(str(pdf))
    doc.close()

    from kazma_core.agent.pipelines import document as pipeline
    from kazma_core.documents.service import DocumentService

    def _unavailable(*_a, **_k):
        raise RuntimeError("documents service unavailable")

    monkeypatch.setattr(DocumentService, "read_transient_sync", _unavailable)
    extracted = pipeline._read_pdf(pdf)
    assert "مرحبا" in extracted, f"logical Arabic came back reordered: {extracted!r}"
    assert "Quarterly report, final." in extracted, f"English was reordered: {extracted!r}"


# ── the gate: nothing outside the home shapes or reorders ────────────────

_FORBIDDEN_MODULES = ("arabic_reshaper", "bidi")


def _outside_home_shaping(sources: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for rel, text in sources.items():
        if rel == HOME:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in _FORBIDDEN_MODULES:
                    problems.append(f"{rel}:{node.lineno} imports {name}")
    return problems


def _product_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for root in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                 "kazma-skills/kazma_skills", "kazma-cli/kazma_cli", "kazma-tui/kazma_tui"):
        for p in (ROOT / root).rglob("*.py"):
            if "tests" in p.parts:
                continue
            out[p.relative_to(ROOT).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    return out


def test_only_arabic_py_imports_the_shaping_libraries():
    problems = _outside_home_shaping(_product_sources())
    assert not problems, (
        "Arabic shaping / bidi reordering outside documents/arabic.py (AGENTS "
        "§19H: one module, one shaping pass). Call arabic.shape_text / "
        "shape_spans / to_logical instead:\n  " + "\n  ".join(problems)
    )


def test_a_second_shaping_routine_is_caught():
    """Negative control: the pre-2026-09-25 shape_for_pdf and pipeline fallback."""
    planted = {
        "kazma-core/kazma_core/documents/rich_render.py": textwrap.dedent(
            """
            def shape_for_pdf(text):
                import arabic_reshaper
                from bidi.algorithm import get_display
                return get_display(arabic_reshaper.reshape(text))
            """
        ),
        HOME: "import arabic_reshaper\nfrom bidi.algorithm import get_display\n",
    }
    problems = _outside_home_shaping(planted)
    assert len(problems) == 2, problems
    assert all(p.startswith("kazma-core/kazma_core/documents/rich_render.py") for p in problems)
