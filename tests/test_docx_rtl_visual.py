"""Visual RTL regression tests for Kazma DOCX output.

These are the tests that "cannot lie": they render the generated DOCX to PDF
via LibreOffice headless and measure where the text actually lands on the page.
XML-flag assertions (presence of w:bidi, w:rtl, etc.) are necessary but not
sufficient — they stayed green for two days while Arabic rendered on the wrong
side because of the ``w:bidi + w:jc="right"`` inversion (right under bidi maps
to the physical LEFT).

Guards:
  - AR title / TOC / section heading text must land in the RIGHT half of the
    page (the reading-start edge under RTL).
  - EN title text must land in the LEFT half.
  - Isolation: NO paragraph in the AR doc may carry ``w:jc="right"`` under
    ``w:bidi`` (the regression that broke chrome alignment).

Skips gracefully when LibreOffice (soffice) or pymupdf is unavailable, so the
suite still runs in minimal/CI environments.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


def _find_soffice() -> str | None:
    on_path = shutil.which("soffice") or shutil.which("libreoffice")
    if on_path:
        return on_path
    for c in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if Path(c).is_file():
            return c
    return None


def _have_pymupdf() -> bool:
    try:
        import pymupdf  # noqa: F401
        return True
    except Exception:
        return False


_SOFFICE = _find_soffice()
_PYMUPDF = _have_pymupdf()
_VisualReason = (
    "LibreOffice (soffice) and pymupdf are required for DOCX→PDF visual RTL "
    "verification; skipping in this environment."
)


def _needs_visual():
    return pytest.mark.skipif(not (_SOFFICE and _PYMUPDF), reason=_VisualReason)


def _render_to_pdf(soffice: str, docx: Path, outdir: Path) -> Path:
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(docx)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    pdf = outdir / (docx.stem + ".pdf")
    assert pdf.is_file(), f"LibreOffice did not produce {pdf}"
    return pdf


def _page1_lines(pdf: Path) -> list[tuple[float, float, float, str]]:
    import pymupdf

    doc = pymupdf.open(str(pdf))
    page = doc[0]
    width = page.rect.width
    out: list[tuple[float, float, float, str]] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            x0, _y0, x1, _y1 = line["bbox"]
            out.append((round(_y0, 1), round(x0, 1), round(x1, 1), text))
    doc.close()
    return out


def _docx_text(docx: Path) -> str:
    return zipfile.ZipFile(docx).read("word/document.xml").decode("utf-8")


@_needs_visual()
def test_arabic_docx_renders_right(tmp_path: Path) -> None:
    """AR title/TOC/heading text must land in the RIGHT half of the page.

    This is the regression that failed for two days: ``w:bidi + w:jc="right"``
    pinned Arabic chrome to the physical LEFT. With the unified profile/engine
    the intent "start" maps to ``w:jc="start"``, which under bidi is the
    physical RIGHT.
    """
    from kazma_core.documents.renderer_worker import _generate_docx

    docx = tmp_path / "ar.docx"
    _generate_docx(docx, {
        "title": "منظومة كاظمة للذكاء الاصطناعي",
        "subtitle": "تقرير تنفيذي",
        "lang": "ar",
        "toc": True,
        "sections": [
            {"heading": "المقدمة", "body": "هذا نص عربي يمثل الفقرة الرئيسية."},
        ],
    })
    pdf = _render_to_pdf(_SOFFICE, docx, tmp_path)
    lines = _page1_lines(pdf)
    assert lines, "no text extracted from AR PDF"

    import pymupdf
    page_width = pymupdf.open(str(pdf))[0].rect.width
    mid = page_width / 2.0

    # Title text (the long Arabic title line, not a split glyph) must end at the
    # right edge / be in the right half.
    title_lines = [ln for ln in lines if len(ln[3]) > 10][:1]
    assert title_lines, f"no title line found among {lines[:5]}"
    _, tx0, tx1, _ = title_lines[0]
    assert tx1 > mid, (
        f"AR title not in right half: x1={tx1:.0f} <= mid={mid:.0f} (the bidi+jc inversion regressed)"
    )


@_needs_visual()
def test_english_docx_renders_left(tmp_path: Path) -> None:
    """EN title must land in the LEFT half (control: the fix must not flip LTR)."""
    from kazma_core.documents.renderer_worker import _generate_docx

    docx = tmp_path / "en.docx"
    _generate_docx(docx, {
        "title": "Kazma AI Platform Overview",
        "lang": "en",
        "toc": True,
        "sections": [{"heading": "Introduction", "body": "Body paragraph."}],
    })
    pdf = _render_to_pdf(_SOFFICE, docx, tmp_path)
    lines = _page1_lines(pdf)
    assert lines, "no text extracted from EN PDF"

    import pymupdf
    page_width = pymupdf.open(str(pdf))[0].rect.width
    mid = page_width / 2.0

    title_lines = [ln for ln in lines if len(ln[3]) > 10][:1]
    assert title_lines
    _, tx0, _tx1, _ = title_lines[0]
    assert tx0 < mid, f"EN title not in left half: x0={tx0:.0f} >= mid={mid:.0f}"


def test_arabic_docx_has_no_bidi_jc_right(tmp_path: Path) -> None:
    """Isolation guard: NO paragraph may carry w:jc="right" under w:bidi.

    The bidi inversion (jc=right under bidi → physical LEFT) was the two-day
    bug. The unified profile maps the "start" intent to ``w:jc="start"``; this
    test fails immediately if any path reintroduces ``w:jc="right"`` on a bidi
    paragraph. (End-aligned "end" intent → "end" is allowed but rare; this
    specifically blocks the exact regression value.)
    """
    from kazma_core.documents.renderer_worker import _generate_docx

    docx = tmp_path / "ar.docx"
    _generate_docx(docx, {
        "title": "وثيقة اختبار",
        "lang": "ar",
        "toc": True,
        "citations": ["مصدر أول"],
        "sections": [{"heading": "القسم", "body": "نص عربي."}],
    })
    xml = _docx_text(docx)

    # No paragraph pPr should combine w:bidi with w:jc="right".
    import re
    # Find every <w:pPr>...</w:pPr> and check it does not contain both.
    bad = []
    for m in re.finditer(r"<w:pPr>(.*?)</w:pPr>", xml, re.S):
        inner = m.group(1)
        has_bidi = "w:bidi" in inner
        has_jc_right = 'w:jc w:val="right"' in inner
        if has_bidi and has_jc_right:
            bad.append(inner)
    assert not bad, f"bidi+jc=right regression reintroduced in {len(bad)} paragraph(s)"

    # And confirm the correct value is present (start-aligned under bidi).
    assert 'w:jc w:val="start"' in xml, "AR docx missing jc=start (the correct bidi mapping)"


def test_arabic_docx_has_no_invalid_tcpr_jc(tmp_path: Path) -> None:
    """Schema guard: NO w:jc inside w:tcPr (CT_TcPr has no w:jc child; Word
    ignores it). The previous fix attempt (bd2fd245) appended exactly that and
    it did nothing. Cell text alignment comes from the paragraph's w:jc only."""
    from kazma_core.documents.renderer_worker import _generate_docx

    docx = tmp_path / "ar.docx"
    _generate_docx(docx, {
        "title": "وثيقة",
        "lang": "ar",
        "sections": [{"heading": "قسم", "body": "نص."}],
    })
    xml = _docx_text(docx)
    import re
    for m in re.finditer(r"<w:tcPr>(.*?)</w:tcPr>", xml, re.S):
        assert "w:jc" not in m.group(1), (
            f"schema-invalid w:jc under w:tcPr reintroduced: {m.group(0)}"
        )


