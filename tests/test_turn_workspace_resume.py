"""An approval resumes in its original workspace, including after restart."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from kazma_core.agent.state import SupervisorState
from kazma_core.agent.turn import _ainvoke
from kazma_core.agent.turn_workspace import turn_workspace
from kazma_core.exceptions import ConfigError, sanitize_error
from kazma_core.ide.workspace_scope import workspace_path_scope
from kazma_core.workspace.binding import resolve_active_root
from kazma_ui.turn_runtime import astream_events, invoke_turn


def _graph(saver):
    async def edit(state):
        if interrupt({"tool": "fixture_edit"}):
            target = resolve_active_root() / "effect.txt"
            target.write_text(target.read_text(encoding="utf-8") + "approved\n", encoding="utf-8")
        return {"messages": [{"role": "assistant", "content": "finished"}]}

    graph = StateGraph(SupervisorState)
    graph.add_node("edit", edit)
    graph.set_entry_point("edit")
    graph.add_edge("edit", END)
    return graph.compile(checkpointer=saver)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["invoke", "stream", "core"])
@pytest.mark.parametrize("resume", ["invoke", "core"])
async def test_reopened_checkpoint_keeps_the_root_and_executes_once(tmp_path, entry, resume):
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        root.mkdir()
        (root / "effect.txt").write_text("", encoding="utf-8")
    config = {"configurable": {"thread_id": f"{entry}-{resume}"}}
    db = str(tmp_path / "checkpoints.db")
    original = {"thread_id": f"{entry}-{resume}", "workspace_root": str(b)}

    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        graph = _graph(saver)
        async with workspace_path_scope(a):
            if entry == "stream":
                async for _ in astream_events(graph, original, config):
                    pass
            elif entry == "core":
                await _ainvoke(graph, original, config)
            else:
                await invoke_turn(graph, original, config, persist=False, register=False, session_id="owned")
        snap = await graph.aget_state(config)
        assert snap.next == ("edit",)
        assert snap.values["workspace_root"] == str(a.resolve())
        assert original["workspace_root"] == str(b), "entry must not mutate its caller's state"
        assert (a / "effect.txt").read_text() == ""

    # Reopen the durable saver and compile a new graph: no process-local
    # binding survives. Even a different caller scope must not redirect it.
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        graph = _graph(saver)
        async with workspace_path_scope(b):
            if resume == "core":
                await _ainvoke(graph, Command(resume=True), config)
            else:
                await invoke_turn(graph, Command(resume=True), config, persist=False, register=False, session_id="owned")
            assert resolve_active_root() == b.resolve()
            # Replaying a completed resume must not execute its effects again.
            await invoke_turn(graph, Command(resume=True), config, persist=False, register=False, session_id="owned")
    assert (a / "effect.txt").read_text() == "approved\n"
    assert (b / "effect.txt").read_text() == ""


@pytest.mark.asyncio
async def test_negative_control_unscoped_resume_edits_the_wrong_workspace(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        root.mkdir()
        (root / "effect.txt").write_text("", encoding="utf-8")
    config = {"configurable": {"thread_id": "negative"}}
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "negative.db")) as saver:
        graph = _graph(saver)
        async with workspace_path_scope(a):
            await invoke_turn(graph, {}, config, persist=False, register=False, session_id="owned")
        async with workspace_path_scope(b):
            # The old pass-through invoke, without checkpoint scope recovery.
            await graph.ainvoke(Command(resume=True), config)
    assert (a / "effect.txt").read_text() == ""
    assert (b / "effect.txt").read_text() == "approved\n"


@pytest.mark.asyncio
async def test_compaction_cannot_retarget_a_paused_turn(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        root.mkdir()
        (root / "effect.txt").write_text("", encoding="utf-8")
    config = {"configurable": {"thread_id": "compact"}}
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "compact.db")) as saver:
        graph = _graph(saver)
        async with workspace_path_scope(a):
            await invoke_turn(graph, {}, config, persist=False, register=False, session_id="owned")
        # /compact re-enters with checkpoint state, not a new user turn.
        async with workspace_path_scope(b):
            compact = {"needs_compaction": True, "workspace_root": str(b)}
            await invoke_turn(graph, compact, config, persist=False, register=False, session_id="owned")
            assert (await graph.aget_state(config)).values["workspace_root"] == str(a.resolve())
            await invoke_turn(graph, Command(resume=True), config, persist=False, register=False, session_id="owned")
            assert resolve_active_root() == b.resolve()
    assert (a / "effect.txt").read_text() == "approved\n"
    assert (b / "effect.txt").read_text() == ""


@pytest.mark.asyncio
async def test_concurrent_resumes_and_exception_cleanup_keep_their_own_scope(tmp_path):
    async def run(root):
        root.mkdir()

        class Graph:
            async def aget_state(self, config):
                return SimpleNamespace(values={"workspace_root": str(root.resolve())})

        async with turn_workspace(Graph(), Command(resume=True), {}) as prepared:
            await asyncio.sleep(0)
            assert resolve_active_root() == root.resolve()
            assert isinstance(prepared, Command)
        return root

    async with workspace_path_scope(tmp_path):
        await asyncio.gather(run(tmp_path / "a"), run(tmp_path / "b"))
        assert resolve_active_root() == tmp_path.resolve()
        with pytest.raises(RuntimeError, match="synthetic"):
            async with turn_workspace(None, {}, {}):
                raise RuntimeError("synthetic")
        assert resolve_active_root() == tmp_path.resolve()


@pytest.mark.asyncio
async def test_checkpoint_failure_and_invalid_saved_root_cannot_invoke(tmp_path):
    called = False

    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values={"workspace_root": "relative/not-trusted"})

        async def ainvoke(self, *args):
            nonlocal called
            called = True

    async with workspace_path_scope(tmp_path):
        with pytest.raises(ConfigError, match="workspace root is invalid") as error:
            await invoke_turn(Graph(), Command(resume=True), {}, persist=False, register=False, session_id="owned")
        assert "start a fresh turn" in sanitize_error(error.value)
        assert resolve_active_root() == tmp_path.resolve()
    assert called is False


@pytest.mark.asyncio
async def test_legacy_checkpoint_without_saved_scope_refuses_to_guess(tmp_path):
    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values={})

    async with workspace_path_scope(tmp_path):
        with pytest.raises(ConfigError, match="older checkpoint did not save its workspace"):
            async with turn_workspace(Graph(), Command(resume=True), {}):
                pytest.fail("Legacy approvals must not execute in a guessed root")
        assert resolve_active_root() == tmp_path.resolve()
