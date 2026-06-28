"""Server + graph end-to-end smoke for the repro.

Two things the attribute-only lifecycle check never did:

1. Boot the REAL FastAPI app through ASGI (TestClient) and hit the README's
   own documented smoke endpoints (`/health`, `/`, `/api/gateway/status`),
   asserting real HTTP responses come back from the running app.

2. Flow a real message through the REAL LangGraph supervisor graph with a stub
   LLM (no network): SUPERVISOR -> TOOL_WORKER -> SUPERVISOR -> RESPOND, with a
   real on-disk AsyncSqliteSaver, then resume from the persisted checkpoint.

Prints SERVER_SMOKE_OK and GRAPH_FLOW_OK on success; exits non-zero on failure.
"""

from __future__ import annotations

import asyncio
import os
import sys


def server_smoke() -> bool:
    from fastapi.testclient import TestClient
    from kazma_ui.app import create_app

    app = create_app()
    with TestClient(app) as client:
        h = client.get("/health")
        print(f"GET /health -> {h.status_code}")
        assert h.status_code == 200, h.text

        root = client.get("/")
        print(f"GET / -> {root.status_code} ({len(root.content)} bytes)")
        assert root.status_code == 200

        gs = client.get("/api/gateway/status")
        print(f"GET /api/gateway/status -> {gs.status_code}")
        assert gs.status_code == 200, gs.text
        body = gs.json()
        assert "adapters" in body and "persistence" in body, f"unexpected body: {body!r}"
    print("SERVER_SMOKE_OK")
    return True


async def graph_flow() -> bool:
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
        def __init__(self) -> None:
            self.calls = 0
            self.tool_out: str | None = None

        async def chat(self, *, messages, tools=None, model=None) -> LLMResponse:
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    tool_calls=[ToolCall(id="c1", name="shell_exec", arguments={"command": "echo flow-ok"})],
                    finish_reason="tool_calls",
                    model="stub",
                    usage={"total_tokens": 9},
                )
            for m in messages:
                if m.get("role") == "tool":
                    self.tool_out = str(m.get("content", ""))
            return LLMResponse(content=f"done: {self.tool_out}", finish_reason="stop", model="stub")

    os.makedirs("kazma-data", exist_ok=True)
    db = "kazma-data/repro_flow.db"
    if os.path.exists(db):
        os.remove(db)
    config = {"configurable": {"thread_id": "repro-flow"}}

    registry = LocalToolRegistry(include_builtins=True)
    llm = StubLLM()
    conn = await aiosqlite.connect(db)
    try:
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        graph = build_supervisor_graph(
            llm=llm,
            system_prompt="test",
            tool_definitions=registry.get_tool_definitions(),
            tool_executor=registry,
            cost_breaker=create_cost_breaker(),
            authority=create_authority(model="test", window=128000),
            tracer=KazmaTracer(backend="console"),
            checkpointer=saver,
        )
        state = initial_supervisor_state(thread_id="repro-flow")
        state["messages"] = [{"role": "user", "content": "run echo"}]
        final = await graph.ainvoke(state, config)

        assert llm.calls >= 2, f"graph did not loop (calls={llm.calls})"
        assert llm.tool_out and "flow-ok" in llm.tool_out, "tool output did not flow back"
        assistant = [m for m in final["messages"] if m.get("role") == "assistant"]
        assert assistant and "flow-ok" in assistant[-1]["content"], "final answer missing tool output"
        print(f"graph: llm_calls={llm.calls} tool_output={llm.tool_out!r}")
    finally:
        await conn.close()

    # Resume: reopen the persisted checkpoint DB and read the conversation back.
    conn2 = await aiosqlite.connect(db)
    try:
        saver2 = AsyncSqliteSaver(conn2)
        await saver2.setup()
        graph2 = build_supervisor_graph(
            llm=StubLLM(),
            system_prompt="test",
            tool_definitions=registry.get_tool_definitions(),
            tool_executor=registry,
            cost_breaker=create_cost_breaker(),
            authority=create_authority(model="test", window=128000),
            tracer=KazmaTracer(backend="console"),
            checkpointer=saver2,
        )
        snap = await graph2.aget_state(config)
        roles = [m.get("role") for m in snap.values.get("messages", [])]
        assert {"user", "assistant", "tool"} <= set(roles), f"checkpoint missing roles: {roles}"
        print(f"resume: recovered roles={roles}")
    finally:
        await conn2.close()
    os.remove(db)
    print("GRAPH_FLOW_OK")
    return True


def main() -> int:
    try:
        server_smoke()
        asyncio.run(graph_flow())
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"SMOKE_FAILED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