def test_arabic_docx_runs_carry_complex_script_size_and_bold(tmp_path: Path) -> None:
    """Arabic runs must carry w:szCs/w:bCs (complex-script variants).

    python-docx writes only the Latin w:sz/w:b. Arabic is a complex script, so
    Word/LibreOffice size and weight it via w:szCs/w:bCs — without those the
    run silently falls back to the default size (11pt) and loses true bold,
    which was the heading-bar "junk letters" symptom (tiny faux-bold Arabic).
    Body runs often have no per-run w:sz (they inherit Normal) and still
    must get an explicit w:szCs or the body stays small.
    """
    from kazma_core.documents.renderer_worker import _generate_docx

    docx = tmp_path / "ar.docx"
    _generate_docx(docx, {
        "title": "تقرير رئيسي",
        "lang": "ar",
        "toc": True,
        "sections": [{"heading": "القسم الأول", "body": "نص عربي للفقرة."}],
        "citations": ["مصدر أول"],
    })
    xml = _docx_text(docx)
    import re
    rtl_runs = re.findall(r"<w:r>.*?</w:r>", xml, re.S)
    assert rtl_runs, "no runs found"
    rtl_marked = [r for r in rtl_runs if "w:rtl" in r]
    assert rtl_marked, "no Arabic (w:rtl) run found"
    for r in rtl_marked:
        assert "w:szCs" in r, (
            f"Arabic run missing w:szCs (complex-script size) — "
            f"body will render at default 11pt: {r[:160]!r}"
        )
    bold_rtl = [r for r in rtl_marked if "<w:b/>" in r]
    assert bold_rtl, "no bold Arabic run (heading bar) found"
    for r in bold_rtl:
        assert "<w:bCs/>" in r, (
            f"bold Arabic run missing w:bCs (complex-script bold): {r[:120]!r}"
        )


