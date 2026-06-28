"""Integration: prove KazmaAgent.run() executes the real supervisor graph.

Before this wiring, KazmaAgent.run() ran a hand-rolled ReAct loop with no
checkpointing while the LangGraph supervisor graph was only exercised by a
side test. These tests prove the *production* agent entry point now invokes the
compiled supervisor StateGraph with a durable AsyncSqliteSaver checkpointer:

  - run() returns the final assistant text from a real graph invocation;
  - a tool call flows through the graph's TOOL_WORKER and back;
  - a checkpoint is persisted to SQLite under the agent's thread id.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from kazma_core.agent import AgentConfig, KazmaAgent
from kazma_core.llm_provider import LLMResponse, ToolCall


@pytest.fixture
def agent(tmp_path) -> KazmaAgent:
    # Point the checkpoint DB at a temp dir so the test is isolated.
    cfg = AgentConfig(
        name="test-kazma",
        version="0.0.0-test",
        language="en",
        rtl=False,
        raw={"storage": {"checkpoint_path": str(tmp_path / "ckpt.db")}},
    )
    return KazmaAgent(config=cfg)


class TestAgentUsesGraph:
    async def test_run_goes_through_graph_and_checkpoints(self, agent: KazmaAgent) -> None:
        final = LLMResponse(content="hello from graph", finish_reason="stop", model="stub")
        with patch.object(agent.llm, "chat", new_callable=AsyncMock, return_value=final):
            out = await agent.run("hi")

        assert out == "hello from graph"

        # The agent built a real compiled graph + checkpointer (not a hand loop).
        assert agent._graph is not None
        assert agent._checkpointer is not None
        assert agent._thread_id

        # A checkpoint was persisted for the agent's thread and is readable.
        config = {"configurable": {"thread_id": agent._thread_id}}
        snap = await agent._graph.aget_state(config)
        assert snap is not None
        roles = [m.get("role") for m in snap.values.get("messages", [])]
        assert "user" in roles and "assistant" in roles
        await agent.shutdown()

    async def test_run_executes_tool_through_graph(self, agent: KazmaAgent) -> None:
        """A tool call flows through the graph's TOOL_WORKER node."""
        tool_resp = LLMResponse(
            content="",
            tool_calls=[ToolCall(id="c1", name="my_tool", arguments={"q": "x"})],
            finish_reason="tool_calls",
            model="stub",
        )
        final = LLMResponse(content="tool done", finish_reason="stop", model="stub")

        exec_mock = AsyncMock(return_value={"content": "OUT", "is_error": False})
        with (
            patch.object(agent.llm, "chat", new_callable=AsyncMock, side_effect=[tool_resp, final]),
            patch.object(agent.tools, "execute", exec_mock),
            patch.object(
                agent.tools,
                "get_tool_definitions",
                return_value=[{"type": "function", "function": {"name": "my_tool"}}],
            ),
        ):
            out = await agent.run("use the tool")
            # The tool executor was actually called by the graph's TOOL_WORKER
            # (assert inside the patch context).
            exec_mock.assert_awaited()

        assert out == "tool done"
        # The persisted conversation contains a tool-role message.
        config = {"configurable": {"thread_id": agent._thread_id}}
        snap = await agent._graph.aget_state(config)
        roles = [m.get("role") for m in snap.values.get("messages", [])]
        assert "tool" in roles
        await agent.shutdown()

    async def test_shutdown_closes_checkpointer(self, agent: KazmaAgent) -> None:
        final = LLMResponse(content="x", finish_reason="stop", model="stub")
        with patch.object(agent.llm, "chat", new_callable=AsyncMock, return_value=final):
            await agent.run("hi")
        assert agent._checkpoint_conn is not None
        await agent.shutdown()
        assert agent._checkpoint_conn is None
        assert agent._graph is None
