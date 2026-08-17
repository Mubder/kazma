"""Tests for the intent engine compat façade (intent_router.py).

Phase 0: should_route is False for everything — the execute allowlist is
empty. These tests verify the façade delegates correctly and that
should_route follows the engine's route decision, not a confidence hack.
"""

from __future__ import annotations

import pytest

from kazma_core.agent.intent_router import (
    CONFIDENCE_THRESHOLD,
    IntentCategory,
    TaskIntent,
    classify_task,
)


class TestFaçadeDelegation:
    def test_reproduce_pdf(self):
        intent = classify_task("reproduce this PDF with better templates")
        assert intent.category == IntentCategory.DOCUMENT
        assert intent.pipeline == "document_generate"
        # Phase 0: should_route is ALWAYS False (empty execute allowlist)
        assert intent.should_route is False

    def test_build_parser_not_document(self):
        """'build a PDF parser' must NOT emit document_generate."""
        intent = classify_task("build a PDF parser in Python")
        assert intent.category != IntentCategory.DOCUMENT

    def test_false_positive_corpus(self):
        """All §20.1 'Must NOT execute' utterances → should_route False."""
        for text in [
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
        ]:
            intent = classify_task(text)
            assert intent.should_route is False, (
                f"'{text}' should_route must be False in Phase 0"
            )
            assert intent.category != IntentCategory.DOCUMENT or (
                intent.category == IntentCategory.DOCUMENT and not intent.should_route
            ), f"'{text}' should not be document or should not route"

    def test_research_detected(self):
        intent = classify_task("research the impact of AI on Kuwait's economy")
        assert intent.category in (IntentCategory.RESEARCH, IntentCategory.GENERAL)

    def test_bare_continue(self):
        intent = classify_task("continue")
        assert intent.category in (IntentCategory.CONTINUE, IntentCategory.GENERAL)

    def test_simple_question_general(self):
        intent = classify_task("what is the weather like today?")
        assert intent.category == IntentCategory.GENERAL
        assert intent.should_route is False

    def test_empty(self):
        intent = classify_task("")
        assert intent.category == IntentCategory.GENERAL
        assert intent.should_route is False


class TestPhase0Safety:
    """Phase 0: nothing executes, everything constrains or loops."""

    @pytest.mark.parametrize("text", [
        "reproduce this PDF",
        "generate an Excel spreadsheet",
        "أنشئ مستند PDF",
        "write me a PDF of the notes",
        "convert this to Word",
    ])
    def test_phase0_never_routes(self, text):
        intent = classify_task(text)
        assert intent.should_route is False, (
            f"Phase 0 execute allowlist is empty — '{text}' must not route"
        )


class TestAttachmentDetection:
    def test_document_with_attachment(self):
        atts = [{"kind": "file", "mime": "application/pdf", "path": "cal.pdf", "filename": "cal.pdf"}]
        intent = classify_task("reproduce this", attachments=atts)
        assert intent.category == IntentCategory.DOCUMENT
        assert intent.should_route is False  # Phase 0


# NOTE: TestSupervisorRouting was deleted per §20.6 — it accepted RESPOND
# OR TOOL_WORKER and swallowed exceptions, so it could never fail.
# Phase 1 will add a proper supervisor unit test.
