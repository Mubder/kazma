"""Swarm handoff is a product feature (Swarm audit SW-E13, owner decision D6).

The shipped LLM-backed worker (``InProcessWorker``) hands its task to another
worker through ``handoff_to_worker``: the worker raises ``HandoffRequest``,
``dispatch_worker`` catches it and ``SwarmEngine._handle_handoff`` runs the
target with the cycle guard (depth 5, ``MAX_VISITS=2``) as it always applied
it. Every test drives the real engine and the real worker with a scripted
model: no model, no network.

* The witness inverted: a shipped worker builds and raises the request, and
  ``dispatch()`` no longer turns it into an error.
* End to end: the tool offered (naming the other workers only), the target
  run with the task and the source's findings (fenced), the handoff recorded
  on the stored task with its reason, the event on the task's stream, the
  source's spend counted.
* The cycle guard intact: A -> B -> A runs; a ping-pong's next hop is refused
  where the engine's guard refuses it, and the worker finishes the task
  itself; with the guard removed (negative control) the ping-pong goes on.
* Pool instances count as their template (instance names as the control).
* The target's danger tool asks the swarm bus; the handoff asks nothing.
* A worker scope without the ``delegate`` act refuses the handoff (a scope
  with it as the control).
* Every ending of a handoff releases the source's in-flight count (a release
  that does nothing as the control: the guard's ending leaked one).
* A handoff keeps the dispatch's scope, task id and workspace without a
  blackboard (the old helper as the control).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from kazma_core.llm_provider import LLMResponse, ToolCall
from kazma_core.swarm.autoscaler import AutoScaler, WorkerTemplate
from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.handoff import (
    HANDOFF_TOOL_NAME,
    HandoffOffer,
    HandoffRequest,
    HandoffTarget,
    handoff_offer,
)
from kazma_core.swarm.task import SwarmTask, TaskType
from kazma_core.swarm.task_store import TaskStore
from kazma_core.swarm.worker import InProcessWorker, SwarmWorker

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _inert(monkeypatch):
    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.worker_dispatch as wd

    async def _no_refiner(task, worker_results, status, total_ms):
        return "refined-stub"

    async def _no_self_improvement(task, worker_results, status):
        return None

    async def _no_memory_index(**_kw):
        return None

    monkeypatch.setattr(patterns, "_synthesize_refined_output", _no_refiner)
    monkeypatch.setattr(patterns, "_run_self_improvement", _no_self_improvement)
    monkeypatch.setattr(wd, "_index_worker_l4_memory", _no_memory_index)
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    monkeypatch.delenv("KAZMA_TEMPORAL_REQUIRED", raising=False)
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "1")
    monkeypatch.setenv("KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE", "1")
    monkeypatch.delenv("KAZMA_SWARM_MAX_ACTIVE", raising=False)


@pytest.fixture
def tools(monkeypatch):
    """A registry of the test's own tools, and a bus that denies everything."""
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry(include_builtins=False)
    monkeypatch.setattr("kazma_core.agent.tool_registry.get_tool_registry", lambda: registry)
    safety = Mock(enabled=True)
    safety.is_danger_tool.side_effect = lambda name: name == "file_write"
    safety.check = AsyncMock(return_value=False)
    monkeypatch.setattr("kazma_core.swarm.safety.get_safety", lambda: safety)
    return registry, safety


# ── The scripted model ──────────────────────────────────────────────────────


def _usage(prompt: int = 10, completion: int = 5) -> dict[str, int]:
    return {"prompt_tokens": prompt, "completion_tokens": completion}


def _final(text: str) -> LLMResponse:
    return LLMResponse(content=text, finish_reason="stop", usage=_usage())


def _call(name: str, arguments: dict[str, Any], call_id: str = "c1",
          usage: dict[str, int] | None = None) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
                       finish_reason="tool_calls", usage=usage or _usage())


