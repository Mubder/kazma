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

__all__ = ["WorkerPhonebook"]

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
        1. Past successful strategies from V2 `recall.search` (fenced).
        2. Past evolution learnings specific to this worker (fenced).
        """
        worker = self.summon(worker_name)
        if worker is None:
            return {"synthesis": f"Worker '{worker_name}' not found", "opinions": []}

        # Inject episodic memory + evolution learnings before dispatch.
        # V2-native recall for past strategies + evolution learnings.
        # The recalled content is untrusted (it originates from past
        # conversation/tool output), so it is fenced via format_untrusted_block
        # before interpolation — the same defense the supervisor chat path uses.
        enriched = task
        try:
            from kazma_core.memory.recall import search
            from kazma_core.memory.config import resolve_tenant_id
            from kazma_core.safety.prompt_fence import format_untrusted_block

            # recall.search is sync SQLite + potential embedding work (first-use
            # SentenceTransformer load is ~12s) — run off the event loop so
            # concurrent SSE/WS turns aren't stalled.
            # resolve_tenant_id requires a platform arg; swarm dispatch has no
            # request platform, so use the "system" convention (mirrors
            # self_improvement). Without it the call raised TypeError and
            # enrichment was silently skipped (workers dispatched unfenced).
            import asyncio as _aio

            _tenant = resolve_tenant_id("system", prefer_context=True)
            strategies_hits = await _aio.to_thread(search, task, limit=3, tenant_id=_tenant)
            evo_hits = await _aio.to_thread(
                search, f"{worker_name} evolution learning", limit=2, tenant_id=_tenant
            )

            # Process past strategies
            if isinstance(strategies_hits, list) and strategies_hits:
                strategies = [
                    h["content"] or h["metadata"].get("summary", "") for h in strategies_hits
                ]
                episodic = " | ".join(s for s in strategies if s)
                if episodic:
                    fenced = format_untrusted_block(
                        f"PREVIOUS_SUCCESSFUL_STRATEGIES: {episodic[:1500]}",
                        source="episodic_memory",
                    )
                    enriched = f"{fenced}\n\n{task}"

            # Process past evolution learnings
            if isinstance(evo_hits, list) and evo_hits:
                learnings = [h["content"] for h in evo_hits if h["content"]]
                if learnings:
                    learning_ctx = "\n".join(f"- {l[:300]}" for l in learnings)
                    fenced = format_untrusted_block(
                        f"PAST_LEARNINGS_FOR_THIS_WORKER:\n{learning_ctx}",
                        source="soul_evolution",
                    )
                    enriched = f"{fenced}\n\n{enriched}"
        except Exception as exc:
            logger.warning("[phonebook] Memory enrichment failed — dispatching without context: %s", exc)

        result = await worker.dispatch(enriched)
        return {"synthesis": result.get("output", ""), "opinions": [result]}
