"""Unit tests for swarm.dispatch_helpers."""

from __future__ import annotations

from kazma_core.swarm.dispatch_helpers import (
    aggregate_outputs,
    build_dispatch_context,
    build_handoff_context,
    normalize_worker_type,
    overall_status,
    resolve_max_concurrent,
)
from kazma_core.swarm.task import SwarmTask, TaskType, WorkerResult


def test_normalize_worker_type():
    assert normalize_worker_type("in-process") == "in_process"
    assert normalize_worker_type("telegram") == "telegram_bot"
    assert normalize_worker_type("other") == "other"


def _wr(worker: str, status: str, output: str = "") -> WorkerResult:
    return WorkerResult(task_id="t", worker=worker, status=status, output=output)


def test_aggregate_outputs():
    results = [
        _wr("a", "success", "one"),
        _wr("b", "failed", "nope"),
        _wr("c", "success", "two"),
    ]
    agg = aggregate_outputs(results)
    assert agg is not None
    assert "one" in agg and "two" in agg and "[a]" in agg


def test_aggregate_empty():
    assert aggregate_outputs([]) is None
    assert aggregate_outputs([_wr("a", "failed")]) is None


def test_overall_status():
    ok = _wr("a", "success")
    bad = _wr("b", "failed")
    to = _wr("c", "timeout")
    assert overall_status([ok, ok]) == "success"
    assert overall_status([ok, bad]) == "partial"
    assert overall_status([to, to]) == "timeout"
    assert overall_status([bad, bad]) == "failed"
    assert overall_status([]) == "success"


def test_build_handoff_and_dispatch_context():
    ctx = build_handoff_context(
        original_prompt="P",
        original_context="C",
        intermediate_results="R",
    )
    assert "Original prompt" in str(ctx)
    assert "Intermediate results" in str(ctx)

    task = SwarmTask(type=TaskType.DISPATCH, prompt="x", context="ctx", workers=["w"])
    assert build_dispatch_context(task) == "ctx"


def test_resolve_max_concurrent():
    task = SwarmTask(type=TaskType.DISPATCH, prompt="x", workers=["w"], metadata={})
    assert resolve_max_concurrent(task, 5) == 5
    task.metadata["max_concurrent"] = 3
    assert resolve_max_concurrent(task, 5) == 3
    task.metadata["max_concurrent"] = "bad"
    assert resolve_max_concurrent(task, 5) == 5