def _handoff(to: str, *, reason: str = "That worker fits the rest of the task.",
             context: str = "", usage: dict[str, int] | None = None) -> LLMResponse:
    return _call(HANDOFF_TOOL_NAME, {"worker": to, "reason": reason, "context": context},
                 call_id="handoff-1", usage=usage)


def _tool_answers(messages: list[dict[str, Any]]) -> list[str]:
    return [str(m.get("content") or "") for m in messages if m.get("role") == "tool"]


def _new_run(messages: list[dict[str, Any]]) -> bool:
    return not any(m.get("role") in ("assistant", "tool") for m in messages)


class _Script:
    """One decision function for every worker; every model call recorded.

    Each worker names its provider ``script:<who>`` (a template's instances
    carry the template's), so the model knows who is asking."""

    def __init__(self, decide) -> None:
        self.decide = decide
        self.calls: list[dict[str, Any]] = []
        self.runs: dict[str, int] = {}

    def registry(self) -> Any:
        script = self

        class _Provider:
            def __init__(self, who: str) -> None:
                self.who = who

            async def chat(self, messages, tools=None, model=None, **_kw):
                if _new_run(messages):
                    script.runs[self.who] = script.runs.get(self.who, 0) + 1
                script.calls.append({
                    "who": self.who,
                    "tools": list(tools or []),
                    "names": [t["function"]["name"] for t in (tools or [])],
                    "messages": list(messages),
                })
                return script.decide(self.who, messages, script)

        class _Registry:
            def get_client_by_provider(self, provider, model=None):
                return _Provider(provider.split(":", 1)[1])

            def get_client(self, model=None):
                raise AssertionError("every scripted worker names its provider")

        return _Registry()


def _engine(monkeypatch, tmp_path: Path, script: _Script, *names: str,
            store: TaskStore | None = None,
            templates: tuple[WorkerTemplate, ...] = ()) -> SwarmEngine:
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", script.registry)
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
    scaler = AutoScaler(engine, templates_path=tmp_path / "templates.json")
    for template in templates:
        scaler.register_template(template)
    engine._autoscaler = scaler
    for name in names:
        engine.add_worker(WorkerConfig(name=name, type="in_process", provider=f"script:{name}",
                                       role=f"the {name}"))
    return engine


class _Events:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str, dict]] = []

    def emit(self, task_id: str, event: str, data: dict) -> None:
        self.seen.append((task_id, event, data))


def _ping_pong(limit: int | None = None):
    """alpha and beta hand the task to each other until a hop is refused (or,
    with *limit*, until their own run *limit*); then they finish it."""
    def decide(who: str, messages, script: _Script) -> LLMResponse:
        if any(a.startswith("Handoff refused") for a in _tool_answers(messages)):
            return _final(f"{who} finished")
        if limit is not None and script.runs[who] >= limit:
            return _final(f"{who} finished")
        return _handoff("beta" if who == "alpha" else "alpha")
    return decide


# ── The witness inverted ────────────────────────────────────────────────────


def test_a_shipped_worker_requests_a_handoff():
    """The SW-E13 witness inverted: product code outside handoff.py builds the
    request (the worker), and the worker lets it out of dispatch()."""
    producers = []
    for pkg in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway"):
        for path in (REPO / pkg).rglob("*.py"):
            if path.name == "handoff.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "HandoffRequest":
                    producers.append(path.relative_to(REPO).as_posix())
    assert "kazma-core/kazma_core/swarm/worker.py" in producers, producers
    # dispatch()'s outer handler lets the request out before its catch-all.
    tree = ast.parse(textwrap.dedent(inspect.getsource(InProcessWorker.dispatch)))
    handlers = next(
        [ast.unparse(h.type) if h.type is not None else "" for h in node.handlers]
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(h.type is not None and ast.unparse(h.type) == "HandoffRequest" for h in node.handlers)
    )
    assert handlers.index("HandoffRequest") < handlers.index("Exception"), handlers


