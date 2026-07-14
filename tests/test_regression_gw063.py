"""Regression tests for gw-063: ReAct iteration counter.

BUG 1: ReAct iteration counter dead — supervisor_node returns iteration unchanged.

BUG 2 and 3 (KG edge attributes) were removed - the dead KG code (KazmaKG, KnowledgeGraphAdapter)
was deleted from kazma-core as they were never used in production.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from kazma_core.llm_provider import LLMResponse, ToolCall

# -----------------------------------------------------------------------
# BUG 1: ReAct iteration counter must increment on tool-call path
# -----------------------------------------------------------------------


class TestIterationCounterIncrement:
    """supervisor_node must return iteration+1 when routing to TOOL_WORKER."""

    @pytest.mark.asyncio
    async def test_iteration_increments_on_tool_calls(self):
        """When LLM returns tool_calls, iteration must be incremented."""
        from kazma_core.agent.graph_builder import supervisor_node
        from kazma_core.agent.state import NodeName

        # Mock LLM response with tool calls
        response = LLMResponse(
            content="",
            tool_calls=[ToolCall(id="tc1", name="search", arguments={"q": "test"})],
            finish_reason="tool_calls",
            model="test-model",
            usage={"total_tokens": 100},
            cost_usd=0.001,
        )

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=response)

        cost_breaker = MagicMock()
        cost_breaker.should_halt.return_value = False

        authority = AsyncMock()
        authority.check_and_enforce = AsyncMock(side_effect=lambda s: s)

        tracer = MagicMock()

        state = {
            "iteration": 3,
            "messages": [{"role": "user", "content": "hello"}],
        }

        result = await supervisor_node(
            state,
            llm=llm,
            system_prompt="test",
            tool_definitions=[{"type": "function", "function": {"name": "search"}}],
            tool_executor=None,
            cost_breaker=cost_breaker,
            authority=authority,
            tracer=tracer,
        )

        assert result["next_node"] == NodeName.TOOL_WORKER
        assert result["iteration"] == 4, (
            f"BUG 1 REGRESSION: iteration should be 4 (3+1), got {result['iteration']}"
        )

    @pytest.mark.asyncio
    async def test_iteration_starts_from_zero(self):
        """First tool-call iteration should return 1, not 0."""
        from kazma_core.agent.graph_builder import supervisor_node

        response = LLMResponse(
            content="",
            tool_calls=[ToolCall(id="tc1", name="read", arguments={"path": "/tmp"})],
            finish_reason="tool_calls",
            model="test-model",
            usage={"total_tokens": 50},
            cost_usd=0.0,
        )

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=response)

        cost_breaker = MagicMock()
        cost_breaker.should_halt.return_value = False

        authority = AsyncMock()
        authority.check_and_enforce = AsyncMock(side_effect=lambda s: s)

        tracer = MagicMock()

        state = {
            "iteration": 0,
            "messages": [{"role": "user", "content": "read file"}],
        }

        result = await supervisor_node(
            state,
            llm=llm,
            system_prompt="test",
            tool_definitions=[],
            tool_executor=None,
            cost_breaker=cost_breaker,
            authority=authority,
            tracer=tracer,
        )

        assert result["iteration"] == 1, (
            f"BUG 1 REGRESSION: first iteration should be 1, got {result['iteration']}"
        )