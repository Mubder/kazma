"""Extraction fidelity on the versioned EN/AR/bilingual corpus holds its floors.

2026-10-13 (F09/Q02). ``kazma_core.documents.fidelity`` builds each case
with Kazma's own engines from a ground-truth spec, parses it with the real
isolated parser and measures it. ``tests/fixtures/documents/
fidelity_thresholds.json`` declares, for the corpus version it was measured
on:

* a regression FLOOR (ceiling for error rates) per case and metric -- every
  run must hold it;
* the RELEASE targets and the list of cases that meet them (``qualified``):
  the list must match the measurement, so a qualification claim can never be
  stale in either direction.

A case whose engine is missing here (LibreOffice for the Arabic PDF route,
Tesseract with its language for OCR) is SKIPPED with the reason -- a skip is
not evidence. ``python scripts/document_fidelity.py`` prints the report;
``--lock`` raises floors and refuses to lower one within a corpus version.

Negative controls: a parser that drops a paragraph, alters a number, swaps
sections or glues table cells fails the same floors.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from kazma_core.documents import fidelity

THRESHOLDS = Path(__file__).parent / "fixtures" / "documents" / "fidelity_thresholds.json"


def _engines_missing(case: fidelity.FidelityCase) -> str | None:
    if case.fmt in {"pdf", "ocr"} and case.language != "en":
        if not (shutil.which("soffice") or shutil.which("libreoffice")
                or Path("C:/Program Files/LibreOffice/program/soffice.exe").exists()):
            return "LibreOffice is not installed (Arabic PDF layout route)"
    if case.fmt == "ocr":
        from kazma_core.documents.ocr.pipeline import get_ocr_health

        health = get_ocr_health(("ara",) if case.language == "ar" else ("eng",))
        if str(getattr(health.readiness, "value", health.readiness)) != "ready":
            return "OCR engine or language is not ready"
    return None


def _check(metrics: dict, floor: dict) -> list[str]:
    """Strict floor check: a declared metric that was not measured fails."""
    missing = [f"{name}: not measured" for name in floor if metrics.get(name) is None]
    return missing + fidelity.meets(metrics, floor)


@pytest.fixture(scope="module")
def declared():
    data = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    assert data["corpus_version"] == fidelity.CORPUS_VERSION, (
        "the corpus changed: re-measure and lock (python scripts/document_fidelity.py --lock)"
    )
    return data


def test_every_case_has_declared_floors_and_the_targets_match(declared):
    assert set(declared["thresholds"]) == {case.case_id for case in fidelity.corpus()}
    assert declared["release_targets"] == fidelity.RELEASE_TARGETS
    assert set(declared["qualified"]) <= set(declared["thresholds"])


@pytest.mark.parametrize("case", fidelity.corpus(), ids=lambda c: c.case_id)
def test_case_holds_its_floor_and_its_qualification_claim(case, declared, tmp_path):
    reason = _engines_missing(case)
    if reason:
        pytest.skip(reason)
    report = fidelity.run_corpus(tmp_path, cases=[case])["cases"][case.case_id]
    assert "error" not in report, report
    metrics = report["metrics"]
    failures = _check(metrics, declared["thresholds"][case.case_id])
    assert not failures, (case.case_id, metrics, failures)
    meets_release = not fidelity.meets(metrics, fidelity.RELEASE_TARGETS)
    claimed = case.case_id in declared["qualified"]
    assert meets_release == claimed, (
        f"{case.case_id}: qualification claim {claimed} but release targets met={meets_release}; "
        "re-lock the thresholds", metrics,
    )


# ── negative controls: the metrics see the failures they exist for ─────────

def _measured(case_id, mutate, tmp_path):
    from kazma_core.documents.service import DocumentService

    case = next(c for c in fidelity.corpus() if c.case_id == case_id)
    source = fidelity.build_case(case, tmp_path)
    ir = DocumentService().parse_ingested_blob(source)
    return fidelity.measure_case(case, mutate(ir), source)


def _rewrite(ir, edit):
    pages = []
    for page in ir.pages:
        blocks = tuple(b for b in (edit(block) for block in page.blocks) if b is not None)
        pages.append(replace(page, blocks=blocks))
    return replace(ir, pages=tuple(pages))


def test_a_dropped_paragraph_fails_the_floor(tmp_path, declared):
    victim = "Two suppliers missed delivery windows"

    def drop(block):
        return replace(block, text=block.text.replace(victim, "")) if victim in block.text else block

    metrics = _measured("en-docx", lambda ir: _rewrite(ir, drop), tmp_path)
    assert _check(metrics, declared["thresholds"]["en-docx"])


def test_an_altered_number_fails_the_floor(tmp_path, declared):
    metrics = _measured("en-docx", lambda ir: _rewrite(
        ir, lambda b: replace(b, text=b.text.replace("1,234,567.89", "1,234,567.98"))), tmp_path)
    assert metrics["number_recall"] < 1.0
    assert _check(metrics, declared["thresholds"]["en-docx"])


def test_swapped_sections_fail_the_floor(tmp_path, declared):
    def swap(ir):
        blocks = [b for page in ir.pages for b in page.blocks]
        lines = [line for b in blocks if str(b.block_type) != "table" for line in b.text.splitlines()]
        half = len(lines) // 2
        shuffled = replace(blocks[0], text="\n".join(lines[half:] + lines[:half]))
        tables = [b for b in blocks if str(b.block_type) == "table"]
        return replace(ir, pages=(replace(ir.pages[0], blocks=(shuffled, *tables)),))

    metrics = _measured("en-docx", swap, tmp_path)
    assert metrics["order"] < 1.0
    assert _check(metrics, declared["thresholds"]["en-docx"])


def test_glued_table_cells_fail_the_floor(tmp_path, declared):
    # The HTML parser's old output: cells run together as text, no table.
    def glue(ir):
        def edit(block):
            if str(block.block_type) == "table":
                return None
            return block
        return _rewrite(ir, edit)

    metrics = _measured("en-html", glue, tmp_path)
    assert metrics["table_cell_recall"] == 0.0
    assert _check(metrics, declared["thresholds"]["en-html"])


def test_ocr_without_the_table_pass_fails_the_floor(tmp_path, declared):
    # The OCR before the table pass (documents.ocr.tables off; the parser runs
    # in its own process, so the setting is the switch): the page's own
    # reading, no TABLE block.
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.service import DocumentService

    case = next(c for c in fidelity.corpus() if c.case_id == "en-ocr-table")
    reason = _engines_missing(case)
    if reason:
        pytest.skip(reason)
    service = DocumentService(config=DocumentConfig(
        storage_root=tmp_path / "store", ocr_tables=False, security_malware_scan="off",
    ))
    metrics = fidelity.run_corpus(tmp_path, service=service, cases=[case])["cases"][case.case_id]["metrics"]
    assert metrics["table_cell_recall"] == 0.0
    assert _check(metrics, declared["thresholds"]["en-ocr-table"])


def _arabic_page(tmp_path, case, *, side: str):
    """Each segment of the case as one short line, set on ``side``."""
    import pymupdf
    from kazma_core.documents.arabic import visual_order

    font_path = (Path(fidelity.__file__).parent / "assets" / "fonts" / "IBMPlexSansArabic-Regular.ttf")
    font = pymupdf.Font(fontfile=str(font_path))
    path = tmp_path / f"arabic-{side}.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        y = 90.0
        for segment in case.segments():
            drawn = visual_order(segment, "R")
            width = font.text_length(drawn, fontsize=11)
            x = 50.0 if side == "left" else 545.0 - width
            page.insert_text((x, y), drawn, fontsize=11, fontname="plex", fontfile=str(font_path))
            y += 30.0
        doc.save(path)
    return path


def test_arabic_set_on_the_wrong_side_fails_alignment(tmp_path):
    case = next(c for c in fidelity.corpus() if c.case_id == "ar-pdf")
    right = fidelity._alignment(case, _arabic_page(tmp_path, case, side="right"))
    left = fidelity._alignment(case, _arabic_page(tmp_path, case, side="left"))
    assert right == 1.0
    assert left == 0.0


def test_reading_across_the_columns_fails_the_floor(tmp_path, declared):
    # Negative control for the two-column case: a reader that takes each
    # visual line straight across both columns interleaves them.
    import pymupdf
    from kazma_core.documents.parsers.pdf_rtl import page_glyph_lines

    case = next(c for c in fidelity.corpus() if c.case_id == "en-pdf-columns")
    source = fidelity.build_case(case, tmp_path)
    from kazma_core.documents.service import DocumentService

    ir = DocumentService().parse_ingested_blob(source)
    with pymupdf.open(source) as doc:
        across = ["\n".join("".join(g.text for g in line) for line in page_glyph_lines(page)) for page in doc]
    pages = tuple(
        replace(page, blocks=(replace(page.blocks[0], text=across[i]),)) for i, page in enumerate(ir.pages)
    )
    metrics = fidelity.measure_case(case, replace(ir, pages=pages), source)
    assert metrics["segment_recall"] < 0.95
    assert _check(metrics, declared["thresholds"]["en-pdf-columns"])
