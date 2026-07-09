"""Handoff cycle detection helpers — extracted from SwarmEngine (S5).

AGENTS.md: max depth 5; workers revisitable up to MAX_VISITS=2
(allows legitimate A→B→A return handoffs).
"""

from __future__ import annotations

import logging
from time import perf_counter

from kazma_core.swarm.task import WorkerResult

logger = logging.getLogger(__name__)

MAX_HANDOFF_DEPTH = 5
MAX_VISITS = 2


def register_visit(visited: dict[str, int], worker_name: str) -> None:
    """Increment visit count for *worker_name* (mutates *visited*)."""
    visited[worker_name] = visited.get(worker_name, 0) + 1


def handoff_guard_error(
    *,
    source_worker: str,
    target_worker: str,
    visited: dict[str, int],
    depth: int,
    started: float,
    max_depth: int = MAX_HANDOFF_DEPTH,
    max_visits: int = MAX_VISITS,
) -> WorkerResult | None:
    """Return an error WorkerResult if depth/cycle limits are hit, else None.

    Call *register_visit* for the source worker before invoking this.
    """
    if depth >= max_depth:
        logger.error(
            "[handoff_guards] Handoff chain too deep (%d) — aborting",
            depth,
        )
        return WorkerResult(
            worker=source_worker,
            task_id="",
            status="error",
            output="",
            error=f"Handoff chain exceeded max depth ({max_depth}). Possible cycle.",
            duration_seconds=perf_counter() - started,
        )

    target_visits = visited.get(target_worker, 0)
    if target_visits >= max_visits:
        logger.error(
            "[handoff_guards] Handoff cycle detected: %s -> %s (visited %dx, max %d)",
            source_worker,
            target_worker,
            target_visits,
            max_visits,
        )
        return WorkerResult(
            worker=source_worker,
            task_id="",
            status="error",
            output="",
            error=f"Handoff cycle detected: {source_worker} -> {target_worker}",
            duration_seconds=perf_counter() - started,
        )
    return None


def build_available_workers_list(workers: dict) -> list[dict]:
    """Build worker info dicts for the capability router."""
    return [
        {
            "name": worker.name,
            "role": worker.role,
            "capabilities": worker.capabilities,
        }
        for worker in workers.values()
    ]
