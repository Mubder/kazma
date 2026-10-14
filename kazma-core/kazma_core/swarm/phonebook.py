"""WorkerPhonebook — extracted from SwarmEngine (P2-1 refactor).

Implements the "phonebook" pattern: query the WorkerRegistry by name and
instantiate a ready SwarmWorker, and build the fenced memory context a
by-name dispatch carries (:func:`memory_context`). It runs nothing: its
summon-and-dispatch ran a worker outside the engine's admission, RUNNING row
and cancellation, and was removed (Swarm audit SW-E06, 2026-10-14) -- the
engine's ``dispatch_by_name`` goes through ``dispatch()``.

The dead ``consult()`` method was removed during extraction (it had
zero callers repo-wide).
"""

from __future__ import annotations

import logging

from kazma_core.swarm.task import WorkerCapabilities
from kazma_core.swarm.worker import InProcessWorker, SwarmWorker

__all__ = ["WorkerPhonebook", "memory_context"]

logger = logging.getLogger(__name__)


async def memory_context(worker_name: str, task: str) -> str:
    """*task* with what memory holds for it: past successful strategies and
    this worker's past learnings from V2 ``recall.search``, each FENCED
    (``format_untrusted_block``) -- the content comes from past conversations
    and tool output, never instructions. Searched off the loop. Memory that
    cannot be read leaves the task as it is (a WARNING)."""
    enriched = task
    try:
        import asyncio as _aio

        from kazma_core.memory.config import resolve_tenant_id
        from kazma_core.memory.recall import search
        from kazma_core.safety.prompt_fence import format_untrusted_block

        # resolve_tenant_id needs a platform; swarm work has none ("system",
        # as self_improvement does).
        tenant = resolve_tenant_id("system", prefer_context=True)
        strategies_hits = await _aio.to_thread(search, task, limit=3, tenant_id=tenant)
        evo_hits = await _aio.to_thread(
            search, f"{worker_name} evolution learning", limit=2, tenant_id=tenant
        )
        if isinstance(strategies_hits, list) and strategies_hits:
            strategies = [h.get("content") or (h.get("metadata") or {}).get("summary", "")
                          for h in strategies_hits]
            episodic = " | ".join(s for s in strategies if s)
            if episodic:
                fenced = format_untrusted_block(
                    f"PREVIOUS_SUCCESSFUL_STRATEGIES: {episodic[:1500]}",
                    source="episodic_memory",
                )
                enriched = f"{fenced}\n\n{task}"
        if isinstance(evo_hits, list) and evo_hits:
            learnings = [h.get("content") for h in evo_hits if h.get("content")]
            if learnings:
                learning_ctx = "\n".join(f"- {item[:300]}" for item in learnings)
                fenced = format_untrusted_block(
                    f"PAST_LEARNINGS_FOR_THIS_WORKER:\n{learning_ctx}",
                    source="soul_evolution",
                )
                enriched = f"{fenced}\n\n{enriched}"
    except Exception as exc:
        logger.warning("[phonebook] Memory enrichment failed -- dispatching without context: %s", exc)
    return enriched


class WorkerPhonebook:
    """Summon workers from the WorkerRegistry."""

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
