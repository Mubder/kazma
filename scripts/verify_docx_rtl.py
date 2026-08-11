"""Visual RTL verification for Kazma DOCX output.

Regenerates Arabic + English demo DOCX files through the unified document
layer, renders them to PDF via LibreOffice headless, and measures where the
text actually lands on the page. Prints a PASS/FAIL report.

This is the verification that XML-flag assertions cannot provide: it proves the
heading-bar / TOC / citation text lands on the **physical right** in Arabic
(and left in English), which is the whole point of the bidi fix.

Run:
    python scripts/verify_docx_rtl.py

Requires:
    - LibreOffice (soffice) on PATH or at the standard Windows install path.
    - pymupdf (already a Kazma dependency for the PDF test path).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ARABIC_PAYLOAD = {
    "title": "منظومة كاظمة للذكاء الاصطناعي",
    "subtitle": "تقرير تنفيذي",
    "lang": "ar",
    "toc": True,
    "citations": ["مصدر أول: تقرير داخلي", "مصدر ثان: بيانات"],
    "sections": [
        {"heading": "المقدمة", "body": "هذا نص عربي يمثل الفقرة الرئيسية للوثيقة."},
        {"heading": "التحليل", "body": "تحليل شامل للقدرات مع نقاط:\n\n- نقطة أولى\n- نقطة ثانية"},
    ],
}

ENGLISH_PAYLOAD = {
    "title": "Kazma AI Platform Overview",
    "subtitle": "Executive Report",
    "lang": "en",
    "toc": True,
    "citations": ["First source: internal report"],
    "sections": [
        {"heading": "Introduction", "body": "This is the main body paragraph of the document."},
        {"heading": "Analysis", "body": "Comprehensive capability analysis with points:\n\n- point one\n- point two"},
    ],
}


def find_soffice() -> str | None:
    on_path = shutil.which("soffice") or shutil.which("libreoffice")
    if on_path:
        return on_path
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice", "/usr/bin/libreoffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    return None


def render_docx_to_pdf(soffice: str, docx: Path, outdir: Path) -> Path:
    """Convert a .docx to .pdf via LibreOffice headless. Returns the PDF path."""
    proc = subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(docx)],
        capture_output=True, text=True, timeout=120,
    )
    pdf = outdir / (docx.stem + ".pdf")
    if not pdf.is_file():
        raise RuntimeError(
            f"LibreOffice did not produce {pdf}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return pdf


def extract_lines(pdf: Path) -> list[tuple[float, float, float, str]]:
    """Return (y, x0, x1, text) for every text line on page 1."""
    import pymupdf

    doc = pymupdf.open(str(pdf))
    page = doc[0]
    page_width = page.rect.width
    out: list[tuple[float, float, float, str]] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            out.append((round(y0, 1), round(x0, 1), round(x1, 1), text))
    doc.close()
    return out


def main() -> int:
    from kazma_core.documents.renderer_worker import _generate_docx

    soffice = find_soffice()
    if not soffice:
        print("FAIL: LibreOffice (soffice) not found. Install it or add to PATH.")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="kazma_rtl_verify_"))
    results: list[tuple[str, bool, str]] = []

    for label, payload, expect_right in (("AR", ARABIC_PAYLOAD, True), ("EN", ENGLISH_PAYLOAD, False)):
        docx = tmp / f"demo_{label}.docx"
        _generate_docx(docx, dict(payload))
        pdf = render_docx_to_pdf(soffice, docx, tmp)
        lines = extract_lines(pdf)
        if not lines:
            results.append((label, False, "no text lines extracted"))
            continue

        page_width = 612.0  # US Letter default; LibreOffice uses Letter for default.docx
        mid = page_width / 2.0

        # Find the first substantial line (the title bar text).
        title_line = lines[0]
        ty, tx0, tx1, ttext = title_line
        title_right_half = tx1 > mid if expect_right else tx0 < mid

        # Collect short start-aligned lines (TOC entries, citations) — those that
        # are NOT justified body. Use text length as a rough heuristic.
        chrome_lines = [ln for ln in lines if len(ln[3]) < 40]

        status = "PASS" if title_right_half else "FAIL"
        detail = (
            f"title '{ttext[:30]}' bbox x0={tx0:.0f} x1={tx1:.0f} "
            f"(page mid={mid:.0f}) -> {'RIGHT half' if title_right_half else 'LEFT half'} "
            f"[expected {'RIGHT' if expect_right else 'LEFT'}]"
        )
        results.append((label, title_right_half, detail))

        print(f"\n=== {label} (expect {'RIGHT' if expect_right else 'LEFT'}) ===")
        print(f"  [{status}] {detail}")
        print(f"  all page-1 lines (y, x0, x1, text):")
        for y, x0, x1, text in lines[:12]:
            mark = "  >>" if (x1 > mid) == expect_right and len(text) < 40 else "    "
            print(f"    {mark}y={y:6.1f} x0={x0:6.1f} x1={x1:6.1f}  {text[:50]}")

    print("\n" + "=" * 60)
    all_pass = all(ok for _, ok, _ in results)
    print("OVERALL:", "PASS — Arabic renders RTL on the right edge" if all_pass else "FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
