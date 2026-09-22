"""Snapshot capture is safe under concurrency, and never fails the turn it observes.

Regression for the 2026-09-22 audit:

* ``SnapshotRecorder._memory`` is an ``OrderedDict`` mutated by captures that
  run in ``asyncio.to_thread`` — several turns at once — while the replay
  routes iterate it. Unlocked, forcing thread switches produced 2,313
  ``OrderedDict mutated during iteration`` errors inside ``capture()`` and
  1,570 in the readers.
* The supervisor awaited ``capture()`` with no guard, so any such error
  propagated out of the node and killed the user's turn.
"""

from __future__ import annotations

import sys
import threading
from collections import Counter
from pathlib import Path

import pytest
from kazma_core.time_travel import SnapshotRecorder


def test_concurrent_captures_and_reads_do_not_race(tmp_path: Path):
    recorder = SnapshotRecorder(
        db_path=str(tmp_path / "snap.db"), max_snapshots=20, max_global_snapshots=120
    )
    errors: Counter[str] = Counter()
    stop = threading.Event()

    def writer(n: int) -> None:
        for i in range(120):
            try:
                recorder.capture({"thread_id": f"t{n}-{i % 5}", "iteration": i, "messages": []})
            except Exception as exc:  # noqa: BLE001 - counting is the assertion
                errors[f"capture: {type(exc).__name__}"] += 1

    def reader() -> None:
        while not stop.is_set():
            try:
                recorder.list_distinct_threads()
                recorder.list_snapshots("t0-1")
                recorder.get_snapshot("t1-2", 7)
            except Exception as exc:  # noqa: BLE001
                errors[f"reader: {type(exc).__name__}"] += 1

    previous = sys.getswitchinterval()
    # Force a thread switch every microsecond: this is how the race was shown.
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        read = threading.Thread(target=reader)
        read.start()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stop.set()
        read.join()
    finally:
        sys.setswitchinterval(previous)
        recorder.close()
    assert not errors, dict(errors)


class _ExplodingRecorder:
    enabled = True

    def capture(self, state):  # noqa: ANN001
        raise RuntimeError("OrderedDict mutated during iteration")


class _AnswerLLM:
    async def chat(self, *, messages, tools=None, model=None, **kwargs):  # noqa: ANN001
        from kazma_core.llm_provider import LLMResponse

        return LLMResponse(
            content="The answer is 4.",
            tool_calls=[],
            finish_reason="stop",
            model="stub",
            usage={"total_tokens": 5},
            cost_usd=0.0,
        )


@pytest.mark.asyncio
async def test_a_failing_snapshot_does_not_fail_the_turn():
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.state import initial_supervisor_state
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.tracing import KazmaTracer

    registry = LocalToolRegistry(include_builtins=False)
    graph = build_supervisor_graph(
        llm=_AnswerLLM(),
        system_prompt="You are a test agent.",
        tool_definitions=registry.get_tool_definitions(),
        tool_executor=registry,
        cost_breaker=create_cost_breaker(),
        authority=create_authority(model="test", window=128000),
        tracer=KazmaTracer(backend="console"),
        snapshot_recorder=_ExplodingRecorder(),
    )
    state = initial_supervisor_state(thread_id="snap-fail")
    state["messages"] = [{"role": "user", "content": "what is 2+2?"}]
    final = await graph.ainvoke(state, {"configurable": {"thread_id": "snap-fail"}})

    assistant = [m for m in final["messages"] if m.get("role") == "assistant"]
    assert assistant and "4" in assistant[-1]["content"]
    assert not final.get("turn_failed"), final.get("error_message")
    assert not final.get("snapshot_id")  # nothing was recorded, and that is fine
