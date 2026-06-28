"""Swarm task and result data models."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any, TypeVar, cast

logger = logging.getLogger(__name__)

EnumT = TypeVar("EnumT", bound=Enum)


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


def _new_task_id() -> str:
    """Return a unique swarm task identifier."""
    return f"task-{uuid.uuid4().hex}"


def _coerce_enum(enum_type: type[EnumT], value: EnumT | str) -> EnumT:
    """Coerce a string value into the target enum type."""
    if isinstance(value, enum_type):
        return value
    return enum_type(value)


def _serialize_value(value: Any) -> Any:
    """Convert nested dataclass values into JSON-compatible primitives."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    return value


class _JsonSerializable:
    """Mixin providing dictionary and JSON serialization helpers."""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation of this model."""
        return {
            dataclass_field.name: _serialize_value(getattr(self, dataclass_field.name))
            for dataclass_field in fields(cast(Any, self))
        }

    def to_json(self) -> str:
        """Serialize this model into JSON."""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


class TaskType(StrEnum):
    """Supported swarm orchestration task types."""

    DISPATCH = "dispatch"
    BROADCAST = "broadcast"
    PIPELINE = "pipeline"
    FAN_OUT = "fan_out"
    CONSULT = "consult"
    CONDITIONAL = "conditional"


class TaskStatus(StrEnum):
    """Lifecycle states for a swarm task."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class HandoffRecord(_JsonSerializable):
    """Record of a task handoff between two workers."""

    from_worker: str
    to_worker: str
    context_transferred: str
    timestamp: str = field(default_factory=_utc_now_iso)

    @classmethod
    def from_dict(cls, data: HandoffRecord | dict[str, Any]) -> HandoffRecord:
        """Create a handoff record from a dictionary."""
        if not isinstance(data, dict):
            return data
        return cls(
            from_worker=data.get("from_worker", ""),
            to_worker=data.get("to_worker", ""),
            context_transferred=data.get("context_transferred", ""),
            timestamp=data.get("timestamp", _utc_now_iso()),
        )

    @classmethod
    def from_json(cls, payload: str) -> HandoffRecord:
        """Create a handoff record from JSON."""
        return cls.from_dict(json.loads(payload))


@dataclass
class WorkerCapabilities(_JsonSerializable):
    """Capabilities declared by a swarm worker."""

    role: str = ""
    expertise: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    model_specialty: str = ""

    def __post_init__(self) -> None:
        self.expertise = list(self.expertise)
        self.tools = list(self.tools)

    @classmethod
    def from_dict(cls, data: WorkerCapabilities | dict[str, Any]) -> WorkerCapabilities:
        """Create worker capabilities from a dictionary."""
        if not isinstance(data, dict):
            return data
        return cls(
            role=data.get("role", ""),
            expertise=list(data.get("expertise", [])),
            tools=list(data.get("tools", [])),
            model_specialty=data.get("model_specialty", ""),
        )

    @classmethod
    def from_json(cls, payload: str) -> WorkerCapabilities:
        """Create worker capabilities from JSON."""
        return cls.from_dict(json.loads(payload))


