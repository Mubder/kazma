"""An idle autoscaler instance takes the next task before a new one is spawned.

Found by the SQ01 swarm soak (2026-10-15): an ``auto`` task whose prompt
matches no template's words goes to the catch-all template (the shipped
``generalist``). The router matches instances by their template's words, so
it never routed such a task to an existing catch-all instance: each one
spawned a NEW instance up to the template's bound, then failed "No capable
workers" until the idle ones were reaped. A pipeline step naming a template
did the same (``spawn_by_name``).

Now :meth:`AutoScaler.maybe_scale` and :meth:`AutoScaler.spawn_by_name` hand
out an idle instance of the template first (no dispatch in flight, no task
already claimed for it), within the bound and the idle reap (AGENTS §14).
Each test runs the real engine and autoscaler; the old rule -- never reuse,
``_idle_instance`` answering None -- is the negative control.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest import mock

import pytest
from kazma_core.swarm.autoscaler import AutoScaler, WorkerTemplate
from kazma_core.swarm.config import SwarmConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import SwarmTask, TaskType
from kazma_core.swarm.worker import InProcessWorker


@pytest.fixture(autouse=True)
def _inert(monkeypatch):
    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.semantic_router as sr
    import kazma_core.swarm.worker_dispatch as wd

    async def _nothing(*_a, **_k):
        return None

    async def _refined(*_a, **_k):
        return "refined"

    monkeypatch.setattr(patterns, "_run_self_improvement", _nothing)
    monkeypatch.setattr(patterns, "_synthesize_refined_output", _refined)
    monkeypatch.setattr(wd, "_index_worker_l4_memory", _nothing)
    monkeypatch.setattr(sr, "_kazma_embedder", lambda: None)  # keyword routing
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")


class _Instances:
    """Every pool instance's dispatch: who ran, and a gate to hold them."""

    def __init__(self) -> None:
        self.ran: list[str] = []
        self.hold: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def __call__(self, worker: Any, task: str, context: Any = None) -> dict:
        self.ran.append(worker.name)
        self.started.set()
        if self.hold is not None:
            await self.hold.wait()
        return {"worker": worker.name, "task_id": "", "status": "success", "output": "ok", "error": None}


def _setup(tmp_path, bound: int = 3) -> tuple[SwarmEngine, AutoScaler, _Instances, list[str]]:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    scaler = AutoScaler(engine, templates_path=tmp_path / "templates.json")
    scaler.register_template(WorkerTemplate.from_dict(
        {"name": "coder", "capabilities": {"expertise": ["python", "debug"]}, "max_instances": 2}))
    scaler.register_template(WorkerTemplate.from_dict(
        {"name": "generalist", "capabilities": {"expertise": ["explain"]}, "catch_all": True,
         "max_instances": bound}))
    engine._autoscaler = scaler
    spawned: list[str] = []
    real = scaler._instantiate

    def counting(template):
        worker = real(template)
        if worker is not None:
            spawned.append(worker.name)
        return worker

    scaler._instantiate = counting
    return engine, scaler, _Instances(), spawned


def _never_reuse(scaler: AutoScaler) -> None:
    """The rule before the fix: an instance is never handed out again."""
    scaler._idle_instance = lambda template_name: None


_UNMATCHED = "what will the weather be tomorrow"


async def _auto(engine: SwarmEngine, prompt: str = _UNMATCHED, **kw) -> Any:
    return await engine.dispatch(SwarmTask(prompt=prompt, workers=["auto"], timeout=0, **kw))


@pytest.mark.parametrize("old_rule", [False, True], ids=["reuse", "old-rule"])
async def test_unmatched_tasks_in_sequence_use_one_catch_all_instance(tmp_path, old_rule) -> None:
    engine, scaler, instances, spawned = _setup(tmp_path, bound=3)
    if old_rule:
        _never_reuse(scaler)

    def dispatch(self, task, context=None):
        return instances(self, task, context)

    with mock.patch.object(InProcessWorker, "dispatch", dispatch):
        results = [await _auto(engine) for _ in range(6)]
    statuses = [r.status for r in results]
    if old_rule:
        # Negative control: a new instance each time, then failures at the bound.
        assert len(spawned) == 3 and statuses.count("failed") == 3, (spawned, statuses)
        assert all("No capable workers" in (r.error or "") for r in results if r.status == "failed")
        return
    assert statuses == ["success"] * 6
    assert len(spawned) == 1 and instances.ran == spawned * 6, (spawned, instances.ran)
    stored = [engine.get_task(r.task_id) for r in results]
    assert [t.workers for t in stored] == [spawned] * 6
    reused = [t.metadata["routing_diagnostics"]["autoscaled"]["reused"] for t in stored]
    assert reused == [False] + [True] * 5


