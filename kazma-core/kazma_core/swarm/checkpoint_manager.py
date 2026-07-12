"""CheckpointManager — extracted from SwarmEngine (P2-1 refactor).

Manages HITL (Human-in-the-Loop) pipeline checkpoint state: storing
paused pipelines, timeout-based auto-rejection, checkpoint info lookup,
and restoring paused tasks from SQLite on startup.

The resume/finalize logic (approve_checkpoint/reject_checkpoint) stays
on SwarmEngine because it calls back into dispatch (_dispatch_worker_by_name,
_finalize_task, resume_pipeline). This module owns the checkpoint *state*
lifecycle; the engine owns the *execution* lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from time import perf_counter
from typing import Any

from kazma_core.swarm.checkpoint import HITLCheckpoint, HITLCheckpointHandler
from kazma_core.swarm.patterns import PatternExecution
from kazma_core.swarm.task import SwarmTask, TaskResult, TaskStatus

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manages HITL checkpoint state for paused pipelines.

    Args:
        checkpoint_handler: The HITLCheckpointHandler instance.
        task_store: Optional TaskStore for persistence.
        task_history: Shared task history dict (owned by the engine).
        max_history: LRU cap for task history.
        task_lock: Shared ``threading.Lock`` (owned by the engine) that
            protects ``task_history`` mutations. Must be the same lock the
            engine uses; otherwise history writes from this manager race
            with engine reads/writes.
    """

    def __init__(
        self,
        checkpoint_handler: HITLCheckpointHandler,
        task_store: Any | None = None,
        task_history: dict[str, SwarmTask] | None = None,
        max_history: int = 500,
        task_lock: threading.Lock | None = None,
    ) -> None:
        self._checkpoint_handler = checkpoint_handler
        self._task_store = task_store
        self._task_history = task_history if task_history is not None else {}
        self._max_history = max_history
        # Engine owns the lock; we borrow it so history mutations are
        # consistent with _finalize_task / list_tasks. If not supplied we
        # fall back to a private lock (degraded but never unlocked).
        self._task_lock = task_lock if task_lock is not None else threading.Lock()

    def handle_pipeline_checkpoint(
        self,
        *,
        task: SwarmTask,
        pattern_result: PatternExecution,
        started: float,
    ) -> TaskResult:
        """Handle a pipeline that paused at an HITL checkpoint.

        Stores the paused state, sets up auto-reject timeout if configured,
        and returns a ``TaskResult`` with ``status="paused"``.
        """
        checkpoint_data = pattern_result.metadata.get("checkpoint", {})
        step = checkpoint_data.get("step", 0)
        worker = checkpoint_data.get("worker", "")
        output_preview = checkpoint_data.get("output_preview", "")
        task_id = checkpoint_data.get("task_id", task.id)

        checkpoint = HITLCheckpoint(
            task_id=task_id,
            step=step,
            worker=worker,
            output_preview=output_preview,
            needs_approval=True,
            status="pending",
        )

        blackboard_data = pattern_result.metadata.get("paused_blackboard", {})

        # Persist checkpoint info INTO task.metadata so restore_paused_tasks
        # can reconstruct the checkpoint after a restart.
        task.metadata["hitl_checkpoint"] = {
            "step": step,
            "worker": worker,
            "output_preview": output_preview,
            "task_id": task_id,
        }
        task.metadata["paused_blackboard"] = blackboard_data

        self._checkpoint_handler.store_paused_pipeline(
            task=task,
            checkpoint=checkpoint,
            worker_results=pattern_result.worker_results,
            blackboard_data=blackboard_data,
        )

        # Set up checkpoint timeout auto-reject if configured.
        timeout_seconds = task.metadata.get("checkpoint_timeout")
        if timeout_seconds and float(timeout_seconds) > 0:
            timeout_task = asyncio.create_task(
                self._checkpoint_timeout_reject(
                    task_id,
                    float(timeout_seconds),
                )
            )
            self._checkpoint_handler.set_timeout_task(task_id, timeout_task)

        # Store in task history as paused.
        task.status = TaskStatus.PAUSED
        task.completed_at = None  # Not completed yet.
        duration = perf_counter() - started

        result = TaskResult(
            task_id=task.id,
            status="paused",
            worker_results=pattern_result.worker_results,
            aggregated_output=pattern_result.aggregated_output,
            total_cost=sum(item.cost for item in pattern_result.worker_results),
            total_tokens=sum(item.tokens_used for item in pattern_result.worker_results),
            duration_seconds=duration,
            metadata=pattern_result.metadata,
        )
        task.result = result
        # Record into shared history under the engine's lock so concurrent
        # list_tasks / _finalize_task can't observe a torn dict.
        with self._task_lock:
            self._task_history[task.id] = SwarmTask.from_dict(task.to_dict())
            if len(self._task_history) > self._max_history:
                excess = len(self._task_history) - self._max_history
                for old_key in list(self._task_history.keys())[:excess]:
                    self._task_history.pop(old_key, None)

        # Persist paused state to SQLite so it survives restart.
        if self._task_store is not None:
            try:
                self._task_store.persist_task(task)
            except Exception:
                logger.exception(
                    "[CheckpointManager] failed to persist paused task '%s'", task.id
                )

        logger.info(
            "[CheckpointManager] pipeline '%s' paused at HITL checkpoint step %d",
            task.id,
            step,
        )
        return result

    async def _checkpoint_timeout_reject(
        self,
        task_id: str,
        timeout_seconds: float,
    ) -> None:
        """Auto-reject a checkpoint after the timeout expires.

        Delegates the actual rejection to the reject_callback set by the engine.
        """
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return

        logger.warning(
            "[CheckpointManager] HITL checkpoint for task '%s' timed out after %.2fs, auto-rejecting",
            task_id,
            timeout_seconds,
        )
        # The engine registers a reject callback so we don't need a
        # circular import back to SwarmEngine.
        if hasattr(self, "_reject_callback") and self._reject_callback:
            await self._reject_callback(
                task_id,
                reason=f"Checkpoint timed out after {timeout_seconds:g}s.",
            )

    def set_reject_callback(self, callback: Any) -> None:
        """Register the engine's reject_checkpoint as the timeout callback."""
        self._reject_callback = callback

    def get_checkpoint_info(self, task_id: str) -> HITLCheckpoint | None:
        """Return checkpoint info for a paused task, or ``None``."""
        return self._checkpoint_handler.get_checkpoint(task_id)

    def restore_paused_tasks(self) -> list[SwarmTask]:
        """Load paused tasks from SQLite so they can be resumed.

        Called on engine startup to restore HITL checkpoint state that
        was persisted before a restart. Returns the list of restored
        paused tasks.
        """
        if self._task_store is None:
            return []
        paused_tasks = self._task_store.get_paused_tasks()
        for task in paused_tasks:
            # Restore into shared history under the engine lock (avoids racing
            # a concurrent list_tasks / _finalize_task).
            with self._task_lock:
                self._task_history[task.id] = task
            # Restore the checkpoint handler state if metadata is present.
            checkpoint_meta = task.metadata.get("hitl_checkpoint", {})
            if checkpoint_meta:
                checkpoint = HITLCheckpoint(
                    task_id=task.id,
                    step=checkpoint_meta.get("step", 0),
                    worker=checkpoint_meta.get("worker", ""),
                    output_preview=checkpoint_meta.get("output_preview", ""),
                    needs_approval=True,
                    status="pending",
                )
                worker_results = (
                    list(task.result.worker_results) if task.result else []
                )
                blackboard_data = task.metadata.get("paused_blackboard", {})
                self._checkpoint_handler.store_paused_pipeline(
                    task=task,
                    checkpoint=checkpoint,
                    worker_results=worker_results,
                    blackboard_data=blackboard_data,
                )
                # Re-arm the auto-reject timeout. Previously this was only
                # armed on first pause (handle_pipeline_checkpoint); after a
                # restart the paused task would hang forever (never auto-
                # rejected, never resumed) until manual action.
                timeout_seconds = task.metadata.get("checkpoint_timeout")
                if timeout_seconds and float(timeout_seconds) > 0:
                    timeout_task = asyncio.create_task(
                        self._checkpoint_timeout_reject(
                            task.id,
                            float(timeout_seconds),
                        )
                    )
                    self._checkpoint_handler.set_timeout_task(task.id, timeout_task)
                logger.info(
                    "[CheckpointManager] restored paused pipeline '%s' at step %d",
                    task.id,
                    checkpoint.step,
                )
        return paused_tasks

    def get_paused_entry(self, task_id: str) -> Any:
        """Return the paused checkpoint entry, or None."""
        return self._checkpoint_handler._paused.get(task_id)

    def pop_paused_entry(self, task_id: str) -> Any:
        """Remove and return a paused checkpoint entry."""
        return self._checkpoint_handler._paused.pop(task_id, None)

    def complete_pipeline(self, task_id: str, result: Any) -> None:
        """Mark a pipeline as complete in the checkpoint handler."""
        self._checkpoint_handler.complete_pipeline(task_id, result)
