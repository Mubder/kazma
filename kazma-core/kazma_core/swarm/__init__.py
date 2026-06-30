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

from kazma_core.swarm.aggregator import ResultAggregator
from kazma_core.swarm.blackboard import BlackboardStore, SwarmDispatchContext
from kazma_core.swarm.bus import (
    ApprovalRequest,
    BusAdapter,
    BusMessage,
    NullBusAdapter,
    SwarmMessageBus,
    SwarmReport,
    get_message_bus,
)
from kazma_core.swarm.checkpoint import HITLCheckpoint, HITLCheckpointHandler
from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine, get_swarm_engine, set_swarm_engine
from kazma_core.swarm.handoff import HandoffRequest, request_handoff
from kazma_core.swarm.manager import SwarmManager
from kazma_core.swarm.metrics import MetricsCollector, WorkerMetricSnapshot
from kazma_core.swarm.registry import WorkerEntry, WorkerRegistry
from kazma_core.swarm.reliability import (
    BoundedConcurrency,
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    FallbackChain,
    OutputValidator,
    RetryPolicy,
    TimeoutGuard,
    TimeoutGuardError,
)
from kazma_core.swarm.router import CapabilityRouter, NoCapableWorkersError
from kazma_core.swarm.safety import SafetyMiddleware, SafetyViolationError, get_safety
from kazma_core.swarm.task import (
    HandoffRecord,
    SwarmTask,
    TaskResult,
    TaskStatus,
    TaskType,
    WorkerCapabilities,
    WorkerResult,
)
from kazma_core.swarm.task_store import TaskStore
from kazma_core.swarm.topology import (
    STANDARD_PIPELINE,
    PipelineEngine,
    PipelineResult,
    PipelineStage,
    RefinerStage,
    StageRole,
)
from kazma_core.swarm.tracing import InMemorySpanExporter, Span, TracingEmitter
from kazma_core.swarm.worker import InProcessWorker, SwarmWorker, TelegramWorker

__all__ = [
    "ApprovalRequest",
    "BoundedConcurrency",
    "BusAdapter",
    "BusMessage",
    "CapabilityRouter",
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
    "NullBusAdapter",
    "OutputValidator",
    "PipelineEngine",
    "PipelineResult",
    "PipelineStage",
    "RefinerStage",
    "ResultAggregator",
    "RetryPolicy",
    "STANDARD_PIPELINE",
    "SafetyMiddleware",
    "SafetyViolationError",
    "Span",
    "StageRole",
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
    "TelegramWorker",
    "TimeoutGuard",
    "TimeoutGuardError",
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
