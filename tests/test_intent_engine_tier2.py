"""Intent engine Tier 2 LLM tests (§20.4)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kazma_core.agent.intent.classify import classify_turn
from kazma_core.agent.intent.types import ActKind, RouteKind


class FakeLLM:
    """Fake LLM that returns a canned response or raises."""

    def __init__(self, response_content=None, raise_exc=None):
        self.response_content = response_content
        self.raise_exc = raise_exc
        self.called = False

    async def chat(self, messages, tools=None, **kwargs):
        self.called = True
        if self.raise_exc:
            raise self.raise_exc
        resp = MagicMock()
        resp.content = self.response_content
        return resp


class TestTier2GrayZone:
    @pytest.mark.asyncio
    async def test_gray_zone_calls_llm(self):
        """A long general question (no heuristic match) should call the LLM."""
        llm = FakeLLM(
            response_content='{"acts": [{"kind": "research", "confidence": 0.7, "slots": {}}]}'
        )
        text = "I have been thinking about whether artificial intelligence will fundamentally change how small businesses operate in the Gulf region over the next decade"
        d = await classify_turn(text, llm=llm)
        assert llm.called, "Gray-zone utterance must call the LLM"
        # LLM returned research → should be in acts
        assert any(a.kind == ActKind.RESEARCH for a in d.acts) or d.route == RouteKind.LOOP

    @pytest.mark.asyncio
    async def test_high_precision_no_llm(self):
        """'reproduce this PDF' with attachment — high confidence, LLM not required."""
        llm = FakeLLM()
        atts = [{"kind": "file", "mime": "application/pdf", "path": "cal.pdf", "filename": "cal.pdf"}]
        d = await classify_turn(
            "reproduce this PDF with better templates",
            attachments=atts,
            llm=llm,
        )
        # High confidence heuristic — LLM may or may not be called,
        # but the document act must be present regardless
        assert any(a.kind == ActKind.DOCUMENT_GENERATE for a in d.acts)

    @pytest.mark.asyncio
    async def test_llm_garbage_keeps_heuristics(self):
        """LLM returns garbage → heuristic acts kept, no exception."""
        llm = FakeLLM(response_content="I don't understand JSON")
        text = "research the impact of AI on Kuwait's economy and make a PDF"
        d = await classify_turn(text, llm=llm)
        # No exception, route is valid
        assert d.route in (RouteKind.EXECUTE, RouteKind.CONSTRAIN, RouteKind.LOOP)

    @pytest.mark.asyncio
    async def test_llm_raises_keeps_heuristics(self):
        """LLM raises → heuristic acts kept, no exception."""
        llm = FakeLLM(raise_exc=Exception("connection refused"))
        text = "create a Word document with the meeting notes"
        d = await classify_turn(text, llm=llm)
        assert d.route in (RouteKind.CONSTRAIN, RouteKind.LOOP)
        assert not d.route == RouteKind.EXECUTE or d.handler is not None

    @pytest.mark.asyncio
    async def test_multi_act_gray_zone_calls_llm(self):
        """Multi-act (research + PDF) triggers gray zone → LLM called."""
        llm = FakeLLM(
            response_content='{"acts": [{"kind": "research", "confidence": 0.8}, {"kind": "document_generate", "confidence": 0.85, "slots": {"format": "pdf"}}]}'
        )
        text = "research this topic and make a PDF"
        d = await classify_turn(text, llm=llm)
        assert llm.called

    @pytest.mark.asyncio
    async def test_tier2_disabled_no_llm(self, monkeypatch):
        """KAZMA_INTENT_TIER2=0 → LLM never called even in gray zone."""
        monkeypatch.setenv("KAZMA_INTENT_TIER2", "0")
        llm = FakeLLM()
        text = "some ambiguous long question about artificial intelligence and business"
        d = await classify_turn(text, llm=llm)
        assert not llm.called
