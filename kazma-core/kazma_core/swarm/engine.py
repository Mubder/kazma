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
from kazma_core.swarm.handoff import HandoffRequest
from kazma_core.swarm.metrics import MetricsCollector
from kazma_core.swarm.patterns import (
    PatternExecution,
    PipelineConfigurationError,
    resume_pipeline,
)
from kazma_core.swarm.phonebook import WorkerPhonebook
from kazma_core.swarm.reliability import (
    BoundedConcurrency,
    CircuitBreaker,
    FallbackChain,
    OutputValidator,
    RetryPolicy,
    TimeoutGuard,
)
from kazma_core.swarm.reliability_registry import ReliabilityRegistry
from kazma_core.routing_engine import UnifiedRouter
from kazma_core.swarm.task import (
    TERMINAL_STATUSES,
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
from kazma_core.swarm.task_lifecycle import list_tasks as _hist_list_tasks
from kazma_core.swarm.task_lifecycle import record_task as _hist_record_task
from kazma_core.swarm.task_lifecycle import update_task as _hist_update_task
from kazma_core.metrics import record_swarm_failure
from kazma_core.swarm.task_store import TaskStore
from kazma_core.swarm.tracing import TracingEmitter
from kazma_core.swarm.worker import SwarmWorker
from kazma_core.swarm.worker_factory import create_worker as _create_worker_impl
from kazma_core.swarm.worker_factory import register_worker as _register_worker
from kazma_core.swarm.worker_factory import unregister_worker as _unregister_worker

__all__ = ["SwarmEngine", "get_swarm_engine", "set_swarm_engine"]

logger = logging.getLogger(__name__)

_swarm_engine: SwarmEngine | None = None


def set_swarm_engine(engine: SwarmEngine | None) -> None:
    """Set the module-level swarm engine singleton."""
    global _swarm_engine
    _swarm_engine = engine


def get_swarm_engine() -> SwarmEngine | None:
    """Return the module-level swarm engine singleton."""
    return _swarm_engine


#: Every status a run can end with, and the task status it is stored as
#: (Swarm audit SW-E08: anything unnamed was stored COMPLETED -- a pipeline
#: whose writing step failed read "completed"). An unknown status is FAILED.
_RESULT_STATUS_TO_TASK: dict[str, TaskStatus] = {
    "success": TaskStatus.COMPLETED,
    "partial": TaskStatus.COMPLETED,  # with metadata.result_status = "partial"
    "failed": TaskStatus.FAILED,
    "error": TaskStatus.FAILED,
    "timeout": TaskStatus.TIMEOUT,
    "cancelled": TaskStatus.CANCELLED,
    "paused": TaskStatus.PAUSED,
}


def _task_status_for(status: str) -> TaskStatus:
    """The task status a run's result status is stored as."""
    return _RESULT_STATUS_TO_TASK.get(status, TaskStatus.FAILED)


#: How long a stop (cancel, watchdog, a caller giving up) waits for the work
#: to end before the ending is recorded anyway, as an unknown outcome.
_STOP_GRACE_S = 10.0


def _new_run_id() -> str:
    """This process and one run of a task: who owns the row (SW-E03)."""
    import os
    import uuid

    return f"{os.getpid()}:{uuid.uuid4().hex[:12]}"


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


# Pattern task types whose ``task.timeout`` is applied PER STEP by the
# pattern executors (patterns.py ``wait_timeout`` wraps each individual
# dispatch), NOT as a whole-task deadline. dispatch() exempts these from
# the whole-task ``wait_for`` and reap_stale_tasks() must scale its
# watchdog deadline by the pattern's step count accordingly.
_PER_STEP_PATTERN_TYPES = (
    TaskType.PIPELINE,
    TaskType.FAN_OUT,
    TaskType.CONSULT,
    TaskType.CONDITIONAL,
)


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
        # The engine's own run of each task (Swarm audit SW-E02): created by
        # dispatch() / a resume before the first await, so cancel and the
        # watchdog can always stop the work, whoever called. The ending a
        # stop asked for (status, error) waits in _interrupts for the run's
        # CancelledError path to record it once the work has stopped.
        self._runs: dict[str, asyncio.Task] = {}
        self._interrupts: dict[str, tuple[str, str, str]] = {}
        # Each run's effect tracker (SW-E04): the tools that change things it
        # invoked, recorded with any interruption.
        self._effects: dict[str, Any] = {}
        # Each task's newest store write (SW-E07): writes run in threads,
        # each after the one before it for the same task.
        self._writes: dict[str, asyncio.Task] = {}
        # Strong refs for fire-and-forget alert tasks (see get_autoscaler).
        self._alert_tasks: set[asyncio.Task] = set()
        # threading.Lock: usable from both sync (_finalize_task) and async paths
        self._task_lock = threading.Lock()  # protects _task_history mutations
        self._max_history = 500  # LRU cap to prevent unbounded memory growth
        self._result_aggregator = result_aggregator or ResultAggregator()
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
            task_lock=self._task_lock,
        )
        # Register reject callback so the checkpoint timeout auto-reject
        # can call back into the engine without a circular reference.
        self._checkpoint_mgr.set_reject_callback(self.reject_checkpoint)
        self._sse = SseBridge()
        # Register SSE emission so a fresh pause notifies listeners the
        # same way _finalize_task does for non-paused outcomes.
        self._checkpoint_mgr.set_sse_emit_callback(self._emit_sse)
        self._checkpoint_mgr.set_persist_callback(self._persist_now)
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
                # Build + load locally, then assign only on success —
                # assigning before load_templates() left a half-initialized
                # singleton (empty _templates) that was never reloaded if
                # load raised (audit finding).
                _scaler = AutoScaler(self)
                _scaler.load_templates()
                self._autoscaler = _scaler
            except Exception as exc:
                logger.warning("[SwarmEngine] AutoScaler initialization failed: %s", exc)
                try:
                    from kazma_core.observability import AlertDispatcher
                    import asyncio
                    loop = asyncio.get_running_loop()
                    _alert_task = loop.create_task(AlertDispatcher.trigger_system_alert(
                        subsystem="AutoScaler",
                        status="DEGRADED",
                        message=f"AutoScaler initialization failed: {exc}"
                    ))
                    # Strong ref: the loop holds only weak refs — a GC'd task
                    # silently dropped the DEGRADED alert (the one signal an
                    # autoscaler failure exists).
                    self._alert_tasks.add(_alert_task)
                    _alert_task.add_done_callback(self._alert_tasks.discard)
                except Exception as alert_exc:
                    logger.warning(
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
        # Direct registry read — the previous "prefer phonebook" branch read
        # ``_phonebook._entries``, an attribute WorkerPhonebook never had, so
        # every call took the exception-per-call fallback.
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
        tasks = _hist_list_tasks(self._task_history, self._task_lock)
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

    def _max_concurrent_tasks(self) -> int:
        """Global admission limit for in-flight swarm tasks (audit M11)."""
        import os

        env = (os.environ.get("KAZMA_SWARM_MAX_ACTIVE") or "").strip()
        if env.isdigit():
            return max(1, int(env))
        return max(
            1,
            int(getattr(self.config, "max_concurrent_tasks", None) or 10),
        )

    async def dispatch(self, task: SwarmTask) -> TaskResult:
        """Dispatch a swarm task to a single worker (or broadcast to all).

        Swarm work never runs on a chat turn's HITL call authority (SW-S01):
        its danger tools ask the swarm bus, whoever awaits this."""
        from kazma_core.safety.call_authority import without_call_authority

        with without_call_authority():
            return await self._run_owned(task, lambda: self._dispatch(task))

    async def _run_owned(
        self, task: SwarmTask, body: Any, *, resumed: bool = False, wait: bool = True,
    ) -> TaskResult:
        """Run *body()* as the engine's own task for *task*.

        One run per task id (SW-E03): a second dispatch of an id that is
        running waits on that run; an id that already ended (or paused) is
        answered from its record and never executed again. The run is
        registered before the first await, so ``cancel_task`` and the
        watchdog can stop it whoever called (SW-E02); a caller that stops
        waiting stops the run too, and waits (bounded) for it to end."""
        current = self._runs.get(task.id)
        if current is not None and not current.done():
            logger.info("[SwarmEngine] task '%s' is already running: waiting on that run", task.id)
            return await asyncio.shield(current)
        if not resumed:
            earlier = self._recorded_result(task.id)
            if earlier is not None:
                logger.info("[SwarmEngine] task '%s' already ran (%s): not run again", task.id, earlier.status)
                return earlier
        from kazma_core.agent.dispatch_effects import DispatchEffects, track_effects

        tracker = DispatchEffects()

        async def _tracked() -> TaskResult:
            with track_effects(tracker):
                return await body()

        run = asyncio.create_task(_tracked(), name=f"swarm-task:{task.id}")
        self._runs[task.id] = run
        self._task_handles[task.id] = run
        self._effects[task.id] = tracker

        def _forget(done: asyncio.Task, tid: str = task.id) -> None:
            if self._runs.get(tid) is done:
                self._runs.pop(tid, None)
                self._effects.pop(tid, None)
            if self._task_handles.get(tid) is done:
                self._task_handles.pop(tid, None)
            self._interrupts.pop(tid, None)

        run.add_done_callback(_forget)
        if not wait:
            # The caller does not wait (an approve answering 202): the run is
            # the engine's own, registered, stoppable and watched like any.
            return TaskResult(
                task_id=task.id, status="running",
                metadata={"resumed": resumed, "generation": int(task.generation or 0)},
            )
        try:
            result = await asyncio.shield(run)
            await self._flush_writes(task.id)
            return result
        except asyncio.CancelledError:
            if not run.done():
                self._interrupts.setdefault(
                    task.id, ("cancelled", "Stopped: the caller stopped waiting.", "caller_stopped"))
                run.cancel()
                await asyncio.wait({run}, timeout=_STOP_GRACE_S)
            await self._flush_writes(task.id)
            # The caller's own cancellation goes on (SW-E04): its deadline
            # raises TimeoutError, never reads as a user's cancel.
            raise

    def _recorded_result(self, task_id: str) -> TaskResult | None:
        """The result of a task this engine already ended or paused, if any."""
        earlier = _hist_get_task(self._task_history, self._task_lock, task_id)
        if earlier is None or earlier.result is None:
            return None
        if earlier.status in TERMINAL_STATUSES or earlier.status == TaskStatus.PAUSED:
            return earlier.result
        return None

    def _interrupted(self, task: SwarmTask, started: float, task_span: Any = None) -> TaskResult:
        """Record the ending a stop asked for, after the work stopped.

        What the work changed outside is recorded from the run's effect
        tracker (SW-E04): the tools that change things it had invoked, and
        ``execution_outcome: unknown`` when there were any."""
        status, error, cause = self._interrupts.pop(
            task.id, ("cancelled", "Cancelled by user", "cancelled_by_user"))
        evidence, note = self._effect_evidence(task.id, cause)
        if task_span is not None:
            self._tracing_emitter.end_span(task_span, status="cancelled" if status == "cancelled" else "error",
                                           status_msg=error)
        return self._finalize_task(
            task,
            worker_results=[],
            status=status,
            error=error + note,
            duration_seconds=perf_counter() - started,
            metadata=evidence,
        )

    def _interrupt_cause(self, task_id: str) -> str | None:
        """Why a task's run is being stopped, if it is (its interruption)."""
        entry = self._interrupts.get(task_id)
        return entry[2] if entry else None

    def _effect_evidence(self, task_id: str, cause: str) -> tuple[dict[str, Any], str]:
        """What an interrupted run is known to have changed, and a sentence
        for its error. With no tracker (nothing recorded what ran) the
        outcome is unknown."""
        tracker = self._effects.get(task_id)
        if tracker is not None and not tracker.invoked:
            return {"execution_outcome": "none_invoked", "interrupted_by": cause}, ""
        tools = list(getattr(tracker, "tools", None) or [])
        evidence = {"execution_outcome": "unknown", "interrupted_by": cause, "invoked_tools": tools}
        if not tools:
            return evidence, ""
        return evidence, (f" Tools that change things had already run ({', '.join(tools)}): "
                          "inspect their effects before running it again.")

    def _at_capacity(self) -> int | None:
        """The admission cap when it is reached (resumed and running tasks
        count; paused ones wait on a human and do not), else None."""
        max_active = self._max_concurrent_tasks()
        active_non_paused = sum(
            1 for t in self._active_tasks.values() if t.status != TaskStatus.PAUSED
        )
        return max_active if active_non_paused >= max_active else None

    def at_capacity(self) -> int | None:
        """Public: the admission cap when a new task would be refused now."""
        return self._at_capacity()

    async def _admit_to_store(self, task: SwarmTask) -> TaskResult | None:
        """Record *task* RUNNING at its new generation (SW-E03). None when
        admitted; the stored outcome when another run already holds the id."""
        if self._task_store is None:
            return None
        try:
            admitted = await asyncio.to_thread(self._task_store.admit, task)
        except Exception:
            # The RUNNING row is the only crash evidence: say it is missing.
            logger.warning(
                "[SwarmEngine] could not record task '%s' as running; it runs without crash evidence",
                task.id, exc_info=True,
            )
            record_swarm_failure("running_row")
            return None
        if admitted:
            return None
        stored = await asyncio.to_thread(self._task_store.get_task, task.id)
        logger.warning(
            "[SwarmEngine] task '%s' is already held at generation %s (%s): not run again",
            task.id, getattr(stored, "generation", "?"), getattr(stored, "status", "?"),
        )
        if stored is not None and stored.result is not None:
            return stored.result
        return TaskResult(
            task_id=task.id, status="failed",
            error="This task is already running or finished elsewhere; it was not run again.",
            metadata={"duplicate_admission": True},
        )

    async def _dispatch(self, task: SwarmTask) -> TaskResult:
        from kazma_core.swarm.durable import (
            durable_enabled,
            durable_required,
            in_durable_activity,
            run_via_durable,
        )

        # Refuse before admission/persistence: an unavailable required backend
        # must not leave a task marked RUNNING without any executor.
        if durable_required() and not durable_enabled() and not in_durable_activity():
            return await run_via_durable(self, task, perf_counter(), None)

        # Sweep stale tasks before checking capacity
        self.reap_stale_tasks()

        # Global admission control (audit M11): reject when at capacity so
        # chat→swarm / panel spam cannot unbounded-grow _active_tasks + LLM spend.
        # PAUSED tasks do NOT count against the cap (audit M-S2): ten
        # pipelines parked at HITL checkpoints must not block all new swarm
        # work with "Swarm at capacity".
        # Bounded inputs from every entry point (Swarm audit SW-S14/SW-E15).
        from kazma_core.swarm import limits

        problem = limits.task_problem(task)
        if problem is None and (task.metadata or {}).get("hitl_checkpoints"):
            paused = sum(1 for t in self._active_tasks.values() if t.status == TaskStatus.PAUSED)
            if paused >= limits.MAX_PAUSED_TASKS:
                problem = ("too_many_paused",
                           f"{paused} pipelines already wait at a checkpoint; answer some first.")
        if problem is not None:
            logger.warning("[SwarmEngine] task '%s' refused: %s", task.id, problem[1])
            return TaskResult(
                task_id=task.id, status="failed", error=problem[1],
                metadata={"input_refused": True, "code": problem[0]},
            )

        max_active = self._at_capacity()
        if max_active is not None:
            logger.warning(
                "[SwarmEngine] Admission denied task=%s max=%d",
                task.id,
                max_active,
            )
            return TaskResult(
                task_id=task.id,
                status="failed",
                error=(
                    f"Swarm at capacity ({max_active} concurrent tasks). "
                    "Wait for running tasks to finish or raise "
                    "swarm.max_concurrent_tasks / KAZMA_SWARM_MAX_ACTIVE."
                ),
                metadata={"admission_denied": True, "max_concurrent_tasks": max_active},
            )

        started = perf_counter()
        task.started_at = task.started_at or _utc_now_iso()
        task.status = TaskStatus.RUNNING
        task.generation = int(task.generation or 0) + 1
        task.run_id = _new_run_id()
        # Stamp tenant for multi-tenant list isolation (metadata filter).
        try:
            from kazma_core.tenant_isolation import require_tenant_id

            task.metadata = dict(task.metadata or {})
            task.metadata.setdefault("tenant_id", require_tenant_id())
        except Exception:
            # An unstamped task is listed for every tenant (SW-E14: was silent).
            logger.warning("[SwarmEngine] could not stamp the tenant of task '%s'", task.id, exc_info=True)
        self._active_tasks[task.id] = task  # track in-flight

        # Persist the RUNNING row immediately (audit H-9): tasks used to be
        # persisted only at terminal/paused states, so a crash mid-task left
        # NO DB row and the boot-time requeue_orphaned_running recovery had
        # nothing to recover — "crash recovery" was decorative. The write is
        # the admission compare-and-set (SW-E03): an id another run holds is
        # not run twice.
        held = await self._admit_to_store(task)
        if held is not None:
            self._active_tasks.pop(task.id, None)
            return held

        self._emit_sse(task.id, "task_started", {
            "task_id": task.id,
            "workers": list(task.workers) if task.workers else [],
        })

        # Broadcast fans out to every worker — dispatch it AFTER the shared
        # preamble above so it is subject to the same admission cap, tenant
        # stamping, and task_started SSE as a normal dispatch (previously it
        # returned before all four, exempting the most expensive pattern).
        if task.type == TaskType.BROADCAST:
            # It must ALSO finalize through the same cancel/timeout/error
            # paths as a normal dispatch: returning before the try/except
            # below let a cancelled BROADCAST leak as RUNNING in
            # _active_tasks until the watchdog reaped it, with no
            # task_completed SSE (deep-audit 2026-08-19).
            try:
                if (durable_enabled() or durable_required()) and not in_durable_activity():
                    return await run_via_durable(self, task, started, None)
                return await self._broadcast_task(task)
            except asyncio.TimeoutError:
                logger.warning(
                    "[SwarmEngine] Broadcast '%s' timed out after %.1fs",
                    task.id, float(task.timeout or 0),
                )
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="timeout",
                    error=f"Task timed out after {float(task.timeout or 0):.1f}s",
                    duration_seconds=perf_counter() - started,
                )
            except asyncio.CancelledError:
                return self._interrupted(task, started)
            except Exception as exc:
                logger.exception(
                    "[SwarmEngine] Unhandled error in broadcast for task '%s'", task.id
                )
                return self._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )

        # Start a root tracing span for this task.
        task_span = self._tracing_emitter.start_task_span(
            task_id=task.id,
            task_type=task.type.value,
            workers=list(task.workers),
        )

        try:
            # Pattern executors (pipeline/fan_out/consult/conditional) already
            # apply ``task.timeout`` PER STEP / per worker internally
            # (patterns.py ``wait_timeout``). Wrapping them here in a whole-task
            # deadline of the same value wrongly capped the TOTAL runtime at one
            # step's budget — e.g. a 3-step pipeline with timeout=0.01 was killed
            # before step 1 finished (2026-08-15 audit). Only single-worker
            # DISPATCH keeps the whole-task deadline as its safety net.
            # (The set lives at module level: _PER_STEP_PATTERN_TYPES.)
            if (durable_enabled() or durable_required()) and not in_durable_activity():
                return await run_via_durable(self, task, started, task_span)
            if (
                task.timeout
                and float(task.timeout) > 0
                and task.type not in _PER_STEP_PATTERN_TYPES
            ):
                return await asyncio.wait_for(
                    self._dispatch_inner(task, started, task_span),
                    timeout=float(task.timeout),
                )
            return await self._dispatch_inner(task, started, task_span)
        except asyncio.TimeoutError:
            logger.warning("[SwarmEngine] Task '%s' timed out after %.1fs", task.id, float(task.timeout or 0))
            self._tracing_emitter.end_span(
                task_span,
                status="error",
                status_msg=f"Task timed out after {float(task.timeout or 0):.1f}s",
            )
            evidence, note = self._effect_evidence(task.id, "deadline")
            return self._finalize_task(
                task,
                worker_results=[],
                status="timeout",
                error=f"Task timed out after {float(task.timeout or 0):.1f}s." + note,
                duration_seconds=perf_counter() - started,
                metadata=evidence,
            )
        except asyncio.CancelledError:
            # Stopped by cancel_task(), the watchdog or a caller that gave up:
            # the work has stopped, so its ending is recorded now (SW-E02).
            return self._interrupted(task, started, task_span)
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

    def reap_stale_tasks(self) -> int:
        """Stop every running task past its deadline (timeout + margin).

        The work is stopped, never only relabelled (Swarm audit SW-E02): a
        task with a run is cancelled with the ending "timeout", which its run
        records once the work has ended; one with no run (nothing executing)
        is finalized here. A task whose ``timeout`` is 0 or less has no
        deadline and is never reaped. A paused task waits on a human: its
        lifetime is its checkpoint timeout, or none, before and after a
        restart alike (SW-E05)."""
        reaped = 0
        for tid, t in list(self._active_tasks.items()):
            if getattr(t, "status", None) == TaskStatus.PAUSED:
                continue
            raw_timeout = getattr(t, "timeout", None)
            if raw_timeout is not None and float(raw_timeout) <= 0:
                continue
            if tid in self._interrupts:
                continue  # already being stopped
            timeout = float(raw_timeout or 300.0)
            # Pattern tasks (PIPELINE/FAN_OUT/CONSULT/CONDITIONAL) get
            # ``task.timeout`` PER STEP, not as a whole-task deadline —
            # dispatch() exempts them from the whole-task wait_for and
            # patterns.py applies wait_timeout(task.timeout) around EACH
            # step. Scale the deadline by the step count: pipeline steps run
            # sequentially (len(workers)); fan-out/consult waves are
            # bounded above by sequential steps; conditional is router +
            # one branch (at least 2).
            step_budget = 1
            if getattr(t, "type", None) in _PER_STEP_PATTERN_TYPES:
                step_budget = max(len(getattr(t, "workers", None) or []), 1)
                if t.type == TaskType.CONDITIONAL:
                    step_budget = max(step_budget, 2)
            started_iso = getattr(t, "started_at", None)
            if not started_iso:
                continue
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - dt).total_seconds()
            except Exception as exc:
                logger.warning("[SwarmEngine] cannot read the start time of task %s: %s", tid, exc)
                continue
            if elapsed <= timeout * step_budget + 30.0:
                continue
            logger.warning(
                "[SwarmEngine] Reaping stale task %s (elapsed: %.1fs, timeout: %.1fs x %d steps)",
                tid, elapsed, timeout, step_budget,
            )
            error = f"Task timed out after {timeout * step_budget:.1f}s (reaped by watchdog)."
            run = self._runs.get(tid)
            if run is not None and not run.done():
                self._interrupts[tid] = ("timeout", error, "deadline")
                run.cancel()
            else:
                self._finalize_task(
                    t,
                    worker_results=[],
                    status="timeout",
                    error=error,
                    duration_seconds=elapsed,
                    metadata={"execution_outcome": "unknown", "interrupted_by": "deadline"},
                )
            reaped += 1
        return reaped

    async def reap_stale_tasks_async(self) -> int:
        """:meth:`reap_stale_tasks`, then wait (bounded) for the stopped runs
        to end. A run that does not stop within the grace has its ending
        recorded anyway, as an unknown outcome; anything it reports later is
        kept as evidence, never as its result (SW-E03)."""
        reaped = self.reap_stale_tasks()
        stopping = {tid: run for tid, run in self._runs.items()
                    if tid in self._interrupts and not run.done()}
        if stopping:
            await asyncio.wait(set(stopping.values()), timeout=_STOP_GRACE_S)
            for tid, run in stopping.items():
                if not run.done():
                    self._record_unstoppable(tid)
        for tid in list(self._writes):
            await self._flush_writes(tid)
        return reaped

    def _record_unstoppable(self, task_id: str) -> None:
        """A run that did not end when stopped: record the ending it was
        stopped for, with the outcome unknown, and say so."""
        task = self._active_tasks.get(task_id)
        status, error, cause = self._interrupts.get(
            task_id, ("cancelled", "Cancelled by user", "cancelled_by_user"))
        logger.warning(
            "[SwarmEngine] task '%s' did not stop within %.0fs of being stopped (%s); "
            "its ending is recorded and anything it still does is unknown",
            task_id, _STOP_GRACE_S, status,
        )
        if task is not None:
            evidence, note = self._effect_evidence(task_id, cause)
            evidence["execution_outcome"] = "unknown"  # it is still running
            self._finalize_task(
                task, worker_results=[], status=status,
                error=f"{error} The work did not stop within {_STOP_GRACE_S:.0f}s." + note,
                duration_seconds=0.0,
                metadata=evidence,
            )

    def redispatch_recovered_tasks(self, max_tasks: int = 5) -> int:
        """Reconcile legacy recovery rows without replaying uncertain effects.

        Older versions requeued interrupted whole-agent tasks. Keep the public
        entry point, but finalize those rows as unknown outcomes for inspection.
        Ordinary pending work and active/paused tasks are never changed here.
        """
        if self._task_store is None:
            return 0
        try:
            rows = self._task_store.list_tasks(status="pending", page_size=max_tasks)
            tasks = rows[0] if isinstance(rows, tuple) else rows
        except Exception:
            # Recovered tasks wait for the next start: say so (SW-E14).
            logger.warning("[SwarmEngine] pending-task recovery read failed", exc_info=True)
            record_swarm_failure("recovery_read")
            return 0
        count = 0
        for task in tasks[:max_tasks]:
            # Only tasks that carry a recovery marker (crash survivors), not
            # rows that some future producer may enqueue for other reasons.
            if not (task.metadata or {}).get("recovery_count"):
                continue
            if task.id in self._active_tasks:
                continue
            task.metadata = dict(task.metadata or {})
            task.metadata["execution_outcome"] = "unknown"
            self._finalize_task(
                task, worker_results=[], status="failed", duration_seconds=0,
                error=("Interrupted task has uncertain external effects; automatic replay refused. "
                       "Inspect checkpoints, Temporal history and actual effects before resubmitting."),
                metadata=task.metadata,
            )
            count += 1
        if count:
            logger.warning("[SwarmEngine] Held %d interrupted task(s) for effect reconciliation", count)
        return 0  # no task was re-dispatched

    def start_maintenance_loop(self, interval_seconds: float = 60.0) -> None:
        """Start the background watchdog (stale-task reap + idle reap).

        Previously ``reap_stale_tasks()`` only ran inside ``dispatch()`` — a
        stuck or paused task held an admission slot FOREVER until some other
        dispatch happened to trigger the sweep (audit H-9). This loop makes
        reaping periodic and independent of traffic.
        """
        if getattr(self, "_maintenance_task", None) is not None:
            return

        async def _loop() -> None:
            import asyncio as _aio

            while True:
                await _aio.sleep(max(15.0, interval_seconds))
                try:
                    await self.reap_stale_tasks_async()
                except Exception:
                    logger.warning("[SwarmEngine] maintenance reap failed", exc_info=True)
                    record_swarm_failure("reap")
                try:
                    scaler = self.get_autoscaler()
                    if scaler is not None:
                        scaler.reap_idle()
                except Exception:
                    logger.warning("[SwarmEngine] maintenance idle-reap failed", exc_info=True)
                    record_swarm_failure("idle_reap")

        from kazma_core.background import spawn_background

        self._maintenance_task = spawn_background(
            _loop(), name="swarm-maintenance", never_completes=True
        )

    async def stop_all_runs(self, reason: str = "Stopped: Kazma shut down.") -> int:
        """Stop every running task at shutdown, recording each ending with
        its cause and effect evidence (SW-E04), and wait (bounded) for the
        work to end. Paused tasks stay paused for the next boot."""
        live = {tid: run for tid, run in self._runs.items() if not run.done()}
        for tid, run in live.items():
            self._interrupts.setdefault(tid, ("cancelled", reason, "shutdown"))
            run.cancel()
        if live:
            await asyncio.wait(set(live.values()), timeout=_STOP_GRACE_S)
            for tid, run in live.items():
                if not run.done():
                    self._record_unstoppable(tid)
            # The endings are written in threads (_persist). Returning before
            # they land let the process exit first, and the next boot's orphan
            # recovery recorded a task the shutdown had stopped as a failure
            # of unknown outcome (the SQ01 soak measured 4 of 4 pending).
            await self._flush_stopped_writes(set(live))
        return len(live)

    async def _flush_stopped_writes(self, task_ids: set[str]) -> None:
        """Wait, bounded, for the store writes of *task_ids* (later writes a
        flushed one chained included); one left unwritten is a WARNING."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _STOP_GRACE_S
        while True:
            pending = {w for tid in task_ids
                       if (w := self._writes.get(tid)) is not None and not w.done()}
            if not pending:
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning(
                    "[SwarmEngine] %d stopped task(s) not yet written %.0fs after shutdown "
                    "stopped them; the next boot records them as interrupted with an "
                    "unknown outcome", len(pending), _STOP_GRACE_S)
                record_swarm_failure("shutdown_write")
                return
            await asyncio.wait(pending, timeout=remaining)

    def stop_maintenance_loop(self) -> None:
        """Stop the background maintenance watchdog (idempotent)."""
        task = getattr(self, "_maintenance_task", None)
        if task is not None:
            task.cancel()
            self._maintenance_task = None

    async def _dispatch_inner(self, task: SwarmTask, started: float, task_span: Any) -> TaskResult:
        """Inner dispatch logic, wrapped by dispatch() for catch-all safety."""
        if task.type == TaskType.BROADCAST:
            return await self._broadcast_task(task)
        from kazma_core.swarm.dispatch_inner import dispatch_inner as _dispatch_inner_impl

        return await _dispatch_inner_impl(self, task, started, task_span)


    async def broadcast(self, task: SwarmTask) -> TaskResult:
        """Dispatch *task* to every registered worker (or its listed ones).

        A thin wrapper over :meth:`dispatch` (Swarm audit SW-E06/SW-S06): a
        broadcast is admitted, recorded RUNNING, tenant-stamped and finalized
        like any task. Called directly it bypassed all four."""
        task.type = TaskType.BROADCAST
        return await self.dispatch(task)

    async def _broadcast_task(self, task: SwarmTask) -> TaskResult:
        """The broadcast executor, run inside dispatch()'s lifecycle only."""
        from kazma_core.swarm.broadcast import broadcast_task

        return await broadcast_task(self, task)

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
        found or already terminal. A paused pipeline restored after a
        restart is in history only, and cancels too.
        """
        run = self._runs.get(task_id)
        if run is not None and not run.done():
            # Stop the work first, then record the ending (SW-E02): the run's
            # CancelledError path records "cancelled" once it has ended.
            self._interrupts.setdefault(task_id, ("cancelled", "Cancelled by user", "cancelled_by_user"))
            run.cancel()
            await asyncio.wait({run}, timeout=_STOP_GRACE_S)
            if not run.done():
                self._record_unstoppable(task_id)
            await self._flush_writes(task_id)
            logger.info("[SwarmEngine] task '%s' cancelled", task_id)
            return True
        cancelled = _cancel_active_task(
            task_id=task_id,
            active_tasks=self._active_tasks,
            task_handles=self._task_handles,
            finalize=self._finalize_task,
            history=self._task_history,
            history_lock=self._task_lock,
        )
        await self._flush_writes(task_id)
        return cancelled

    async def retry_task(self, task_id: str, *, acknowledge_unknown: bool = False) -> SwarmTask | None:
        """Retry a failed/timeout/cancelled task by creating a fresh dispatch.

        Builds a new SwarmTask with a new ID, copying the prompt/type/
        workers/context from the original. The original task's ID is
        recorded in metadata['retry_of'] for lineage.

        Returns the new SwarmTask, or None if the original was not found.
        """
        return _build_retry_task(
            task_id=task_id,
            history=self._task_history,
            history_lock=self._task_lock,
            active_tasks=self._active_tasks,
            task_store=self._task_store,
            acknowledge_unknown=acknowledge_unknown,
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
        _visited: dict[str, int] | None = None,
        _depth: int = 0,
    ) -> WorkerResult:
        worker = self.get_worker(worker_name)
        if worker is None:
            # One name resolution for every pattern (Swarm audit SW-E12): a
            # template's name means a worker spawned from it, as "auto" does.
            scaler = self.get_autoscaler()
            spawn = getattr(scaler, "spawn_by_name", None)
            worker = spawn(worker_name) if callable(spawn) else None
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
            _visited=_visited,
            _depth=_depth,
        )
        result = results[-1]

        # If the primary worker succeeded or no fallback chain, return.
        if result.status == "success" or not fallback_chain:
            return result

        # Execute the fallback chain (returns final result + all attempted).
        # Thread hop budget so A→B→fallback→A cannot reset visit counts (M13).
        final_result, _all_results = await self._execute_fallback_chain(
            result,
            fallback_chain,
            prompt=prompt,
            context=context,
            timeout=timeout,
            validation_schema=validation_schema,
            trace_id=trace_id,
            _visited=_visited if _visited is not None else {},
            _depth=_depth,
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
        _visited: dict[str, int] | None = None,
        _depth: int = 0,
    ) -> tuple[WorkerResult, list[WorkerResult]]:
        """Execute fallback workers sequentially after primary failure.

        Each fallback is dispatched through the full reliability path
        (circuit breaker check, retry, timeout, validation).  The first
        successful fallback ends the chain.  If all fail, a terminal
        failure result is returned with a summary error.

        Hop budget (``_visited`` / ``_depth``) is threaded through so
        fallbacks cannot reset cycle guards (audit M13).

        Returns:
            A tuple of ``(final_result, all_attempted_results)``.  The
            ``all_attempted_results`` list includes the primary result
            followed by each fallback result in order.
        """
        chain = FallbackChain(fallback_workers=fallback_chain)
        task_id = primary_result.task_id
        visited = _visited if _visited is not None else {}

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
                _visited=visited,
                _depth=_depth + 1,
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
        admission: Any = None,
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
            # Cycle/depth abort: count as failure for the source breaker so
            # half-open probes don't stick and we don't skip accounting.
            try:
                await asyncio.to_thread(breaker.record_failure, admission)
            except Exception:
                logger.warning("[SwarmEngine] could not record a handoff guard failure on a breaker",
                               exc_info=True)
                if hasattr(breaker, "release_probe"):
                    await asyncio.to_thread(breaker.release_probe)
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
            outcome_ok = True
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
            outcome_ok = False

        # The source's outcome, with its admission (SW-E11), and the shared
        # copy -- settings I/O -- in a thread (SW-E07).
        def _record_source(ok: bool = outcome_ok) -> None:
            if ok:
                breaker.record_success(admission)
            else:
                breaker.record_failure(admission)
            try:
                if hasattr(self, "_reliability") and hasattr(
                    self._reliability, "note_breaker_outcome"
                ):
                    self._reliability.note_breaker_outcome(source_worker.name)
                else:
                    breaker.persist_shared(source_worker.name)
            except Exception:
                logger.warning("[SwarmEngine] could not share the breaker of '%s'",
                               source_worker.name, exc_info=True)

        await asyncio.to_thread(_record_source)

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
        # Idempotent terminal finalize (audit H7): avoid double SSE/persist
        # when cancel() and CancelledError both race.
        prior_status = getattr(task, "status", None)
        if (
            prior_status in TERMINAL_STATUSES
            and task.id not in self._active_tasks
            and task.result is not None
        ):
            # A finished task is never reopened (SW-E03): a late "paused" from
            # a run the watchdog already ended used to bring it back.
            if status != getattr(task.result, "status", status):
                self._keep_as_evidence(task, status, error)
            return task.result

        task.status = _task_status_for(status)
        if status not in _RESULT_STATUS_TO_TASK:
            logger.warning("[SwarmEngine] task '%s' ended with an unknown status %r: recorded failed",
                           task.id, status)
        uncertain = status != "success" and any(
            not getattr(wr, "retry_safe", True) for wr in worker_results)
        if status == "partial" or uncertain:
            # What "completed" hides (SW-E08): only part of the work finished,
            # and a step that changed things did not finish.
            metadata = dict(metadata or {})
            metadata.setdefault("result_status", status)
            if uncertain:
                metadata["effects_uncertain"] = True
                metadata.setdefault("execution_outcome", "unknown")
                if task.status == TaskStatus.COMPLETED:
                    # A step that changes things did not finish: the task did
                    # not complete, whatever part of it did.
                    task.status = TaskStatus.FAILED
                tracker = self._effects.get(task.id)
                if tracker is not None and tracker.tools:
                    metadata.setdefault("invoked_tools", list(tracker.tools))
        task.completed_at = _utc_now_iso()

        # Remove from in-flight tracking (unless paused — paused tasks
        # stay visible until resumed or rejected).
        if task.status != TaskStatus.PAUSED:
            self._active_tasks.pop(task.id, None)
            self._task_handles.pop(task.id, None)
            self._close_open_checkpoint(task.id)

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

        # Persist to SQLite when a task store is configured. The store's
        # transition table refuses a write from a superseded run or over a
        # finished row; that result is kept as evidence (SW-E03).
        if self._task_store is not None:
            self._persist(task, status, error)

        return result

    def _persist(self, task: SwarmTask, status: str = "", error: str | None = None) -> None:
        """Write *task* as it is now. With an event loop running the write
        runs in a thread (Swarm audit SW-E07: every task write ran on the
        loop), after the task's previous write; :meth:`_flush_writes` waits
        for it. A write the transition table refuses is kept as evidence."""
        if self._task_store is None:
            return
        snapshot = SwarmTask.from_dict(task.to_dict())
        store = self._task_store

        def _write() -> None:
            try:
                if not store.persist_task(snapshot):
                    self._keep_as_evidence(snapshot, status or snapshot.status.value, error)
            except Exception:
                logger.exception("[SwarmEngine] failed to persist task '%s'", snapshot.id)
                record_swarm_failure("task_write")

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            _write()
            return
        previous = self._writes.get(task.id)

        async def _in_order() -> None:
            if previous is not None and not previous.done():
                await asyncio.wait({previous})
            await asyncio.to_thread(_write)

        from kazma_core.background import spawn_background

        write = spawn_background(_in_order(), name=f"swarm-persist:{task.id}")
        self._writes[task.id] = write

        def _done(finished: asyncio.Task, tid: str = task.id) -> None:
            if self._writes.get(tid) is finished:
                self._writes.pop(tid, None)

        write.add_done_callback(_done)

    async def _flush_writes(self, task_id: str) -> None:
        """Wait for *task_id*'s store writes so far (bounded)."""
        for _ in range(8):
            write = self._writes.get(task_id)
            if write is None or write.done():
                return
            await asyncio.wait({write}, timeout=_STOP_GRACE_S)

    def _persist_now(self, task: SwarmTask) -> None:
        """The checkpoint manager's pause write, ordered with the rest."""
        self._persist(task, "paused", None)

    def _keep_as_evidence(self, task: SwarmTask, status: str, error: str | None) -> None:
        """A late or superseded outcome: on the row as evidence, never as the
        task's result (Swarm audit SW-E03)."""
        logger.warning(
            "[SwarmEngine] task '%s': a %s outcome from run %s (generation %s) arrived after "
            "the task was recorded otherwise; kept as evidence",
            task.id, status, getattr(task, "run_id", ""), getattr(task, "generation", ""),
        )
        if self._task_store is None:
            return
        try:
            self._task_store.record_orphan(task.id, {
                "status": status,
                "error": error,
                "generation": int(getattr(task, "generation", 0) or 0),
                "run_id": getattr(task, "run_id", ""),
                "at": _utc_now_iso(),
            })
        except Exception:
            logger.warning("[SwarmEngine] could not keep the late outcome of '%s'", task.id, exc_info=True)
            record_swarm_failure("orphan_evidence")

    def _close_open_checkpoint(self, task_id: str) -> None:
        """Close the checkpoint of a paused pipeline that ended another way.

        Approve and reject close their own checkpoint. Cancel and the
        stale-task reaper end a paused pipeline too, and until 2026-09-25
        cancel left the checkpoint open: the panel kept offering Approve on
        a finished task, its auto-reject timer kept running, and its gate
        row stayed pending on the approvals list (the reaper dropped the
        entry but never settled the row). Every terminal finalize comes
        through here, so no ending can leave one behind.
        """
        if self._checkpoint_handler.close(task_id) is None:
            return
        logger.info(
            "[SwarmEngine] closed the open checkpoint of finished task '%s'", task_id
        )
        from kazma_core.swarm.checkpoint_manager import _gate_settle_pipeline

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop in this thread, so there is nothing to block.
            _gate_settle_pipeline(task_id, "deny")
            return
        from kazma_core.background import spawn_background

        spawn_background(
            asyncio.to_thread(_gate_settle_pipeline, task_id, "deny"),
            name=f"swarm-gate-settle:{task_id}",
        )

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
        """Load paused tasks from the store so they can be resumed.

        They are tracked as PAUSED in-flight tasks (Swarm audit SW-E09), not
        in history alone: the history is an LRU, and a restored pipeline
        evicted from it could not be cancelled, and its reject was never
        saved -- it came back paused at every boot. Paused tasks do not count
        against admission."""
        restored = self._checkpoint_mgr.restore_paused_tasks()
        for task in restored:
            if task.status == TaskStatus.PAUSED:
                self._active_tasks.setdefault(task.id, task)
        return restored

    async def arm_pending_checkpoint_timeouts(self) -> int:
        """Arm checkpoint auto-reject timeouts deferred during sync-boot restore."""
        return await self._checkpoint_mgr.arm_pending_checkpoint_timeouts()

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

    async def approve_checkpoint(self, task_id: str, *, wait: bool = True) -> TaskResult | None:
        """Approve a paused HITL checkpoint and resume the pipeline.

        Returns the final ``TaskResult`` after the remaining pipeline steps
        complete, or ``None`` if no active checkpoint exists for *task_id*.
        """
        # Terminal-state guard (audit M-S1): a task reaped as TIMEOUT by the
        # watchdog used to keep its paused entry approvable — the approve
        # re-executed the remaining steps (real LLM spend) and the fresh
        # result was then silently discarded by _finalize_task's idempotency
        # guard. refuse to approve anything already terminal.
        try:
            existing = (await asyncio.to_thread(self._task_store.get_task, task_id)
                        if self._task_store else None)
        except Exception:
            # The admission compare-and-set still refuses a second run (SW-E03).
            logger.warning("[SwarmEngine] could not read task %s before approving it", task_id, exc_info=True)
            existing = None
        if existing is not None and getattr(existing, "status", None) in TERMINAL_STATUSES:
            logger.warning(
                "[SwarmEngine] approve_checkpoint refused for terminal task %s "
                "(status=%s) — stale card", task_id, existing.status,
            )
            self._close_open_checkpoint(task_id)
            return existing.result if getattr(existing, "result", None) is not None else None

        # The same admission as dispatch (SW-E05): a resumed pipeline runs, so
        # it needs a slot; with none free the checkpoint stays open.
        cap = self._at_capacity()
        if cap is not None and self._checkpoint_handler.get_checkpoint(task_id) is not None:
            logger.warning("[SwarmEngine] approve of '%s' deferred: swarm at capacity (%d)", task_id, cap)
            return TaskResult(
                task_id=task_id,
                status="failed",
                error=(f"Swarm at capacity ({cap} concurrent tasks). The checkpoint stays open: "
                       "approve again when a running task finishes."),
                metadata={"admission_denied": True, "max_concurrent_tasks": cap, "checkpoint_open": True},
            )

        entry = self._checkpoint_handler.try_claim(task_id, "approving")
        if entry is None:
            return None

        checkpoint = entry.checkpoint
        task = entry.task
        worker_results = list(entry.worker_results)
        blackboard_data = dict(entry.blackboard_data)

        self._checkpoint_handler._cancel_timeout_if_foreign(entry)

        # PAUSED -> RUNNING at the next generation, recorded before anything
        # resumes (SW-E03, SW-E05): a second approval of this checkpoint --
        # another tab, another process -- finds the generation taken.
        task.status = TaskStatus.RUNNING
        task.generation = int(task.generation or 0) + 1
        task.run_id = _new_run_id()
        task.started_at = _utc_now_iso()  # the resumed run's own watchdog start
        self._active_tasks[task.id] = task
        held = await self._admit_to_store(task)
        if held is not None:
            # Another run resumed this checkpoint first: it owns the task now.
            self._active_tasks.pop(task.id, None)
            self._checkpoint_handler.close(task_id)
            return held

        checkpoint.status = "approved"
        checkpoint.needs_approval = False
        logger.info(
            "[SwarmEngine] checkpoint approved for pipeline '%s' at step %d, resuming",
            task_id,
            checkpoint.step,
        )
        # Gate registry (P4): record the decision. Best-effort.
        try:
            from kazma_core.swarm.checkpoint_manager import _gate_settle_pipeline

            await asyncio.to_thread(_gate_settle_pipeline, task_id, "approve")
        except Exception:
            logger.warning("[SwarmEngine] gate settle failed for '%s'", task_id, exc_info=True)

        # Remove from paused state before resuming.
        self._checkpoint_mgr.pop_paused_entry(task_id)

        next_step = checkpoint.step + 1
        return await self._run_owned(
            task,
            lambda: self._resume(task, next_step, worker_results, blackboard_data),
            resumed=True,
            wait=wait,
        )

    async def _journal_step(self, task: SwarmTask, step: int, state: str) -> None:
        """Record a resumed step's begin and commit on the task's row, under
        its generation (SW-E01): a run that dies mid-step leaves "begun" and
        is held as an unknown outcome at the next boot, never resumed or
        offered again (its row is RUNNING, not PAUSED)."""
        task.metadata = dict(task.metadata or {})
        task.metadata["step_journal"] = {
            "step": int(step), "state": state, "generation": int(task.generation or 0),
            "at": _utc_now_iso(),
        }
        if self._task_store is None:
            return
        try:
            await asyncio.to_thread(self._task_store.persist_task, task)
        except Exception:
            logger.warning("[SwarmEngine] could not journal step %d of '%s'", step, task.id, exc_info=True)

    async def _resume(
        self,
        task: SwarmTask,
        next_step: int,
        worker_results: list[WorkerResult],
        blackboard_data: dict[str, Any],
    ) -> TaskResult:
        """The resumed pipeline, as the engine's own run of the task."""
        started = perf_counter()
        try:
            pattern_result = await resume_pipeline(
                task,
                starting_step=next_step,
                worker_results=worker_results,
                blackboard_data=blackboard_data,
                dispatch_worker_by_name=self._dispatch_worker_by_name,
                on_step=self._journal_step,
            )
        except PipelineConfigurationError as exc:
            return self._finalize_task(
                task,
                worker_results=worker_results,
                status="failed",
                error=str(exc),
                duration_seconds=perf_counter() - started,
            )
        except asyncio.CancelledError:
            return self._interrupted(task, started)
        except Exception as exc:
            logger.exception("[SwarmEngine] resumed pipeline '%s' failed", task.id)
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
        ``None`` if *task_id* is neither at a checkpoint nor paused. A paused
        task with no checkpoint pending is closed the same way.
        """
        # "Already terminally finalized" is decided BEFORE the handler runs.
        # ``_checkpoint_handler.reject`` marks the shared task object failed
        # and gives it a result, and judging afterwards took this reject's own
        # change for an earlier terminal record: a pipeline restored after a
        # restart (in history, not in _active_tasks) was never saved as
        # rejected, and came back paused at every boot. Live 2026-09-25: four
        # rejected with 200s, still ``paused`` in Postgres.
        _before = _hist_get_task(self._task_history, self._task_lock, task_id)
        _already_final = (
            _before is not None
            and getattr(_before, "status", None) in TERMINAL_STATUSES
            and getattr(_before, "result", None) is not None
        )
        result = await self._checkpoint_handler.reject(task_id, reason=reason)
        if (
            result is None
            and _before is not None
            and getattr(_before, "status", None) == TaskStatus.PAUSED
        ):
            # Paused with no checkpoint pending: nothing can approve or resume
            # it. Product code only pauses a pipeline together with its
            # checkpoint (CheckpointManager.handle_pipeline_checkpoint); the
            # live store had one such row, left by a 2026-08-14 test run.
            # Rejecting closes it, where the route used to answer "not found"
            # for a task that exists.
            result = TaskResult(
                task_id=task_id,
                status="failed",
                error=f"{reason} (no checkpoint was pending)",
            )
        if result is not None:
            # Gate registry (P4): record the rejection. Best-effort.
            try:
                from kazma_core.swarm.checkpoint_manager import (
                    _gate_settle_pipeline,
                )

                await asyncio.to_thread(_gate_settle_pipeline, task_id, "deny")
            except Exception:
                logger.warning("[SwarmEngine] could not settle the gate row of rejected task '%s'",
                               task_id, exc_info=True)
                record_swarm_failure("gate_settle")
            # Clear in-flight maps (audit H6) — paused tasks stay in
            # _active_tasks until reject/resume.
            active = self._active_tasks.get(task_id)
            if active is not None:
                self._finalize_task(
                    active,
                    worker_results=list(getattr(result, "worker_results", None) or []),
                    status="failed",
                    error=reason,
                    duration_seconds=float(getattr(result, "duration_seconds", 0.0) or 0.0),
                    aggregated_output=getattr(result, "aggregated_output", None),
                    metadata=dict(getattr(result, "metadata", None) or {}),
                )
            else:
                # Update task history with the failed result — unless the
                # task already carried a terminal record BEFORE this reject
                # (reaped/cancelled while paused): overwriting would persist a
                # SECOND terminal result for the same task (deep-audit
                # 2026-08-19). See _already_final above for why "before".
                if _already_final:
                    logger.debug(
                        "[SwarmEngine] reject_checkpoint('%s'): task already "
                        "terminally finalized (status=%s) — skipping duplicate persist",
                        task_id,
                        getattr(_before, "status", None),
                    )
                else:
                    def _mark_failed(task: SwarmTask) -> None:
                        task.status = TaskStatus.FAILED
                        task.result = result
                        # The handler stamps this for a checkpointed task; a
                        # task with no checkpoint ends here and must say when,
                        # or retention dates it by its creation.
                        task.completed_at = task.completed_at or _utc_now_iso()

                    updated = _hist_update_task(
                        self._task_history,
                        self._task_lock,
                        task_id,
                        _mark_failed,
                        max_history=self._max_history,
                    )
                    if updated is not None:
                        self._persist(updated, "failed", reason)
                    else:
                        # If task not in history, store the result directly.
                        self._checkpoint_mgr.complete_pipeline(task_id, result)
            # Ensure active maps cleared even if finalize path missed
            self._active_tasks.pop(task_id, None)
            self._task_handles.pop(task_id, None)
            await self._flush_writes(task_id)
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
        """Dispatch *task* to one worker by name, through :meth:`dispatch`
        (Swarm audit SW-E06: it ran a summoned worker outside admission, the
        RUNNING row and cancellation). The task carries the fenced memory
        context (:func:`phonebook.memory_context`); a name the engine does not
        hold is summoned from the WorkerRegistry and registered first."""
        from kazma_core.swarm.phonebook import memory_context

        if self.get_worker(worker_name) is None:
            summoned = self._phonebook.summon(worker_name)
            if summoned is not None:
                self._workers.setdefault(worker_name, summoned)
        prompt = await memory_context(worker_name, task)
        result = await self.dispatch(SwarmTask(prompt=prompt, workers=[worker_name]))
        if result.worker_results:
            return result.worker_results[0].to_dict()
        return {"worker": worker_name, "task_id": result.task_id, "status": result.status,
                "output": "", "error": result.error}
