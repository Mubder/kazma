"""Chaos Testing Framework for Kazma.

Provides failure injection capabilities for resilience testing:
- Latency injection
- Error injection (HTTP errors, exceptions)
- Timeout simulation
- Circuit breaker forcing
- Resource exhaustion simulation
- Network partition simulation
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Optional
from functools import wraps

logger = logging.getLogger(__name__)


def _chaos_enabled() -> bool:
    """Production kill-switch: chaos injections only ever fire when this
    is explicitly truthy. Mirrors the env var that gates the ``/api/chaos``
    HTTP routes (kazma_ui/routes_chaos.py) so there's one toggle for the
    whole feature — this check runs at the actual injection-trigger point
    too, so a stray ``add_injection()`` call or a decorator left on a
    production code path can never cause real failures unless someone
    explicitly opted in.
    """
    return os.environ.get("KAZMA_CHAOS_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class FailureType(Enum):
    """Types of failures that can be injected."""
    LATENCY = "latency"
    ERROR = "error"
    TIMEOUT = "timeout"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    NETWORK_PARTITION = "network_partition"
    DATA_CORRUPTION = "data_corruption"
    PARTIAL_DEGRADATION = "partial_degradation"


class InjectionTarget(Enum):
    """Target components for failure injection."""
    LLM_PROVIDER = "llm_provider"
    DATABASE = "database"
    MESSAGE_BUS = "message_bus"
    TOOL_EXECUTOR = "tool_executor"
    SWARM_ENGINE = "swarm_engine"
    GATEWAY_ADAPTER = "gateway_adapter"
    WEBHOOK_HANDLER = "webhook_handler"
    CACHE = "cache"
    EXTERNAL_API = "external_api"


@dataclass
class FailureInjection:
    """Configuration for a single failure injection."""
    failure_type: FailureType
    target: InjectionTarget
    probability: float = 0.1  # 0.0 to 1.0
    severity: str = "medium"  # low, medium, high, critical
    duration_seconds: Optional[float] = None  # None = permanent until removed
    error_message: str = "Chaos injection: simulated failure"
    error_code: int = 500
    latency_ms: int = 0  # For latency injection
    metadata: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: Optional[datetime] = None
    injection_id: str = field(default_factory=lambda: f"inj_{int(time.time()*1000)}_{random.randint(1000,9999)}")

    def __post_init__(self):
        if self.duration_seconds:
            from datetime import timedelta
            self.expires_at = datetime.now(UTC) + timedelta(seconds=self.duration_seconds)

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at

    def should_inject(self) -> bool:
        """Check if this injection should trigger based on probability."""
        if not self.enabled or self.is_expired():
            return False
        return random.random() < self.probability


class FailureInjector:
    """Central registry and executor for failure injections."""
    
    def __init__(self):
        self._injections: dict[str, FailureInjection] = {}
        self._target_injections: dict[InjectionTarget, list[str]] = {
            target: [] for target in InjectionTarget
        }
        self._lock = asyncio.Lock()
        self._stats: dict[str, dict] = {}
    
    async def add_injection(self, injection: FailureInjection) -> str:
        """Add a new failure injection."""
        async with self._lock:
            self._injections[injection.injection_id] = injection
            self._target_injections[injection.target].append(injection.injection_id)
            self._stats[injection.injection_id] = {
                "triggered": 0,
                "last_triggered": None,
                "total_calls": 0,
            }
            logger.info(
                f"[Chaos] Added injection {injection.injection_id}: "
                f"{injection.failure_type.value} -> {injection.target.value} "
                f"(prob={injection.probability})"
            )
            return injection.injection_id
    
    async def remove_injection(self, injection_id: str) -> bool:
        """Remove a failure injection by ID."""
        async with self._lock:
            injection = self._injections.pop(injection_id, None)
            if injection:
                self._target_injections[injection.target] = [
                    i for i in self._target_injections[injection.target]
                    if i != injection_id
                ]
                self._stats.pop(injection_id, None)
                logger.info(f"[Chaos] Removed injection {injection_id}")
                return True
            return False
    
    async def get_injection(self, injection_id: str) -> Optional[FailureInjection]:
        """Get injection by ID."""
        return self._injections.get(injection_id)
    
    async def list_injections(self, target: Optional[InjectionTarget] = None) -> list[FailureInjection]:
        """List all active injections, optionally filtered by target."""
        async with self._lock:
            if target:
                ids = self._target_injections.get(target, [])
                return [self._injections[i] for i in ids if i in self._injections]
            return list(self._injections.values())
    
    async def clear_all(self) -> int:
        """Remove all injections. Returns count removed."""
        async with self._lock:
            count = len(self._injections)
            self._injections.clear()
            for target in self._target_injections:
                self._target_injections[target].clear()
            self._stats.clear()
            logger.info(f"[Chaos] Cleared all {count} injections")
            return count

    async def stop_all(self) -> int:
        """Stop all injections on *this* instance. Alias for clear_all()."""
        return await self.clear_all()
    
    async def clear_expired(self) -> int:
        """Remove expired injections. Returns count removed."""
        async with self._lock:
            expired_ids = [
                inj_id for inj_id, inj in self._injections.items()
                if inj.is_expired()
            ]
            for inj_id in expired_ids:
                await self.remove_injection(inj_id)
            return len(expired_ids)
    
    async def should_inject(self, target: InjectionTarget) -> Optional[FailureInjection]:
        """Check if any injection should trigger for the given target."""
        if not _chaos_enabled():
            return None
        await self.clear_expired()
        
        async with self._lock:
            for inj_id in self._target_injections.get(target, []):
                injection = self._injections.get(inj_id)
                if injection and injection.should_inject():
                    self._stats[inj_id]["triggered"] += 1
                    self._stats[inj_id]["last_triggered"] = datetime.now(UTC).isoformat()
                    return injection
        return None
    
    def has_injections(self, target: InjectionTarget) -> bool:
        """Is anything registered against *target*? Lock-free by design.

        This is the gate that keeps decorated hot paths cheap on a
        deployment where chaos is armed but idle. A plain dict read cannot
        deadlock, cannot block the loop, and cannot serialise callers --
        properties worth more here than being exactly current, since an
        injection registered microseconds ago being missed by one call is
        not a wrong answer for a test harness.
        """
        return bool(self._target_injections.get(target))

    async def record_call(self, target: InjectionTarget):
        """Record a call to the target for statistics.

        Guarded by the kill switch as well as the decorator's fast path:
        this takes a process-wide lock, and nothing that takes a
        process-wide lock should be reachable on a hot path when the
        feature is off.
        """
        if not _chaos_enabled():
            return
        async with self._lock:
            for inj_id in self._target_injections.get(target, []):
                if inj_id in self._stats:
                    self._stats[inj_id]["total_calls"] += 1
    
    async def get_stats(self, injection_id: Optional[str] = None) -> dict:
        """Get injection statistics."""
        async with self._lock:
            if injection_id:
                return self._stats.get(injection_id, {})
            return dict(self._stats)


# Global injector instance
_injector: Optional[FailureInjector] = None


def get_injector() -> FailureInjector:
    """Get the global failure injector."""
    global _injector
    if _injector is None:
        _injector = FailureInjector()
    return _injector


def set_injector(injector: FailureInjector) -> None:
    """Set the global failure injector (for testing)."""
    global _injector
    _injector = injector


# ─── Decorator for Easy Injection ─────────────────────────────────────────

def chaos_injection(target: InjectionTarget):
    """Decorator to enable chaos injection on async functions.
    
    Usage:
        @chaos_injection(InjectionTarget.LLM_PROVIDER)
        async def call_llm(...):
            ...
    """
    def decorator(func: Callable):
        if not asyncio.iscoroutinefunction(func):
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                # Same fast path as below; see the note there.
                if not _chaos_enabled():
                    return func(*args, **kwargs)
                if not get_injector().has_injections(target):
                    return func(*args, **kwargs)
                injection = _should_inject_sync(target)
                if injection:
                    logger.warning(
                        "[Chaos] Injecting %s into %s: %s",
                        injection.failure_type.value, target.value,
                        injection.error_message,
                    )
                    _apply_injection_sync(injection)
                return func(*args, **kwargs)
            return sync_wrapper

        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Fast path FIRST, before touching the injector at all.
            #
            # This decorator is meant to sit on hot paths -- the LLM call,
            # the database, the tool executor. The obvious implementation
            # (record_call, then should_inject, then call through) costs two
            # awaits and a shared asyncio.Lock acquisition on EVERY call,
            # in production, forever, with chaos switched off: record_call
            # takes the lock unconditionally, so one global lock would
            # serialise every decorated call in the process. Instrumenting
            # for resilience testing must not itself become a bottleneck.
            #
            # os.environ.get is a dict lookup, and reading it per call
            # rather than caching keeps the kill switch live: an operator
            # can turn chaos off without a restart, which matters most in
            # exactly the situation where they want it off.
            if not _chaos_enabled():
                return await func(*args, **kwargs)

            injector = get_injector()
            # Second fast path, and on this deployment the important one.
            # KAZMA_CHAOS_ENABLED is switched ON against the live install,
            # so the disabled path above never runs there. "Armed but
            # nothing aimed at this target" is the steady state of a chaos
            # framework -- injections are registered per experiment and
            # live only in memory -- and it must not cost two locks.
            if not injector.has_injections(target):
                return await func(*args, **kwargs)

            await injector.record_call(target)

            injection = await injector.should_inject(target)
            if injection:
                logger.warning(
                    "[Chaos] Injecting %s into %s: %s",
                    injection.failure_type.value, target.value,
                    injection.error_message,
                )
                await _apply_injection(injection)

            return await func(*args, **kwargs)
        return wrapper
    return decorator


def _should_inject_sync(target: InjectionTarget) -> FailureInjection | None:
    """Pick a due injection without touching the async lock.

    Sync call sites (most database access) cannot await, and a decorator
    that only supports coroutines would leave the database target -- one of
    the nine -- permanently unwireable.

    Reading the dicts without the lock is safe enough here: the worst case
    is that an injection added in the same millisecond is missed by one
    call. Chaos is a test harness, and a fault that fires one call later is
    not a wrong answer.
    """
    injector = get_injector()
    for inj_id in list(injector._target_injections.get(target, [])):
        injection = injector._injections.get(inj_id)
        if injection and injection.should_inject():
            stats = injector._stats.get(inj_id)
            if stats is not None:
                stats["triggered"] += 1
                stats["last_triggered"] = datetime.now(UTC).isoformat()
            return injection
    return None


def _apply_injection_sync(injection: FailureInjection) -> None:
    """Synchronous twin of :func:`_apply_injection`.

    Latency blocks the calling thread rather than yielding the loop, which
    is the honest simulation of a slow blocking driver -- the thing a sync
    database call actually does to an event loop when it goes slow.
    """
    ft = injection.failure_type
    if ft == FailureType.LATENCY:
        delay = injection.latency_ms / 1000.0
        if delay > 0:
            time.sleep(delay)
        return
    if ft == FailureType.PARTIAL_DEGRADATION:
        time.sleep(random.uniform(0.5, 2.0))
        return
    if ft == FailureType.TIMEOUT:
        time.sleep(30.0)
        raise ChaosInjectionError(
            "Chaos injection: simulated timeout", error_code=504,
            injection_id=injection.injection_id,
        )
    _codes = {
        FailureType.CIRCUIT_BREAKER_OPEN: (503, "circuit breaker forced open"),
        FailureType.RESOURCE_EXHAUSTION: (507, "resource exhausted (memory/connections)"),
        FailureType.NETWORK_PARTITION: (503, "network partition simulated"),
        FailureType.DATA_CORRUPTION: (500, "data corruption detected"),
    }
    if ft in _codes:
        code, msg = _codes[ft]
        raise ChaosInjectionError(f"Chaos injection: {msg}", error_code=code,
                                  injection_id=injection.injection_id)
    raise ChaosInjectionError(
        injection.error_message, error_code=injection.error_code,
        injection_id=injection.injection_id, metadata=injection.metadata,
    )


async def _apply_injection(injection: FailureInjection) -> None:
    """Apply the configured failure injection."""
    if injection.failure_type == FailureType.LATENCY:
        delay = injection.latency_ms / 1000.0
        if delay > 0:
            await asyncio.sleep(delay)
    
    elif injection.failure_type == FailureType.ERROR:
        # Raise a generic exception that can be caught
        raise ChaosInjectionError(
            injection.error_message,
            error_code=injection.error_code,
            injection_id=injection.injection_id,
            metadata=injection.metadata,
        )
    
    elif injection.failure_type == FailureType.TIMEOUT:
        # Simulate timeout by sleeping longer than typical timeout
        await asyncio.sleep(30.0)  # 30 second timeout
        raise ChaosInjectionError(
            "Chaos injection: simulated timeout",
            error_code=504,
            injection_id=injection.injection_id,
        )
    
    elif injection.failure_type == FailureType.CIRCUIT_BREAKER_OPEN:
        # Force circuit breaker to appear open
        raise ChaosInjectionError(
            "Chaos injection: circuit breaker forced open",
            error_code=503,
            injection_id=injection.injection_id,
        )
    
    elif injection.failure_type == FailureType.RESOURCE_EXHAUSTION:
        raise ChaosInjectionError(
            "Chaos injection: resource exhausted (memory/connections)",
            error_code=507,
            injection_id=injection.injection_id,
        )
    
    elif injection.failure_type == FailureType.NETWORK_PARTITION:
        raise ChaosInjectionError(
            "Chaos injection: network partition simulated",
            error_code=503,
            injection_id=injection.injection_id,
        )
    
    elif injection.failure_type == FailureType.DATA_CORRUPTION:
        # This would typically corrupt return values
        # For now, raise an error
        raise ChaosInjectionError(
            "Chaos injection: data corruption detected",
            error_code=500,
            injection_id=injection.injection_id,
        )
    
    elif injection.failure_type == FailureType.PARTIAL_DEGRADATION:
        # Degrade performance without failing
        delay = random.uniform(0.5, 2.0)
        await asyncio.sleep(delay)


class ChaosInjectionError(Exception):
    """Exception raised by chaos injection."""
    
    def __init__(
        self,
        message: str,
        error_code: int = 500,
        injection_id: str = "",
        metadata: Optional[dict] = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.injection_id = injection_id
        self.metadata = metadata or {}


# ─── Context Manager for Scoped Injections ────────────────────────────────

@asynccontextmanager
async def chaos_experiment(
    target: InjectionTarget,
    failure_type: FailureType,
    probability: float = 0.5,
    duration_seconds: float = 60.0,
    **kwargs,
):
    """Context manager for running a chaos experiment.
    
    Usage:
        async with chaos_experiment(
            InjectionTarget.LLM_PROVIDER,
            FailureType.LATENCY,
            probability=1.0,
            latency_ms=5000,
            duration_seconds=30,
        ):
            # Run test code here
            await call_llm(...)
    """
    injector = get_injector()
    injection = FailureInjection(
        failure_type=failure_type,
        target=target,
        probability=probability,
        duration_seconds=duration_seconds,
        **kwargs,
    )
    inj_id = await injector.add_injection(injection)
    try:
        yield inj_id
    finally:
        await injector.remove_injection(inj_id)


# ─── Experiment Definitions ──────────────────────────────────────────────

@dataclass
class ChaosExperiment:
    """A predefined chaos experiment."""
    name: str
    description: str
    target: InjectionTarget
    failure_type: FailureType
    probability: float
    duration_seconds: float
    params: dict[str, Any] = field(default_factory=dict)


# Predefined experiments
PREDEFINED_EXPERIMENTS = {
    "llm_high_latency": ChaosExperiment(
        name="LLM High Latency",
        description="Simulate slow LLM responses (5-10s latency)",
        target=InjectionTarget.LLM_PROVIDER,
        failure_type=FailureType.LATENCY,
        probability=0.3,
        duration_seconds=60,
        params={"latency_ms": 7500},
    ),
    "llm_intermittent_errors": ChaosExperiment(
        name="LLM Intermittent Errors",
        description="Random 500 errors from LLM provider",
        target=InjectionTarget.LLM_PROVIDER,
        failure_type=FailureType.ERROR,
        probability=0.1,
        duration_seconds=120,
        params={"error_code": 500, "error_message": "LLM provider temporary error"},
    ),
    "llm_timeout": ChaosExperiment(
        name="LLM Timeout",
        description="Simulate LLM request timeouts",
        target=InjectionTarget.LLM_PROVIDER,
        failure_type=FailureType.TIMEOUT,
        probability=0.05,
        duration_seconds=60,
    ),
    "database_slow": ChaosExperiment(
        name="Database Slow Queries",
        description="Inject latency into database operations",
        target=InjectionTarget.DATABASE,
        failure_type=FailureType.LATENCY,
        probability=0.2,
        duration_seconds=60,
        params={"latency_ms": 3000},
    ),
    "database_errors": ChaosExperiment(
        name="Database Errors",
        description="Random database connection errors",
        target=InjectionTarget.DATABASE,
        failure_type=FailureType.ERROR,
        probability=0.05,
        duration_seconds=120,
        params={"error_code": 503, "error_message": "Database connection failed"},
    ),
    "message_bus_partition": ChaosExperiment(
        name="Message Bus Network Partition",
        description="Simulate message bus network partition",
        target=InjectionTarget.MESSAGE_BUS,
        failure_type=FailureType.NETWORK_PARTITION,
        probability=0.1,
        duration_seconds=30,
    ),
    "tool_executor_failures": ChaosExperiment(
        name="Tool Executor Failures",
        description="Random tool execution failures",
        target=InjectionTarget.TOOL_EXECUTOR,
        failure_type=FailureType.ERROR,
        probability=0.1,
        duration_seconds=60,
        params={"error_code": 500, "error_message": "Tool execution failed"},
    ),
    "swarm_engine_degradation": ChaosExperiment(
        name="Swarm Engine Degradation",
        description="Partial degradation of swarm orchestration",
        target=InjectionTarget.SWARM_ENGINE,
        failure_type=FailureType.PARTIAL_DEGRADATION,
        probability=0.2,
        duration_seconds=120,
    ),
    "gateway_adapter_errors": ChaosExperiment(
        name="Gateway Adapter Errors",
        description="Platform adapter (Telegram/Discord/Slack) errors",
        target=InjectionTarget.GATEWAY_ADAPTER,
        failure_type=FailureType.ERROR,
        probability=0.05,
        duration_seconds=60,
        params={"error_code": 502, "error_message": "Platform API error"},
    ),
    "circuit_breaker_force_open": ChaosExperiment(
        name="Force Circuit Breaker Open",
        description="Force circuit breaker to appear open",
        target=InjectionTarget.LLM_PROVIDER,
        failure_type=FailureType.CIRCUIT_BREAKER_OPEN,
        probability=0.5,
        duration_seconds=30,
    ),
    "resource_exhaustion": ChaosExperiment(
        name="Resource Exhaustion",
        description="Simulate memory/connection exhaustion",
        target=InjectionTarget.DATABASE,
        failure_type=FailureType.RESOURCE_EXHAUSTION,
        probability=0.1,
        duration_seconds=60,
    ),
}


async def run_predefined_experiment(experiment_name: str) -> str:
    """Run a predefined chaos experiment by name. Returns injection ID."""
    if experiment_name not in PREDEFINED_EXPERIMENTS:
        raise ValueError(f"Unknown experiment: {experiment_name}")
    
    exp = PREDEFINED_EXPERIMENTS[experiment_name]
    injector = get_injector()
    
    injection = FailureInjection(
        failure_type=exp.failure_type,
        target=exp.target,
        probability=exp.probability,
        duration_seconds=exp.duration_seconds,
        **exp.params,
    )
    
    return await injector.add_injection(injection)


async def list_predefined_experiments() -> list[dict]:
    """List all predefined experiments."""
    return [
        {
            "name": name,
            "description": exp.description,
            "target": exp.target.value,
            "failure_type": exp.failure_type.value,
            "probability": exp.probability,
            "duration_seconds": exp.duration_seconds,
        }
        for name, exp in PREDEFINED_EXPERIMENTS.items()
    ]


async def list_active_injections() -> list[dict]:
    """List all currently active fault injections."""
    injector = get_injector()
    injections = await injector.list_injections()
    return [
        {
            "injection_id": inj.injection_id,
            "failure_type": inj.failure_type.value,
            "target": inj.target.value,
            "probability": inj.probability,
            "severity": inj.severity,
            "duration_seconds": inj.duration_seconds,
            "created_at": inj.created_at.isoformat(),
            "expires_at": inj.expires_at.isoformat() if inj.expires_at else None,
            "enabled": inj.enabled,
            "metadata": inj.metadata,
        }
        for inj in injections
    ]
