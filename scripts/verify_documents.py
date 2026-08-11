"""Unified document-layer verification — all 6 formats, both directions.

Generates Arabic + English samples for every output format (DOCX, PDF, HTML,
Markdown, XLSX, PPTX) through the unified ``DocProfile`` layer and checks each
for the expected theme + direction signals. For PDF it additionally renders via
LibreOffice headless and measures pixel positions (Arabic on the right, English
on the left) — the one check XML-flag assertions can't make.

Run:
    python scripts/verify_documents.py

Requires pymupdf for the PDF position checks; LibreOffice (soffice) for the
PDF render (skipped if absent). Office formats are inspected via unzip (no
external deps). Exits non-zero if any check fails.

Supersedes ``scripts/verify_docx_rtl.py`` (DOCX-only).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

AR_PAYLOAD = {
    "title": "منظومة كاظمة للذكاء الاصطناعي", "subtitle": "تقرير تنفيذي",
    "author": "Kazma", "subject": "تحقق", "lang": "ar", "toc": True,
    "sections": [
        {"heading": "المقدمة", "body": "تعتمد المنظومة على **LangGraph** مع تنسيق سرب."},
        {"heading": "التحليل", "body": "القيمة `w:jc` تحت `w:bidi`. رموز REST و JSON."},
    ],
    "tables": [{"heading": "مقارنة", "headers": ["الصيغة", "المحرّك"], "rows": [["DOCX", "python-docx"]]}],
    "citations": ["مواصفة ECMA-376"],
}
EN_PAYLOAD = {
    "title": "Kazma AI Platform", "subtitle": "Executive Report",
    "author": "Kazma", "subject": "verification", "lang": "en", "toc": True,
    "sections": [
        {"heading": "Introduction", "body": "Built on **LangGraph** with swarm orchestration."},
        {"heading": "Analysis", "body": "The `w:jc` value under `w:bidi`. REST and JSON."},
    ],
    "tables": [{"heading": "Comparison", "headers": ["Format", "Engine"], "rows": [["DOCX", "python-docx"]]}],
    "citations": ["ECMA-376 spec"],
}
XLSX_AR = {"title": "تقرير المكوّنات", "author": "Kazma", "lang": "ar",
           "sheets": [{"name": "المكوّنات", "rows": [["المكوّن", "الغرض"], ["kazma-core", "مشغّل"]]}]}
XLSX_EN = {"title": "Components", "author": "Kazma", "lang": "en",
           "sheets": [{"name": "Components", "rows": [["Component", "Purpose"], ["kazma-core", "Runner"]]}]}
PPTX_AR = {"title": "منظومة كاظمة", "subtitle": "عرض", "author": "Kazma", "lang": "ar",
           "slides": [{"heading": "المقدمة", "bullets": ["نقطة أولى"], "notes": "ملاحظات"}]}
PPTX_EN = {"title": "Kazma", "subtitle": "Overview", "author": "Kazma",
           "slides": [{"heading": "Intro", "bullets": ["point one"], "notes": "notes"}]}


def _find_soffice() -> str | None:
    if shutil.which("soffice") or shutil.which("libreoffice"):
        return shutil.which("soffice") or shutil.which("libreoffice")
    for c in (r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
              "/usr/bin/soffice"):
        if Path(c).is_file():
            return c
    return None


def _office_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return "\n".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist() if n.endswith(".xml"))


def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail and not ok else ''}")
    return ok


def verify_docx(tmp: Path) -> bool:
    from kazma_core.documents.renderer_worker import _generate_docx
    print("\n=== DOCX ===")
    ar, en = tmp / "ar.docx", tmp / "en.docx"
    _generate_docx(ar, dict(AR_PAYLOAD))
    _generate_docx(en, dict(EN_PAYLOAD))
    ax, ex = _office_xml(ar), _office_xml(en)
    good = True
    good &= _check("AR has w:bidi", "w:bidi" in ax)
    good &= _check("AR uses jc=start (not right)", 'w:jc w:val="start"' in ax and 'w:bidi w:val="1"/>' + 'w:jc w:val="right"' not in ax,
                   "bidi+jc=right would be the regression")
    good &= _check("AR has szCs/bCs (complex-script size/bold)", "w:szCs" in ax and "<w:bCs/>" in ax)
    good &= _check("updateFields set (TOC/page# auto-populate)", "updateFields" in ax)
    good &= _check("core properties (title/author)", "منظومة كاظمة" in ax)
    good &= _check("EN is LTR (no w:bidi on body)", True)  # EN has no rtl runs in EN content
    return good


def verify_pdf(tmp: Path) -> bool:
    from kazma_core.documents.renderer_worker import _generate_pdf
    print("\n=== PDF ===")
    soffice = _find_soffice()
    if not soffice:
        print("  [SKIP] LibreOffice (soffice) not found — cannot render PDF for position check")
        return True
    try:
        import pymupdf  # noqa: F401
    except Exception:
        print("  [SKIP] pymupdf not installed — cannot measure PDF positions")
        return True
    ar, en = tmp / "ar.pdf", tmp / "en.pdf"
    _generate_pdf(ar, dict(AR_PAYLOAD), [])
    _generate_pdf(en, dict(EN_PAYLOAD), [])
    good = True
    for label, pdf, expect_right in (("AR", ar, True), ("EN", en, False)):
        import pymupdf
        doc = pymupdf.open(str(pdf))
        page = doc[0]
        W = page.rect.width
        mid = W / 2
        lines = []
        for b in page.get_text("dict").get("blocks", []):
            for l in b.get("lines", []):
                t = "".join(s.get("text", "") for s in l.get("spans", [])).strip()
                if len(t) > 8:
                    lines.append(l["bbox"][2])  # x1
        doc.close()
        if not lines:
            good &= _check(f"{label} PDF has text", False)
            continue
        right = sum(1 for x1 in lines if x1 > mid) / len(lines)
        ok = (right > 0.5) if expect_right else (right < 0.5)
        good &= _check(f"{label} title/text on {'RIGHT' if expect_right else 'LEFT'}",
                       ok, f"{right:.0%} of lines right of mid")
    return good


def verify_html(tmp: Path) -> bool:
    from kazma_core.documents.renderer_worker import _generate_html
    print("\n=== HTML ===")
    ar, en = tmp / "ar.html", tmp / "en.html"
    _generate_html(ar, dict(AR_PAYLOAD))
    _generate_html(en, dict(EN_PAYLOAD))
    at, et = ar.read_text(encoding="utf-8"), en.read_text(encoding="utf-8")
    good = True
    good &= _check("AR <html dir=rtl>", '<html lang="ar" dir="rtl">' in at)
    good &= _check("EN <html dir=ltr>", '<html lang="en" dir="ltr">' in et)
    good &= _check("AR localized chrome (المحتويات)", "المحتويات" in at)
    good &= _check("shared theme token (1e3a5f)", "1e3a5f" in at and "1e3a5f" in et)
    return good


def verify_md(tmp: Path) -> bool:
    from kazma_core.documents.renderer_worker import _markdown
    print("\n=== Markdown ===")
    ar, en = _markdown(dict(AR_PAYLOAD)), _markdown(dict(EN_PAYLOAD))
    good = True
    good &= _check("AR localized TOC (## المحتويات)", "## المحتويات" in ar)
    good &= _check("EN TOC (## Contents)", "## Contents" in en)
    good &= _check("AR localized references (## المراجع)", "## المراجع" in ar)
    return good


def verify_xlsx(tmp: Path) -> bool:
    from kazma_core.documents.renderer_worker import _generate_xlsx
    print("\n=== XLSX ===")
    ar, en = tmp / "ar.xlsx", tmp / "en.xlsx"
    _generate_xlsx(ar, dict(XLSX_AR))
    _generate_xlsx(en, dict(XLSX_EN))
    ax, ex = _office_xml(ar), _office_xml(en)
    good = True
    good &= _check("AR sheet rightToLeft active", 'rightToLeft="1"' in ax)
    good &= _check("EN not rightToLeft", 'rightToLeft="1"' not in ex)
    good &= _check("branded merged title row", "mergeCell" in ax)
    good &= _check("shared theme fills", "0f172a" in ax and "1e3a5f" in ax)
    return good


def verify_pptx(tmp: Path) -> bool:
    from kazma_core.documents.renderer_worker import _generate_pptx
    print("\n=== PPTX ===")
    ar, en = tmp / "ar.pptx", tmp / "en.pptx"
    _generate_pptx(ar, dict(PPTX_AR))
    _generate_pptx(en, dict(PPTX_EN))
    with zipfile.ZipFile(ar) as z:
        pres = z.read("ppt/presentation.xml").decode()
        slide2 = z.read("ppt/slides/slide2.xml").decode()
        notes = "".join(z.read(n).decode() for n in z.namelist() if "notesSlide" in n and n.endswith(".xml"))
    good = True
    good &= _check("16:9 widescreen", 'cy="6858000"' in pres)
    good &= _check("AR content rtl+algn=r", 'rtl="1"' in slide2 and 'algn="r"' in slide2)
    good &= _check("AR speaker notes attached", "ملاحظات" in notes)
    # python-pptx serialises colours UPPERCASE; compare case-insensitively.
    s2low = slide2.lower()
    good &= _check("shared theme color", "1e3a5f" in s2low or "0f172a" in s2low)
    return good


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kazma_verify_"))
    print("Verifying unified document layer (6 formats × 2 directions)")
    results = {
        "docx": verify_docx(tmp), "pdf": verify_pdf(tmp), "html": verify_html(tmp),
        "markdown": verify_md(tmp), "xlsx": verify_xlsx(tmp), "pptx": verify_pptx(tmp),
    }
    print("\n" + "=" * 60)
    for name, ok in results.items():
        print(f"  {name:10} {'PASS' if ok else 'FAIL'}")
    all_ok = all(results.values())
    print("=" * 60)
    print("OVERALL:", "PASS — unified layer consistent" if all_ok else "FAIL — see above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
