"""Human-in-the-Loop (HITL) checkpoint support for pipeline execution.

Pipelines can define pause points via ``metadata.hitl_checkpoints`` -- a list
of 1-based step indices where execution halts after the step completes.  The
engine stores the paused pipeline state and exposes approve/reject controls.

When a checkpoint timeout is configured (``metadata.checkpoint_timeout``),
the engine auto-rejects the checkpoint after the specified number of seconds.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from kazma_core.swarm.task import SwarmTask, TaskResult, WorkerResult

__all__ = ["HITLCheckpoint", "HITLCheckpointHandler"]

logger = logging.getLogger(__name__)


@dataclass
class HITLCheckpoint:
    """Information about a pending HITL checkpoint."""

    task_id: str
    step: int
    worker: str
    output_preview: str
    needs_approval: bool = True
    status: str = "pending"  # pending | approved | rejected


@dataclass
class _PausedPipeline:
    """Internal state for a pipeline paused at an HITL checkpoint."""

    task: SwarmTask
    checkpoint: HITLCheckpoint
    worker_results: list[WorkerResult]
    blackboard_data: dict[str, Any]
    completion_event: asyncio.Event = field(default_factory=asyncio.Event)
    timeout_task: asyncio.Task[None] | None = None
    final_result: TaskResult | None = None
    claim: str = "pending"  # pending | approving | rejecting


class HITLCheckpointHandler:
    """Manages HITL checkpoint state for the swarm engine.

    Tracks paused pipelines and coordinates approve/reject/timeout flows.
    """

    def __init__(self) -> None:
        self._paused: dict[str, _PausedPipeline] = {}

    def has_active_checkpoint(self, task_id: str) -> bool:
        """Return whether the given task has an active checkpoint."""
        return task_id in self._paused

    def get_checkpoint(self, task_id: str) -> HITLCheckpoint | None:
        """Return the checkpoint info for a paused task, or ``None``."""
        entry = self._paused.get(task_id)
        return entry.checkpoint if entry else None

    def store_paused_pipeline(
        self,
        *,
        task: SwarmTask,
        checkpoint: HITLCheckpoint,
        worker_results: list[WorkerResult],
        blackboard_data: dict[str, Any],
    ) -> None:
        """Store a pipeline that has been paused at a checkpoint."""
        self._paused[task.id] = _PausedPipeline(
            task=task,
            checkpoint=checkpoint,
            worker_results=list(worker_results),
            blackboard_data=dict(blackboard_data),
        )
        logger.info(
            "[HITL] pipeline '%s' paused at step %d (checkpoint for worker '%s')",
            task.id,
            checkpoint.step,
            checkpoint.worker,
        )

    def set_timeout_task(
        self,
        task_id: str,
        timeout_task: asyncio.Task[None],
    ) -> None:
        """Associate a timeout auto-reject task with a paused pipeline."""
        entry = self._paused.get(task_id)
        if entry:
            entry.timeout_task = timeout_task

    def try_claim(self, task_id: str, action: str) -> _PausedPipeline | None:
        """Claim a pending checkpoint before any await (audit T-2 / M-14).

        ``action`` is ``approving`` or ``rejecting``. Returns the entry, or
        ``None`` if missing or already claimed.
        """
        entry = self._paused.get(task_id)
        if entry is None:
            return None
        if entry.claim != "pending":
            return None
        entry.claim = action
        return entry

    def _cancel_timeout_if_foreign(self, entry: _PausedPipeline) -> None:
        """Cancel a timeout arm unless it is the currently running task.

        The auto-reject path IS that timeout task. Cancelling it here used
        to raise CancelledError on the next await in ``reject_checkpoint``,
        so the task stayed PAUSED (audit T-2).
        """
        task = entry.timeout_task
        entry.timeout_task = None
        if task is None or task.done():
            return
        current = asyncio.current_task()
        if current is not None and task is current:
            return
        task.cancel()

    async def reject(
        self,
        task_id: str,
        reason: str = "Checkpoint rejected by user",
    ) -> TaskResult | None:
        """Reject a paused pipeline.

        Returns the finalized ``TaskResult`` with status ``failed``, or
        ``None`` if no active checkpoint exists.
        """
        entry = self.try_claim(task_id, "rejecting")
        if entry is None:
            return None

        self._cancel_timeout_if_foreign(entry)

        entry.checkpoint.status = "rejected"
        entry.checkpoint.needs_approval = False
        logger.info(
            "[HITL] checkpoint rejected for pipeline '%s' at step %d",
            task_id,
            entry.checkpoint.step,
        )

        # Finalize the task as failed.
        result = TaskResult(
            task_id=task_id,
            status="failed",
            worker_results=list(entry.worker_results),
            aggregated_output=entry.worker_results[-1].output
            if entry.worker_results
            else None,
            error=reason,
        )
        entry.final_result = result
        entry.task.status = "failed"  # type: ignore[assignment]
        entry.task.completed_at = datetime.now(UTC).isoformat()
        entry.task.result = result

        # Signal any waiting approve() callers that the result is ready.
        entry.completion_event.set()

        # Remove from active pausing.
        self._paused.pop(task_id, None)
        return result

    def complete_pipeline(
        self,
        task_id: str,
        result: TaskResult,
    ) -> None:
        """Store the final result for a paused pipeline that was approved.

        Called by the engine after the remaining steps finish.
        """
        entry = self._paused.get(task_id)
        if entry:
            entry.final_result = result
            # Signal waiters (approve callers) that the result is ready.
            entry.completion_event.set()
            # Clean up.
            self._paused.pop(task_id, None)

    def cleanup(self, task_id: str) -> None:
        """Remove a paused pipeline entry (cleanup on error/completion)."""
        entry = self._paused.pop(task_id, None)
        if entry and entry.timeout_task is not None and not entry.timeout_task.done():
            entry.timeout_task.cancel()

    @property
    def active_checkpoint_count(self) -> int:
        """Return the number of currently active checkpoints."""
        return len(self._paused)