async def test_the_worker_raises_the_request_only_with_an_offer(monkeypatch, tools):
    """With an offer bound, the model's call ends the dispatch with the
    request. Negative control: with none (a dispatch outside the engine) the
    tool is not offered, the registry answers "not found" and the worker
    returns."""
    script = _Script(lambda who, messages, s: (
        _final("done") if _tool_answers(messages) else _handoff("coder", context="half done")))
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", script.registry)
    worker = InProcessWorker(name="planner", provider="script:planner")
    offer = HandoffOffer(source="planner", targets=lambda: (HandoffTarget("coder", "writes code"),),
                         refusal=lambda target: None)
    with handoff_offer(offer), pytest.raises(HandoffRequest) as raised:
        await worker.dispatch("Fix the bug.")
    assert raised.value.target_worker == "coder"
    assert raised.value.task == "Fix the bug." and raised.value.context == "half done"
    assert raised.value.tokens_used == 15
    assert HANDOFF_TOOL_NAME in script.calls[0]["names"]

    script.calls.clear()
    result = await worker.dispatch("Fix the bug.")
    assert result["status"] == "success" and result["output"] == "done"
    assert HANDOFF_TOOL_NAME not in script.calls[0]["names"]
    assert "not found" in _tool_answers(script.calls[1]["messages"])[0]


# ── End to end ──────────────────────────────────────────────────────────────


async def test_an_llm_worker_hands_its_task_on_through_the_tool(monkeypatch, tmp_path, tools):
    def decide(who: str, messages, script: _Script) -> LLMResponse:
        if who == "planner":
            assert _new_run(messages), "the planner stopped when it handed off"
            return _handoff("coder", reason="It needs a code change.",
                            context="The bug is in parse().", usage=_usage(100, 20))
        return _final("Fixed parse().")

    script = _Script(decide)
    store = TaskStore(db_path=str(tmp_path / "tasks.db"))
    engine = _engine(monkeypatch, tmp_path, script, "planner", "coder", store=store)
    events = _Events()
    engine.set_sse_bus(events)
    task = SwarmTask(prompt="Fix the failing import.", workers=["planner"], timeout=60)

    result = await engine.dispatch(task)

    assert result.status == "success", result.error
    assert result.aggregated_output == "Fixed parse()."
    assert [w.worker for w in result.worker_results] == ["planner", "coder"]
    # Offered to the planner, naming the other worker only.
    offered = next(t for t in script.calls[0]["tools"]
                   if t["function"]["name"] == HANDOFF_TOOL_NAME)
    assert offered["function"]["parameters"]["properties"]["worker"]["enum"] == ["coder"]
    # The coder ran the task with the planner's findings, fenced.
    coder = next(c for c in script.calls if c["who"] == "coder")
    asked = coder["messages"][-1]["content"]
    assert asked.startswith("Fix the failing import.")
    assert "The bug is in parse()." in asked and 'source="swarm_handoff"' in asked
    # Recorded on the task, stored with its reason.
    record = result.worker_results[0].handoffs[0]
    assert (record.from_worker, record.to_worker, record.reason) == (
        "planner", "coder", "It needs a code change.")
    stored = store.get_task(task.id)
    assert stored.result.worker_results[0].handoffs[0].reason == "It needs a code change."
    # The planner's spend before it handed off is the task's too.
    assert result.worker_results[0].tokens_used == 120
    assert result.total_tokens == 120 + 15
    # The Swarm page's chain, and the coder's own events, on the task's stream.
    assert (task.id, "handoff", {"from": "planner", "to": "coder",
                                 "reason": "It needs a code change."}) in events.seen
    assert (task.id, "worker_started", {"worker": "coder", "step": 0}) in events.seen
    assert all(w.in_flight == 0 for w in engine.list_workers())


# ── The cycle guard, intact ─────────────────────────────────────────────────


