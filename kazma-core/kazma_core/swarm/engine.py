"""Core swarm orchestration engine."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from kazma_core.swarm.aggregator import ResultAggregator
from kazma_core.swarm.blackboard import BlackboardStore, SwarmDispatchContext
from kazma_core.swarm.checkpoint import HITLCheckpoint, HITLCheckpointHandler
from kazma_core.swarm.checkpoint_manager import CheckpointManager
from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.consultation import (
    ConsultationConfigurationError,
    execute_consult,
)
from kazma_core.swarm.handoff import HandoffRequest
from kazma_core.swarm.metrics import MetricsCollector
from kazma_core.swarm.patterns import (
    ConditionalConfigurationError,
    FanOutConfigurationError,
    PatternExecution,
    PipelineConfigurationError,
    execute_conditional,
    execute_fan_out,
    execute_pipeline,
    resume_pipeline,
)
from kazma_core.swarm.phonebook import WorkerPhonebook
from kazma_core.swarm.reliability import (
    BoundedConcurrency,
    CircuitBreaker,
    CircuitBreakerOpenError,
    FallbackChain,
    OutputValidator,
    RetryPolicy,
    TimeoutGuard,
)
from kazma_core.swarm.reliability_registry import ReliabilityRegistry
from kazma_core.routing_engine import UnifiedRouter, NoCapableWorkersError
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
from kazma_core.swarm.tracing import TracingEmitter
from kazma_core.swarm.worker import InProcessWorker, SwarmWorker

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
        task_store: TaskStore | None = None,
        metrics_collector: MetricsCollector | None = None,
        tracing_emitter: TracingEmitter | None = None,
    ) -> None:
        self.config = config or SwarmConfig(enabled=True, workers=[])
        self._workers: dict[str, SwarmWorker] = {}
        self._task_history: dict[str, SwarmTask] = {}
        self._active_tasks: dict[str, SwarmTask] = {}  # in-flight tasks
        self._task_handles: dict[str, asyncio.Task] = {}  # asyncio handles for cancel
        # threading.Lock: usable from both sync (_finalize_task) and async paths
        self._task_lock = threading.Lock()  # protects _task_history mutations
        self._max_history = 500  # LRU cap to prevent unbounded memory growth
        self._result_aggregator = result_aggregator or ResultAggregator()
        from kazma_core.routing_engine import UnifiedRouter
        self._routing_engine = UnifiedRouter()
        # Reliability config delegated to ReliabilityRegistry (P2-1 refactor).
        self._reliability = ReliabilityRegistry(
            worker_names=lambda: list(self._workers.keys()),
        )
        self._checkpoint_handler = HITLCheckpointHandler()
        self._task_store = task_store
        self._metrics_collector = metrics_collector or MetricsCollector(task_store=task_store)
        self._tracing_emitter = tracing_emitter or TracingEmitter()
        self._autoscaler = None  # lazily initialized
        self._phonebook = WorkerPhonebook()
        self._checkpoint_mgr = CheckpointManager(
            checkpoint_handler=self._checkpoint_handler,
            task_store=task_store,
            task_history=self._task_history,
            max_history=self._max_history,
        )
        # Register reject callback so the checkpoint timeout auto-reject
        # can call back into the engine without a circular reference.
        self._checkpoint_mgr.set_reject_callback(self.reject_checkpoint)
        self._sse_bus: Any = None
        self._build_workers()

    def get_autoscaler(self):
        """Return the AutoScaler, lazily initializing it."""
        if self._autoscaler is None:
            try:
                from kazma_core.swarm.autoscaler import AutoScaler
                self._autoscaler = AutoScaler(self)
                self._autoscaler.load_templates()
            except Exception as exc:
                logger.warning("[SwarmEngine] AutoScaler initialization failed: %s", exc)
                try:
                    from kazma_core.observability import AlertDispatcher
                    import asyncio
                    loop = asyncio.get_running_loop()
                    loop.create_task(AlertDispatcher.trigger_system_alert(
                        subsystem="AutoScaler",
                        status="DEGRADED",
                        message=f"AutoScaler initialization failed: {exc}"
                    ))
                except Exception as alert_exc:
                    logger.debug(
                        "[SwarmEngine] Failed to dispatch degraded alert for AutoScaler: %s",
                        alert_exc,
                        exc_info=True,
                    )
        return self._autoscaler

    def _build_workers(self) -> None:
        """Instantiate workers from the configured topology."""
        for worker_config in self.config.workers:
            self.add_worker(worker_config)

    def _create_worker(self, worker_config: WorkerConfig) -> SwarmWorker:
        """Instantiate a concrete worker from its config.

        ``telegram_bot`` is accepted for backward compatibility with
        persisted configs but now resolves to an :class:`InProcessWorker`
        (the legacy ``TelegramWorker`` subprocess path was vestigial and is
        removed).
        """
        if worker_config.type in ("in_process", "telegram_bot"):
            return InProcessWorker(
                name=worker_config.name,
                role=worker_config.role,
                model=worker_config.model,
                provider=worker_config.provider,
                capabilities=worker_config.capabilities,
                system_prompt=getattr(worker_config, "system_prompt", ""),
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
        """Unregister a worker by name and clean up reliability state."""
        if name not in self._workers:
            raise KeyError(f"Worker '{name}' not found.")
        worker = self._workers.pop(name)
        # Clean up reliability-layer state to prevent memory leaks.
        self._reliability.cleanup_worker(name)
        logger.info("[SwarmEngine] removed worker '%s'", name)
        return worker

    def get_worker(self, name: str) -> SwarmWorker | None:
        """Return a worker by name."""
        return self._workers.get(name)

    def get_task(self, task_id: str) -> SwarmTask | None:
        """Return a task by id from the history."""
        return self._task_history.get(task_id)

    def list_active_tasks(self) -> list[SwarmTask]:
        """Return all in-flight (running or paused) tasks."""
        return list(self._active_tasks.values())

    def get_active_task(self, task_id: str) -> SwarmTask | None:
        """Public accessor for a specific active task (avoids direct _active_tasks)."""
        return self._active_tasks.get(task_id)

    def get_task_handle(self, task_id: str) -> Any | None:
        """Public accessor for task handle (avoids direct _task_handles access from UI)."""
        return self._task_handles.get(task_id)

    def register_task_handle(self, task_id: str, handle: Any) -> None:
        """Register a task handle publicly (used by swarm panel for SSE task tracking)."""
        self._task_handles[task_id] = handle

    def unregister_task_handle(self, task_id: str) -> None:
        """Remove a task handle publicly."""
        self._task_handles.pop(task_id, None)

    def list_workers(self) -> list:
        """Public accessor returning worker objects. Preferred over direct _workers access from UI layers."""
        # Prefer phonebook when possible; fallback to internal for compatibility
        try:
            if hasattr(self, "_phonebook") and self._phonebook:
                return list(self._phonebook._entries.values())  # internal but stable for now
        except AttributeError as attr_exc:
            logger.debug(
                "[SwarmEngine] Phonebook attribute lookup failed: %s",
                attr_exc,
                exc_info=True,
            )
        return list(getattr(self, "_workers", {}).values())

    def set_sse_bus(self, bus: Any) -> None:
        """Register an SSEEventBus for task/worker lifecycle events.

        This is the clean replacement for previous monkey-patching of
        dispatch/_finalize_task/_dispatch_worker.
        """
        self._sse_bus = bus

    def _emit_sse(self, task_id: str, event: str, data: dict[str, Any]) -> None:
        """Internal helper to emit via registered SSE bus (if any)."""
        if self._sse_bus is None:
            return
        try:
            self._sse_bus.emit(task_id, event, data)
        except Exception as sse_exc:
            logger.debug(
                "[SwarmEngine] SSE emit failed for %s:%s: %s",
                task_id,
                event,
                sse_exc,
                exc_info=True,
            )

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
        self._active_tasks[task.id] = task  # track in-flight

        self._emit_sse(task.id, "task_started", {
            "task_id": task.id,
            "workers": list(task.workers) if task.workers else [],
        })

        # Start a root tracing span for this task.
        task_span = self._tracing_emitter.start_task_span(
            task_id=task.id,
            task_type=task.type.value,
            workers=list(task.workers),
        )

        try:
            return await self._dispatch_inner(task, started, task_span)
        except asyncio.CancelledError:
            # Task was cancelled via cancel_task() — finalize as cancelled.
            self._tracing_emitter.end_span(task_span, status="cancelled")
            return self._finalize_task(
                task,
                worker_results=[],
                status="cancelled",
                error="Cancelled by user",
                duration_seconds=perf_counter() - started,
            )
        except Exception as exc:
            # Catch-all: any unhandled exception finalizes the task as
            # failed and closes the tracing span so neither leaks.
            logger.exception("[SwarmEngine] Unhandled error in dispatch for task '%s'", task.id)
            self._tracing_emitter.record_exception(task_span, exc)
            self._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
            return self._finalize_task(
                task,
                worker_results=[],
                status="failed",
                error=str(exc),
                duration_seconds=perf_counter() - started,
            )

    async def _dispatch_inner(self, task: SwarmTask, started: float, task_span: Any) -> TaskResult:
        """Inner dispatch logic, wrapped by dispatch() for catch-all safety."""

        # Auto-routing: resolve workers=["auto"] via Polymorphic Routing Engine.
        if list(task.workers) == ["auto"]:
            try:
                routed = await self._routing_engine.route(
                    task,
                    self._build_available_workers_list(),
                )
                task.workers = routed
            except NoCapableWorkersError as exc:
                # Try auto-scaling before giving up
                scaler = self.get_autoscaler()
                spawned = None
                if scaler is not None:
                    spawned = scaler.maybe_scale(task.prompt)
                if spawned is not None:
                    task.workers = [spawned.name]
                    logger.info("[SwarmEngine] Auto-scaled worker '%s' for task", spawned.name)
                    # Fall through to normal dispatch with the new worker
                else:
                    self._tracing_emitter.record_exception(task_span, exc)
                    self._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
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
                self._tracing_emitter.record_exception(task_span, exc)
                self._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )

            # HITL checkpoint: pipeline paused at a checkpoint step.
            if pattern_result.status == "paused":
                self._tracing_emitter.end_span(task_span, status="ok")
                return self._handle_pipeline_checkpoint(
                    task=task,
                    pattern_result=pattern_result,
                    started=started,
                )

            self._tracing_emitter.end_span(task_span, status="ok")
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
                self._tracing_emitter.record_exception(task_span, exc)
                self._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )
            except ValueError as exc:
                self._tracing_emitter.record_exception(task_span, exc)
                self._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )

            # Emit aggregate span if an aggregation strategy was used.
            aggregation = task.aggregation or "collect"
            if aggregation != "collect":
                agg_span = self._tracing_emitter.start_aggregate_span(
                    task_span.trace_id, aggregation
                )
                agg_span.set_attribute("swarm.aggregation.count", len(pattern_result.worker_results))
                self._tracing_emitter.end_span(agg_span, status="ok")

            self._tracing_emitter.end_span(task_span, status="ok")
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
                self._tracing_emitter.record_exception(task_span, exc)
                self._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )

            # Emit synthesize span when synthesis was performed.
            if consult_result.synthesized_output:
                synth_span = self._tracing_emitter.start_synthesize_span(
                    task_span.trace_id,
                )
                synth_span.set_attribute(
                    "swarm.synthesize.opinion_count",
                    len(consult_result.individual_opinions),
                )
                self._tracing_emitter.end_span(synth_span, status="ok")

            self._tracing_emitter.end_span(task_span, status="ok")
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
                self._tracing_emitter.record_exception(task_span, exc)
                self._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )

            self._tracing_emitter.end_span(task_span, status="ok")
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
            self._tracing_emitter.end_span(task_span, status="error", status_msg="Dispatch requires at least one worker.")
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
            msg = f"Worker '{worker_name}' not found."
            self._tracing_emitter.end_span(task_span, status="error", status_msg=msg)
            return self._finalize_task(
                task,
                worker_results=[],
                status="failed",
                error=msg,
                duration_seconds=perf_counter() - started,
            )

        # Build a blackboard-wrapped dispatch context so that single-dispatch
        # tasks get the same SwarmDispatchContext treatment (system prompt,
        # blackboard, metadata) as broadcast/fanout/pipeline.
        dispatch_blackboard = BlackboardStore()
        dispatch_context = self._build_dispatch_context(
            task, blackboard=dispatch_blackboard
        )

        # Dispatch the primary worker (returns all results including handoffs).
        all_worker_results = await self._dispatch_worker(
            worker,
            task.prompt,
            dispatch_context,
            timeout=task.timeout,
            validation_schema=task.validation_schema,
            trace_id=task_span.trace_id,
        )
        worker_result = all_worker_results[-1]

        # Execute fallback chain if the primary failed and a chain is configured.
        if worker_result.status != "success" and task.fallback_chain:
            fallback_result, fallback_all = await self._execute_fallback_chain(
                worker_result,
                task.fallback_chain,
                prompt=task.prompt,
                context=dispatch_context,
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

        span_status = "ok" if result_status in ("success", "partial") else "error"
        self._tracing_emitter.end_span(task_span, status=span_status)

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
        self._active_tasks[task.id] = task  # track in-flight

        # Start a root tracing span for this broadcast task.
        task_span = self._tracing_emitter.start_task_span(
            task_id=task.id,
            task_type=task.type.value,
            workers=list(task.workers),
        )

        blackboard = BlackboardStore()
        dispatch_context = self._build_dispatch_context(task, blackboard=blackboard)

        target_names = list(task.workers) if task.workers else list(self._workers.keys())
        if not target_names:
            self._tracing_emitter.end_span(task_span, status="ok")
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

        span_status = "ok" if result_status in ("success", "partial") else "error"
        self._tracing_emitter.end_span(task_span, status=span_status)

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
        results = await asyncio.gather(
            *(worker.start() for worker in self._workers.values()),
            return_exceptions=True,
        )
        failed = sum(1 for r in results if isinstance(r, Exception))
        if failed:
            logger.warning("[SwarmEngine] %d/%d workers failed to start", failed, len(results))
        else:
            logger.info("[SwarmEngine] all %d workers started", len(self._workers))

    async def stop_all(self) -> None:
        """Stop all registered workers."""
        results = await asyncio.gather(
            *(worker.stop() for worker in self._workers.values()),
            return_exceptions=True,
        )
        failed = sum(1 for r in results if isinstance(r, Exception))
        if failed:
            logger.warning("[SwarmEngine] %d/%d workers failed to stop cleanly", failed, len(results))
        else:
            logger.info("[SwarmEngine] all workers stopped")

    async def start_worker(self, name: str) -> bool:
        """Start a single worker by name.

        Returns True if the worker was started (or already running).
        Returns False if the worker is not found.
        """
        worker = self._workers.get(name)
        if worker is None:
            logger.warning("[SwarmEngine] start_worker: '%s' not found", name)
            return False
        if getattr(worker, "_running", False):
            logger.debug("[SwarmEngine] worker '%s' already running", name)
            return True
        await worker.start()
        logger.info("[SwarmEngine] worker '%s' started", name)
        return True

    async def stop_worker(self, name: str) -> bool:
        """Stop a single worker by name.

        Returns True if the worker was stopped (or already stopped).
        Returns False if the worker is not found.
        """
        worker = self._workers.get(name)
        if worker is None:
            logger.warning("[SwarmEngine] stop_worker: '%s' not found", name)
            return False
        if not getattr(worker, "_running", True):
            logger.debug("[SwarmEngine] worker '%s' already stopped", name)
            return True
        await worker.stop()
        logger.info("[SwarmEngine] worker '%s' stopped", name)
        return True

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task by task_id.

        Cancels the asyncio task handle if one is stored, then finalizes
        the SwarmTask with CANCELLED status.

        Returns True if the task was found and cancelled, False if not
        found or already terminal.
        """
        # Check if the task is active
        if task_id not in self._active_tasks:
            logger.warning("[SwarmEngine] cancel_task: '%s' not in active tasks", task_id)
            return False

        task = self._active_tasks[task_id]

        # Cancel the asyncio handle if we have one
        handle = self._task_handles.get(task_id)
        if handle is not None and not handle.done():
            handle.cancel()
            logger.info("[SwarmEngine] cancelled asyncio handle for task '%s'", task_id)

        # Finalize with cancelled status
        self._finalize_task(
            task=task,
            status="cancelled",
            worker_results=[],
            error="Cancelled by user",
            duration_seconds=0.0,
        )
        logger.info("[SwarmEngine] task '%s' cancelled", task_id)
        return True

    async def retry_task(self, task_id: str) -> SwarmTask | None:
        """Retry a failed/timeout/cancelled task by creating a fresh dispatch.

        Builds a new SwarmTask with a new ID, copying the prompt/type/
        workers/context from the original. The original task's ID is
        recorded in metadata['retry_of'] for lineage.

        Returns the new SwarmTask, or None if the original was not found.
        """
        # Find the original task — check history first, then active
        original = self._task_history.get(task_id)
        if original is None:
            original = self._active_tasks.get(task_id)
        if original is None:
            # Check the TaskStore
            if self._task_store is not None:
                original = self._task_store.get_task(task_id)
        if original is None:
            logger.warning("[SwarmEngine] retry_task: '%s' not found", task_id)
            return None

        # Build a fresh task from the original's core fields
        new_metadata = dict(original.metadata or {})
        new_metadata["retry_of"] = task_id

        new_task = SwarmTask(
            prompt=original.prompt,
            type=original.type,
            context=original.context,
            workers=list(original.workers),
            timeout=original.timeout,
            fallback_chain=list(original.fallback_chain) if original.fallback_chain else None,
            metadata=new_metadata,
        )
        logger.info("[SwarmEngine] retrying task '%s' as '%s'", task_id, new_task.id)
        return new_task

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
        trace_id: str | None = None,
        _visited: dict[str, int] | None = None,
        _depth: int = 0,
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
            trace_id=trace_id,
            _visited=_visited,
            _depth=_depth,
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
        trace_id: str | None = None,
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
            trace_id=trace_id,
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
        trace_id: str | None = None,
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
                trace_id=trace_id,
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
        trace_id: str | None = None,
        _visited: dict[str, int] | None = None,
        _depth: int = 0,
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

        # Emit a dispatch span if tracing is active and a trace_id is available.
        dispatch_span = None
        if trace_id:
            dispatch_span = self._tracing_emitter.start_dispatch_span(
                trace_id, worker.name
            )

        # Circuit breaker pre-check: reject immediately if open.
        try:
            breaker.check_or_raise(worker.name)
        except CircuitBreakerOpenError as exc:
            logger.warning("[SwarmEngine] %s", exc)
            if dispatch_span:
                self._tracing_emitter.record_exception(dispatch_span, exc)
                self._tracing_emitter.end_span(dispatch_span, status="error", status_msg=str(exc))
            result = WorkerResult(
                worker=worker.name,
                task_id="",
                status="error",
                output="",
                error=str(exc),
            )
            self._metrics_collector.record_worker_result(result)
            return [result]

        # Execute with retry policy.
        started = perf_counter()

        # Emit worker_started (for SSE / observers)
        worker_name = worker.name if hasattr(worker, "name") else str(worker)
        task_id_for_sse = ""
        if isinstance(context, SwarmDispatchContext):
            task_id_for_sse = context.task_id or ""
        self._emit_sse(
            task_id_for_sse,
            "worker_started",
            {"worker": worker_name, "step": 0},
        )

        # Mutable container for handoff state captured inside _attempt.
        captured_handoff: dict[str, Any] = {}

        async def _attempt() -> dict[str, Any]:
            worker.mark_dispatched(prompt)
            # Record activity for auto-scaler reaping
            if self._autoscaler is not None:
                self._autoscaler.record_activity(worker.name)
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
            # End the dispatch span before handoff.
            if dispatch_span:
                self._tracing_emitter.end_span(dispatch_span, status="ok")
            return await self._handle_handoff(
                handoff_req=handoff_req,
                source_worker=worker,
                prompt=prompt,
                context=context,
                timeout=timeout,
                validation_schema=validation_schema,
                started=started,
                breaker=breaker,
                trace_id=trace_id,
                _visited=_visited,
                _depth=_depth + 1,
            )

        worker_result = WorkerResult.from_dict(raw_result)
        if worker_result.duration_seconds <= 0:
            worker_result.duration_seconds = perf_counter() - started

        # End the dispatch span.
        if dispatch_span:
            span_status = "ok" if worker_result.status == "success" else "error"
            if worker_result.error:
                dispatch_span.set_attribute("error.message", worker_result.error[:200])
            self._tracing_emitter.end_span(dispatch_span, status=span_status)

        # Update circuit breaker based on outcome.
        if worker_result.status == "success":
            breaker.record_success()
        else:
            breaker.record_failure()

        worker.mark_completed(worker_result.status)

        # Emit worker_completed for observers (SSE etc.)
        output_preview = ""
        task_id_for_sse = ""
        if isinstance(context, SwarmDispatchContext):
            task_id_for_sse = context.task_id or ""
        if worker_result.output:
            output_preview = str(worker_result.output)[:200]
        self._emit_sse(
            task_id_for_sse,
            "worker_completed",
            {
                "worker": worker.name if hasattr(worker, "name") else str(worker),
                "status": worker_result.status,
                "output_preview": output_preview,
            },
        )
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
        trace_id: str | None = None,
        _visited: dict[str, int] | None = None,
        _depth: int = 0,
    ) -> list[WorkerResult]:
        """Process a handoff request from a worker.

        Dispatches to the target worker with accumulated context, records
        a :class:`HandoffRecord`, and returns all results (source + target chain).

        Includes cycle detection: tracks visit counts per worker. A worker
        may be revisited up to ``_MAX_VISITS`` times (allowing return
        handoffs A->B->A) before the handoff is aborted. The depth limit
        is the hard backstop against infinite recursion.
        """
        # ── Cycle detection ──────────────────────────────────────
        _MAX_HANDOFF_DEPTH = 5
        _MAX_VISITS = 2  # allows A->B->A return handoff, blocks ping-pong
        if _visited is None:
            _visited = {}
        _visited[source_worker.name] = _visited.get(source_worker.name, 0) + 1

        if _depth >= _MAX_HANDOFF_DEPTH:
            logger.error(
                "[SwarmEngine] Handoff chain too deep (%d) — aborting to prevent infinite recursion",
                _depth,
            )
            return [WorkerResult(
                worker=source_worker.name,
                task_id="",
                status="error",
                output="",
                error=f"Handoff chain exceeded max depth ({_MAX_HANDOFF_DEPTH}). Possible cycle.",
                duration_seconds=perf_counter() - started,
            )]

        target_visits = _visited.get(handoff_req.target_worker, 0)
        if target_visits >= _MAX_VISITS:
            logger.error(
                "[SwarmEngine] Handoff cycle detected: %s -> %s (visited %dx, max %d)",
                source_worker.name,
                handoff_req.target_worker,
                target_visits,
                _MAX_VISITS,
            )
            return [WorkerResult(
                worker=source_worker.name,
                task_id="",
                status="error",
                output="",
                error=f"Handoff cycle detected: {source_worker.name} -> {handoff_req.target_worker}",
                duration_seconds=perf_counter() - started,
            )]

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

        # Emit a tracing span for the handoff.
        if trace_id:
            handoff_span = self._tracing_emitter.start_handoff_span(
                trace_id,
                from_worker=source_worker.name,
                to_worker=handoff_req.target_worker,
            )
            handoff_span.set_attribute("swarm.handoff.task", handoff_req.task[:200])
            self._tracing_emitter.end_span(handoff_span, status="ok")

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
            _visited=_visited,
            _depth=_depth,
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
            else TaskStatus.PAUSED
            if status == "paused"
            else TaskStatus.CANCELLED
            if status == "cancelled"
            else TaskStatus.COMPLETED
        )
        task.completed_at = _utc_now_iso()

        # Remove from in-flight tracking (unless paused — paused tasks
        # stay visible until resumed or rejected).
        if task.status != TaskStatus.PAUSED:
            self._active_tasks.pop(task.id, None)
            self._task_handles.pop(task.id, None)

        # Record per-worker metrics for any worker results not yet recorded.
        for wr in worker_results:
            self._metrics_collector.record_worker_result(wr)

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
        with self._task_lock:
            self._task_history[task.id] = SwarmTask.from_dict(task.to_dict())
            if len(self._task_history) > self._max_history:
                excess = len(self._task_history) - self._max_history
                for old_key in list(self._task_history.keys())[:excess]:
                    self._task_history.pop(old_key, None)

        # Emit lifecycle event for observers (SSE etc.)
        if status == "paused":
            checkpoint_data = (metadata or {}).get("checkpoint", {})
            self._emit_sse(
                task.id,
                "checkpoint",
                {
                    "step": checkpoint_data.get("step", 0),
                    "needs_approval": True,
                    "output_preview": checkpoint_data.get("output_preview", ""),
                },
            )
        else:
            self._emit_sse(
                task.id,
                "task_completed",
                {"task_id": task.id, "result": result.to_dict()},
            )

        # Persist to SQLite when a task store is configured.
        if self._task_store is not None:
            try:
                self._task_store.persist_task(task)
            except Exception:
                logger.exception(
                    "[SwarmEngine] failed to persist task '%s'", task.id
                )

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
        system_prompt: str = "",
    ) -> str | SwarmDispatchContext:
        """Build the context passed to worker.dispatch().

        When a *blackboard* is provided (broadcast/fanout/pipeline), a
        :class:`SwarmDispatchContext` wrapping the blackboard is returned.
        The *system_prompt* (if any) is propagated so workers receive
        task-level or stage-level guidance in addition to their own
        configured system prompt.

        When no blackboard is provided, the raw context string is returned
        (single-dispatch path).  Workers fall back to their own
        ``self.system_prompt`` in that case.
        """
        if blackboard is None:
            return task.context
        return SwarmDispatchContext(
            task.context,
            blackboard=blackboard,
            metadata=task.metadata,
            task_id=task.id,
            task_type=task.type.value,
            system_prompt=system_prompt,
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
    # HITL Checkpoint management — state delegated to CheckpointManager
    # (P2-1 refactor). Resume/finalize logic stays on the engine.
    # ------------------------------------------------------------------

    def _handle_pipeline_checkpoint(
        self, *, task: SwarmTask, pattern_result: PatternExecution, started: float,
    ) -> TaskResult:
        """Handle a pipeline that paused at an HITL checkpoint."""
        return self._checkpoint_mgr.handle_pipeline_checkpoint(
            task=task, pattern_result=pattern_result, started=started,
        )

    def get_checkpoint_info(self, task_id: str) -> HITLCheckpoint | None:
        """Return checkpoint info for a paused task, or ``None``."""
        return self._checkpoint_mgr.get_checkpoint_info(task_id)

    def restore_paused_tasks(self) -> list[SwarmTask]:
        """Load paused tasks from SQLite so they can be resumed."""
        return self._checkpoint_mgr.restore_paused_tasks()

    @property
    def task_store(self) -> TaskStore | None:
        """Return the configured task store, or ``None``."""
        return self._task_store

    @property
    def metrics_collector(self) -> MetricsCollector:
        """Return the metrics collector."""
        return self._metrics_collector

    @property
    def tracing_emitter(self) -> TracingEmitter:
        """Return the tracing emitter."""
        return self._tracing_emitter

    async def approve_checkpoint(self, task_id: str) -> TaskResult | None:
        """Approve a paused HITL checkpoint and resume the pipeline.

        Returns the final ``TaskResult`` after the remaining pipeline steps
        complete, or ``None`` if no active checkpoint exists for *task_id*.
        """
        entry = self._checkpoint_handler._paused.get(task_id)
        if entry is None:
            return None

        checkpoint = entry.checkpoint
        task = entry.task
        worker_results = list(entry.worker_results)
        blackboard_data = dict(entry.blackboard_data)

        # Cancel the timeout task if present.
        if entry.timeout_task is not None and not entry.timeout_task.done():
            entry.timeout_task.cancel()
            entry.timeout_task = None

        checkpoint.status = "approved"
        checkpoint.needs_approval = False
        logger.info(
            "[SwarmEngine] checkpoint approved for pipeline '%s' at step %d, resuming",
            task_id,
            checkpoint.step,
        )

        # Remove from paused state before resuming.
        self._checkpoint_mgr.pop_paused_entry(task_id)

        # Resume from the next step.
        next_step = checkpoint.step + 1
        started = perf_counter()

        try:
            pattern_result = await resume_pipeline(
                task,
                starting_step=next_step,
                worker_results=worker_results,
                blackboard_data=blackboard_data,
                dispatch_worker_by_name=self._dispatch_worker_by_name,
            )
        except PipelineConfigurationError as exc:
            return self._finalize_task(
                task,
                worker_results=worker_results,
                status="failed",
                error=str(exc),
                duration_seconds=perf_counter() - started,
            )

        # If the resumed pipeline hit another checkpoint, handle it.
        if pattern_result.status == "paused":
            return self._handle_pipeline_checkpoint(
                task=task,
                pattern_result=pattern_result,
                started=started,
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

    async def reject_checkpoint(
        self,
        task_id: str,
        reason: str = "Checkpoint rejected by user",
    ) -> TaskResult | None:
        """Reject a paused HITL checkpoint and abort the pipeline.

        Returns the finalized ``TaskResult`` with ``status="failed"``, or
        ``None`` if no active checkpoint exists for *task_id*.
        """
        result = await self._checkpoint_handler.reject(task_id, reason=reason)
        if result is not None:
            # Update task history with the failed result.
            with self._task_lock:
                task = self._task_history.get(task_id)
                if task is not None:
                    task.status = TaskStatus.FAILED
                    task.result = result
                    self._task_history[task_id] = SwarmTask.from_dict(task.to_dict())
                    # Persist to SQLite.
                    if self._task_store is not None:
                        try:
                            self._task_store.persist_task(task)
                        except Exception:
                            logger.exception(
                                "[SwarmEngine] failed to persist rejected task '%s'",
                                task_id,
                            )
                else:
                    # If task not in history, store the result directly.
                    self._checkpoint_mgr.complete_pipeline(task_id, result)
        return result

    # ------------------------------------------------------------------
    # Reliability layer — delegated to ReliabilityRegistry (P2-1 refactor)
    # ------------------------------------------------------------------

    def get_circuit_breaker(self, worker_name: str) -> CircuitBreaker:
        """Return (or create) the circuit breaker for a worker."""
        return self._reliability.get_circuit_breaker(worker_name)

    def reset_circuit_breaker(self, worker_name: str) -> CircuitBreaker:
        """Manually reset a worker's circuit breaker to closed state."""
        return self._reliability.reset_circuit_breaker(worker_name)

    def get_retry_policy(self, worker_name: str) -> RetryPolicy:
        """Return the retry policy for a worker (or the default)."""
        return self._reliability.get_retry_policy(worker_name)

    def set_retry_policy(self, worker_name: str, policy: RetryPolicy) -> None:
        """Set a per-worker retry policy."""
        self._reliability.set_retry_policy(worker_name, policy)

    def set_circuit_breaker_config(
        self, worker_name: str, *,
        failure_threshold: int = 5, cooldown_seconds: float = 60.0,
    ) -> CircuitBreaker:
        """Create or reconfigure a per-worker circuit breaker."""
        return self._reliability.set_circuit_breaker_config(
            worker_name, failure_threshold=failure_threshold, cooldown_seconds=cooldown_seconds,
        )

    def get_circuit_breaker_status(self, worker_name: str) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of a worker's circuit breaker."""
        return self._reliability.get_circuit_breaker_status(worker_name)

    def get_all_circuit_breaker_status(self) -> dict[str, dict[str, Any]]:
        """Return circuit breaker status for all registered workers."""
        return self._reliability.get_all_circuit_breaker_status()

    def get_timeout_guard(
        self, worker_name: str, task_timeout: float | None = None,
    ) -> TimeoutGuard:
        """Return (or create) the timeout guard for a worker."""
        return self._reliability.get_timeout_guard(worker_name, task_timeout)

    def set_timeout_guard(self, worker_name: str, guard: TimeoutGuard) -> None:
        """Set a per-worker timeout guard."""
        self._reliability.set_timeout_guard(worker_name, guard)

    def get_output_validator(
        self, worker_name: str, task_schema: dict[str, Any] | None = None,
    ) -> OutputValidator | None:
        """Return the output validator for a worker or task schema."""
        return self._reliability.get_output_validator(worker_name, task_schema)

    def set_output_validator(self, worker_name: str, validator: OutputValidator) -> None:
        """Set a per-worker output validator."""
        self._reliability.set_output_validator(worker_name, validator)

    def get_bounded_concurrency(self, task_max_concurrent: int | None = None) -> BoundedConcurrency:
        """Return a BoundedConcurrency instance for the given concurrency limit."""
        return self._reliability.get_bounded_concurrency(task_max_concurrent)

    # ------------------------------------------------------------------
    # Phonebook — delegated to WorkerPhonebook (P2-1 refactor)
    # -----------------------------------------------------------------

    def summon(self, worker_name: str) -> SwarmWorker | None:
        """Instantiate a worker from the WorkerRegistry by name."""
        return self._phonebook.summon(worker_name)

    async def dispatch_by_name(self, worker_name: str, task: str) -> dict[str, Any]:
        """Summon a worker by name and dispatch a task with episodic memory context."""
        return await self._phonebook.dispatch_by_name(worker_name, task)
