"""A real durable approval resumes in its original workspace on both backends."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager

import pytest
from kazma_core.agent.state import SupervisorState
from kazma_core.ide.workspace_scope import workspace_path_scope
from kazma_core.workspace.binding import resolve_active_root
from kazma_ui.turn_runtime import invoke_turn
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

pytestmark = pytest.mark.postgres


def test_restored_approval_keeps_root_and_has_one_effect(tmp_path):
    pg = os.environ.get("KAZMA_DB_BACKEND") == "postgres"
    dsn = os.environ.get("KAZMA_DATABASE_URL")
    if pg and (os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1" or not dsn):
        pytest.fail("Real Postgres admission required")
    thread = "backend-recovery-" + uuid.uuid4().hex
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()

    @asynccontextmanager
    async def open_saver():
        if pg:
            from kazma_core.checkpoints_pg import close_postgres_checkpointer, open_postgres_checkpointer

            saver = await open_postgres_checkpointer(dsn, max_size=2)
            try:
                yield saver
            finally:
                await close_postgres_checkpointer(saver)
        else:
            async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.db")) as saver:
                yield saver

    def compile_graph(saver):
        async def edit(state):
            if interrupt({"tool": "fixture_write"}):
                with (resolve_active_root() / "effect.txt").open("a", encoding="utf-8") as stream:
                    stream.write("once\n")
            return {"messages": [{"role": "assistant", "content": "finished"}]}

        graph = StateGraph(SupervisorState)
        graph.add_node("edit", edit)
        graph.set_entry_point("edit")
        graph.add_edge("edit", END)
        return graph.compile(checkpointer=saver)

    async def scenario():
        config = {"configurable": {"thread_id": thread}}
        async with open_saver() as saver:
            graph = compile_graph(saver)
            async with workspace_path_scope(a):
                await invoke_turn(graph, {"thread_id": thread}, config, persist=False, register=False)
            state = await graph.aget_state(config)
            assert state.next == ("edit",) and state.values["workspace_root"] == str(a.resolve())
        async with open_saver() as saver:
            try:
                graph = compile_graph(saver)
                async with workspace_path_scope(b):
                    await invoke_turn(graph, Command(resume=True), config, persist=False, register=False)
                    await invoke_turn(graph, Command(resume=True), config, persist=False, register=False)
                assert (a / "effect.txt").read_text() == "once\n"
                assert not (b / "effect.txt").exists()
                assert not (await graph.aget_state(config)).next
            finally:
                await saver.adelete_thread(thread)

    factory = None
    if sys.platform == "win32":
        factory = lambda: asyncio.SelectorEventLoop()  # noqa: E731
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(scenario())
