"""Core swarm orchestration engine."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from kazma_core.swarm.aggregator import ResultAggregator
from kazma_core.swarm.blackboard import BlackboardStore, SwarmDispatchContext
from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.consultation import (
    ConsultationConfigurationError,
    execute_consult,
)
from kazma_core.swarm.patterns import (
    ConditionalConfigurationError,
    FanOutConfigurationError,
    PipelineConfigurationError,
    execute_conditional,
    execute_fan_out,
    execute_pipeline,
)
from kazma_core.swarm.reliability import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    RetryPolicy,
)
from kazma_core.swarm.router import CapabilityRouter, NoCapableWorkersError
from kazma_core.swarm.task import (
    SwarmTask,
    TaskResult,
    TaskStatus,
    TaskType,
    WorkerCapabilities,
    WorkerResult,
)
from kazma_core.swarm.worker import InProcessWorker, SwarmWorker, TelegramWorker

logger = logging.getLogger(__name__)

_swarm_engine: SwarmEngine | None = None


def set_swarm_engine(engine: SwarmEngine | None) -> None:
    """Set the module-level swarm engine singleton."""
    global _swarm_engine
    _swarm_engine = engine


def get_swarm_engine() -> SwarmEngine | None:
    """Return the module-level swarm engine singleton."""
    return _swarm_engine


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


class SwarmEngine:
    """Central async orchestrator for swarm workers."""

    def __init__(
        self,
        config: SwarmConfig | None = None,
        *,
        result_aggregator: ResultAggregator | None = None,
        capability_router: CapabilityRouter | None = None,
    ) -> None:
        self.config = config or SwarmConfig(enabled=True, workers=[])
        self._workers: dict[str, SwarmWorker] = {}
        self._task_history: dict[str, SwarmTask] = {}
        self._result_aggregator = result_aggregator or ResultAggregator()
        self._capability_router = capability_router or CapabilityRouter()
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._retry_policies: dict[str, RetryPolicy] = {}
        self._default_retry_policy = RetryPolicy(max_retries=0)
        self._build_workers()

    def _build_workers(self) -> None:
        """Instantiate workers from the configured topology."""
        for worker_config in self.config.workers:
            self.add_worker(worker_config)

    def _create_worker(self, worker_config: WorkerConfig) -> SwarmWorker:
        """Instantiate a concrete worker from its config."""
        if worker_config.type == "in_process":
            return InProcessWorker(
                name=worker_config.name,
                role=worker_config.role,
                model=worker_config.model,
                provider=worker_config.provider,
                capabilities=worker_config.capabilities,
            )
        if worker_config.type == "telegram_bot":
            return TelegramWorker(
                name=worker_config.name,
                profile=worker_config.profile,
                bot_token_env=worker_config.bot_token_env,
                group_chat_id=self.config.group_chat_id,
                role=worker_config.role,
                model=worker_config.model,
                provider=worker_config.provider,
                capabilities=worker_config.capabilities,
            )
        raise ValueError(f"Unknown worker type: '{worker_config.type}'")

    def add_worker(self, worker_config: WorkerConfig) -> SwarmWorker:
        """Register a worker in the unified registry."""
        if worker_config.name in self._workers:
            raise ValueError(f"Worker '{worker_config.name}' already registered.")

        worker = self._create_worker(worker_config)
        self._workers[worker_config.name] = worker
        logger.info(
            "[SwarmEngine] registered worker '%s' (type=%s)",
            worker_config.name,
            worker_config.type,
        )
        return worker

    def remove_worker(self, name: str) -> SwarmWorker:
        """Unregister a worker by name."""
        if name not in self._workers:
            raise KeyError(f"Worker '{name}' not found.")
        worker = self._workers.pop(name)
        logger.info("[SwarmEngine] removed worker '%s'", name)
        return worker

    def get_worker(self, name: str) -> SwarmWorker | None:
        """Return a worker by name."""
        return self._workers.get(name)

    def get_task(self, task_id: str) -> SwarmTask | None:
        """Return a completed task snapshot by identifier."""
        return self._task_history.get(task_id)

    def list_tasks(self, task_type: TaskType | str | None = None) -> list[SwarmTask]:
        """Return completed task snapshots, optionally filtered by task type."""
        normalized_type = (
            task_type.value
            if isinstance(task_type, TaskType)
            else str(task_type).strip().lower()
            if task_type
            else None
        )
        tasks = list(self._task_history.values())
        if normalized_type:
            tasks = [task for task in tasks if task.type.value == normalized_type]
        return sorted(
            tasks,
            key=lambda item: item.completed_at or item.started_at or item.created_at,
            reverse=True,
        )

    @property
    def worker_names(self) -> list[str]:
        """Return registered worker names in sorted order."""
        return sorted(self._workers.keys())

    async def spawn_worker(
        self,
        name: str,
        role: str,
        capabilities: WorkerCapabilities | dict[str, Any] | None,
        model: str = "",
        provider: str = "",
        worker_type: str = "in_process",
    ) -> SwarmWorker:
        """Create and register a worker at runtime."""
        normalized_type = {
            "in-process": "in_process",
            "in_process": "in_process",
            "telegram": "telegram_bot",
            "telegram_bot": "telegram_bot",
        }.get(worker_type, worker_type)
        worker_capabilities = WorkerCapabilities.from_dict(
            capabilities or {"role": role}
        )
        if not worker_capabilities.role:
            worker_capabilities.role = role
        worker_config = WorkerConfig(
            name=name,
            type=normalized_type,
            model=model,
            provider=provider,
            role=role,
            capabilities=worker_capabilities,
        )
        return self.add_worker(worker_config)

    async def dispatch(self, task: SwarmTask) -> TaskResult:
        """Dispatch a swarm task to a single worker."""
        if task.type == TaskType.BROADCAST:
            return await self.broadcast(task)

        started = perf_counter()
        task.started_at = task.started_at or _utc_now_iso()
        task.status = TaskStatus.RUNNING

        # Auto-routing: resolve workers=["auto"] via CapabilityRouter.
        if list(task.workers) == ["auto"]:
            try:
                routed = self._capability_router.route(
                    task,
                    self._build_available_workers_list(),
                )
                task.workers = routed
            except NoCapableWorkersError as exc:
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )

        if task.type == TaskType.PIPELINE:
            try:
                pattern_result = await execute_pipeline(
                    task,
                    dispatch_worker_by_name=self._dispatch_worker_by_name,
                )
            except PipelineConfigurationError as exc:
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )

            return self._finalize_task(
                task,
                worker_results=pattern_result.worker_results,
                status=pattern_result.status,
                aggregated_output=pattern_result.aggregated_output,
                error=pattern_result.error,
                duration_seconds=perf_counter() - started,
                metadata=pattern_result.metadata,
            )

        if task.type == TaskType.FAN_OUT:
            try:
                pattern_result = await execute_fan_out(
                    task,
                    dispatch_worker_by_name=self._dispatch_worker_by_name,
                    aggregator=self._result_aggregator,
                    max_concurrent=self._resolve_max_concurrent(task),
                )
            except FanOutConfigurationError as exc:
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )
            except ValueError as exc:
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )

            return self._finalize_task(
                task,
                worker_results=pattern_result.worker_results,
                status=pattern_result.status,
                aggregated_output=pattern_result.aggregated_output,
                synthesized_output=pattern_result.synthesized_output,
                error=pattern_result.error,
                duration_seconds=perf_counter() - started,
                metadata=pattern_result.metadata,
            )

        if task.type == TaskType.CONSULT:
            try:
                pattern_result = await execute_consult(
                    task,
                    resolve_worker=self.get_worker,
                    dispatch_worker_by_name=self._dispatch_worker_by_name,
                    aggregator=self._result_aggregator,
                    max_concurrent=self._resolve_max_concurrent(task),
                )
            except ConsultationConfigurationError as exc:
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )

            return self._finalize_task(
                task,
                worker_results=pattern_result.worker_results,
                individual_opinions=pattern_result.individual_opinions,
                status=pattern_result.status,
                aggregated_output=pattern_result.aggregated_output,
                synthesized_output=pattern_result.synthesized_output,
                error=pattern_result.error,
                duration_seconds=perf_counter() - started,
                metadata=pattern_result.metadata,
            )

        if task.type == TaskType.CONDITIONAL:
            try:
                pattern_result = await execute_conditional(
                    task,
                    dispatch_worker_by_name=self._dispatch_worker_by_name,
                )
            except ConditionalConfigurationError as exc:
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )

            return self._finalize_task(
                task,
                worker_results=pattern_result.worker_results,
                status=pattern_result.status,
                aggregated_output=pattern_result.aggregated_output,
                error=pattern_result.error,
                duration_seconds=perf_counter() - started,
                metadata=pattern_result.metadata,
            )

        if not task.workers:
            return self._finalize_task(
                task,
                worker_results=[],
                status="failed",
                error="Dispatch requires at least one worker.",
                duration_seconds=perf_counter() - started,
            )

        worker_name = task.workers[0]
        worker = self.get_worker(worker_name)
        if worker is None:
            return self._finalize_task(
                task,
                worker_results=[],
                status="failed",
                error=f"Worker '{worker_name}' not found.",
                duration_seconds=perf_counter() - started,
            )

        worker_result = await self._dispatch_worker(worker, task.prompt, task.context)
        result_status = self._overall_status([worker_result])
        aggregated_output = worker_result.output if worker_result.status == "success" else None

        return self._finalize_task(
            task,
            worker_results=[worker_result],
            status=result_status,
            aggregated_output=aggregated_output,
            error=worker_result.error if result_status != "success" else None,
            duration_seconds=perf_counter() - started,
            metadata=dict(task.metadata) if task.metadata else None,
        )

    async def broadcast(self, task: SwarmTask) -> TaskResult:
        """Dispatch a task to all registered workers or the targeted subset."""
        started = perf_counter()
        task.started_at = task.started_at or _utc_now_iso()
        task.status = TaskStatus.RUNNING
        blackboard = BlackboardStore()
        dispatch_context = self._build_dispatch_context(task, blackboard=blackboard)

        target_names = list(task.workers) if task.workers else list(self._workers.keys())
        if not target_names:
            return self._finalize_task(
                task,
                worker_results=[],
                status="success",
                aggregated_output=None,
                duration_seconds=perf_counter() - started,
                metadata=await self._build_result_metadata(blackboard),
            )

        worker_results = await asyncio.gather(
            *(
                self._dispatch_worker_by_name(name, task.prompt, dispatch_context)
                for name in target_names
            )
        )
        result_status = self._overall_status(worker_results)
        aggregated_output = self._aggregate_outputs(worker_results)
        error = None
        if result_status != "success":
            error_messages = [
                result.error
                for result in worker_results
                if result.error
            ]
            error = "; ".join(error_messages) if error_messages else None

        return self._finalize_task(
            task,
            worker_results=worker_results,
            status=result_status,
            aggregated_output=aggregated_output,
            error=error,
            duration_seconds=perf_counter() - started,
            metadata=await self._build_result_metadata(blackboard),
        )

    async def start_all(self) -> None:
        """Start all registered workers."""
        await asyncio.gather(*(worker.start() for worker in self._workers.values()))
        logger.info("[SwarmEngine] all %d workers started", len(self._workers))

    async def stop_all(self) -> None:
        """Stop all registered workers."""
        await asyncio.gather(*(worker.stop() for worker in self._workers.values()))
        logger.info("[SwarmEngine] all workers stopped")

    async def status(self) -> list[dict[str, Any]]:
        """Return status for all registered workers."""
        return await asyncio.gather(*(worker.status() for worker in self._workers.values()))

    async def _dispatch_worker_by_name(
        self,
        worker_name: str,
        prompt: str,
        context: str | SwarmDispatchContext,
    ) -> WorkerResult:
        worker = self.get_worker(worker_name)
        if worker is None:
            return WorkerResult(
                worker=worker_name,
                task_id="",
                status="error",
                output="",
                error=f"Worker '{worker_name}' not found.",
            )
        return await self._dispatch_worker(worker, prompt, context)

    async def _dispatch_worker(
        self,
        worker: SwarmWorker,
        prompt: str,
        context: str | SwarmDispatchContext,
    ) -> WorkerResult:
        breaker = self.get_circuit_breaker(worker.name)
        retry_policy = self.get_retry_policy(worker.name)

        # Circuit breaker pre-check: reject immediately if open.
        try:
            breaker.check_or_raise(worker.name)
        except CircuitBreakerOpenError as exc:
            logger.warning("[SwarmEngine] %s", exc)
            return WorkerResult(
                worker=worker.name,
                task_id="",
                status="error",
                output="",
                error=str(exc),
            )

        # Execute with retry policy.
        started = perf_counter()

        async def _attempt() -> dict[str, Any]:
            worker.mark_dispatched(prompt)
            try:
                raw_result = await worker.dispatch(prompt, context=context)
            except Exception as exc:
                logger.exception(
                    "[SwarmEngine] dispatch failed for worker '%s'", worker.name
                )
                raw_result = {
                    "worker": worker.name,
                    "task_id": "",
                    "status": "error",
                    "output": "",
                    "error": str(exc)[:500],
                }
            return raw_result

        raw_result = await retry_policy.execute_with_retry(
            _attempt, worker_name=worker.name
        )

        worker_result = WorkerResult.from_dict(raw_result)
        if worker_result.duration_seconds <= 0:
            worker_result.duration_seconds = perf_counter() - started

        # Update circuit breaker based on outcome.
        if worker_result.status == "success":
            breaker.record_success()
        else:
            breaker.record_failure()

        worker.mark_completed(worker_result.status)
        return worker_result

    @staticmethod
    def _aggregate_outputs(worker_results: list[WorkerResult]) -> str | None:
        successful = [result for result in worker_results if result.status == "success" and result.output]
        if not successful:
            return None
        if len(successful) == 1:
            return successful[0].output
        return "\n\n".join(f"[{result.worker}] {result.output}" for result in successful)

    @staticmethod
    def _overall_status(worker_results: list[WorkerResult]) -> str:
        if not worker_results:
            return "success"

        successes = [result for result in worker_results if result.status == "success"]
        timeouts = [result for result in worker_results if result.status == "timeout"]

        if len(successes) == len(worker_results):
            return "success"
        if successes:
            return "partial"
        if timeouts and len(timeouts) == len(worker_results):
            return "timeout"
        return "failed"

    def _finalize_task(
        self,
        task: SwarmTask,
        worker_results: list[WorkerResult],
        status: str,
        duration_seconds: float,
        individual_opinions: list[WorkerResult] | None = None,
        aggregated_output: str | None = None,
        synthesized_output: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskResult:
        task.status = (
            TaskStatus.TIMEOUT
            if status == "timeout"
            else TaskStatus.FAILED
            if status == "failed"
            else TaskStatus.COMPLETED
        )
        task.completed_at = _utc_now_iso()

        result = TaskResult(
            task_id=task.id,
            status=status,
            worker_results=worker_results,
            individual_opinions=list(individual_opinions or []),
            aggregated_output=aggregated_output,
            synthesized_output=synthesized_output,
            error=error,
            total_cost=sum(item.cost for item in worker_results),
            total_tokens=sum(item.tokens_used for item in worker_results),
            duration_seconds=duration_seconds,
            metadata=dict(metadata or {}),
        )
        task.result = result
        self._task_history[task.id] = SwarmTask.from_dict(task.to_dict())
        return result

    @staticmethod
    def _build_dispatch_context(
        task: SwarmTask,
        *,
        blackboard: BlackboardStore | None = None,
    ) -> str | SwarmDispatchContext:
        if blackboard is None:
            return task.context
        return SwarmDispatchContext(
            task.context,
            blackboard=blackboard,
            metadata=task.metadata,
            task_id=task.id,
            task_type=task.type.value,
        )

    @staticmethod
    async def _build_result_metadata(
        blackboard: BlackboardStore | None = None,
    ) -> dict[str, Any]:
        if blackboard is None:
            return {}
        snapshot = await blackboard.snapshot()
        return {
            "blackboard": snapshot,
            "blackboard_snapshot": snapshot,
        }

    def _resolve_max_concurrent(self, task: SwarmTask) -> int:
        """Resolve the fan-out concurrency limit for a task."""
        configured_default = max(1, int(getattr(self.config, "max_concurrent", 5) or 5))
        configured_value = task.metadata.get("max_concurrent", configured_default)
        try:
            resolved = int(configured_value)
        except (TypeError, ValueError):
            return configured_default
        return max(1, resolved)

    def _build_available_workers_list(self) -> list[dict[str, Any]]:
        """Build worker info dicts for the capability router."""
        return [
            {
                "name": worker.name,
                "role": worker.role,
                "capabilities": worker.capabilities,
            }
            for worker in self._workers.values()
        ]

    # ------------------------------------------------------------------
    # Reliability layer — circuit breaker & retry policy management
    # ------------------------------------------------------------------

    def get_circuit_breaker(self, worker_name: str) -> CircuitBreaker:
        """Return (or create) the circuit breaker for a worker."""
        if worker_name not in self._circuit_breakers:
            self._circuit_breakers[worker_name] = CircuitBreaker()
        return self._circuit_breakers[worker_name]

    def reset_circuit_breaker(self, worker_name: str) -> CircuitBreaker:
        """Manually reset a worker's circuit breaker to closed state."""
        breaker = self.get_circuit_breaker(worker_name)
        breaker.reset()
        logger.info("[SwarmEngine] circuit breaker reset for worker '%s'", worker_name)
        return breaker

    def get_retry_policy(self, worker_name: str) -> RetryPolicy:
        """Return the retry policy for a worker (or the default)."""
        return self._retry_policies.get(worker_name, self._default_retry_policy)

    def set_retry_policy(
        self,
        worker_name: str,
        policy: RetryPolicy,
    ) -> None:
        """Set a per-worker retry policy."""
        self._retry_policies[worker_name] = policy

    def set_circuit_breaker_config(
        self,
        worker_name: str,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
    ) -> CircuitBreaker:
        """Create or reconfigure a per-worker circuit breaker."""
        self._circuit_breakers[worker_name] = CircuitBreaker(
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )
        return self._circuit_breakers[worker_name]

    def get_circuit_breaker_status(self, worker_name: str) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of a worker's circuit breaker."""
        breaker = self.get_circuit_breaker(worker_name)
        return breaker.to_dict()

    def get_all_circuit_breaker_status(self) -> dict[str, dict[str, Any]]:
        """Return circuit breaker status for all registered workers."""
        return {
            name: self.get_circuit_breaker(name).to_dict()
            for name in self._workers
        }