async def test_a_return_handoff_runs_and_a_ping_pong_stops_at_the_guard(
        monkeypatch, tmp_path, tools):
    """alpha -> beta -> alpha (the return) runs, and so does the next hop to
    beta (each worker runs at most MAX_VISITS=2 times); beta's hop back to
    alpha would be alpha's third run, and the guard refuses it. The model is
    told, and beta finishes the task itself."""
    script = _Script(_ping_pong())
    engine = _engine(monkeypatch, tmp_path, script, "alpha", "beta")

    result = await engine.dispatch(SwarmTask(prompt="p", workers=["alpha"], timeout=60))

    assert [w.worker for w in result.worker_results] == ["alpha", "beta", "alpha", "beta"]
    refusals = [a for c in script.calls for a in _tool_answers(c["messages"])]
    assert len(set(refusals)) == 1
    assert "Handoff cycle detected: beta -> alpha" in refusals[0]
    assert result.status == "success" and result.aggregated_output == "beta finished"
    assert script.runs == {"alpha": 2, "beta": 2}
    assert all(w.in_flight == 0 for w in engine.list_workers())


async def test_without_the_guard_the_ping_pong_goes_on(monkeypatch, tmp_path, tools):
    """Negative control: the guard removed, the same model keeps handing the
    task back and forth until its own limit (four runs each here)."""
    import kazma_core.swarm.engine as engine_module

    monkeypatch.setattr(engine_module, "_handoff_guard_error", lambda **_kw: None)
    script = _Script(_ping_pong(limit=4))
    engine = _engine(monkeypatch, tmp_path, script, "alpha", "beta")

    result = await engine.dispatch(SwarmTask(prompt="p", workers=["alpha"], timeout=60))

    assert len(result.worker_results) == 7, [w.worker for w in result.worker_results]
    assert script.runs == {"alpha": 4, "beta": 3}


async def test_the_tool_refuses_the_hop_the_engine_guard_refuses(monkeypatch, tmp_path):
    """The tool asks the engine's guard; the engine applies it again. A
    worker that raises the request itself (no tool, no asking first) is
    refused at the same hop, by ``_handle_handoff``."""

    class Raising(SwarmWorker):
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def dispatch(self, task, context=""):
            raise HandoffRequest("beta" if self.name == "alpha" else "alpha", task)

    engine = _engine(monkeypatch, tmp_path, _Script(_ping_pong()))
    for name in ("alpha", "beta"):
        engine._workers[name] = Raising(name)

    result = await engine.dispatch(SwarmTask(prompt="p", workers=["alpha"], timeout=60))

    assert [w.worker for w in result.worker_results] == ["alpha", "beta", "alpha", "beta"]
    assert result.status == "failed"
    assert "Handoff cycle detected: beta -> alpha" in (result.worker_results[-1].error or "")
    assert all(w.in_flight == 0 for w in engine.list_workers())


def _templates() -> tuple[WorkerTemplate, ...]:
    return tuple(WorkerTemplate(name=n, role=f"the {n}", provider=f"script:{n}", max_instances=5)
                 for n in ("drafter", "checker"))


def _template_ping_pong(who: str, messages, script: _Script) -> LLMResponse:
    if any(a.startswith("Handoff refused") for a in _tool_answers(messages)):
        return _final(f"{who} finished")
    return _handoff("checker" if who == "drafter" else "drafter")


async def test_pool_instances_count_as_their_template(monkeypatch, tmp_path, tools):
    """A hop to a template spawns a new instance each time; the guard counts
    the template, so the template ping-pong stops where two registered
    workers' does (four runs), and only templates are offered."""
    script = _Script(_template_ping_pong)
    engine = _engine(monkeypatch, tmp_path, script, templates=_templates())

    result = await engine.dispatch(SwarmTask(prompt="p", workers=["drafter"], timeout=60))

    ran = [w.worker.split("-pool-")[0] for w in result.worker_results]
    assert ran == ["drafter", "checker", "drafter", "checker"], ran
    assert len({w.worker for w in result.worker_results}) == 4  # four instances
    first = next(t for t in script.calls[0]["tools"] if t["function"]["name"] == HANDOFF_TOOL_NAME)
    assert first["function"]["parameters"]["properties"]["worker"]["enum"] == ["checker"]


