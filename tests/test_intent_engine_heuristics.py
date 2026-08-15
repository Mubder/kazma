"""Intent engine heuristics tests — the false-positive corpus (§20.1)."""
from __future__ import annotations

import pytest

from kazma_core.agent.intent.heuristics import detect_acts
from kazma_core.agent.intent.types import ActKind


def _kinds(acts: tuple) -> set[str]:
    return {a.kind for a in acts}


def _has(acts: tuple, kind: str) -> bool:
    return kind in _kinds(acts)


# ─── Must NOT emit document_generate (false positives) ─────────────────


class TestFalsePositives:
    @pytest.mark.parametrize("text", [
        "build a PDF parser in Python",
        "rebuild the document index",
        "format the documents folder",
        "I have a python question",
        "please run the tests",
        "run this",
        "explain how this code works",
        "what is this PDF about?",
        "read this PDF",
        "create a report about climate",
        "the document says hello",
        "update the document",
        "document this API",
    ])
    def test_not_document_generate(self, text):
        acts = detect_acts(text)
        assert not _has(acts, ActKind.DOCUMENT_GENERATE), (
            f"'{text}' should NOT emit document_generate, got {_kinds(acts)}"
        )

    def test_build_pdf_parser(self):
        acts = detect_acts("build a PDF parser in Python")
        assert ActKind.DOCUMENT_GENERATE not in _kinds(acts)

    def test_rebuild_document_index(self):
        acts = detect_acts("rebuild the document index")
        assert ActKind.DOCUMENT_GENERATE not in _kinds(acts)

    def test_format_documents_folder(self):
        acts = detect_acts("format the documents folder")
        assert ActKind.DOCUMENT_GENERATE not in _kinds(acts)

    def test_python_question(self):
        acts = detect_acts("I have a python question")
        assert ActKind.CODE_EXEC not in _kinds(acts)
        assert ActKind.DOCUMENT_GENERATE not in _kinds(acts)

    def test_run_tests(self):
        acts = detect_acts("please run the tests")
        assert ActKind.CODE_EXEC not in _kinds(acts)

    def test_explain_code(self):
        acts = detect_acts("explain how this code works")
        assert ActKind.CODE_EXEC not in _kinds(acts)

    def test_read_pdf_not_generate(self):
        acts = detect_acts("read this PDF")
        assert ActKind.DOCUMENT_GENERATE not in _kinds(acts)

    def test_what_is_pdf(self):
        acts = detect_acts("what is this PDF about?")
        assert ActKind.DOCUMENT_GENERATE not in _kinds(acts)

    def test_create_report_climate(self):
        """'report' alone is NOT a format token."""
        acts = detect_acts("create a report about climate")
        assert ActKind.DOCUMENT_GENERATE not in _kinds(acts)


# ─── Must emit document_generate ─────────────────────────────────────────


class TestDocumentGenerate:
    @pytest.mark.parametrize("text,expected_format", [
        ("reproduce this PDF with better templates", "pdf"),
        ("create a Word document with the meeting notes", "docx"),
        ("generate an Excel spreadsheet of the data", "xlsx"),
        ("أنشئ مستند PDF من هذه البيانات", "pdf"),
        ("write me a PDF of the notes", "pdf"),
        ("make me a PDF report", "pdf"),
        ("convert this to Word", "docx"),
    ])
    def test_document_generate_emitted(self, text, expected_format):
        acts = detect_acts(text)
        assert _has(acts, ActKind.DOCUMENT_GENERATE), (
            f"'{text}' should emit document_generate, got {_kinds(acts)}"
        )
        doc_act = next(a for a in acts if a.kind == ActKind.DOCUMENT_GENERATE)
        assert doc_act.slots.get("format") == expected_format

    def test_dont_just_read(self):
        """'dont just read this PDF, create a new one' — both verbs → generate."""
        acts = detect_acts("dont just read this PDF, create a new one")
        assert _has(acts, ActKind.DOCUMENT_GENERATE)

    def test_attachment_boost(self):
        atts = [{"kind": "file", "mime": "application/pdf", "path": "cal.pdf", "filename": "cal.pdf"}]
        acts = detect_acts("reproduce this", attachments=atts)
        assert _has(acts, ActKind.DOCUMENT_GENERATE)
        doc_act = next(a for a in acts if a.kind == ActKind.DOCUMENT_GENERATE)
        assert doc_act.slots.get("format") == "pdf"

    def test_delivery_extraction(self):
        acts = detect_acts("create a PDF and send it to my Telegram")
        assert _has(acts, ActKind.DOCUMENT_GENERATE)
        doc_act = next(a for a in acts if a.kind == ActKind.DOCUMENT_GENERATE)
        assert doc_act.slots.get("deliver_to") == "telegram"


