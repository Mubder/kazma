"""Asked for a swarm task's result, the tool waits for it.

Live 2026-10-20 (Telegram), test 25: the researcher finished in about 36 s,
but ``check_swarm_task`` answered "still running" at once and its description
said "poll every few seconds": the model polled three times in two seconds,
the stagnation guard ended the turn 3.8 s in, and the answer was "partial --
ask me again". The tool now waits (``wait_seconds``, default 60, at most 90, below the 120 s tool timeout)
and answers the moment the task finishes.
"""

from __future__ import annotations

import asyncio
import time

from kazma_core.swarm.config import SwarmConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import SwarmTask, TaskResult, TaskStatus
from kazma_core.swarm.task_store import TaskStore


def _setup(monkeypatch, tmp_path):
    import kazma_core.swarm.engine as engine_mod
    from kazma_core.agent.tool_builtins.system import register_system_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    store = TaskStore(str(tmp_path / "swarm_tasks.db"))
    monkeypatch.setattr(engine_mod, "_swarm_engine", SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store))
    task = SwarmTask(prompt="two facts about Kuwait Towers", workers=["researcher"])
    task.status = TaskStatus.RUNNING
    store.persist_task(task)
    reg = LocalToolRegistry(include_builtins=False)
    register_system_tools(reg)
    return store, task, reg


def _finish_later(store, task, after: float):
    async def finish():
        await asyncio.sleep(after)
        task.status = TaskStatus.COMPLETED
        task.result = TaskResult(task_id=task.id, status="success",
                                 aggregated_output="Kuwait Towers opened in 1979.")
        await asyncio.to_thread(store.persist_task, task)
    return finish()


def _check(reg, store, task, **args):
    async def run():
        finisher = asyncio.create_task(_finish_later(store, task, 1.5))
        started = time.monotonic()
        out = await reg.execute("check_swarm_task", {"task_id": task.id, **args})
        await finisher
        return str(out.get("content", "")), time.monotonic() - started
    return asyncio.run(run())


def test_the_tool_waits_for_the_result(monkeypatch, tmp_path):
    store, task, reg = _setup(monkeypatch, tmp_path)
    try:
        out, took = _check(reg, store, task, wait_seconds=10)
        assert "completed" in out and "1979" in out, out
        assert took < 8, "it answers as soon as the task finishes, not at the end of the wait"
    finally:
        store.close()


def test_negative_control_no_wait_answers_still_running(monkeypatch, tmp_path):
    store, task, reg = _setup(monkeypatch, tmp_path)
    try:
        out, _ = _check(reg, store, task, wait_seconds=0)
        assert "still running" in out and "again with the same id" in out, out
    finally:
        store.close()


def test_the_default_waits():
    import inspect

    from kazma_core.agent.tool_builtins import system

    src = inspect.getsource(system)
    assert "async def check_swarm_task(task_id: str, wait_seconds: int = 60)" in src
    assert "min(float(wait_seconds or 0), 90.0)" in src