async def test_counting_instances_lets_a_template_ping_pong_run_to_the_depth_limit(
        monkeypatch, tmp_path, tools):
    """Negative control: the guard counting instance names never sees a
    template twice; only the depth limit stops the chain, one run later."""
    monkeypatch.setattr(SwarmEngine, "_handoff_identity", staticmethod(lambda worker: worker.name))
    script = _Script(_template_ping_pong)
    engine = _engine(monkeypatch, tmp_path, script, templates=_templates())

    result = await engine.dispatch(SwarmTask(prompt="p", workers=["drafter"], timeout=60))

    ran = [w.worker.split("-pool-")[0] for w in result.worker_results]
    assert ran == ["drafter", "checker", "drafter", "checker", "drafter"], ran


# ── Approvals: the handoff approves nothing ─────────────────────────────────


async def test_the_targets_danger_tool_asks_the_bus_and_the_handoff_asks_nothing(
        monkeypatch, tmp_path, tools):
    registry, safety = tools
    written = tmp_path / "out.txt"

    @registry.register(name="file_write")
    async def file_write(path: str, content: str) -> str:
        written.write_text(content, encoding="utf-8")
        return "written"

    def decide(who: str, messages, script: _Script) -> LLMResponse:
        if who == "planner":
            return _handoff("coder")
        if _tool_answers(messages):
            return _final("The write was refused.")
        return _call("file_write", {"path": "out.txt", "content": "x"})

    script = _Script(decide)
    engine = _engine(monkeypatch, tmp_path, script, "planner", "coder")

    result = await engine.dispatch(SwarmTask(prompt="Write the file.", workers=["planner"],
                                             timeout=60))

    assert [w.worker for w in result.worker_results] == ["planner", "coder"]
    assert [c.kwargs["tool_name"] for c in safety.check.await_args_list] == ["file_write"]
    assert not written.exists()
    assert "denied by HITL approval gate" in _tool_answers(script.calls[-1]["messages"])[0]


# ── The commitment layer's worker scope ─────────────────────────────────────


@pytest.mark.parametrize("acts, ran", [
    ({"mutate_fs"}, ["planner"]),
    ({"mutate_fs", "delegate"}, ["planner", "coder"]),  # negative control
])
async def test_a_worker_scope_without_delegate_refuses_the_handoff(
        monkeypatch, tmp_path, tools, acts, ran):
    from kazma_core.safety.commitment.scope import ScopeToken

    def decide(who: str, messages, script: _Script) -> LLMResponse:
        if who == "planner" and not _tool_answers(messages):
            return _handoff("coder")
        return _final(f"{who} finished")

    script = _Script(decide)
    engine = _engine(monkeypatch, tmp_path, script, "planner", "coder")
    scope = ScopeToken(allowed_acts=frozenset(acts)).to_metadata()

    result = await engine.dispatch(SwarmTask(prompt="p", workers=["planner"], timeout=60,
                                             metadata={"commitment_scope": scope}))

    assert [w.worker for w in result.worker_results] == ran
    if ran == ["planner"]:
        answer = _tool_answers(script.calls[-1]["messages"])[0]
        assert answer.startswith("Handoff refused") and "worker scope" in answer


# ── Every ending releases the source ────────────────────────────────────────


class _Planned(SwarmWorker):
    """A worker whose run hands off to its ``next`` worker, fails or returns."""

    plan: dict[str, str] = {}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def dispatch(self, task, context=""):
        step = self.plan.get(self.name, "ok")
        if step == "fail":
            return {"worker": self.name, "status": "error", "output": "", "error": "failed"}
        if step == "hang":
            await asyncio.Event().wait()
        if step.startswith("->"):
            raise HandoffRequest(step[2:], task)
        return {"worker": self.name, "status": "success", "output": "done"}


_ENDINGS = {
    "target_succeeded": {"a": "->b"},
    "target_failed": {"a": "->b", "b": "fail"},
    "target_missing": {"a": "->ghost"},
    "cycle_refused": {"a": "->b", "b": "->a"},
    "depth_refused": {"a": "->b", "b": "->c", "c": "->d", "d": "->e", "e": "->f", "f": "->g"},
    "target_cancelled": {"a": "->b", "b": "hang"},
}


