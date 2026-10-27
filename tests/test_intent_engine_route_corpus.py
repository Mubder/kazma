"""The intent engine's route decisions on the §20.1 corpus.

These cases were asked through ``agent/intent_router.py``, a compat façade
over :func:`kazma_core.agent.intent.classify.classify_turn_sync` that only
the dead document pipeline imported. The façade, the pipeline registry and
the pipeline were removed (audit 2026-10-26 AUD-075); the cases now ask the
engine itself, with the façade's mapping (a document act is a
``document_generate``/``document_intel`` primary act, "routes" is the
engine's EXECUTE route).
"""

from __future__ import annotations

import pytest

from kazma_core.agent.intent.classify import classify_turn_sync

_DOCUMENT_KINDS = {"document_generate", "document_intel"}


def _kind(decision) -> str:
    return decision.primary.kind if decision.primary is not None else "general"


def _routes(decision) -> bool:
    return decision.route.value == "execute"


def test_reproduce_pdf_is_a_document_generate_that_does_not_execute() -> None:
    decision = classify_turn_sync("reproduce this PDF with better templates")
    assert _kind(decision) == "document_generate"
    assert not _routes(decision)


def test_build_a_parser_is_not_a_document_act() -> None:
    assert _kind(classify_turn_sync("build a PDF parser in Python")) not in _DOCUMENT_KINDS


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
def test_must_not_execute_corpus(text: str) -> None:
    assert not _routes(classify_turn_sync(text)), text


@pytest.mark.parametrize("text", [
    "reproduce this PDF",
    "generate an Excel spreadsheet",
    "أنشئ مستند PDF",
    "write me a PDF of the notes",
    "convert this to Word",
])
def test_document_requests_do_not_execute(text: str) -> None:
    assert not _routes(classify_turn_sync(text)), text


def test_research_and_continue_and_plain_questions() -> None:
    assert _kind(classify_turn_sync(
        "research the impact of AI on Kuwait's economy"
    )) in {"research", "research_deep", "general"}
    assert _kind(classify_turn_sync("continue")) in {"continue", "general"}
    weather = classify_turn_sync("what is the weather like today?")
    assert _kind(weather) == "general" and not _routes(weather)
    empty = classify_turn_sync("")
    assert _kind(empty) == "general" and not _routes(empty)


def test_an_attached_pdf_makes_reproduce_this_a_document_act() -> None:
    atts = [{"kind": "file", "mime": "application/pdf", "path": "cal.pdf", "filename": "cal.pdf"}]
    decision = classify_turn_sync("reproduce this", attachments=atts)
    assert _kind(decision) in _DOCUMENT_KINDS
    assert not _routes(decision)