async def test_concurrent_unmatched_tasks_spawn_up_to_the_bound_then_reuse(tmp_path) -> None:
    """Three at once take three instances (busy ones are never handed out); a
    fourth at the same time finds the bound reached. Once they finish, the
    next tasks reuse them: no new instance."""
    engine, scaler, instances, spawned = _setup(tmp_path, bound=3)
    instances.hold = asyncio.Event()

    def dispatch(self, task, context=None):
        return instances(self, task, context)

    with mock.patch.object(InProcessWorker, "dispatch", dispatch):
        running = [asyncio.create_task(_auto(engine)) for _ in range(3)]
        for _ in range(200):
            if len(instances.ran) == 3:
                break
            await asyncio.sleep(0.01)
        assert sorted(instances.ran) == sorted(spawned) and len(set(spawned)) == 3
        fourth = await _auto(engine)
        assert fourth.status == "failed" and "No capable workers" in (fourth.error or "")
        instances.hold.set()
        assert [r.status for r in await asyncio.gather(*running)] == ["success"] * 3
        instances.hold = None
        later = [await _auto(engine) for _ in range(4)]
    assert [r.status for r in later] == ["success"] * 4
    assert len(spawned) == 3, spawned  # nothing new: the idle ones took them


def test_an_instance_handed_out_is_not_handed_out_again_before_its_dispatch(tmp_path) -> None:
    """Between the hand-out and the dispatch's start its in-flight count is
    still zero: the claim keeps a second task from getting it then."""
    _engine, scaler, _instances, spawned = _setup(tmp_path, bound=3)
    first = scaler.maybe_scale(_UNMATCHED)
    second = scaler.maybe_scale(_UNMATCHED)
    assert first is not None and second is not None and first.name != second.name
    scaler.record_activity(first.name)  # its dispatch began ... and ended
    third = scaler.maybe_scale(_UNMATCHED)
    assert third is first and len(spawned) == 2


def test_a_claim_whose_dispatch_never_began_ends(tmp_path, monkeypatch) -> None:
    """Refused before it ran (an open breaker): the instance is free again
    once the claim's time is up, and it is not reaped while claimed."""
    from types import SimpleNamespace

    import kazma_core.swarm.autoscaler as mod

    _engine, scaler, _instances, spawned = _setup(tmp_path, bound=1)
    now = [1000.0]
    monkeypatch.setattr(mod, "time", SimpleNamespace(monotonic=lambda: now[0], time=lambda: now[0]))
    first = scaler.maybe_scale(_UNMATCHED)
    assert scaler.maybe_scale(_UNMATCHED) is None  # claimed, and the bound is 1
    scaler._idle_ttl = 1.0
    now[0] += 30.0  # idle past the TTL, still claimed
    assert scaler.reap_idle() == 0
    scaler._idle_ttl = 3600.0
    now[0] += mod._CLAIM_S
    assert scaler.maybe_scale(_UNMATCHED) is first and spawned == [first.name]


async def test_a_pipeline_step_naming_a_template_reuses_its_instance(tmp_path) -> None:
    """``spawn_by_name`` is the same rule: two steps naming one template run
    on one instance when the first is done before the second asks."""
    engine, scaler, instances, spawned = _setup(tmp_path)

    def dispatch(self, task, context=None):
        return instances(self, task, context)

    with mock.patch.object(InProcessWorker, "dispatch", dispatch):
        result = await engine.dispatch(SwarmTask(prompt="p", workers=["coder", "coder"],
                                                 type=TaskType.PIPELINE, timeout=0))
    assert result.status == "success", result.error
    assert len(spawned) == 1 and instances.ran == spawned * 2


@pytest.mark.parametrize("claim_ends", [True, False], ids=["refusal-ends-claim", "claim-lingers"])
async def test_an_open_breaker_is_not_dodged_by_spawning(tmp_path, claim_ends) -> None:
    """The catch-all instance's breaker is open (its model fails): the next
    tasks get that instance and its refusal, not a new instance whose new
    breaker would send them to the failing model again. A refused dispatch
    never begins, so the refusal ends its claim. Control: a claim that
    lingers makes the next task spawn a second instance."""
    engine, scaler, instances, spawned = _setup(tmp_path, bound=3)

    def dispatch(self, task, context=None):
        return instances(self, task, context)

    with mock.patch.object(InProcessWorker, "dispatch", dispatch):
        first = await _auto(engine)
        assert first.status == "success" and len(spawned) == 1
        breaker = engine.set_circuit_breaker_config(spawned[0], failure_threshold=1, cooldown_seconds=3600)
        breaker.record_failure()
        if not claim_ends:
            scaler.record_activity = lambda name: None
        refused = [await _auto(engine) for _ in range(2)]
    assert all("Circuit breaker is open" in (r.error or "") for r in refused[:1])
    if claim_ends:
        assert len(spawned) == 1 and all("Circuit breaker is open" in (r.error or "") for r in refused)
    else:
        assert len(spawned) == 2, spawned


def test_reuse_keeps_the_template_order(tmp_path) -> None:
    """A matching template's idle instance comes before the catch-all's."""
    _engine, scaler, _instances, spawned = _setup(tmp_path)
    general = scaler.maybe_scale(_UNMATCHED)
    coder = scaler.maybe_scale("debug this python function")
    for w in (general, coder):
        scaler.record_activity(w.name)
    assert scaler.maybe_scale("debug this python function") is coder
    assert scaler.maybe_scale(_UNMATCHED) is general
    assert len(spawned) == 2
