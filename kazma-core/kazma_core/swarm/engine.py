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
from kazma_core.swarm.dispatch_helpers import (
    aggregate_outputs as _aggregate_outputs_impl,
    build_dispatch_context as _build_dispatch_context_impl,
    build_handoff_context as _build_handoff_context_impl,
    build_result_metadata as _build_result_metadata_impl,
    normalize_worker_type as _normalize_worker_type,
    overall_status as _overall_status_impl,
    resolve_max_concurrent as _resolve_max_concurrent_impl,
)
from kazma_core.swarm.handoff_guards import (
    build_available_workers_list as _build_available_workers_list_impl,
    handoff_guard_error as _handoff_guard_error,
    register_visit as _register_handoff_visit,
)
from kazma_core.swarm.sse_bridge import SseBridge
from kazma_core.swarm.task_control import build_retry_task as _build_retry_task
from kazma_core.swarm.task_control import cancel_active_task as _cancel_active_task
from kazma_core.swarm.task_lifecycle import get_task as _hist_get_task
from kazma_core.swarm.task_lifecycle import record_task as _hist_record_task
from kazma_core.swarm.task_lifecycle import update_task as _hist_update_task
from kazma_core.swarm.task_store import TaskStore
from kazma_core.swarm.tracing import TracingEmitter
from kazma_core.swarm.worker import SwarmWorker
from kazma_core.swarm.worker_factory import create_worker as _create_worker_impl
from kazma_core.swarm.worker_factory import register_worker as _register_worker
from kazma_core.swarm.worker_factory import unregister_worker as _unregister_worker

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
        self._sse = SseBridge()
        self._build_workers()

    @property
    def _sse_bus(self) -> Any:
        """Backward-compat alias for tests/callers that read ``_sse_bus``."""
        return self._sse.bus

    @_sse_bus.setter
    def _sse_bus(self, bus: Any) -> None:
        self._sse.set_bus(bus)

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
        """Instantiate a concrete worker from its config (delegates to worker_factory)."""
        return _create_worker_impl(worker_config)

    def add_worker(self, worker_config: WorkerConfig) -> SwarmWorker:
        """Register a worker in the unified registry."""
        return _register_worker(
            self._workers,
            worker_config,
            factory=self._create_worker,
        )

    def remove_worker(self, name: str) -> SwarmWorker:
        """Unregister a worker by name and clean up reliability state."""
        return _unregister_worker(
            self._workers,
            name,
            on_removed=self._reliability.cleanup_worker,
        )

    def get_worker(self, name: str) -> SwarmWorker | None:
        """Return a worker by name."""
        return self._workers.get(name)

    def get_task(self, task_id: str) -> SwarmTask | None:
        """Return a task by id from the history."""
        return _hist_get_task(self._task_history, self._task_lock, task_id)

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
        self._sse.set_bus(bus)

    def _emit_sse(self, task_id: str, event: str, data: dict[str, Any]) -> None:
        """Internal helper to emit via registered SSE bus (if any)."""
        self._sse.emit(task_id, event, data)

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
        normalized_type = _normalize_worker_type(worker_type)
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
        from kazma_core.swarm.dispatch_inner import dispatch_inner as _dispatch_inner_impl

        return await _dispatch_inner_impl(self, task, started, task_span)


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
        return _cancel_active_task(
            task_id=task_id,
            active_tasks=self._active_tasks,
            task_handles=self._task_handles,
            finalize=self._finalize_task,
        )

    async def retry_task(self, task_id: str) -> SwarmTask | None:
        """Retry a failed/timeout/cancelled task by creating a fresh dispatch.

        Builds a new SwarmTask with a new ID, copying the prompt/type/
        workers/context from the original. The original task's ID is
        recorded in metadata['retry_of'] for lineage.

        Returns the new SwarmTask, or None if the original was not found.
        """
        return _build_retry_task(
            task_id=task_id,
            history=self._task_history,
            active_tasks=self._active_tasks,
            task_store=self._task_store,
        )

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
        """Dispatch a worker and return all results (including handoff chain)."""
        from kazma_core.swarm.worker_dispatch import dispatch_worker as _dispatch_worker_impl

        return await _dispatch_worker_impl(
            self,
            worker,
            prompt,
            context,
            timeout=timeout,
            validation_schema=validation_schema,
            trace_id=trace_id,
            _visited=_visited,
            _depth=_depth,
        )


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
        # ── Cycle detection (handoff_guards) ─────────────────────
        if _visited is None:
            _visited = {}
        _register_handoff_visit(_visited, source_worker.name)
        guard_err = _handoff_guard_error(
            source_worker=source_worker.name,
            target_worker=handoff_req.target_worker,
            visited=_visited,
            depth=_depth,
            started=started,
        )
        if guard_err is not None:
            return [guard_err]

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
        return _aggregate_outputs_impl(worker_results)

    @staticmethod
    def _overall_status(worker_results: list[WorkerResult]) -> str:
        return _overall_status_impl(worker_results)

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
        _hist_record_task(
            self._task_history,
            self._task_lock,
            task,
            max_history=self._max_history,
        )

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
        """Build the accumulated context for a handoff target worker."""
        return _build_handoff_context_impl(
            original_prompt=original_prompt,
            original_context=original_context,
            intermediate_results=intermediate_results,
            blackboard=blackboard,
        )

    @staticmethod
    def _build_dispatch_context(
        task: SwarmTask,
        *,
        blackboard: BlackboardStore | None = None,
        system_prompt: str = "",
    ) -> str | SwarmDispatchContext:
        """Build the context passed to worker.dispatch()."""
        return _build_dispatch_context_impl(
            task,
            blackboard=blackboard,
            system_prompt=system_prompt,
        )

    @staticmethod
    async def _build_result_metadata(
        blackboard: BlackboardStore | None = None,
    ) -> dict[str, Any]:
        return await _build_result_metadata_impl(blackboard)

    def _resolve_max_concurrent(self, task: SwarmTask) -> int:
        """Resolve the fan-out concurrency limit for a task."""
        configured_default = max(1, int(getattr(self.config, "max_concurrent", 5) or 5))
        return _resolve_max_concurrent_impl(task, configured_default)

    def _build_available_workers_list(self) -> list[dict[str, Any]]:
        """Build worker info dicts for the capability router."""
        return _build_available_workers_list_impl(self._workers)

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
            def _mark_failed(task: SwarmTask) -> None:
                task.status = TaskStatus.FAILED
                task.result = result

            updated = _hist_update_task(
                self._task_history,
                self._task_lock,
                task_id,
                _mark_failed,
                max_history=self._max_history,
            )
            if updated is not None:
                if self._task_store is not None:
                    try:
                        self._task_store.persist_task(updated)
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
