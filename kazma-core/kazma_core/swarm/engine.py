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
from kazma_core.swarm.handoff import HandoffRequest
from kazma_core.swarm.patterns import (
    ConditionalConfigurationError,
    FanOutConfigurationError,
    PipelineConfigurationError,
    execute_conditional,
    execute_fan_out,
    execute_pipeline,
)
from kazma_core.swarm.reliability import (
    BoundedConcurrency,
    CircuitBreaker,
    CircuitBreakerOpenError,
    FallbackChain,
    OutputValidator,
    RetryPolicy,
    TimeoutGuard,
)
from kazma_core.swarm.router import CapabilityRouter, NoCapableWorkersError
from kazma_core.swarm.task import (
    HandoffRecord,
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
        self._timeout_guards: dict[str, TimeoutGuard] = {}
        self._default_timeout_guard = TimeoutGuard()
        self._output_validators: dict[str, OutputValidator] = {}
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
                consult_result = await execute_consult(
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
                worker_results=consult_result.worker_results,
                individual_opinions=consult_result.individual_opinions,
                status=consult_result.status,
                aggregated_output=consult_result.aggregated_output,
                synthesized_output=consult_result.synthesized_output,
                error=consult_result.error,
                duration_seconds=perf_counter() - started,
                metadata=consult_result.metadata,
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

        # Dispatch the primary worker (returns all results including handoffs).
        all_worker_results = await self._dispatch_worker(
            worker,
            task.prompt,
            task.context,
            timeout=task.timeout,
            validation_schema=task.validation_schema,
        )
        worker_result = all_worker_results[-1]

        # Execute fallback chain if the primary failed and a chain is configured.
        if worker_result.status != "success" and task.fallback_chain:
            fallback_result, fallback_all = await self._execute_fallback_chain(
                worker_result,
                task.fallback_chain,
                prompt=task.prompt,
                context=task.context,
                timeout=task.timeout,
                validation_schema=task.validation_schema,
            )
            all_worker_results = fallback_all
            worker_result = fallback_result

        # Determine overall status: if fallbacks ran, the final result's
        # status determines the outcome (intermediate failures are expected).
        if len(all_worker_results) > 1 and task.fallback_chain:
            result_status = (
                "success" if worker_result.status == "success" else "failed"
            )
        else:
            result_status = self._overall_status(all_worker_results)
        aggregated_output = worker_result.output if worker_result.status == "success" else None

        return self._finalize_task(
            task,
            worker_results=all_worker_results,
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

        max_concurrent = self._resolve_max_concurrent(task)
        concurrency = BoundedConcurrency(max_concurrent=max_concurrent)

        async def _dispatch_with_concurrency(name: str) -> WorkerResult:
            async with concurrency:
                return await self._dispatch_worker_by_name(
                    name,
                    task.prompt,
                    dispatch_context,
                    timeout=task.timeout,
                    validation_schema=task.validation_schema,
                )

        worker_results = await asyncio.gather(
            *(_dispatch_with_concurrency(name) for name in target_names)
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

    async def _dispatch_worker_by_name_all(
        self,
        worker_name: str,
        prompt: str,
        context: str | SwarmDispatchContext,
        *,
        timeout: float | None = None,
        validation_schema: dict[str, Any] | None = None,
    ) -> list[WorkerResult]:
        """Dispatch by name and return all results (including handoff chain)."""
        worker = self.get_worker(worker_name)
        if worker is None:
            return [WorkerResult(
                worker=worker_name,
                task_id="",
                status="error",
                output="",
                error=f"Worker '{worker_name}' not found.",
            )]
        return await self._dispatch_worker(
            worker,
            prompt,
            context,
            timeout=timeout,
            validation_schema=validation_schema,
        )

    async def _dispatch_worker_by_name(
        self,
        worker_name: str,
        prompt: str,
        context: str | SwarmDispatchContext,
        *,
        timeout: float | None = None,
        validation_schema: dict[str, Any] | None = None,
        fallback_chain: list[str] | None = None,
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
        results = await self._dispatch_worker(
            worker,
            prompt,
            context,
            timeout=timeout,
            validation_schema=validation_schema,
        )
        result = results[-1]

        # If the primary worker succeeded or no fallback chain, return.
        if result.status == "success" or not fallback_chain:
            return result

        # Execute the fallback chain (returns final result + all attempted).
        final_result, _all_results = await self._execute_fallback_chain(
            result,
            fallback_chain,
            prompt=prompt,
            context=context,
            timeout=timeout,
            validation_schema=validation_schema,
        )
        return final_result

    async def _execute_fallback_chain(
        self,
        primary_result: WorkerResult,
        fallback_chain: list[str],
        *,
        prompt: str,
        context: str | SwarmDispatchContext,
        timeout: float | None = None,
        validation_schema: dict[str, Any] | None = None,
    ) -> tuple[WorkerResult, list[WorkerResult]]:
        """Execute fallback workers sequentially after primary failure.

        Each fallback is dispatched through the full reliability path
        (circuit breaker check, retry, timeout, validation).  The first
        successful fallback ends the chain.  If all fail, a terminal
        failure result is returned with a summary error.

        Returns:
            A tuple of ``(final_result, all_attempted_results)``.  The
            ``all_attempted_results`` list includes the primary result
            followed by each fallback result in order.
        """
        chain = FallbackChain(fallback_workers=fallback_chain)
        task_id = primary_result.task_id

        async def _dispatch_fallback(name: str) -> WorkerResult:
            fallback_worker = self.get_worker(name)
            if fallback_worker is None:
                return WorkerResult(
                    worker=name,
                    task_id=task_id,
                    status="error",
                    output="",
                    error=f"Worker '{name}' not found.",
                )
            results = await self._dispatch_worker(
                fallback_worker,
                prompt,
                context,
                timeout=timeout,
                validation_schema=validation_schema,
            )
            return results[-1]

        final_result = await chain.execute(
            primary_result, dispatch_worker=_dispatch_fallback
        )
        all_results = [primary_result] + list(chain.attempted_results)
        return final_result, all_results

    async def _dispatch_worker(
        self,
        worker: SwarmWorker,
        prompt: str,
        context: str | SwarmDispatchContext,
        *,
        timeout: float | None = None,
        validation_schema: dict[str, Any] | None = None,
    ) -> list[WorkerResult]:
        """Dispatch a worker and return all results (including handoff chain).

        Returns a list of :class:`WorkerResult` objects.  In the normal
        case (no handoff) the list has a single element.  When a handoff
        occurs the list contains the source worker's result followed by
        every result from the target chain.
        """
        breaker = self.get_circuit_breaker(worker.name)
        retry_policy = self.get_retry_policy(worker.name)
        timeout_guard = self.get_timeout_guard(worker.name)
        output_validator = self.get_output_validator(worker.name, validation_schema)

        # Circuit breaker pre-check: reject immediately if open.
        try:
            breaker.check_or_raise(worker.name)
        except CircuitBreakerOpenError as exc:
            logger.warning("[SwarmEngine] %s", exc)
            return [WorkerResult(
                worker=worker.name,
                task_id="",
                status="error",
                output="",
                error=str(exc),
            )]

        # Execute with retry policy.
        started = perf_counter()

        # Mutable container for handoff state captured inside _attempt.
        captured_handoff: dict[str, Any] = {}

        async def _attempt() -> dict[str, Any]:
            worker.mark_dispatched(prompt)
            try:
                raw_result = await timeout_guard.execute(
                    lambda: worker.dispatch(prompt, context=context),
                    timeout=timeout,
                    worker_name=worker.name,
                )
            except HandoffRequest as handoff_req:
                # Capture the handoff request for the outer handler.
                captured_handoff["request"] = handoff_req
                # Return success so the retry loop exits immediately.
                return {
                    "worker": worker.name,
                    "task_id": "",
                    "status": "success",
                    "output": "",
                    "error": None,
                }
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

            # Validate output on success.
            if raw_result.get("status") == "success" and output_validator is not None:
                validation_error = output_validator.validate(
                    raw_result.get("output", "")
                )
                if validation_error is not None:
                    raw_result["status"] = "error"
                    raw_result["error"] = (
                        f"Output validation failed: {validation_error}"
                    )

            return raw_result

        raw_result = await retry_policy.execute_with_retry(
            _attempt, worker_name=worker.name
        )

        # Handle handoff if one was captured during _attempt.
        if captured_handoff.get("request") is not None:
            handoff_req: HandoffRequest = captured_handoff["request"]
            return await self._handle_handoff(
                handoff_req=handoff_req,
                source_worker=worker,
                prompt=prompt,
                context=context,
                timeout=timeout,
                validation_schema=validation_schema,
                started=started,
                breaker=breaker,
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
        return [worker_result]

    async def _handle_handoff(
        self,
        *,
        handoff_req: HandoffRequest,
        source_worker: SwarmWorker,
        prompt: str,
        context: str | SwarmDispatchContext,
        timeout: float | None,
        validation_schema: dict[str, Any] | None,
        started: float,
        breaker: CircuitBreaker,
    ) -> list[WorkerResult]:
        """Process a handoff request from a worker.

        Dispatches to the target worker with accumulated context, records
        a :class:`HandoffRecord`, and returns all results (source + target chain).
        """
        logger.info(
            "[SwarmEngine] worker '%s' handoff to '%s'",
            source_worker.name,
            handoff_req.target_worker,
        )

        # Emit SSE handoff event (logged for observability).
        logger.info(
            "[SSE] swarm.handoff.%s->%s",
            source_worker.name,
            handoff_req.target_worker,
        )

        # Build accumulated context for the target worker.
        handoff_context = self._build_handoff_context(
            original_prompt=prompt,
            original_context=context,
            intermediate_results=handoff_req.context,
            blackboard=(
                context.blackboard
                if isinstance(context, SwarmDispatchContext)
                else None
            ),
        )

        # Dispatch to the target worker (recursive, supports multi-hop chaining).
        target_results = await self._dispatch_worker_by_name_all(
            handoff_req.target_worker,
            handoff_req.task,
            handoff_context,
            timeout=timeout,
            validation_schema=validation_schema,
        )
        target_result = target_results[-1]

        # Record the handoff.
        handoff_record = HandoffRecord(
            from_worker=source_worker.name,
            to_worker=handoff_req.target_worker,
            context_transferred=handoff_req.context,
        )

        # Build source worker result reflecting the handoff outcome.
        duration = perf_counter() - started
        if target_result.status == "success":
            source_result = WorkerResult(
                worker=source_worker.name,
                task_id=target_result.task_id,
                status="success",
                output=target_result.output,
                duration_seconds=duration,
                handoffs=[handoff_record],
            )
            breaker.record_success()
        else:
            source_result = WorkerResult(
                worker=source_worker.name,
                task_id=target_result.task_id,
                status="error",
                output="",
                error=target_result.error,
                duration_seconds=duration,
                handoffs=[handoff_record],
            )
            breaker.record_failure()

        source_worker.mark_completed(source_result.status)
        return [source_result] + target_results

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
    def _build_handoff_context(
        *,
        original_prompt: str,
        original_context: str | SwarmDispatchContext,
        intermediate_results: str,
        blackboard: BlackboardStore | None = None,
    ) -> str | SwarmDispatchContext:
        """Build the accumulated context for a handoff target worker.

        Includes the original prompt, original context, intermediate results
        from the source worker, and optionally a blackboard snapshot.
        """
        sections: list[str] = []
        original_ctx_text = str(original_context).strip()
        if original_ctx_text:
            sections.append(f"Original context:\n{original_ctx_text}")
        sections.append(f"Original prompt:\n{original_prompt}")
        if intermediate_results:
            sections.append(f"Intermediate results:\n{intermediate_results}")

        context_text = "\n\n".join(sections)

        if blackboard is not None:
            return SwarmDispatchContext(
                context_text,
                blackboard=blackboard,
            )
        return context_text

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

    # ------------------------------------------------------------------
    # Reliability layer — timeout guard management
    # ------------------------------------------------------------------

    def get_timeout_guard(
        self,
        worker_name: str,
        task_timeout: float | None = None,
    ) -> TimeoutGuard:
        """Return (or create) the timeout guard for a worker."""
        if task_timeout is not None and task_timeout > 0:
            return TimeoutGuard(default_timeout=task_timeout)
        if worker_name not in self._timeout_guards:
            self._timeout_guards[worker_name] = self._default_timeout_guard
        return self._timeout_guards[worker_name]

    def set_timeout_guard(
        self,
        worker_name: str,
        guard: TimeoutGuard,
    ) -> None:
        """Set a per-worker timeout guard."""
        self._timeout_guards[worker_name] = guard

    # ------------------------------------------------------------------
    # Reliability layer — output validator management
    # ------------------------------------------------------------------

    def get_output_validator(
        self,
        worker_name: str,
        task_schema: dict[str, Any] | None = None,
    ) -> OutputValidator | None:
        """Return the output validator for a worker or task schema.

        Returns ``None`` when no schema is configured (validation skipped).
        """
        if task_schema is not None:
            return OutputValidator(schema=task_schema)
        return self._output_validators.get(worker_name)

    def set_output_validator(
        self,
        worker_name: str,
        validator: OutputValidator,
    ) -> None:
        """Set a per-worker output validator."""
        self._output_validators[worker_name] = validator

    # ------------------------------------------------------------------
    # Reliability layer — bounded concurrency management
    # ------------------------------------------------------------------

    def get_bounded_concurrency(
        self,
        task_max_concurrent: int | None = None,
    ) -> BoundedConcurrency:
        """Return a BoundedConcurrency instance for the given concurrency limit.

        Task-level override takes precedence over the engine default.
        """
        limit = task_max_concurrent or self._resolve_max_concurrent_from_config()
        return BoundedConcurrency(max_concurrent=limit)

    def _resolve_max_concurrent_from_config(self) -> int:
        """Resolve the default concurrency limit from engine config."""
        return max(1, int(getattr(self.config, "max_concurrent", 5) or 5))