@dataclass
class WorkerResult(_JsonSerializable):
    """Result returned by a single swarm worker."""

    worker: str
    task_id: str
    status: str
    output: str
    error: str | None = None
    tokens_used: int = 0
    cost: float = 0.0
    duration_seconds: float = 0.0
    handoffs: list[HandoffRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.handoffs = [HandoffRecord.from_dict(record) for record in self.handoffs]

    @classmethod
    def from_dict(cls, data: WorkerResult | dict[str, Any]) -> WorkerResult:
        """Create a worker result from a dictionary."""
        if not isinstance(data, dict):
            return data
        return cls(
            worker=data.get("worker", ""),
            task_id=data.get("task_id", ""),
            status=data.get("status", ""),
            output=data.get("output", ""),
            error=data.get("error"),
            tokens_used=int(data.get("tokens_used", 0)),
            cost=float(data.get("cost", 0.0)),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            handoffs=[HandoffRecord.from_dict(record) for record in data.get("handoffs", [])],
        )

    @classmethod
    def from_json(cls, payload: str) -> WorkerResult:
        """Create a worker result from JSON."""
        return cls.from_dict(json.loads(payload))


@dataclass
class TaskResult(_JsonSerializable):
    """Aggregated result for a swarm task."""

    task_id: str
    status: str
    worker_results: list[WorkerResult] = field(default_factory=list)
    aggregated_output: str | None = None
    synthesized_output: str | None = None
    error: str | None = None
    total_cost: float = 0.0
    total_tokens: int = 0
    duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        self.worker_results = [WorkerResult.from_dict(result) for result in self.worker_results]

    @classmethod
    def from_dict(cls, data: TaskResult | dict[str, Any]) -> TaskResult:
        """Create a task result from a dictionary."""
        if not isinstance(data, dict):
            return data
        return cls(
            task_id=data.get("task_id", ""),
            status=data.get("status", ""),
            worker_results=[WorkerResult.from_dict(result) for result in data.get("worker_results", [])],
            aggregated_output=data.get("aggregated_output"),
            synthesized_output=data.get("synthesized_output"),
            error=data.get("error"),
            total_cost=float(data.get("total_cost", 0.0)),
            total_tokens=int(data.get("total_tokens", 0)),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
        )

    @classmethod
    def from_json(cls, payload: str) -> TaskResult:
        """Create a task result from JSON."""
        return cls.from_dict(json.loads(payload))


@dataclass
class SwarmTask(_JsonSerializable):
    """Definition of a swarm task handled by the orchestration engine."""

    prompt: str
    id: str = field(default_factory=_new_task_id)
    type: TaskType = TaskType.DISPATCH
    context: str = ""
    workers: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    priority: int = 0
    timeout: float = 300.0
    validation_schema: dict[str, Any] | None = None
    fallback_chain: list[str] = field(default_factory=list)
    aggregation: str = "collect"
    status: TaskStatus = TaskStatus.PENDING
    result: TaskResult | None = None
    created_at: str = field(default_factory=_utc_now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    cost_estimate: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.type = _coerce_enum(TaskType, self.type)
        self.status = _coerce_enum(TaskStatus, self.status)
        self.workers = list(self.workers)
        self.dependencies = list(self.dependencies)
        self.fallback_chain = list(self.fallback_chain)
        self.validation_schema = (
            dict(self.validation_schema) if self.validation_schema is not None else None
        )
        self.metadata = dict(self.metadata)
        if self.result is not None:
            self.result = TaskResult.from_dict(self.result)

    @classmethod
    def from_dict(cls, data: SwarmTask | dict[str, Any]) -> SwarmTask:
        """Create a swarm task from a dictionary."""
        if not isinstance(data, dict):
            return data
        result_data = data.get("result")
        return cls(
            prompt=data.get("prompt", ""),
            id=data.get("id", _new_task_id()),
            type=data.get("type", TaskType.DISPATCH.value),
            context=data.get("context", ""),
            workers=list(data.get("workers", [])),
            dependencies=list(data.get("dependencies", [])),
            priority=int(data.get("priority", 0)),
            timeout=float(data.get("timeout", 300.0)),
            validation_schema=data.get("validation_schema"),
            fallback_chain=list(data.get("fallback_chain", [])),
            aggregation=data.get("aggregation", "collect"),
            status=data.get("status", TaskStatus.PENDING.value),
            result=TaskResult.from_dict(result_data) if result_data is not None else None,
            created_at=data.get("created_at", _utc_now_iso()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            cost_estimate=float(data.get("cost_estimate", 0.0)),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def from_json(cls, payload: str) -> SwarmTask:
        """Create a swarm task from JSON."""
        return cls.from_dict(json.loads(payload))
