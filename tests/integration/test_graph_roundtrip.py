"""Integration: execute the REAL LangGraph supervisor graph end-to-end.

The existing suite only *compiles* the supervisor graph and checks its
topology (tests/test_supervisor.py::TestGraphBuilder) — it never invokes a
compiled graph, so the README's two flagship claims ("Built on LangGraph" +
"durable SQLite checkpointing / resume") were unverified at the behavior level.

These tests drive the real graph through
    SUPERVISOR -> TOOL_WORKER -> SUPERVISOR -> RESPOND -> END
with a stub LLM (no network), backed by a real AsyncSqliteSaver, and assert:
  1. The graph actually runs a ReAct loop: a tool gets executed and its
     output flows back into the final assistant message.
  2. A checkpoint is persisted to SQLite for the thread.
  3. The graph can be re-opened from that checkpoint (resume from saved state).
"""

from __future__ import annotations

import aiosqlite
from kazma_core.agent.graph_builder import build_supervisor_graph
from kazma_core.agent.state import initial_supervisor_state
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.authority import create_authority
from kazma_core.cost_breaker import create_cost_breaker
from kazma_core.llm_provider import LLMResponse, ToolCall
from kazma_core.tracing import KazmaTracer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class StubLLM:
    """A scripted LLM driving the real graph without any network call.

    Call 1: ask for a `shell_exec` tool call (-> TOOL_WORKER).
    Call 2+: return a final text answer that echoes the tool output (-> RESPOND).
    """

    def __init__(self) -> None:
        self.calls = 0
        self.last_tool_output: str | None = None

    async def chat(self, *, messages, tools=None, model=None) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(id="call_1", name="shell_exec", arguments={"command": "echo kazma-graph-ok"})
                ],
                finish_reason="tool_calls",
                model="stub",
                usage={"total_tokens": 11},
                cost_usd=0.0,
            )
        # Capture the tool-role message the worker appended so we can prove
        # the tool output actually flowed back into the conversation.
        for m in messages:
            if m.get("role") == "tool":
                self.last_tool_output = str(m.get("content", ""))
        return LLMResponse(
            content=f"Done. Tool said: {self.last_tool_output}",
            tool_calls=[],
            finish_reason="stop",
            model="stub",
            usage={"total_tokens": 7},
            cost_usd=0.0,
        )


def _build(checkpointer):
    registry = LocalToolRegistry(include_builtins=True)
    llm = StubLLM()
    return llm, build_supervisor_graph(
        llm=llm,
        system_prompt="You are a test agent.",
        tool_definitions=registry.get_tool_definitions(),
        tool_executor=registry,
        cost_breaker=create_cost_breaker(),
        authority=create_authority(model="test", window=128000),
        tracer=KazmaTracer(backend="console"),
        checkpointer=checkpointer,
    )


class TestRealGraphRoundTrip:
    async def test_react_loop_executes_real_tool(self) -> None:
        """Graph runs SUPERVISOR->TOOL_WORKER->SUPERVISOR->RESPOND with a real tool."""
        conn = await aiosqlite.connect(":memory:")
        try:
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            llm, graph = _build(saver)

            state = initial_supervisor_state(thread_id="rt-thread-1")
            state["messages"] = [{"role": "user", "content": "run echo"}]
            config = {"configurable": {"thread_id": "rt-thread-1"}}

            final = await graph.ainvoke(state, config)

            # LLM was called at least twice (tool round + final answer).
            assert llm.calls >= 2
            # A real tool executed and its stdout flowed back into the loop.
            assert llm.last_tool_output is not None
            assert "kazma-graph-ok" in llm.last_tool_output
            # The final assistant message reflects the tool output.
            assistant = [m for m in final["messages"] if m.get("role") == "assistant"]
            assert assistant, "no assistant message produced"
            assert "kazma-graph-ok" in assistant[-1]["content"]
            # A tool-role message is present in the persisted conversation.
            assert any(m.get("role") == "tool" for m in final["messages"])
        finally:
            await conn.close()

    async def test_checkpoint_persisted_and_resumable(self) -> None:
        """A checkpoint is written to SQLite and the graph resumes from it."""
        db = "kazma-data/test_roundtrip_ckpt.db"
        import os

        os.makedirs("kazma-data", exist_ok=True)
        if os.path.exists(db):
            os.remove(db)

        config = {"configurable": {"thread_id": "rt-thread-2"}}

        # ── Run once, persisting to a real on-disk SQLite checkpoint DB ──
        conn = await aiosqlite.connect(db)
        try:
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            _, graph = _build(saver)
            state = initial_supervisor_state(thread_id="rt-thread-2")
            state["messages"] = [{"role": "user", "content": "run echo"}]
            final = await graph.ainvoke(state, config)
            assert any(m.get("role") == "tool" for m in final["messages"])

            # The checkpointer has state for this thread.
            snap = await graph.aget_state(config)
            assert snap is not None
            assert snap.values.get("messages"), "checkpoint stored no messages"
        finally:
            await conn.close()

        # ── Re-open the DB in a fresh process-like context and resume ──
        conn2 = await aiosqlite.connect(db)
        try:
            saver2 = AsyncSqliteSaver(conn2)
            await saver2.setup()
            _, graph2 = _build(saver2)
            snap2 = await graph2.aget_state(config)
            assert snap2 is not None
            # The conversation we built earlier survived the reopen.
            roles = [m.get("role") for m in snap2.values.get("messages", [])]
            assert "user" in roles
            assert "assistant" in roles
            assert "tool" in roles
        finally:
            await conn2.close()
        os.remove(db)