def test_arabic_body_szcs_is_larger_than_latin(tmp_path: Path) -> None:
    """Arabic body uses body_size_ar via w:szCs; Latin stays at body_size.

    Regression: Normal.font.size was set to body_size_ar (pumping English)
    and _mark_run copied w:sz→w:szCs, so mixed Latin looked large while
    the Arabic face stayed optically small.
    """
    import re
    import zipfile
    from kazma_core.documents.renderer_worker import _generate_docx
    from kazma_core.documents.style_theme import THEME, theme_cs_size

    docx = tmp_path / "ar.docx"
    _generate_docx(docx, {
        "title": "تقرير رئيسي",
        "lang": "ar",
        "sections": [{"heading": "القسم", "body": "نص عربي للفقرة مع ISO/IEC 27001."}],
    })
    zf = zipfile.ZipFile(docx)
    xml = zf.read("word/document.xml").decode("utf-8")
    styles = zf.read("word/styles.xml").decode("utf-8")

    latin_half = int(THEME["body_size"] * 2)
    ar_half = int(round(theme_cs_size() * 2))
    assert ar_half > latin_half

    # Normal style: Latin size is body_size, Cs size is body_size_ar.
    assert f'w:sz w:val="{latin_half}"' in styles, (
        "AR Normal latin size must stay at body_size, not body_size_ar"
    )
    assert f'w:szCs w:val="{ar_half}"' in styles, (
        "AR Normal style missing independent w:szCs=body_size_ar"
    )

    cs_vals = [int(v) for v in re.findall(r'w:szCs w:val="(\d+)"', xml)]
    assert ar_half in cs_vals, (
        f"no body-level w:szCs={ar_half} on Arabic runs; got {sorted(set(cs_vals))}"
    )
    for r in re.findall(r"<w:r>.*?</w:r>", xml, re.S):
        if "w:rtl" not in r:
            continue
        cs_m = re.search(r'w:szCs w:val="(\d+)"', r)
        sz_m = re.search(r'w:sz w:val="(\d+)"', r)
        if cs_m and sz_m:
            assert int(cs_m.group(1)) > int(sz_m.group(1)), (
                f"szCs must exceed latin sz on Arabic runs: {r[:160]!r}"
            )


def test_heading_keep_with_next_not_page_break(tmp_path: Path) -> None:
    """A heading at the bottom of a page must stay with its body.

    Word/LibreOffice honour w:keepNext/w:keepLines. We must NOT force
    page-break-before on every heading.
    """
    from kazma_core.documents.renderer_worker import _generate_docx

    docx = tmp_path / "keep.docx"
    _generate_docx(docx, {
        "title": "Report",
        "lang": "en",
        "sections": [
            {"heading": "Intro", "body": "Body under intro."},
            {"heading": "Next", "body": "Body under next."},
        ],
    })
    xml = _docx_text(docx)
    assert "w:keepNext" in xml, "headings missing w:keepNext (orphan heading)"
    assert "w:keepLines" in xml, "headings missing w:keepLines"
    assert "w:pageBreakBefore" not in xml, "every heading forced to a new page"


