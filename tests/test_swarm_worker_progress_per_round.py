"""A swarm worker's own rounds are its task's progress (audit 2026-10-26 AUD-043).

The run supervisor judges a swarm task by the progress the engine stamps
(``runs.progress``), and the engine stamped only its events: the worker
started, the worker completed. A single worker working through many model
calls and tool rounds looked idle in between, so a busy task was judged
"stalled" 600 s after its worker started and its owner paged. The worker
now stamps each model reply and each finished tool round.

The real worker (``InProcessWorker.dispatch``) with a scripted model whose
every call takes five minutes of a fake clock, a read tool of the test's own,
and the real swarm run adapter and verdict.

Negative control: the same dispatch with the worker's stamps switched off
(the old worker) leaves the task judged stalled at the same moment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.llm_provider import LLMResponse, ToolCall
from kazma_core.runs import progress
from kazma_core.runs.adapters.swarm import ADAPTER
from kazma_core.runs.contract import Verdict, judge
from kazma_core.runs.kinds import KINDS
from kazma_core.swarm import worker as worker_mod
from kazma_core.swarm.blackboard import SwarmDispatchContext
from kazma_core.swarm.task import SwarmTask, TaskStatus, TaskType
from kazma_core.swarm.worker import InProcessWorker

T0 = 1_800_000_000.0
CALL_S = 300.0  # every model call takes five minutes
ROUNDS = 4


@pytest.fixture
def world(monkeypatch):
    from kazma_core.agent.tool_registry import LocalToolRegistry

    clock = {"t": T0}
    monkeypatch.setattr(progress, "time", SimpleNamespace(time=lambda: clock["t"]))
    progress.reset()
    registry = LocalToolRegistry(include_builtins=False)

    @registry.register(name="file_read")
    async def file_read(path: str) -> str:
        return f"contents of {path}"

    monkeypatch.setattr("kazma_core.agent.tool_registry.get_tool_registry", lambda: registry)
    task = SwarmTask(type=TaskType.DISPATCH, prompt="audit every module", workers=["w"],
                     status=TaskStatus.RUNNING,
                     started_at=datetime.fromtimestamp(T0, UTC).isoformat())
    verdicts: list[Verdict] = []

    class _Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, tools=None, model=None, **_kw):
            self.calls += 1
            clock["t"] += CALL_S
            # Asked while the model thinks: what does the supervisor make of the task?
            run = ADAPTER._run(task, owned=True, engine_up=True)
            verdicts.append(judge(run, KINDS["swarm_task"], clock["t"]))
            if self.calls <= ROUNDS:
                return LLMResponse(tool_calls=[ToolCall(id=f"c{self.calls}", name="file_read",
                                                        arguments={"path": f"module_{self.calls}.py"})],
                                   finish_reason="tool_calls", usage={"prompt_tokens": 1, "completion_tokens": 1})
            return LLMResponse(content="All modules audited.", finish_reason="stop",
                               usage={"prompt_tokens": 1, "completion_tokens": 1})

    provider = _Provider()

    class _Registry:
        def get_client_by_provider(self, name, model=None):
            return provider

    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: _Registry())
    monkeypatch.setattr(worker_mod, "worker_iteration_budget", lambda thread_id=None: 20)
    yield SimpleNamespace(task=task, verdicts=verdicts, clock=clock)
    progress.reset()


async def _dispatch(world) -> dict[str, Any]:
    # The engine's own event as the worker starts (worker_dispatch).
    progress.note(f"swarm_task:{world.task.id}", "worker_started")
    worker = InProcessWorker(name="w", provider="script:w")
    return await worker.dispatch("Audit every module.", context=SwarmDispatchContext(task_id=world.task.id))


async def test_a_busy_worker_is_never_judged_stalled(world):
    result = await _dispatch(world)
    assert result["status"] == "success" and result["output"] == "All modules audited."
    assert world.clock["t"] - T0 == CALL_S * (ROUNDS + 1)  # 25 minutes of work
    assert Verdict.STALLED not in world.verdicts, world.verdicts
    stamp = progress.last(f"swarm_task:{world.task.id}")
    assert stamp is not None and stamp[1] == "worker_model_reply"


async def test_a_dispatch_outside_the_engine_stamps_nothing(world):
    worker = InProcessWorker(name="w", provider="script:w")
    await worker.dispatch("Audit every module.")
    assert progress.last(f"swarm_task:{world.task.id}") is None


async def test_negative_control_the_old_worker_is_judged_stalled(world, monkeypatch):
    monkeypatch.setattr(worker_mod, "_note_progress", lambda context, what: None)  # the worker as it was
    await _dispatch(world)
    assert Verdict.STALLED in world.verdicts, "only the engine's events: stalled at 600 s while working"
