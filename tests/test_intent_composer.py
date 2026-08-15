"""Composer tests — research_then_document (§19)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.agent.intent.handlers.compose import run_research_then_document
from kazma_core.agent.intent.types import (
    ActKind,
    EntitySet,
    IntentAct,
    RouteKind,
    TurnDecision,
)


def _multi_act_decision():
    return TurnDecision(
        focus="normal",
        acts=(
            IntentAct(kind=ActKind.RESEARCH_DEEP, confidence=0.90, slots={"topic": "AI in Kuwait"}),
            IntentAct(kind=ActKind.DOCUMENT_GENERATE, confidence=0.86, slots={"format": "pdf"}),
        ),
        entities=EntitySet(),
        route=RouteKind.EXECUTE,
        handler="research_then_document",
        reason="composer",
    )


class TestComposer:
    @pytest.mark.asyncio
    async def test_research_success_returns_plan(self):
        """Research starts successfully → composed plan note returned."""
        mock_sess = MagicMock()
        mock_sess.id = "rs_comp_123"
        mock_sess.status = "running"

        decision = _multi_act_decision()

        with patch(
            "kazma_core.tools.research_session.start_deep_research",
            new_callable=AsyncMock,
            return_value=mock_sess,
        ):
            result = await run_research_then_document(decision, {"messages": []})

        assert result.ok
        assert "rs_comp_123" in result.message
        assert result.artifacts.get("composed") is True
        assert result.artifacts.get("next_action") == "generate_document"
        assert "INTENT ENGINE" in result.message

    @pytest.mark.asyncio
    async def test_research_failure_escalates(self):
        """Research fails → escalate, do NOT generate a PDF of the error."""
        decision = _multi_act_decision()

        with patch(
            "kazma_core.tools.research_session.start_deep_research",
            new_callable=AsyncMock,
            side_effect=Exception("SearXNG down"),
        ):
            result = await run_research_then_document(decision, {"messages": []})

        assert not result.ok
        assert result.escalate
        assert "SearXNG down" in result.message or "research failed" in result.message.lower()

    @pytest.mark.asyncio
    async def test_no_research_act_escalates(self):
        """Decision without research_deep act → escalate."""
        decision = TurnDecision(
            focus="normal",
            acts=(IntentAct(kind=ActKind.DOCUMENT_GENERATE, confidence=0.90, slots={"format": "pdf"}),),
            entities=EntitySet(),
            route=RouteKind.EXECUTE,
            handler="research_then_document",
            reason="test",
        )
        result = await run_research_then_document(decision, {"messages": []})
        assert not result.ok
        assert result.escalate

    @pytest.mark.asyncio
    async def test_no_document_act_escalates(self):
        """Decision without document_generate act → escalate."""
        decision = TurnDecision(
            focus="normal",
            acts=(IntentAct(kind=ActKind.RESEARCH_DEEP, confidence=0.90, slots={"topic": "test"}),),
            entities=EntitySet(),
            route=RouteKind.EXECUTE,
            handler="research_then_document",
            reason="test",
        )
        result = await run_research_then_document(decision, {"messages": []})
        assert not result.ok


class TestComposerRegistration:
    def test_composer_registered(self):
        from kazma_core.agent.intent.registry import get_registry

        registry = get_registry()
        composer = registry.get_composer(
            frozenset({ActKind.RESEARCH_DEEP, ActKind.DOCUMENT_GENERATE})
        )
        assert composer is not None
        assert composer.name == "research_then_document"

    def test_multi_act_routes_to_composer(self):
        """Multi-act research+PDF should find the composer in the registry."""
        from kazma_core.agent.intent.classify import classify_turn_sync
        from kazma_core.agent.intent.registry import get_registry

        d = classify_turn_sync("research this topic and make a PDF")
        if len([a for a in d.acts if a.kind != ActKind.GENERAL]) > 1:
            kinds = frozenset(a.kind for a in d.acts if a.kind != ActKind.GENERAL)
            composer = get_registry().get_composer(kinds)
            # If both acts detected, composer should exist for that set
            if ActKind.RESEARCH_DEEP in kinds and ActKind.DOCUMENT_GENERATE in kinds:
                assert composer is not None
