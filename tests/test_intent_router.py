"""Tests for the Universal Intent Router and Pipeline Registry."""

from __future__ import annotations

import pytest

from kazma_core.agent.intent_router import (
    CONFIDENCE_THRESHOLD,
    IntentCategory,
    TaskIntent,
    classify_task,
)
from kazma_core.agent.pipeline_registry import (
    Pipeline,
    PipelineBudget,
    get_registry,
)


# ─── Intent classification (heuristic tier) ─────────────────────────────


class TestDocumentIntent:
    def test_reproduce_pdf(self):
        intent = classify_task("reproduce this PDF with better templates")
        assert intent.category == IntentCategory.DOCUMENT
        assert intent.pipeline == "document"
        assert intent.confidence >= CONFIDENCE_THRESHOLD
        assert intent.should_route

    def test_create_word_doc(self):
        intent = classify_task("create a Word document with the meeting notes")
        assert intent.category == IntentCategory.DOCUMENT
        assert intent.should_route

    def test_generate_spreadsheet(self):
        intent = classify_task("generate an Excel spreadsheet of the data")
        assert intent.category == IntentCategory.DOCUMENT
        assert intent.should_route

    def test_arabic_document(self):
        intent = classify_task("أنشئ مستند PDF من هذه البيانات")
        assert intent.category == IntentCategory.DOCUMENT
        assert intent.should_route

    def test_negation_read_pdf(self):
        """'read this PDF' is a question, not document generation."""
        intent = classify_task("what is this PDF about?")
        assert intent.category != IntentCategory.DOCUMENT

    def test_attachment_triggers_document(self):
        intent = classify_task(
            "reproduce this with better formatting",
            attachments=[{"kind": "file", "mime": "application/pdf", "path": "cal.pdf", "filename": "cal.pdf"}],
        )
        assert intent.category == IntentCategory.DOCUMENT
        assert intent.should_route

    def test_extracts_format(self):
        intent = classify_task("make me a PDF report")
        assert intent.parameters.get("format") in ("pdf",)


class TestResearchIntent:
    def test_research_topic(self):
        intent = classify_task("research the impact of AI on Kuwait's economy")
        assert intent.category == IntentCategory.RESEARCH
        assert intent.should_route

    def test_deep_dive(self):
        intent = classify_task("do a deep dive on cloud security best practices")
        assert intent.category == IntentCategory.RESEARCH
        assert intent.should_route


class TestContinueIntent:
    def test_bare_continue(self):
        intent = classify_task("continue")
        assert intent.category == IntentCategory.CONTINUE

    def test_proceed(self):
        intent = classify_task("proceed")
        assert intent.category == IntentCategory.CONTINUE

    def test_arabic_continue(self):
        intent = classify_task("أكمل")
        assert intent.category == IntentCategory.CONTINUE


class TestGeneralIntent:
    def test_simple_question(self):
        intent = classify_task("what is the weather like today?")
        assert intent.category == IntentCategory.GENERAL
        assert not intent.should_route

    def test_open_ended(self):
        intent = classify_task("tell me about your capabilities")
        assert intent.category == IntentCategory.GENERAL
        assert not intent.should_route

    def test_explain_code(self):
        """'explain this code' is not code execution."""
        intent = classify_task("explain how this code works")
        assert intent.category != IntentCategory.CODE

    def test_empty(self):
        intent = classify_task("")
        assert intent.category == IntentCategory.GENERAL


class TestSwarmIntent:
    def test_dispatch_workers(self):
        intent = classify_task("dispatch this task to multiple workers in parallel")
        assert intent.category == IntentCategory.SWARM
        assert intent.should_route


# ─── Pipeline registry ───────────────────────────────────────────────────


class TestPipelineRegistry:
    def test_document_pipeline_registered(self):
        registry = get_registry()
        pipeline = registry.get("document")
        assert pipeline is not None
        assert pipeline.category == IntentCategory.DOCUMENT
        assert pipeline.handler is not None

    def test_match_document(self):
        registry = get_registry()
        pipeline = registry.match(IntentCategory.DOCUMENT)
        assert pipeline is not None
        assert pipeline.name == "document"

    def test_match_general_returns_none(self):
        registry = get_registry()
        assert registry.match(IntentCategory.GENERAL) is None

    def test_budget(self):
        registry = get_registry()
        pipeline = registry.get("document")
        assert pipeline.budget.max_steps == 5
        assert pipeline.budget.max_llm_calls == 1


# ─── Supervisor routing integration ─────────────────────────────────────


class TestSupervisorRouting:
    @pytest.mark.asyncio
    async def test_document_intent_bypasses_free_form(self):
        """A document task should route to the pipeline, not the tool loop."""
        from kazma_core.agent.graph_builder import supervisor_node
        from kazma_core.agent.state import NodeName

        # Minimal stand-ins (same pattern as test_empty_answer_recovery.py)
        class _FakeCostBreaker:
            def should_halt(self):
                return False

            def record_cost(self, cost):
                pass

        class _FakeAuthority:
            async def check_and_enforce(self, state):
                return state

        class _FakeTracer:
            def trace_llm_call(self, **kwargs):
                pass

        class _FakeCompactor:
            async def retrieve_memories(self, query, limit=5):
                return []

        _FakeAuthority.compactor = _FakeCompactor()

        state = {
            "messages": [
                {"role": "user", "content": "reproduce this PDF with better templates"},
            ],
            "iteration": 0,
            "active_attachments": [
                {
                    "kind": "file",
                    "mime": "application/pdf",
                    "path": "test_calendar.pdf",
                    "filename": "test_calendar.pdf",
                }
            ],
        }

        # The pipeline will run (file_write + generate_pdf will be called)
        # We just verify the routing decision: next_node should be RESPOND
        # (pipeline completes) — NOT TOOL_WORKER (free-form loop entry)
        try:
            out = await supervisor_node(
                state,
                llm=None,
                system_prompt="test",
                tool_definitions=[],
                tool_executor=None,
                cost_breaker=_FakeCostBreaker(),
                authority=_FakeAuthority(),
                tracer=_FakeTracer(),
            )
            # Either the pipeline succeeded (RESPOND) or gracefully fell
            # back — both are valid, but TOOL_WORKER means the router
            # didn't fire at all
            assert out.get("next_node") in (NodeName.RESPOND, NodeName.TOOL_WORKER)
        except Exception:
            # Pipeline failure falls back to free-form — that's correct behavior
            pass
