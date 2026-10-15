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

The OCR cases (2026-10-15, corpus version 4) are measured with each OCR
engine Kazma runs (``fidelity.OCR_ENGINES``: Tesseract and the neural
engine), each against its own floors and qualified list
(``ocr_engines.<engine>``). A case read by another engine than the one asked
(a fallback) fails: it would hold the wrong engine's floors.

A case whose engine is missing here (LibreOffice for the Arabic PDF route,
Tesseract with its language, the neural engine's packages and models) is
SKIPPED with the reason -- a skip is not evidence. ``python
scripts/document_fidelity.py`` prints the report; ``--lock`` raises floors
and refuses to lower one within a corpus version.

Negative controls: a parser that drops a paragraph, alters a number, swaps
sections or glues table cells fails the same floors; a lock refuses an OCR
case another engine read.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from kazma_core.documents import fidelity

THRESHOLDS = Path(__file__).parent / "fixtures" / "documents" / "fidelity_thresholds.json"
_OCR = [case for case in fidelity.corpus() if case.fmt == "ocr"]
_PLAIN = [case for case in fidelity.corpus() if case.fmt != "ocr"]


def _engines_missing(case: fidelity.FidelityCase, engine: str | None = None) -> str | None:
    if case.fmt in {"pdf", "ocr"} and case.language != "en":
        if not (shutil.which("soffice") or shutil.which("libreoffice")
                or Path("C:/Program Files/LibreOffice/program/soffice.exe").exists()):
            return "LibreOffice is not installed (Arabic PDF layout route)"
    if case.fmt == "ocr":
        languages = ("ara",) if case.language == "ar" else ("eng",)
        return fidelity.engine_available(engine or "tesseract", languages)
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


def _floors(declared: dict, case_id: str, engine: str | None) -> tuple[dict, list[str]]:
    if engine is None:
        return declared["thresholds"][case_id], declared["qualified"]
    section = declared["ocr_engines"][engine]
    return section["thresholds"][case_id], section["qualified"]


def test_every_case_has_declared_floors_and_the_targets_match(declared):
    assert set(declared["thresholds"]) == {case.case_id for case in _PLAIN}
    assert declared["release_targets"] == fidelity.RELEASE_TARGETS
    assert set(declared["qualified"]) <= set(declared["thresholds"])
    assert set(declared["ocr_engines"]) == set(fidelity.OCR_ENGINES)
    for engine, section in declared["ocr_engines"].items():
        assert set(section["thresholds"]) == {case.case_id for case in _OCR}, engine
        assert set(section["qualified"]) <= set(section["thresholds"]), engine


_RUNS = [pytest.param(case, None, id=case.case_id) for case in _PLAIN] + [
    pytest.param(case, engine, id=f"{case.case_id}[{engine}]")
    for case in _OCR for engine in fidelity.OCR_ENGINES
]


@pytest.mark.parametrize(("case", "engine"), _RUNS)
def test_case_holds_its_floor_and_its_qualification_claim(case, engine, declared, tmp_path):
    reason = _engines_missing(case, engine)
    if reason:
        pytest.skip(reason)
    report = fidelity.run_corpus(tmp_path, cases=[case], ocr_engine=engine)["cases"][case.case_id]
    assert "error" not in report, report
    if engine is not None:
        assert report["ocr_engine"] == engine, f"{engine} was asked for; {report['ocr_engine']} read the page"
    metrics = report["metrics"]
    floor, qualified = _floors(declared, case.case_id, engine)
    failures = _check(metrics, floor)
    assert not failures, (case.case_id, engine, metrics, failures)
    meets_release = not fidelity.meets(metrics, fidelity.RELEASE_TARGETS)
    claimed = case.case_id in qualified
    assert meets_release == claimed, (
        f"{case.case_id} [{engine}]: qualification claim {claimed} but release targets met={meets_release}; "
        "re-lock the thresholds", metrics,
    )


def test_a_lock_refuses_an_ocr_case_another_engine_read():
    # Negative control for the per-engine floors: a neural run that fell back
    # to Tesseract must not become the neural engine's floors.
    measured = {"metrics": {"token_recall": 1.0}, "ocr_engine": "tesseract"}
    report = {"corpus_version": fidelity.CORPUS_VERSION, "cases": {},
              "ocr_engines": {"neural": {"cases": {"ar-ocr": measured}}}}
    with pytest.raises(ValueError, match="another engine"):
        fidelity.lock_thresholds(report, None)
    report["ocr_engines"]["neural"]["cases"]["ar-ocr"] = {**measured, "ocr_engine": "neural"}
    locked = fidelity.lock_thresholds(report, None)
    assert locked["ocr_engines"]["neural"]["thresholds"]["ar-ocr"] == {"token_recall": 0.98}


def test_a_lock_keeps_the_floors_of_an_engine_not_measured(declared):
    report = {"corpus_version": declared["corpus_version"], "cases": {}, "ocr_engines": {}}
    previous = {**declared, "thresholds": {}, "qualified": []}
    locked = fidelity.lock_thresholds(report, previous)
    assert locked["ocr_engines"] == declared["ocr_engines"]
    with pytest.raises(ValueError, match="OCR cases must be measured per engine"):
        fidelity.lock_thresholds({**report, "cases": {"ar-ocr": {"metrics": {}}}}, previous)


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
    reason = _engines_missing(case, "tesseract")
    if reason:
        pytest.skip(reason)
    service = DocumentService(config=DocumentConfig(
        storage_root=tmp_path / "store", ocr_tables=False, security_malware_scan="off", ocr_engine="tesseract",
    ))
    metrics = fidelity.run_corpus(tmp_path, service=service, cases=[case])["cases"][case.case_id]["metrics"]
    assert metrics["table_cell_recall"] == 0.0
    assert _check(metrics, declared["ocr_engines"]["tesseract"]["thresholds"]["en-ocr-table"])


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
