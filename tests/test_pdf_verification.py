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


# ── Live 2026-10-20: a table with punctuation always "failed" ────────────

#: The table of the live call (Telegram, "Kuwait's weather by season").
LIVE_TABLE = {
    "headers": ["Season", "Months", "Avg high (°C)", "Avg low (°C)", "Rainfall", "Dominant character"],
    "rows": [["Summer", "Jun – Sep", "43 – 46", "29 – 32", "~0 mm", "Extreme heat, fully dry"]],
}


def _rendered_live_table(tmp_path: Path) -> Path:
    return _make_pdf(tmp_path / "live.pdf", "\n".join([
        "Season  Months  Avg high (°C)  Avg low (°C)  Rainfall  Dominant character",
        "Summer  Jun – Sep  43 – 46  29 – 32  ~0 mm  Extreme heat, fully dry",
    ]))


def test_a_table_with_punctuation_in_its_cells_verifies(tmp_path: Path) -> None:
    assert _verify_pdf_content(_rendered_live_table(tmp_path), None, [LIVE_TABLE]) is None


def test_negative_control_the_old_cell_comparison_failed_it(tmp_path: Path) -> None:
    """The old rule: the raw cell against the punctuation-free text."""
    import re

    pdf = _rendered_live_table(tmp_path)
    doc = fitz.open(str(pdf))
    norm = re.sub(r"[^a-z0-9]+", " ", " ".join(p.get_text() for p in doc).lower())
    doc.close()
    cells = LIVE_TABLE["headers"] + LIVE_TABLE["rows"][0]
    assert [c for c in cells if c.lower() not in norm] == [
        "Avg high (°C)", "Avg low (°C)", "Jun – Sep", "43 – 46", "29 – 32", "~0 mm",
        "Extreme heat, fully dry"]


def test_a_cell_that_did_not_reach_the_pdf_still_fails(tmp_path: Path) -> None:
    table = {"headers": LIVE_TABLE["headers"], "rows": [["Monsoon", "Jun – Sep", "1", "2", "3", "4"]]}
    err = _verify_pdf_content(_rendered_live_table(tmp_path), None, [table])
    assert err is not None and "Monsoon" in err


@pytest.mark.anyio
async def test_a_failed_check_is_a_correctable_error_not_an_unknown_effect(monkeypatch):
    """The check asked the model to call again; returned as an unknown effect
    it failed the turn and forbade that retry (live 2026-10-20)."""
    from kazma_core.agent.tool_refusal import KnownEffectError
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.swarm import safety as swarm_safety

    monkeypatch.setattr(swarm_safety, "get_safety", lambda: type("S", (), {"enabled": False})())

    async def generate_pdf(title: str) -> str:
        raise KnownEffectError("Error: PDF VERIFICATION FAILED (incomplete file: x.pdf)")

    reg = LocalToolRegistry()
    reg.register(name="generate_pdf", description="test", category="test")(generate_pdf)
    out = await reg.execute("generate_pdf", {"title": "t"})
    assert out["is_error"] and "VERIFICATION FAILED" in out["content"]
    assert not out.get("effect_uncertain")


def test_generate_pdf_raises_the_known_effect_error():
    import inspect

    from kazma_skills.native.document_generator import tools

    source = inspect.getsource(tools.generate_pdf)
    assert "raise KnownEffectError" in source and "effect_unknown(_err)" not in source
