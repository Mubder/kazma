"""WorkerPhonebook — extracted from SwarmEngine (P2-1 refactor).

Implements the "phonebook" pattern: query the WorkerRegistry by name,
instantiate a ready SwarmWorker, and dispatch tasks to it. This path
bypasses the engine's reliability layer (circuit breakers, retries) —
it's used for direct summon-and-dispatch from the topology/DAG executor.

The dead ``consult()`` method was removed during extraction (it had
zero callers repo-wide).
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.swarm.task import WorkerCapabilities
from kazma_core.swarm.worker import InProcessWorker, SwarmWorker

logger = logging.getLogger(__name__)


class WorkerPhonebook:
    """Summon workers from the WorkerRegistry and dispatch tasks to them."""

    def summon(self, worker_name: str) -> SwarmWorker | None:
        """Instantiate a worker from the WorkerRegistry by name.

        Fetches the worker's Soul (system_prompt), applies the configured
        model/provider, and returns a ready SwarmWorker instance.

        This is the "phonebook" pattern: query the registry, get the
        entry, build the worker.
        """
        from kazma_core.swarm.registry import get_worker_registry

        registry = get_worker_registry()
        entry = registry.get(worker_name)
        if entry is None:
            logger.warning("[Phonebook] summon failed — no worker named '%s'", worker_name)
            return None
        if not entry.enabled:
            logger.warning("[Phonebook] summon skipped — worker '%s' is disabled", worker_name)
            return None

        # All worker types resolve to InProcessWorker (the legacy
        # TelegramWorker subprocess path was vestigial and is removed).
        return InProcessWorker(
            name=entry.name,
            role=entry.roles[0] if entry.roles else "leaf",
            model=entry.model,
            provider=entry.provider,
            system_prompt=entry.system_prompt,
            capabilities=WorkerCapabilities(
                role=entry.roles[0] if entry.roles else "leaf",
                expertise=entry.expertise,
                tools=getattr(entry, "tools", []),
            ),
        )

    async def dispatch_by_name(self, worker_name: str, task: str) -> dict[str, Any]:
        """Summon a worker by name and dispatch a task with episodic memory context.

        Injects both:
        1. Past successful strategies from the 4-layer memory adapter.
        2. Past evolution learnings (SoulEvolution entries) specific to this worker.
        """
        worker = self.summon(worker_name)
        if worker is None:
            return {"synthesis": f"Worker '{worker_name}' not found", "opinions": []}

        # Inject episodic memory + evolution learnings before dispatch.
        # Issue both queries in parallel to avoid doubling dispatch latency.
        enriched = task
        try:
            import asyncio as _asyncio
            from kazma_core.swarm.memory.adapter import get_adapter
            adapter = get_adapter()
            if adapter is not None:
                # Co-issue both memory queries in parallel
                strategies_hits, evo_hits = await _asyncio.gather(
                    adapter.search(task, limit=3),
                    adapter.search(f"{worker_name} evolution learning", limit=2),
                    return_exceptions=True,
                )

                # Process past strategies
                if isinstance(strategies_hits, list) and strategies_hits:
                    strategies = [h.content or h.metadata.get("summary", "") for h in strategies_hits]
                    episodic = " | ".join(s for s in strategies if s)
                    if episodic:
                        enriched = f"PREVIOUS_SUCCESSFUL_STRATEGIES: {episodic[:1500]}\n\n{task}"

                # Process past evolution learnings
                if isinstance(evo_hits, list) and evo_hits:
                    learnings = [h.content for h in evo_hits if h.content]
                    if learnings:
                        learning_ctx = "\n".join(f"- {l[:300]}" for l in learnings)
                        enriched = f"PAST_LEARNINGS_FOR_THIS_WORKER:\n{learning_ctx}\n\n{enriched}"
        except Exception as exc:
            logger.debug("Episodic memory lookup failed: %s", exc)

        result = await worker.dispatch(enriched)
        return {"synthesis": result.get("output", ""), "opinions": [result]}
