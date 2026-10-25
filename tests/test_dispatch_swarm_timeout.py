"""The agent's ``dispatch_swarm`` takes the time limit the user asks for.

Asked how to see a run-supervision card with buttons on Telegram (2026-10-25),
there was no way to make a task started from a chat fail on purpose: the tool
gave every task 300 s, and ``/swarm`` takes no limit. The tool now passes the
model's ``timeout_seconds``, kept within 5..1800.
"""

from __future__ import annotations

import asyncio

import pytest
from kazma_core.agent.tool_builtins import system


class _Registry:
    def __init__(self) -> None:
        self.tools: dict = {}

    def register(self, *args, **kwargs):
        def wrap(fn):
            self.tools[fn.__name__] = fn
            return fn
        return wrap


class _Engine:
    worker_names = ["probe"]

    def __init__(self) -> None:
        self.tasks: list = []

    async def dispatch(self, task):
        self.tasks.append(task)

    def register_task_handle(self, *_):
        pass

    def unregister_task_handle(self, *_):
        pass


@pytest.fixture
def dispatched(monkeypatch):
    import kazma_core.swarm as swarm
    import kazma_core.swarm.single_operator as single

    engine = _Engine()
    monkeypatch.setattr(swarm, "get_swarm_engine", lambda: engine)
    monkeypatch.setattr(single, "swarm_refusal", lambda: "")
    reg = _Registry()
    system.register_system_tools(reg)

    def run(**kwargs) -> float:
        async def go():
            await reg.tools["dispatch_swarm"]("check kazma.ai", worker="probe", **kwargs)
            await asyncio.sleep(0)
        asyncio.run(go())
        return engine.tasks[-1].timeout
    return run


@pytest.mark.parametrize(("asked", "runs_under"), [
    ({}, 300.0), ({"timeout_seconds": 5}, 5.0), ({"timeout_seconds": 0.5}, 5.0),
    ({"timeout_seconds": 99999}, 1800.0), ({"timeout_seconds": "ten"}, 300.0),
    ({"timeout_seconds": float("nan")}, 300.0),
])
def test_a_dispatched_task_runs_under_the_limit_asked(dispatched, asked, runs_under):
    assert dispatched(**asked) == runs_under


def test_the_old_tool_ignored_the_limit(dispatched, monkeypatch):
    """Negative control: the fixed 300 s the tool gave every task."""
    monkeypatch.setattr(system, "_dispatch_timeout", lambda _value: 300.0)
    assert dispatched(timeout_seconds=5) == 300.0
