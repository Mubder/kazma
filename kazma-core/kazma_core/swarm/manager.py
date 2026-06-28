"""SwarmManager — unified orchestration layer for Kazma workers.

Workers are registered from :class:`SwarmConfig` and dispatched tasks via
:meth:`dispatch` (single worker) or :meth:`broadcast` (all workers).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.worker import (
    InProcessWorker,
    SwarmWorker,
    TelegramWorker,
)

logger = logging.getLogger(__name__)


class SwarmManager:
    """Manages a pool of :class:`SwarmWorker` instances.

    Args:
        config: A :class:`SwarmConfig` describing the swarm topology.
    """

    def __init__(self, config: SwarmConfig) -> None:
        self.config = config
        self._workers: dict[str, SwarmWorker] = {}
        self._build_workers()

    # ------------------------------------------------------------------
    # Worker registry
    # ------------------------------------------------------------------

    def _build_workers(self) -> None:
        """Instantiate workers from the config."""
        for wc in self.config.workers:
            self.add_worker(wc)

    def add_worker(self, wc: WorkerConfig) -> None:
        """Register a new worker from a :class:`WorkerConfig`.

        Raises:
            ValueError: If a worker with the same name already exists.
        """
        if wc.name in self._workers:
            raise ValueError(f"Worker '{wc.name}' already registered.")

        if wc.type == "in_process":
            worker: SwarmWorker = InProcessWorker(
                name=wc.name,
                role=wc.role,
                model=wc.model,
                provider=wc.provider,
            )
        elif wc.type == "telegram_bot":
            worker = TelegramWorker(
                name=wc.name,
                profile=wc.profile,
                bot_token_env=wc.bot_token_env,
                group_chat_id=self.config.group_chat_id,
                role=wc.role,
                model=wc.model,
                provider=wc.provider,
            )
        else:
            raise ValueError(f"Unknown worker type: '{wc.type}'")

        self._workers[wc.name] = worker
        logger.info("[SwarmManager] registered worker '%s' (type=%s)", wc.name, wc.type)

    def remove_worker(self, name: str) -> SwarmWorker:
        """Unregister a worker by name.

        Raises:
            KeyError: If the worker doesn't exist.
        """
        if name not in self._workers:
            raise KeyError(f"Worker '{name}' not found.")
        worker = self._workers.pop(name)
        logger.info("[SwarmManager] removed worker '%s'", name)
        return worker

    def get_worker(self, name: str) -> SwarmWorker | None:
        """Look up a worker by name."""
        return self._workers.get(name)

    @property
    def worker_names(self) -> list[str]:
        """Return sorted list of registered worker names."""
        return sorted(self._workers.keys())

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
        worker = self._workers.get(worker_name)
        if worker is None:
            raise KeyError(f"Worker '{worker_name}' not found.")
        return await worker.dispatch(task, context=context)

    async def broadcast(
        self,
        task: str,
        context: str = "",
    ) -> list[dict[str, Any]]:
        """Send a task to all registered workers in parallel.

        Returns:
            List of result dicts, one per worker.
        """
        if not self._workers:
            logger.warning("[SwarmManager] broadcast called with no workers")
            return []

        coros = [
            worker.dispatch(task, context=context)
            for worker in self._workers.values()
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)

        output: list[dict[str, Any]] = []
        for worker, result in zip(self._workers.values(), results):
            if isinstance(result, BaseException):
                output.append({
                    "worker": worker.name,
                    "task_id": "",
                    "status": "error",
                    "output": "",
                    "error": str(result)[:500],
                })
            elif isinstance(result, dict):
                output.append(result)
            else:
                output.append({
                    "worker": worker.name,
                    "task_id": "",
                    "status": "error",
                    "output": "",
                    "error": f"Unexpected result type: {type(result).__name__}",
                })
        return output

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_all(self) -> None:
        """Start all registered workers."""
        for worker in self._workers.values():
            await worker.start()
        logger.info("[SwarmManager] all %d workers started", len(self._workers))

    async def stop_all(self) -> None:
        """Stop all registered workers."""
        for worker in self._workers.values():
            await worker.stop()
        logger.info("[SwarmManager] all workers stopped")

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def status(self) -> list[dict[str, Any]]:
        """Return health status for each registered worker."""
        return [await worker.status() for worker in self._workers.values()]
