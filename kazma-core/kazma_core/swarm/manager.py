"""Backward-compatible wrapper over :class:`kazma_core.swarm.engine.SwarmEngine`."""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import SwarmTask, TaskType
from kazma_core.swarm.task_store import TaskStore
from kazma_core.swarm.worker import SwarmWorker

__all__ = ["SwarmManager"]

logger = logging.getLogger(__name__)


class SwarmManager:
    """Backward-compatible façade for the shared swarm engine.

    Args:
        config: A :class:`SwarmConfig` describing the swarm topology.
        task_store: Optional persistence store for tasks and metrics.
    """

    def __init__(self, config: SwarmConfig, task_store: TaskStore | None = None) -> None:
        self.config = config
        self.engine = SwarmEngine(config, task_store=task_store)
        self._workers = self.engine._workers

    def add_worker(self, wc: WorkerConfig) -> None:
        """Register a new worker via the shared engine."""
        self.engine.add_worker(wc)
        logger.info("[SwarmManager] registered worker '%s' (type=%s)", wc.name, wc.type)

    def remove_worker(self, name: str) -> SwarmWorker:
        """Unregister a worker by name."""
        worker = self.engine.remove_worker(name)
        logger.info("[SwarmManager] removed worker '%s'", name)
        return worker

    def get_worker(self, name: str) -> SwarmWorker | None:
        """Look up a worker by name."""
        return self.engine.get_worker(name)

    @property
    def worker_names(self) -> list[str]:
        """Return sorted list of registered worker names."""
        return self.engine.worker_names

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        worker_name: str,
        task: str,
        context: str = "",
    ) -> dict[str, Any]:
        """Send a task to a specific worker.

        Args:
            worker_name: Name of the target worker.
            task:        Task description / prompt.
            context:     Optional background context.

        Returns:
            Result dict from the worker.

        Raises:
            KeyError: If the worker doesn't exist.
        """
        result = await self.engine.dispatch(
            SwarmTask(
                prompt=task,
                context=context,
                workers=[worker_name],
                type=TaskType.DISPATCH,
            )
        )
        if result.status == "failed" and not result.worker_results:
            raise KeyError(result.error or f"Worker '{worker_name}' not found.")
        if not result.worker_results:
            return {
                "worker": worker_name,
                "task_id": result.task_id,
                "status": result.status,
                "output": "",
                "error": result.error,
            }
        return result.worker_results[0].to_dict()

    async def broadcast(
        self,
        task: str,
        context: str = "",
    ) -> list[dict[str, Any]]:
        """Send a task to all registered workers in parallel.

        Returns:
            List of result dicts, one per worker.
        """
        result = await self.engine.broadcast(
            SwarmTask(
                prompt=task,
                context=context,
                type=TaskType.BROADCAST,
            )
        )
        return [worker_result.to_dict() for worker_result in result.worker_results]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_all(self) -> None:
        """Start all registered workers."""
        await self.engine.start_all()
        logger.info("[SwarmManager] all %d workers started", len(self._workers))

    async def stop_all(self) -> None:
        """Stop all registered workers."""
        await self.engine.stop_all()
        logger.info("[SwarmManager] all workers stopped")

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def status(self) -> list[dict[str, Any]]:
        """Return health status for each registered worker."""
        return await self.engine.status()
