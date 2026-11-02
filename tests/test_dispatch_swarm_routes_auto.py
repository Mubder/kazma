"""The agent's ``dispatch_swarm`` lets the swarm route "auto" (FA-113).

The 2026-11 functional audit: the tool registered a permanent ``researcher``
worker when none was registered and replaced "auto" with the alphabetically
first registered worker -- with ``analyst`` and ``coder`` registered, a coding
task went to ``analyst``. The Swarm page and ``/swarm`` pass ``workers=["auto"]``
to the engine, whose router picks the best worker or the autoscaler spawns one
from a template (AGENTS §14); the agent's main tool never did.

Through the real tool and the real engine and autoscaler: with no worker
registered, a coding task runs on an instance of the ``coder`` template and
no permanent worker is added; with ``analyst`` and ``coder`` registered, it
runs on ``coder``. The old resolution -- the first worker by name -- is the
negative control.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest import mock

import pytest
from kazma_core.agent.tool_builtins import system
from kazma_core.swarm.autoscaler import AutoScaler, WorkerTemplate
from kazma_core.swarm.config import SwarmConfig, WorkerCapabilities, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.worker import InProcessWorker

_CODING = "write a python function that parses dates, then debug it"


@pytest.fixture(autouse=True)
def _inert(monkeypatch):
    import kazma_core.swarm as swarm
    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.semantic_router as sr
    import kazma_core.swarm.single_operator as single
    import kazma_core.swarm.worker_dispatch as wd

    async def _nothing(*_a, **_k):
        return None

    monkeypatch.setattr(patterns, "_run_self_improvement", _nothing)
    monkeypatch.setattr(wd, "_index_worker_l4_memory", _nothing)
    monkeypatch.setattr(sr, "_kazma_embedder", lambda: None)  # keyword routing
    monkeypatch.setattr(single, "swarm_refusal", lambda: "")
    monkeypatch.setattr(swarm, "get_swarm_engine", lambda: _ENGINE[0])
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")


_ENGINE: list[Any] = [None]


class _Registry:
    def __init__(self) -> None:
        self.tools: dict = {}

    def register(self, *args, **kwargs):
        def wrap(fn):
            self.tools[fn.__name__] = fn
            return fn
        return wrap


def _engine(tmp_path, registered: tuple[str, ...]) -> SwarmEngine:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    scaler = AutoScaler(engine, templates_path=tmp_path / "templates.json")
    scaler.register_template(WorkerTemplate.from_dict(
        {"name": "coder", "capabilities": {"expertise": ["python", "debug", "code"]}, "max_instances": 2}))
    scaler.register_template(WorkerTemplate.from_dict(
        {"name": "generalist", "capabilities": {"expertise": ["explain"]}, "catch_all": True}))
    engine._autoscaler = scaler
    expertise = {"analyst": ["analysis", "data", "statistics"], "coder": ["python", "debug", "code"]}
    for name in registered:
        engine.add_worker(WorkerConfig(name=name, type="in_process",
                                       capabilities=WorkerCapabilities(role=name, expertise=expertise[name])))
    _ENGINE[0] = engine
    return engine


async def _dispatch_through_the_tool(engine: SwarmEngine) -> tuple[str, list[str], str]:
    """The real tool, the real engine; returns (the tool's answer, who ran, the task's workers)."""
    ran: list[str] = []

    async def dispatch(self, task, context=None):
        ran.append(self.name)
        return {"worker": self.name, "task_id": "", "status": "success", "output": "def parse(): ...",
                "error": None}

    reg = _Registry()
    system.register_system_tools(reg)
    with mock.patch.object(InProcessWorker, "dispatch", dispatch):
        answer = await reg.tools["dispatch_swarm"](_CODING)
        for _ in range(400):
            if not system._pending_dispatch_tasks:
                break
            await asyncio.sleep(0.01)
    task_id = answer.split("(id: ", 1)[1].split(")", 1)[0]
    stored = engine.get_task(task_id)
    return answer, ran, list(stored.workers) if stored else []


async def test_with_nothing_registered_a_template_runs_it_and_nothing_is_registered(tmp_path):
    engine = _engine(tmp_path, ())
    answer, ran, workers = await _dispatch_through_the_tool(engine)
    assert len(ran) == 1 and ran[0].startswith("coder-pool-"), ran
    assert workers == ran
    assert "researcher" not in engine.worker_names
    assert all(name.startswith("coder-pool-") for name in engine.worker_names), engine.worker_names
    assert "best suited" in answer and "researcher" not in answer


async def test_with_two_registered_the_router_picks_the_coder(tmp_path):
    engine = _engine(tmp_path, ("analyst", "coder"))
    before = sorted(engine.worker_names)
    _answer, ran, workers = await _dispatch_through_the_tool(engine)
    assert ran == ["coder"] and workers == ["coder"]
    assert sorted(engine.worker_names) == before, "no worker was added or removed"


async def test_a_named_worker_is_still_honoured(tmp_path):
    _engine(tmp_path, ("analyst", "coder"))
    ran: list[str] = []

    async def dispatch(self, task, context=None):
        ran.append(self.name)
        return {"worker": self.name, "task_id": "", "status": "success", "output": "ok", "error": None}

    reg = _Registry()
    system.register_system_tools(reg)
    with mock.patch.object(InProcessWorker, "dispatch", dispatch):
        answer = await reg.tools["dispatch_swarm"](_CODING, worker="analyst")
        for _ in range(400):
            if not system._pending_dispatch_tasks:
                break
            await asyncio.sleep(0.01)
    assert ran == ["analyst"] and "worker 'analyst'" in answer


def test_the_old_resolution_sent_the_coding_task_to_the_analyst(tmp_path):
    """Negative control: the tool before FA-113 replaced "auto" with
    ``engine.worker_names[0]`` -- sorted by name -- so the routed answer
    (``coder``) and the old one differ on the same engine."""
    engine = _engine(tmp_path, ("analyst", "coder"))
    old_choice = engine.worker_names[0] if engine.worker_names else "researcher"
    assert old_choice == "analyst"
    empty = _engine(tmp_path, ())
    assert (empty.worker_names[0] if empty.worker_names else "researcher") == "researcher"