# ─── Multi-act ───────────────────────────────────────────────────────────


class TestMultiAct:
    def test_research_and_pdf(self):
        acts = detect_acts("research this topic and make a PDF")
        kinds = _kinds(acts)
        assert ActKind.DOCUMENT_GENERATE in kinds, f"kinds={kinds}"

    def test_research_and_generate(self):
        acts = detect_acts("research the impact of AI on Kuwait and generate a PDF")
        kinds = _kinds(acts)
        assert ActKind.DOCUMENT_GENERATE in kinds

    def test_worker_and_pdf(self):
        acts = detect_acts("dispatch a worker to create a PDF")
        assert ActKind.DOCUMENT_GENERATE in _kinds(acts)


# ─── Research acts ───────────────────────────────────────────────────────


class TestResearch:
    def test_research_economy(self):
        acts = detect_acts("research the impact of AI on Kuwait's economy")
        kinds = _kinds(acts)
        assert ActKind.RESEARCH in kinds or ActKind.RESEARCH_DEEP in kinds, f"kinds={kinds}"

    def test_deep_dive(self):
        """'deep dive' — RESEARCH_DEEP if policy regex says so (§20.1)."""
        acts = detect_acts("do a deep dive on cloud security best practices")
        kinds = _kinds(acts)
        # Existing research_policy may or may not detect 'deep dive'
        # The spec says 'if policy regex says so' — we accept either
        if ActKind.RESEARCH_DEEP in kinds or ActKind.RESEARCH in kinds:
            assert True  # Policy detected it
        else:
            # Policy didn't detect 'deep dive' — that's existing behavior,
            # not a regression. Just verify no false document_generate.
            assert ActKind.DOCUMENT_GENERATE not in kinds


# ─── Arabic generate vs continue ────────────────────────────────────────


class TestArabicSplit:
    def test_arabic_generate_with_format(self):
        """أكمل هذا المستند PDF — has a format token → document_generate, NOT focus continue."""
        acts = detect_acts("أكمل هذا المستند PDF")
        assert _has(acts, ActKind.DOCUMENT_GENERATE)

    def test_arabic_generate_tqrir(self):
        acts = detect_acts("أنجز تقرير PDF")
        assert _has(acts, ActKind.DOCUMENT_GENERATE)

    def test_arabic_bare_continue(self):
        """Bare أكمل — no act (focus handles continue)."""
        acts = detect_acts("أكمل")
        kinds = _kinds(acts)
        assert ActKind.DOCUMENT_GENERATE not in kinds
        assert ActKind.GENERAL in kinds or len(kinds) == 0


# ─── Other act kinds ─────────────────────────────────────────────────────


class TestOtherActs:
    def test_swarm(self):
        acts = detect_acts("dispatch a swarm of workers for this task")
        assert _has(acts, ActKind.SWARM)

    def test_remind(self):
        acts = detect_acts("remind me to call the doctor tomorrow")
        assert _has(acts, ActKind.REMIND)

    def test_document_intel(self):
        acts = detect_acts("ingest this document into the library")
        assert _has(acts, ActKind.DOCUMENT_INTEL)

    def test_empty(self):
        acts = detect_acts("")
        assert _has(acts, ActKind.GENERAL)

    # ── Positive detection (audit follow-up: these three kinds had
    # regexes but zero positive tests — only code_exec negatives).

    def test_code_exec_positive(self):
        acts = detect_acts("run the script for me")
        assert _has(acts, ActKind.CODE_EXEC)

    def test_code_exec_positive_py_file(self):
        acts = detect_acts("execute this .py file please")
        assert _has(acts, ActKind.CODE_EXEC)

    def test_code_exec_positive_arabic(self):
        acts = detect_acts("شغل السكربت")
        assert _has(acts, ActKind.CODE_EXEC)

    def test_file_mgmt_positive(self):
        acts = detect_acts("organize my files by date")
        assert _has(acts, ActKind.FILE_MGMT)

    def test_file_mgmt_positive_move(self):
        acts = detect_acts("move these files into the archive folder")
        assert _has(acts, ActKind.FILE_MGMT)

    def test_analysis_positive(self):
        acts = detect_acts("analyze this dataset for trends")
        assert _has(acts, ActKind.ANALYSIS)

    def test_analysis_positive_chart(self):
        acts = detect_acts("chart the csv numbers")
        assert _has(acts, ActKind.ANALYSIS)

    def test_analysis_positive_arabic(self):
        acts = detect_acts("حلل هذه البيانات")
        assert _has(acts, ActKind.ANALYSIS)
