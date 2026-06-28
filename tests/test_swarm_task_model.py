"""Tests for swarm task data models."""

from __future__ import annotations

from kazma_core.swarm import WorkerCapabilities
from kazma_core.swarm.task import (
    HandoffRecord,
    SwarmTask,
    TaskResult,
    TaskStatus,
    TaskType,
    WorkerResult,
)


def test_swarm_task_defaults() -> None:
    """SwarmTask provides sensible defaults for a new task."""
    task = SwarmTask(prompt="Investigate the failure")

    assert task.id
    assert task.type is TaskType.DISPATCH
    assert task.context == ""
    assert task.workers == []
    assert task.dependencies == []
    assert task.priority == 0
    assert task.timeout == 300.0
    assert task.validation_schema is None
    assert task.fallback_chain == []
    assert task.aggregation == "collect"
    assert task.status is TaskStatus.PENDING
    assert task.result is None
    assert task.created_at
    assert task.started_at is None
    assert task.completed_at is None
    assert task.cost_estimate == 0.0
    assert task.metadata == {}


def test_swarm_task_supports_all_fields_and_nested_result_serialization() -> None:
    """SwarmTask serializes nested results into plain JSON-compatible dicts."""
    handoff = HandoffRecord(
        from_worker="planner",
        to_worker="reviewer",
        context_transferred="Draft architecture plan",
        timestamp="2026-06-28T00:02:00+00:00",
    )
    worker_result = WorkerResult(
        worker="planner",
        task_id="task-123",
        status="success",
        output="Initial plan",
        error=None,
        tokens_used=321,
        cost=1.23,
        duration_seconds=4.5,
        handoffs=[handoff],
    )
    task_result = TaskResult(
        task_id="task-123",
        status="success",
        worker_results=[worker_result],
        aggregated_output="Merged plan",
        synthesized_output="Final recommendation",
        error=None,
        total_cost=1.23,
        total_tokens=321,
        duration_seconds=4.5,
    )
    task = SwarmTask(
        id="task-123",
        type="consult",
        prompt="Design the swarm architecture",
        context="Use existing swarm workers",
        workers=["planner", "reviewer"],
        dependencies=["task-122"],
        priority=2,
        timeout=90.0,
        validation_schema={"type": "object"},
        fallback_chain=["backup-reviewer"],
        aggregation="synthesize",
        status="running",
        result=task_result,
        created_at="2026-06-28T00:00:00+00:00",
        started_at="2026-06-28T00:01:00+00:00",
        completed_at=None,
        cost_estimate=2.5,
        metadata={"trace_id": "trace-123"},
    )

    data = task.to_dict()

    assert data["id"] == "task-123"
    assert data["type"] == "consult"
    assert data["status"] == "running"
    assert data["workers"] == ["planner", "reviewer"]
    assert data["result"]["aggregated_output"] == "Merged plan"
    assert data["result"]["worker_results"][0]["handoffs"][0]["to_worker"] == "reviewer"
    assert data["metadata"] == {"trace_id": "trace-123"}


def test_task_result_round_trips_through_json() -> None:
    """TaskResult supports JSON serialization and deserialization."""
    result = TaskResult(
        task_id="task-456",
        status="partial",
        worker_results=[
            WorkerResult(
                worker="alpha",
                task_id="task-456",
                status="success",
                output="Answer",
                error=None,
                tokens_used=42,
                cost=0.12,
                duration_seconds=1.5,
            )
        ],
        aggregated_output="Collected answer",
        synthesized_output=None,
        error=None,
        total_cost=0.12,
        total_tokens=42,
        duration_seconds=1.5,
    )

    restored = TaskResult.from_json(result.to_json())

    assert restored == result
    assert restored.worker_results[0].handoffs == []


def test_worker_result_handoffs_default_to_independent_empty_lists() -> None:
    """WorkerResult uses an empty handoffs list by default."""
    first = WorkerResult(worker="one", task_id="task-1", status="success", output="ok")
    second = WorkerResult(worker="two", task_id="task-1", status="success", output="ok")

    assert first.handoffs == []
    assert second.handoffs == []
    assert first.handoffs is not second.handoffs


def test_handoff_record_contains_all_required_fields() -> None:
    """HandoffRecord serializes all required fields."""
    record = HandoffRecord(
        from_worker="alpha",
        to_worker="beta",
        context_transferred="Carry forward prior findings",
        timestamp="2026-06-28T12:00:00+00:00",
    )

    assert record.to_dict() == {
        "from_worker": "alpha",
        "to_worker": "beta",
        "context_transferred": "Carry forward prior findings",
        "timestamp": "2026-06-28T12:00:00+00:00",
    }


def test_worker_capabilities_is_importable_from_package_root() -> None:
    """WorkerCapabilities is exported for external imports."""
    capabilities = WorkerCapabilities(
        role="backend_core",
        expertise=["python", "asyncio"],
        tools=["read_file", "search_files"],
        model_specialty="reasoning",
    )

    assert capabilities.to_dict() == {
        "role": "backend_core",
        "expertise": ["python", "asyncio"],
        "tools": ["read_file", "search_files"],
        "model_specialty": "reasoning",
    }


def test_swarm_task_from_dict_rehydrates_nested_models() -> None:
    """SwarmTask.from_dict restores enums and nested dataclasses."""
    task = SwarmTask.from_dict(
        {
            "id": "task-789",
            "type": "broadcast",
            "prompt": "Fan out the work",
            "context": "Context text",
            "workers": ["w1", "w2"],
            "dependencies": ["task-001"],
            "priority": 1,
            "timeout": 15,
            "validation_schema": {"type": "string"},
            "fallback_chain": ["backup"],
            "aggregation": "merge_all",
            "status": "completed",
            "result": {
                "task_id": "task-789",
                "status": "success",
                "worker_results": [
                    {
                        "worker": "w1",
                        "task_id": "task-789",
                        "status": "success",
                        "output": "done",
                        "error": None,
                        "tokens_used": 10,
                        "cost": 0.01,
                        "duration_seconds": 0.5,
                        "handoffs": [],
                    }
                ],
                "aggregated_output": "done",
                "synthesized_output": None,
                "error": None,
                "total_cost": 0.01,
                "total_tokens": 10,
                "duration_seconds": 0.5,
            },
            "created_at": "2026-06-28T00:00:00+00:00",
            "started_at": "2026-06-28T00:00:01+00:00",
            "completed_at": "2026-06-28T00:00:02+00:00",
            "cost_estimate": 0.75,
            "metadata": {"route": "all"},
        }
    )

    assert task.type is TaskType.BROADCAST
    assert task.status is TaskStatus.COMPLETED
    assert isinstance(task.result, TaskResult)
    assert isinstance(task.result.worker_results[0], WorkerResult)
    assert task.result.worker_results[0].handoffs == []
