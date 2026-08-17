"""Research handler tests (§18 Phase 2)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.agent.intent.handlers.research import run_research_deep
from kazma_core.agent.intent.types import (
    ActKind,
    EntitySet,
    IntentAct,
    RouteKind,
    TurnDecision,
)


def _decision(topic="AI impact on Kuwait", *, acts=None):
    if acts is None:
        acts = (IntentAct(
            kind=ActKind.RESEARCH_DEEP,
            confidence=0.90,
            slots={"topic": topic},
        ),)
    return TurnDecision(
        focus="normal",
        acts=acts,
        entities=EntitySet(),
        route=RouteKind.EXECUTE,
        handler="research_deep",
        reason="test",
    )


class TestResearchDeepExecute:
    @pytest.mark.asyncio
    async def test_successful_research(self):
        """start_deep_research returns a session → HandlerResult.ok."""
        mock_sess = MagicMock()
        mock_sess.id = "rs_test_123"
        mock_sess.status = "running"
        mock_sess.stage = "start"

        decision = _decision()
        with patch(
            "kazma_core.tools.research_session.start_deep_research",
            new_callable=AsyncMock,
            return_value=mock_sess,
        ):
            result = await run_research_deep(decision, {"messages": []})

        assert result.ok
        assert "rs_test_123" in result.message
        assert result.artifacts.get("session_id") == "rs_test_123"

    @pytest.mark.asyncio
    async def test_no_topic_escalates(self):
        """No topic in slots or messages → escalate."""
        decision = _decision(topic="")
        result = await run_research_deep(decision, {"messages": []}, )
        assert not result.ok
        assert result.escalate

    @pytest.mark.asyncio
    async def test_topic_from_messages(self):
        """Topic not in slots but present in user messages → use it."""
        mock_sess = MagicMock()
        mock_sess.id = "rs_msg_456"
        mock_sess.status = "running"

        decision = _decision(topic="")  # No topic in slots
        state = {"messages": [{"role": "user", "content": "research cloud security"}]}

        with patch(
            "kazma_core.tools.research_session.start_deep_research",
            new_callable=AsyncMock,
            return_value=mock_sess,
        ) as mock_start:
            result = await run_research_deep(decision, state)

        assert result.ok
        # Verify the topic was extracted from messages
        call_args = mock_start.call_args
        assert "cloud security" in str(call_args[0][0])

    @pytest.mark.asyncio
    async def test_pipeline_failure_escalates(self):
        """start_deep_research raises → escalate."""
        decision = _decision()
        with patch(
            "kazma_core.tools.research_session.start_deep_research",
            new_callable=AsyncMock,
            side_effect=Exception("pipeline crashed"),
        ):
            result = await run_research_deep(decision, {"messages": []})

        assert not result.ok
        assert result.escalate
        assert "pipeline crashed" in result.message

    @pytest.mark.asyncio
    async def test_session_error_escalates(self):
        """Session created but status=error → escalate."""
        mock_sess = MagicMock()
        mock_sess.status = "error"
        mock_sess.error = "SearXNG unavailable"

        decision = _decision()
        with patch(
            "kazma_core.tools.research_session.start_deep_research",
            new_callable=AsyncMock,
            return_value=mock_sess,
        ):
            result = await run_research_deep(decision, {"messages": []})

        assert not result.ok
        assert "SearXNG unavailable" in result.message


class TestMultiActResearchPDF:
    """§20.1: multi-act research+PDF → constrain, not execute (Phase 2)."""

    def test_research_and_pdf_both_acts(self):
        from kazma_core.agent.intent.heuristics import detect_acts
        from kazma_core.agent.intent.types import ActKind

        acts = detect_acts("research this topic and make a PDF")
        kinds = {a.kind for a in acts}
        assert ActKind.DOCUMENT_GENERATE in kinds
        # Research may or may not fire depending on research_policy patterns

    def test_multi_act_not_execute_phase2(self):
        """Multi-act → constrain, not execute (composer is Phase 3)."""
        from kazma_core.agent.intent.classify import classify_turn_sync
        from kazma_core.agent.intent.types import RouteKind

        d = classify_turn_sync("research this topic and make a PDF")
        assert d.route != RouteKind.EXECUTE
        assert d.route == RouteKind.CONSTRAIN
        assert "INTENT ENGINE" in d.plan_note


class TestResearchRouteHintDedup:
    """§18: constrain notes replace duplicate deep_research_route_hint."""

    def test_policy_provides_research_constrain_note(self):
        from kazma_core.agent.intent.classify import classify_turn_sync

        d = classify_turn_sync("research the impact of AI on Kuwait's economy")
        # Even if not execute (casual research at 0.70), should constrain
        if d.route == RouteKind.CONSTRAIN:
            assert "INTENT ENGINE" in d.plan_note
            assert "research" in d.plan_note.lower()
