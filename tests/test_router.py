"""Integration tests for DialectRouter and DualEngineTokenizer."""

from __future__ import annotations

import pytest
from kazma_core.kuwaiti_tokenizer import TokenType
from kazma_core.router import (
    AgentRequest,
    AgentResponse,
    BasePipeline,
    DialectRouter,
    KuwaitiPipeline,
    MSAPipeline,
)
from kazma_core.tokenizer import DualEngineTokenizer


class TestDualEngineTokenizer:
    """Test the DualEngineTokenizer integration."""

    def setup_method(self):
        self.tok = DualEngineTokenizer()

    def test_kuwaiti_routing(self):
        result = self.tok.tokenize("شلونك وين ليش")
        assert result.is_kuwaiti
        assert result.dialect.dialect == "kw"
        assert len(result.tokens) > 0
        assert result.text == "شلونك وين ليش"

    def test_msa_routing(self):
        result = self.tok.tokenize("هذا تقرير رسمي من الحكومة")
        assert result.is_msa
        assert result.dialect.dialect == "msa"

    def test_token_texts_property(self):
        result = self.tok.tokenize("وين ليش")
        texts = result.token_texts
        assert "وين" in texts
        assert "ليش" in texts

    def test_dialect_tokens_property(self):
        result = self.tok.tokenize("شلونك وين")
        dialect = result.dialect_tokens
        assert len(dialect) >= 2
        assert all(t.token_type == TokenType.DIALECT for t in dialect)

    def test_code_switch_tokens_property(self):
        result = self.tok.tokenize("Meeting الحين")
        cs = result.code_switch_tokens
        assert len(cs) >= 1

    def test_empty_input(self):
        result = self.tok.tokenize("")
        assert result.tokens == []
        assert result.dialect.dialect == "msa"

    def test_batch_tokenization(self):
        results = self.tok.tokenize_batch(
            [
                "شلونك وين",
                "هذا تقرير",
                "Meeting الحين",
            ]
        )
        assert len(results) == 3
        assert results[0].is_kuwaiti
        assert results[1].is_msa
        assert results[2].dialect.dialect in ("kw", "msa")

    def test_batch_empty(self):
        results = self.tok.tokenize_batch([])
        assert results == []


class TestDialectRouter:
    """Test the DialectRouter integration."""

    def setup_method(self):
        self.router = DialectRouter()

    @pytest.mark.asyncio
    async def test_kuwaiti_request_routes_to_kuwaiti_pipeline(self):
        req = AgentRequest(text="شلونك وين ليش")
        resp = await self.router.route(req)
        assert resp.pipeline_used == "kuwaiti"
        assert resp.dialect == "kw"
        assert resp.confidence > 0.5

    @pytest.mark.asyncio
    async def test_msa_request_routes_to_msa_pipeline(self):
        req = AgentRequest(text="هذا تقرير رسمي من الحكومة")
        resp = await self.router.route(req)
        assert resp.pipeline_used == "msa"
        assert resp.dialect == "msa"

    @pytest.mark.asyncio
    async def test_unknown_dialect_falls_back_to_msa(self):
        req = AgentRequest(text="hello world")
        resp = await self.router.route(req)
        # English text should default to MSA pipeline
        assert resp.pipeline_used == "msa"

    @pytest.mark.asyncio
    async def test_response_metadata(self):
        req = AgentRequest(text="شلونك وين")
        resp = await self.router.route(req)
        assert "pipeline" in resp.metadata
        assert "total_tokens" in resp.metadata
        assert resp.metadata["pipeline"] == "kuwaiti"

    @pytest.mark.asyncio
    async def test_empty_request(self):
        req = AgentRequest(text="")
        resp = await self.router.route(req)
        assert resp.dialect == "msa"

    @pytest.mark.asyncio
    async def test_request_with_session_id(self):
        req = AgentRequest(
            text="شلونك",
            session_id="sess-123",
            user_id="user-456",
        )
        resp = await self.router.route(req)
        assert resp.pipeline_used == "kuwaiti"

    def test_get_pipeline(self):
        kw_pipeline = self.router.get_pipeline("kw")
        assert isinstance(kw_pipeline, KuwaitiPipeline)
        msa_pipeline = self.router.get_pipeline("msa")
        assert isinstance(msa_pipeline, MSAPipeline)

    def test_get_pipeline_unknown_falls_back_to_msa(self):
        pipeline = self.router.get_pipeline("unknown")
        assert isinstance(pipeline, MSAPipeline)

    def test_register_custom_pipeline(self):
        class CustomPipeline(BasePipeline):
            name = "custom"

            async def execute(self, request, token_result):
                return AgentResponse(
                    text=request.text,
                    dialect="custom",
                    confidence=1.0,
                    pipeline_used="custom",
                )

        self.router.register_pipeline("custom", CustomPipeline())
        assert "custom" in self.router.pipelines
        assert self.router.get_pipeline("custom").name == "custom"


class TestEndToEnd:
    """End-to-end integration tests."""

    def setup_method(self):
        self.router = DialectRouter()

    @pytest.mark.asyncio
    async def test_kuwaiti_to_msa_switch(self):
        """Two requests: first Kuwaiti, second MSA."""
        resp1 = await self.router.route(AgentRequest(text="شلونك وين ليش"))
        resp2 = await self.router.route(AgentRequest(text="التقرير الرسمي من الوزارة"))
        assert resp1.pipeline_used == "kuwaiti"
        assert resp2.pipeline_used == "msa"

    @pytest.mark.asyncio
    async def test_mixed_content_defaults_to_detected_dialect(self):
        """Text with some dialect markers and some formal text."""
        resp = await self.router.route(AgentRequest(text="وين التقرير الرسمي"))
        # Should detect Kuwaiti due to "وين"
        assert resp.dialect == "kw"

    @pytest.mark.asyncio
    async def test_performance_route(self):
        """Routing should be reasonably fast (after warmup).

        The threshold is generous (500ms) to avoid flakiness on slower
        machines (e.g. Windows without GPU). The warmup call avoids
        measuring the one-time dialect-model load.
        """
        import time

        # Warm up the router (first call loads the dialect model).
        warmup_req = AgentRequest(text="مرحبا")
        await self.router.route(warmup_req)

        req = AgentRequest(text="شلونك وين ليش هلا تمام خوش")
        start = time.perf_counter()
        resp = await self.router.route(req)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 500, f"Routing took {elapsed_ms:.1f}ms"