async def _end(engine: SwarmEngine, ending: str) -> None:
    task = SwarmTask(prompt="p", workers=["a"], timeout=60)
    if ending != "target_cancelled":
        await engine.dispatch(task)
        return
    run = asyncio.create_task(engine.dispatch(task))
    for _ in range(200):
        if engine.get_worker("b").in_flight:
            break
        await asyncio.sleep(0.01)
    assert await engine.cancel_task(task.id)
    await asyncio.wait_for(run, 15)


def _planned_engine(monkeypatch, tmp_path: Path, ending: str) -> SwarmEngine:
    monkeypatch.setattr(_Planned, "plan", _ENDINGS[ending])
    engine = _engine(monkeypatch, tmp_path, _Script(_ping_pong()))
    for name in "abcdefg":
        engine._workers[name] = _Planned(name)
    return engine


@pytest.mark.parametrize("ending", sorted(_ENDINGS))
async def test_every_ending_of_a_handoff_releases_the_source(monkeypatch, tmp_path, ending):
    engine = _planned_engine(monkeypatch, tmp_path, ending)
    await _end(engine, ending)
    busy = {w.name: w.in_flight for w in engine.list_workers() if w.in_flight}
    assert busy == {}, f"{ending}: still in flight {busy}"


async def test_a_handoff_that_ends_without_its_release_is_caught(monkeypatch, tmp_path):
    """Negative control: with no release (the guard's ending had none until
    2026-10-15), the refused source stays in flight -- busy for good, never
    reaped."""
    monkeypatch.setattr(SwarmEngine, "_release_handoff_source", lambda self, worker, status: None)
    engine = _planned_engine(monkeypatch, tmp_path, "cycle_refused")
    await _end(engine, "cycle_refused")
    assert any(w.in_flight for w in engine.list_workers())


# ── The handoff keeps the dispatch's own fields ─────────────────────────────


def _lost_fields(build) -> list[str]:
    """The per-dispatch fields of SwarmDispatchContext a handoff loses.

    Enumerated from ``SwarmDispatchContext.__new__``; ``system_prompt`` is
    declared not handed on (the target runs as itself)."""
    from kazma_core.swarm.blackboard import SwarmDispatchContext

    not_handed_on = {"system_prompt": "the target runs with its own soul"}
    original = SwarmDispatchContext(
        "ctx", blackboard=None,
        metadata={"commitment_scope": {"allowed_acts": ["mutate_fs"]}, "workspace_id": "ws-1",
                  "tenant_id": "t-1"},
        task_id="task-1", task_type=TaskType.CONDITIONAL.value, system_prompt="the source's soul",
    )
    handed = build(original_prompt="p", original_context=original, intermediate_results="r",
                   blackboard=None)
    params = [n for n in inspect.signature(SwarmDispatchContext.__new__).parameters
              if n not in ("cls", "text", "blackboard")]
    return [n for n in params
            if n not in not_handed_on and getattr(handed, n, None) != getattr(original, n)]


def test_a_handoff_keeps_the_dispatch_scope_task_and_workspace_without_a_blackboard():
    from kazma_core.swarm.dispatch_helpers import build_handoff_context

    assert _lost_fields(build_handoff_context) == []


def test_the_old_handoff_context_lost_them():
    """Negative control: the helper before 2026-10-15 kept metadata on the
    blackboard path only and never the task id."""
    from kazma_core.swarm.blackboard import SwarmDispatchContext

    def old(*, original_prompt, original_context, intermediate_results, blackboard=None):
        text = f"Original prompt:\n{original_prompt}\n\nIntermediate results:\n{intermediate_results}"
        meta = dict(original_context.metadata or {})
        if blackboard is not None:
            return SwarmDispatchContext(text, blackboard=blackboard, metadata=meta)
        return text

    assert _lost_fields(old) == ["metadata", "task_id", "task_type"]
