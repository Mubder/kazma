"""generate_pdf post-render verification — no false ✅ on incomplete PDFs.

2026-08-27 incident: the model passed a SUMMARY as section bodies ("26
names, all confirmed available…"), the renderer produced exactly that
(headings only, zero names), and the agent reported success. The guardrail
extracts the rendered PDF text and fails the call when the requested
bodies/tables did not reach the document.
"""

from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from kazma_skills.native.document_generator.tools import _verify_pdf_content  # noqa: E402


def _make_pdf(path: Path, text: str) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


NAMES_BODY = (
    "voimfit tonosfit nisusfit audaxfit meginfit tarmofit fortitudofit "
    "labosfit pondusfit pugnafit saxumfit"
)


def test_catches_content_dropped_from_pdf(tmp_path: Path) -> None:
    """The renderer produced headings only — the requested names never made
    it in. Verification must fail the call with a self-correcting error."""
    broken = _make_pdf(
        tmp_path / "broken.pdf",
        "Clean Name Candidates\nTier 1 heading only, no names here",
    )
    err = _verify_pdf_content(
        broken, [{"heading": "Tier 1", "body": NAMES_BODY}], None
    )
    assert err is not None
    assert "VERIFICATION FAILED" in err and "voimfit" in err
    assert "do NOT report success" in err


def test_passes_when_content_present(tmp_path: Path) -> None:
    good = _make_pdf(tmp_path / "good.pdf", "Tier 1\n" + NAMES_BODY)
    assert _verify_pdf_content(
        good, [{"heading": "Tier 1", "body": NAMES_BODY}], None
    ) is None


def test_arabic_only_body_skips_safely(tmp_path: Path) -> None:
    """Latin-only matching: an Arabic body has nothing provable to check —
    verification must pass silently (never a false alarm on RTL text)."""
    pdf = _make_pdf(tmp_path / "ar.pdf", "قصة الاسم بالعربية")
    assert _verify_pdf_content(
        pdf, [{"heading": "الخلاصة", "body": "قصة الاسم بالعربية"}], None
    ) is None


def test_table_cells_verified(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "t.pdf", "Name | Origin\nvoimfit | Finnish")
    ok_table = {"headers": ["Name", "Origin"], "rows": [["voimfit", "Finnish"]]}
    assert _verify_pdf_content(pdf, None, [ok_table]) is None
    dropped = {"headers": ["Name", "Origin"], "rows": [["tonosfit", "Greek"]]}
    err = _verify_pdf_content(pdf, None, [dropped])
    assert err is not None and "tonosfit" in err


def test_missing_file_and_exceptions_never_block(tmp_path: Path) -> None:
    assert _verify_pdf_content(tmp_path / "nope.pdf", [{"heading": "h", "body": "words here"}], None) is None
    # Non-PDF path silently skips.
    assert _verify_pdf_content("x.docx", [{"heading": "h", "body": "words"}], None) is None
