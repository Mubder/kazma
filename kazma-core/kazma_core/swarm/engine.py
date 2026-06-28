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
from kazma_core.swarm.patterns import (
    FanOutConfigurationError,
    PipelineConfigurationError,
    execute_fan_out,
    execute_pipeline,
)
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
    ) -> None:
        self.config = config or SwarmConfig(enabled=True, workers=[])
        self._workers: dict[str, SwarmWorker] = {}
        self._result_aggregator = result_aggregator or ResultAggregator()
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
        started = perf_counter()
        worker.mark_dispatched(prompt)
        try:
            raw_result = await worker.dispatch(prompt, context=context)
        except Exception as exc:
            logger.exception("[SwarmEngine] dispatch failed for worker '%s'", worker.name)
            raw_result = {
                "worker": worker.name,
                "task_id": "",
                "status": "error",
                "output": "",
                "error": str(exc)[:500],
            }

        worker_result = WorkerResult.from_dict(raw_result)
        if worker_result.duration_seconds <= 0:
            worker_result.duration_seconds = perf_counter() - started
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
            aggregated_output=aggregated_output,
            synthesized_output=synthesized_output,
            error=error,
            total_cost=sum(item.cost for item in worker_results),
            total_tokens=sum(item.tokens_used for item in worker_results),
            duration_seconds=duration_seconds,
            metadata=dict(metadata or {}),
        )
        task.result = result
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
