"""Worker instantiation factory — extracted from SwarmEngine (S3/S5)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kazma_core.swarm.config import WorkerConfig
from kazma_core.swarm.worker import InProcessWorker, SwarmWorker

__all__ = ["create_worker", "register_worker", "unregister_worker"]

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Types that resolve to InProcessWorker (telegram_bot kept for legacy configs)
_IN_PROCESS_TYPES = frozenset({"in_process", "telegram_bot"})


def create_worker(worker_config: WorkerConfig) -> SwarmWorker:
    """Instantiate a concrete worker from its config.

    ``telegram_bot`` is accepted for backward compatibility with
    persisted configs but resolves to :class:`InProcessWorker`
    (the legacy ``TelegramWorker`` subprocess path was removed).
    """
    if worker_config.type in _IN_PROCESS_TYPES:
        return InProcessWorker(
            name=worker_config.name,
            role=worker_config.role,
            model=worker_config.model,
            provider=worker_config.provider,
            capabilities=worker_config.capabilities,
            system_prompt=getattr(worker_config, "system_prompt", ""),
        )
    raise ValueError(f"Unknown worker type: '{worker_config.type}'")


def register_worker(
    workers: dict[str, SwarmWorker],
    worker_config: WorkerConfig,
    *,
    factory=create_worker,
) -> SwarmWorker:
    """Create and register a worker; raises if name already present."""
    if worker_config.name in workers:
        raise ValueError(f"Worker '{worker_config.name}' already registered.")
    worker = factory(worker_config)
    workers[worker_config.name] = worker
    logger.info(
        "[worker_factory] registered worker '%s' (type=%s)",
        worker_config.name,
        worker_config.type,
    )
    return worker


def unregister_worker(
    workers: dict[str, SwarmWorker],
    name: str,
    *,
    on_removed=None,
) -> SwarmWorker:
    """Remove a worker by name; optional *on_removed(name)* for cleanup hooks."""
    if name not in workers:
        raise KeyError(f"Worker '{name}' not found.")
    worker = workers.pop(name)
    if on_removed is not None:
        on_removed(name)
    logger.info("[worker_factory] unregistered worker '%s'", name)
    return worker