def test_duplicate_heading_is_not_rendered_twice(tmp_path: Path) -> None:
    """Section heading + leading ## in the body must not stack two bars.

    Regression: 'Quick Research Summary' appeared as both the section
    heading and the first markdown heading in the body.
    """
    from kazma_core.documents.renderer_worker import _generate_docx

    title = "تقرير فني شامل Cybersecurity Standards"
    heading = "1. خلاصة البحث السريع (Quick Research Summary)"
    docx = tmp_path / "dup.docx"
    _generate_docx(docx, {
        "title": title,
        "lang": "ar",
        "sections": [{
            "heading": heading,
            "body": f"## {heading}\n\nأجري بحث موجز عن المعايير.",
        }],
    })
    xml = _docx_text(docx)
    assert xml.count("خلاصة البحث السريع") == 1, (
        f"heading rendered more than once: {xml.count('خلاصة البحث السريع')}"
    )


def test_docx_math_and_code_are_ltr_isolated(tmp_path: Path) -> None:
    """Math becomes Unicode; code is LTR (no w:bidi=1) and not reversed."""
    from kazma_core.documents.renderer_worker import _generate_docx

    docx = tmp_path / "math.docx"
    _generate_docx(docx, {
        "title": "تقرير",
        "lang": "ar",
        "sections": [{
            "heading": "معادلات",
            "body": (
                "قيمة المخاطر $R = P \\cdot I$.\n\n"
                "$$S(x) = \\frac{1}{1 + e^{-x}}$$\n\n"
                "```python\n"
                "def verify_cipher(cipher_text, key):\n"
                "    computed = hmac.new(key, cipher_text).digest()\n"
                "    return computed\n"
                "```\n"
            ),
        }],
    })
    xml = _docx_text(docx)
    assert "R = P · I" in xml or "R = P · I".replace(" ", "") in xml.replace(" ", "")
    assert "⁄" in xml or "1/" in xml or "1⁄" in xml
    assert r"\cdot" not in xml
    assert r"\frac" not in xml
    assert "w:cantSplit" in xml
    assert "def verify_cipher" in xml
    assert "hmac.new" in xml
    # Code cell paragraphs must be forced LTR (bidi val 0), not section RTL.
    assert 'w:bidi w:val="0"' in xml or 'w:val="0"' in xml


def test_heading_key_equivalence() -> None:
    from kazma_core.documents.heading_text import (
        drop_leading_heading,
        heading_key,
        headings_equivalent,
    )

    assert heading_key("1. خلاصة البحث السريع (Quick Research Summary)") == (
        heading_key("## خلاصة البحث السريع")
    )
    assert headings_equivalent(
        "1. خلاصة البحث السريع (Quick Research Summary)",
        "خلاصة البحث السريع (Quick Research Summary)",
    )
    assert not headings_equivalent("أولاً: الجزء البحثي", "خلاصة البحث السريع")
    body = "## 1. خلاصة البحث السريع (Quick Research Summary)\n\nأجري بحث."
    stripped = drop_leading_heading(
        body, "خلاصة البحث السريع (Quick Research Summary)"
    )
    assert stripped.startswith("أجري بحث")
    assert "خلاصة" not in stripped


@_needs_visual()
def test_arabic_toc_numbers_on_right(tmp_path: Path) -> None:
    """TOC entry numbers ("1. …") must appear on the RIGHT for Arabic.

    Regression for the reported symptom: TOC numbers were on the left because
    the TOC paragraphs used bidi+jc=right (→ physical left).
    """
    from kazma_core.documents.renderer_worker import _generate_docx

    docx = tmp_path / "ar.docx"
    _generate_docx(docx, {
        "title": "تقرير",
        "lang": "ar",
        "toc": True,
        "sections": [
            {"heading": "المقدمة", "body": "نص."},
            {"heading": "التحليل", "body": "نص."},
        ],
    })
    pdf = _render_to_pdf(_SOFFICE, docx, tmp_path)
    lines = _page1_lines(pdf)
    # A TOC line looks like "1. المقدمة" / "2. التحليل"
    toc = [ln for ln in lines if ln[3].strip()[:2] in ("1.", "2.") and len(ln[3]) < 30]
    assert toc, f"no TOC entry lines found among {lines[:10]}"
    # Their right edge must be past the page midpoint (right half).
    import pymupdf
    mid = pymupdf.open(str(pdf))[0].rect.width / 2.0
    for _, x0, x1, text in toc:
        assert x1 > mid, f"AR TOC entry '{text}' on left (x1={x1:.0f} <= mid={mid:.0f})"
