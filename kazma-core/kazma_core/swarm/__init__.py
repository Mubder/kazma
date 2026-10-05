"""Unified SwarmManager engine.

Provides a single orchestration layer for managing Kazma workers in two modes:
- in_process: lightweight sub-agent spawning (same model, fast)
- telegram_bot: persistent Kazma profile bots (separate process, different model)

Usage::

    from kazma_core.swarm import SwarmManager, SwarmConfig

    config = SwarmConfig.from_yaml("kazma.yaml")
    manager = SwarmManager(config)
    await manager.start_all()
    result = await manager.dispatch("core", "Fix the auth bug")
"""

from __future__ import annotations

import importlib
from typing import Any

# Importing a workflow leaf must not boot the swarm, its provider clients or
# memory libraries inside Temporal's deterministic sandbox. Exports retain
# their defining-module identity and are resolved only when requested.
_EXPORT_GROUPS = {
    "kazma_core.swarm.aggregator": ("ResultAggregator",),
    "kazma_core.swarm.blackboard": ("BlackboardStore", "SwarmDispatchContext"),
    "kazma_core.swarm.bus": ("ApprovalRequest", "BusAdapter", "BusMessage", "FanOutBusAdapter", "NullBusAdapter", "SwarmMessageBus", "SwarmReport", "get_message_bus"),
    "kazma_core.swarm.checkpoint": ("HITLCheckpoint", "HITLCheckpointHandler"),
    "kazma_core.swarm.config": ("SwarmConfig", "WorkerConfig"),
    "kazma_core.swarm.engine": ("SwarmEngine", "get_swarm_engine", "set_swarm_engine"),
    "kazma_core.swarm.handoff": ("HandoffRequest", "request_handoff"),
    "kazma_core.swarm.manager": ("SwarmManager",),
    "kazma_core.swarm.metrics": ("MetricsCollector", "WorkerMetricSnapshot"),
    "kazma_core.swarm.registry": ("WorkerEntry", "WorkerRegistry"),
    "kazma_core.swarm.reliability": ("BoundedConcurrency", "CircuitBreaker", "CircuitBreakerOpenError", "CircuitState", "FallbackChain", "OutputValidator", "RetryPolicy", "TimeoutGuard"),
    "kazma_core.routing_engine": ("UnifiedRouter", "NoCapableWorkersError"),
    "kazma_core.swarm.safety": ("SafetyMiddleware", "SafetyViolationError", "get_safety"),
    "kazma_core.swarm.task": ("HandoffRecord", "SwarmTask", "TaskResult", "TaskStatus", "TaskType", "WorkerCapabilities", "WorkerResult"),
    "kazma_core.swarm.task_store": ("TaskStore",),
    "kazma_core.swarm.tracing": ("InMemorySpanExporter", "Span", "TracingEmitter"),
    "kazma_core.swarm.worker": ("InProcessWorker", "SwarmWorker"),
}
_EXPORTS = {name: module for module, names in _EXPORT_GROUPS.items() for name in names}


def __getattr__(name: str) -> Any:
    """Load a public export without loading every sibling subsystem."""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "ApprovalRequest",
    "BoundedConcurrency",
    "BusAdapter",
    "BusMessage",
    "UnifiedRouter",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CircuitState",
    "FallbackChain",
    "HITLCheckpoint",
    "HITLCheckpointHandler",
    "HandoffRecord",
    "HandoffRequest",
    "InMemorySpanExporter",
    "InProcessWorker",
    "MetricsCollector",
    "NoCapableWorkersError",
    "FanOutBusAdapter",
    "NullBusAdapter",
    "OutputValidator",
    "ResultAggregator",
    "RetryPolicy",
    "SafetyMiddleware",
    "SafetyViolationError",
    "Span",
    "SwarmConfig",
    "SwarmEngine",
    "SwarmManager",
    "SwarmMessageBus",
    "SwarmReport",
    "SwarmTask",
    "SwarmWorker",
    "TaskResult",
    "TaskStatus",
    "TaskStore",
    "TaskType",
    "TimeoutGuard",
    "TracingEmitter",
    "WorkerCapabilities",
    "WorkerConfig",
    "WorkerEntry",
    "WorkerMetricSnapshot",
    "WorkerRegistry",
    "WorkerResult",
    "BlackboardStore",
    "SwarmDispatchContext",
    "get_message_bus",
    "get_safety",
    "get_swarm_engine",
    "request_handoff",
    "set_swarm_engine",
]

