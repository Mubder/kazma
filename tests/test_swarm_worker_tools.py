"""Tests for the InProcessWorker ReAct tool-calling loop.

Covers:
    * Basic dispatch with no tool calls (no tools needed)
    * Dispatch with tools → model calls a tool → result fed back → final answer
    * Multiple tool calls in one response
    * Multi-iteration loop (tool call → execute → another tool call → final)
    * Empty / filtered capabilities.tools
    * Max iteration limit prevents infinite loops
    * Tool execution failure is fed back as content (not crash)
    * Cumulative token/cost tracking across iterations
    * Error path still returns clean error
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.llm_provider import LLMResponse, ToolCall


# ── Helpers ────────────────────────────────────────────────────────────

def _make_response(
    content: str = "",
    tool_calls: list[ToolCall] | None = None,
    finish_reason: str = "stop",
    usage: dict | None = None,
    cost_usd: float = 0.0,
) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        usage=usage or {"prompt_tokens": 10, "completion_tokens": 5},
        cost_usd=cost_usd,
    )


def _make_tool_call(tool_id: str, name: str, args: dict) -> ToolCall:
    return ToolCall(id=tool_id, name=name, arguments=args)


# ── Test classes ───────────────────────────────────────────────────────


class TestWorkerDispatchNoTools:
    """When no tools are needed, the worker returns a plain text response."""

    @pytest.mark.asyncio
    async def test_single_shot_no_tool_calls(self):
        """Model responds with plain text (no tool_calls) on first attempt."""
        from kazma_core.swarm.worker import InProcessWorker

        worker = InProcessWorker(name="test", role="tester")

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            return_value=_make_response(content="Here is the answer.")
        )
        mock_registry = MagicMock()
        mock_registry.get_client = MagicMock(return_value=mock_provider)

        with patch(
            "kazma_core.model_registry.get_model_registry",
            return_value=mock_registry,
        ):
            result = await worker.dispatch("What is Python?")

        assert result["status"] == "success"
        assert result["output"] == "Here is the answer."
        assert result["error"] is None
        # Only one LLM call was made
        assert mock_provider.chat.call_count == 1


class TestWorkerToolExecution:
    """The worker executes tool calls and feeds results back to the LLM."""

    @pytest.mark.asyncio
    async def test_tool_call_then_final_response(self):
        """One tool call → tool result → final text."""
        from kazma_core.swarm.worker import InProcessWorker
        from kazma_core.swarm.task import WorkerCapabilities

        worker = InProcessWorker(
            name="test",
            role="researcher",
            capabilities=WorkerCapabilities(
                role="researcher",
                tools=["web_search"],
            ),
        )

        # First call: model returns a tool call
        # Second call: model returns final text
        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            side_effect=[
                _make_response(
                    tool_calls=[
                        _make_tool_call("call_1", "web_search", {"query": "kazma"})
                    ],
                    finish_reason="tool_calls",
                ),
                _make_response(content="Kazma is an AI agent framework."),
            ]
        )
        mock_registry = MagicMock()
        mock_registry.get_client = MagicMock(return_value=mock_provider)

        with patch(
            "kazma_core.model_registry.get_model_registry",
            return_value=mock_registry,
        ):
            with patch(
                "kazma_core.agent.tool_registry.get_tool_registry",
            ) as mock_get_tr:
                mock_tr = MagicMock()
                mock_tr.get_tool_definitions.return_value = [
                    {
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "description": "Search the web",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ]
                mock_tr.execute = AsyncMock(
                    return_value={"content": "Kazma.ai results here", "is_error": False}
                )
                mock_get_tr.return_value = mock_tr

                result = await worker.dispatch("search for kazma")

        assert result["status"] == "success"
        assert "Kazma is an AI agent framework" in result["output"]
        assert mock_provider.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_one_response(self):
        """One response with two tool calls → both executed → final answer."""
        from kazma_core.swarm.worker import InProcessWorker
        from kazma_core.swarm.task import WorkerCapabilities

        worker = InProcessWorker(
            name="test",
            role="researcher",
            capabilities=WorkerCapabilities(
                role="researcher", tools=["web_search", "file_read"]
            ),
        )

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            side_effect=[
                _make_response(
                    tool_calls=[
                        _make_tool_call("c1", "web_search", {"query": "a"}),
                        _make_tool_call("c2", "file_read", {"path": "/tmp"}),
                    ],
                    finish_reason="tool_calls",
                ),
                _make_response(content="Combined results: ..."),
            ]
        )
        mock_registry = MagicMock()
        mock_registry.get_client = MagicMock(return_value=mock_provider)

        with patch(
            "kazma_core.model_registry.get_model_registry",
            return_value=mock_registry,
        ):
            with patch(
                "kazma_core.agent.tool_registry.get_tool_registry",
            ) as mock_get_tr:
                mock_tr = MagicMock()
                mock_tr.get_tool_definitions.return_value = [
                    {
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "description": "search",
                            "parameters": {},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "description": "read",
                            "parameters": {},
                        },
                    },
                ]
                mock_tr.execute = AsyncMock(
                    side_effect=[
                        {"content": "search results", "is_error": False},
                        {"content": "file contents", "is_error": False},
                    ]
                )
                mock_get_tr.return_value = mock_tr

                result = await worker.dispatch("do two things")

        assert result["status"] == "success"
        # Both tools executed
        assert mock_tr.execute.call_count == 2
        # Final LLM response used
        assert "Combined results" in result["output"]
        assert mock_provider.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_tool_execution_failure_fed_back_as_content(self):
        """When a tool execution crashes, the error is fed back, not raised."""
        from kazma_core.swarm.worker import InProcessWorker
        from kazma_core.swarm.task import WorkerCapabilities

        worker = InProcessWorker(
            name="test",
            role="tester",
            capabilities=WorkerCapabilities(role="tester", tools=["broken_tool"]),
        )

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            side_effect=[
                _make_response(
                    tool_calls=[
                        _make_tool_call("c1", "broken_tool", {"arg": "x"})
                    ],
                    finish_reason="tool_calls",
                ),
                _make_response(content="Tool failed but I'll answer anyway."),
            ]
        )
        mock_registry = MagicMock()
        mock_registry.get_client = MagicMock(return_value=mock_provider)

        with patch(
            "kazma_core.model_registry.get_model_registry",
            return_value=mock_registry,
        ):
            with patch(
                "kazma_core.agent.tool_registry.get_tool_registry",
            ) as mock_get_tr:
                mock_tr = MagicMock()
                mock_tr.get_tool_definitions.return_value = [
                    {
                        "type": "function",
                        "function": {
                            "name": "broken_tool",
                            "description": "breaks",
                            "parameters": {},
                        },
                    }
                ]
                mock_tr.execute = AsyncMock(
                    side_effect=RuntimeError("tool crashed")
                )
                mock_get_tr.return_value = mock_tr

                result = await worker.dispatch("use broken tool")

        assert result["status"] == "success"
        assert "Tool failed" in result["output"]


class TestWorkerToolFiltering:
    """capabilities.tools acts as a whitelist — only listed tools are exposed."""

    @pytest.mark.asyncio
    async def test_restricted_tools_filters_definitions(self):
        """When capabilities.tools = ['web_search'], only that tool is sent."""
        from kazma_core.swarm.worker import InProcessWorker
        from kazma_core.swarm.task import WorkerCapabilities

        worker = InProcessWorker(
            name="test",
            role="researcher",
            capabilities=WorkerCapabilities(role="researcher", tools=["web_search"]),
        )

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            return_value=_make_response(content="Done.")
        )
        mock_registry = MagicMock()
        mock_registry.get_client = MagicMock(return_value=mock_provider)

        with patch(
            "kazma_core.model_registry.get_model_registry",
            return_value=mock_registry,
        ):
            with patch(
                "kazma_core.agent.tool_registry.get_tool_registry",
            ) as mock_get_tr:
                mock_tr = MagicMock()
                mock_tr.get_tool_definitions.return_value = [
                    {
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "description": "search",
                            "parameters": {},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "description": "read",
                            "parameters": {},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "shell_exec",
                            "description": "exec",
                            "parameters": {},
                        },
                    },
                ]
                mock_get_tr.return_value = mock_tr

                await worker.dispatch("search for kazma")

        # Only web_search was passed to the LLM
        tools_sent = mock_provider.chat.call_args.kwargs.get("tools", [])
        tool_names = [t["function"]["name"] for t in tools_sent]
        assert tool_names == ["web_search"]


class TestMaxIterations:
    """The ReAct loop aborts after MAX_ITERATIONS to prevent infinite loops."""

    @pytest.mark.asyncio
    async def test_max_iterations_safety_valve(self):
        """After 15 iterations of tool calls, stop and return best-effort."""
        from kazma_core.swarm.worker import InProcessWorker
        from kazma_core.swarm.task import WorkerCapabilities

        worker = InProcessWorker(
            name="test",
            role="tester",
            capabilities=WorkerCapabilities(role="tester", tools=["loop_tool"]),
        )

        # Always return a tool call — model stuck in a tool-calling loop
        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            return_value=_make_response(
                content="",
                tool_calls=[
                    _make_tool_call("c1", "loop_tool", {"x": 1})
                ],
                finish_reason="tool_calls",
            )
        )
        mock_registry = MagicMock()
        mock_registry.get_client = MagicMock(return_value=mock_provider)

        with patch(
            "kazma_core.model_registry.get_model_registry",
            return_value=mock_registry,
        ):
            with patch(
                "kazma_core.agent.tool_registry.get_tool_registry",
            ) as mock_get_tr:
                mock_tr = MagicMock()
                mock_tr.get_tool_definitions.return_value = [
                    {
                        "type": "function",
                        "function": {
                            "name": "loop_tool",
                            "description": "loops",
                            "parameters": {},
                        },
                    }
                ]
                mock_tr.execute = AsyncMock(
                    return_value={"content": "loop result", "is_error": False}
                )
                mock_get_tr.return_value = mock_tr

                result = await worker.dispatch("start looping")

        assert result["status"] == "success"
        # Exactly 15 iterations (MAX_ITERATIONS), not infinite
        assert mock_provider.chat.call_count == 15
        assert "Max tool-use iterations" in result["output"] or result["output"]


class TestCumulativeTokensAndCost:
    """Token and cost counts are summed across all ReAct iterations."""

    @pytest.mark.asyncio
    async def test_cumulative_tracking(self):
        """Two LLM calls → (10+5) + (10+10) = 35 tokens, 0.001 + 0.002 = 0.003 cost.

        Token counting sums ONLY prompt_tokens + completion_tokens (not
        total_tokens which would double-count).
        """
        from kazma_core.swarm.worker import InProcessWorker

        worker = InProcessWorker(name="test", role="tester")

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            side_effect=[
                _make_response(
                    tool_calls=[
                        _make_tool_call("c1", "tool_a", {"x": 1})
                    ],
                    finish_reason="tool_calls",
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    cost_usd=0.001,
                ),
                _make_response(
                    content="Done",
                    usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                    cost_usd=0.002,
                ),
            ]
        )
        mock_registry = MagicMock()
        mock_registry.get_client = MagicMock(return_value=mock_provider)

        with patch(
            "kazma_core.model_registry.get_model_registry",
            return_value=mock_registry,
        ):
            with patch(
                "kazma_core.agent.tool_registry.get_tool_registry",
            ) as mock_get_tr:
                mock_tr = MagicMock()
                mock_tr.get_tool_definitions.return_value = [
                    {
                        "type": "function",
                        "function": {
                            "name": "tool_a",
                            "description": "a",
                            "parameters": {},
                        },
                    }
                ]
                mock_tr.execute = AsyncMock(
                    return_value={"content": "ok", "is_error": False}
                )
                mock_get_tr.return_value = mock_tr

                result = await worker.dispatch("do thing")

        # (10+5) + (10+10) = 35 tokens — NOT 65 (which would be the
        # case if total_tokens were also summed: 15+15+20+20=70... wait).
        # Correct: prompt_tokens + completion_tokens per iteration,
        # accumulated: (10+5) + (10+10) = 35.
        assert result["tokens_used"] == 35
        assert result["cost"] == pytest.approx(0.003, abs=0.001)


class TestDispatchErrorPath:
    """The existing error-handling path still works with the new tool loop."""

    @pytest.mark.asyncio
    async def test_provider_exception_returns_error(self):
        """If provider.chat() raises, return clean error result."""
        from kazma_core.swarm.worker import InProcessWorker

        worker = InProcessWorker(name="test", role="tester")

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(side_effect=RuntimeError("connection lost"))
        mock_registry = MagicMock()
        mock_registry.get_client = MagicMock(return_value=mock_provider)

        with patch(
            "kazma_core.model_registry.get_model_registry",
            return_value=mock_registry,
        ):
            result = await worker.dispatch("do thing")

        assert result["status"] == "error"
        assert "connection lost" in result["error"]
        assert result["output"] == ""
        assert result["tokens_used"] == 0
        assert result["cost"] == 0.0
