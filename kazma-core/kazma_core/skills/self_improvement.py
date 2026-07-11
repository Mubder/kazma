"""Native Self-Improvement Engine — "Soul Evolution".

After every pipeline completion or validation failure, this skill queries
the 4-layer memory adapter for historical performance profiles, computes
deltas, and writes optimised system-prompt refinements back to the
worker's Soul via ``WorkerRegistry.update()``.

Security boundary:
    CAN modify:   WorkerEntry.system_prompt, WorkerEntry.metadata
    CANNOT modify: SafetyMiddleware, tool registry, security thresholds
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Bound self-improvement prompt growth so approved deltas cannot accumulate
# forever and eventually overflow the model context window.
_MAX_SYSTEM_PROMPT_CHARS = 8000
_MAX_EVOLUTION_BLOCKS = 12
_MAX_DELTA_CHARS = 1000  # per-delta cap so one runaway delta can't blow the prompt
_EVOLUTION_MARKER = "[SelfImprovement]"


def _cap_evolution_prompt(base_prompt: str, new_delta: str) -> str:
    """Append a SelfImprovement delta, bounded by max chars/blocks.

    Splits *base_prompt* into the original Soul text and any previously
    accumulated ``[SelfImprovement]`` blocks, appends *new_delta*, then
    keeps only the most recent blocks that fit within
    ``_MAX_SYSTEM_PROMPT_CHARS`` and ``_MAX_EVOLUTION_BLOCKS`` so the
    system prompt cannot grow without bound across pipeline runs.
    """
    if not new_delta:
        return base_prompt
    soul, _, rest = base_prompt.partition(_EVOLUTION_MARKER)
    soul = soul.rstrip()
    existing = [
        f"\n\n{_EVOLUTION_MARKER}{blk}"
        for blk in rest.split(_EVOLUTION_MARKER)
        if blk.strip()
    ]
    delta = new_delta.strip()
    if _EVOLUTION_MARKER not in delta:
        delta = f"{_EVOLUTION_MARKER} {delta}"
    if not delta.startswith("\n\n"):
        delta = "\n\n" + delta
    # Truncate a single runaway delta so it cannot exceed the cap alone.
    if len(delta) > _MAX_DELTA_CHARS:
        delta = delta[:_MAX_DELTA_CHARS].rstrip() + "…"
    blocks = existing + [delta]
    kept: list[str] = []
    total = len(soul)
    for blk in reversed(blocks):
        if len(kept) >= _MAX_EVOLUTION_BLOCKS:
            break
        if total + len(blk) > _MAX_SYSTEM_PROMPT_CHARS and kept:
            break
        kept.append(blk)
        total += len(blk)
    kept.reverse()
    return soul + "".join(kept)


class SelfImprovementSkill:
    """Automated feedback loop for worker Soul optimisation.

    Hooks into the terminal step of PipelineEngine.  Analyses execution
    telemetry, queries historical memory, and applies targeted prompt
    refinements.

    Args:
        enabled:  Whether self-improvement is active.  When False, all
                  methods return no-op results.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._mutation_log: list[dict[str, Any]] = []

    # ── Main entry point ────────────────────────────────────────────────

    async def analyze(
        self,
        worker_name: str,
        task: str,
        stages: list[Any],        # PipelineStage list
        status: str,               # "completed" | "failed" | "partial"
    ) -> dict[str, Any]:
        """Analyze pipeline results and decide whether to mutate the worker's Soul.

        Returns:
            ``{"action": "mutate"|"skip", "delta": str, "reason": str}``
        """
        if not self._enabled:
            return {"action": "skip", "delta": "", "reason": "Self-improvement disabled"}

        # Compute success metrics
        total = len(stages)
        succeeded = sum(1 for s in stages if getattr(s, "status", "") == "completed")
        failed = sum(1 for s in stages if getattr(s, "status", "") == "failed")
        success_rate = succeeded / total if total > 0 else 0.0

        if status == "completed" and success_rate >= 1.0:
            return await self._analyze_success(worker_name, task, stages, success_rate)
        elif status in ("failed", "partial", "timeout") or success_rate < 0.5:
            return await self._analyze_failure(worker_name, task, stages, success_rate, failed)
        return {"action": "skip", "delta": "", "reason": f"Mixed results — rate={success_rate:.0%}"}

    # ── Success analysis ────────────────────────────────────────────────

    async def _analyze_success(
        self, worker_name: str, task: str, stages: list[Any], rate: float
    ) -> dict[str, Any]:
        """Generate a reinforcement delta via LLM Meta-Refiner."""
        # Collect stage details for context
        stage_details = []
        for s in stages:
            role = getattr(s, "role", str(s))
            output = getattr(s, "output", "") or ""
            dur = getattr(s, "duration_ms", 0)
            stage_details.append(f"- {role} ({dur:.0f}ms): {output[:200]}")

        # Query memory adapter for past patterns
        past_context = ""
        try:
            from kazma_core.swarm.memory.adapter import get_adapter
            adapter = get_adapter()
            if adapter is not None:
                results = await adapter.search(f"{worker_name} pipeline {task[:100]}", limit=3)
                if results:
                    past_context = "\nPast memory:\n" + "\n".join(
                        f"  [{r.source_layer}] {r.content[:200]}" for r in results[:3]
                    )
        except Exception as exc:
            logger.debug("Memory adapter search failed: %s", exc)

        # Construct Meta-Refiner prompt
        refiner_prompt = f"""You are a Meta-Refiner for the Kazma self-improvement engine.
Worker '{worker_name}' completed a task successfully ({rate:.0%} rate).

Task: {task[:200]}

Stage results:
{chr(10).join(stage_details)}{past_context}

Generate a CONCISE (2-3 sentence) reinforcement delta for the worker's SOUL.md.
Focus on what patterns to preserve and strengthen.
Output ONLY the delta text, no preamble."""

        try:
            from kazma_core.model_registry import get_model_registry
            provider = get_model_registry().get_client()
            if provider is not None:
                response = await provider.chat([
                    {"role": "system", "content": "You are a concise meta-learning expert."},
                    {"role": "user", "content": refiner_prompt},
                ])
                delta = f"\n\n[SelfImprovement] {response.content}\nTask context: '{task[:100]}'"
                reason = f"LLM-generated reinforcement from {len(stages)} stages ({rate:.0%} rate)"
                logger.info("[SelfImprovement] %s SUCCESS — %s", worker_name, reason)
                return {"action": "mutate", "delta": delta, "reason": reason}
        except Exception as exc:
            logger.warning("[SelfImprovement] LLM delta failed: %s", exc)

        # Fallback: minimal template
        delta = f"\n\n[SelfImprovement] Task successfully completed ({rate:.0%} rate).\nTask hint: '{task[:100]}'."
        return {"action": "mutate", "delta": delta, "reason": f"Template fallback — {len(stages)} stages"}

    # ── Failure analysis ────────────────────────────────────────────────

    async def _analyze_failure(
        self, worker_name: str, task: str, stages: list[Any], rate: float, failed_count: int
    ) -> dict[str, Any]:
        """Generate a corrective delta via LLM Meta-Refiner."""
        if failed_count == 0:
            return {"action": "skip", "delta": "", "reason": "No failed stages"}

        # Collect failure details
        failed_details = []
        for s in stages:
            if getattr(s, "status", "") == "failed":
                role = getattr(s, "role", str(s))
                error = getattr(s, "error", "")
                failed_details.append(f"- {role}: {error[:200] if error else 'unknown error'}")

        # Query memory for past failure patterns
        past_context = ""
        try:
            from kazma_core.swarm.memory.adapter import get_adapter
            adapter = get_adapter()
            if adapter is not None:
                results = await adapter.search(f"{worker_name} failure {task[:100]}", limit=3)
                if results:
                    past_context = "\nPast failures:\n" + "\n".join(
                        f"  [{r.source_layer}] {r.content[:200]}" for r in results[:3]
                    )
        except Exception as exc:
            logger.debug("Memory adapter search failed: %s", exc)

        refiner_prompt = f"""You are a Meta-Refiner for the Kazma self-improvement engine.
Worker '{worker_name}' FAILED {failed_count}/{len(stages)} stages (success rate {rate:.0%}).

Task: {task[:200]}

Failures:
{chr(10).join(failed_details)}{past_context}

Generate a CONCISE (2-3 sentence) corrective delta for the worker's SOUL.md.
Focus on what to AVOID and how to improve next time.
Output ONLY the delta text, no preamble."""

        try:
            from kazma_core.model_registry import get_model_registry
            provider = get_model_registry().get_client()
            if provider is not None:
                response = await provider.chat([
                    {"role": "system", "content": "You are a concise meta-learning expert."},
                    {"role": "user", "content": refiner_prompt},
                ])
                delta = f"\n\n[SelfImprovement] {response.content}\nTask context: '{task[:100]}'"
                reason = f"LLM-generated correction from {failed_count} failures"
                logger.warning("[SelfImprovement] %s FAILURE — %s", worker_name, reason)
                return {"action": "mutate", "delta": delta, "reason": reason}
        except Exception as exc:
            logger.warning("[SelfImprovement] LLM delta failed: %s", exc)

        delta = f"\n\n[SelfImprovement] {failed_count}/{len(stages)} stages failed. Review: {task[:100]}"
        return {"action": "mutate", "delta": delta, "reason": f"Template fallback — {failed_count} failures"}

    # ── Apply mutation ──────────────────────────────────────────────────

    async def apply_mutation(self, worker_name: str, delta: str) -> bool:
        """Auto-apply the Soul delta to the worker's system prompt.

        Deltas are applied immediately, subject to the ``_cap_evolution_prompt``
        safeguards (max 12 blocks, 8000 chars).  The applied delta is also
        logged to the 4-layer memory adapter via ``log_evolution`` so future
        runs can retrieve it.

        Returns True if the delta was applied.
        """
        if not delta or not self._enabled:
            return False
        try:
            return self._auto_apply(worker_name, delta)
        except Exception as exc:
            logger.warning("[SelfImprovement] Auto-apply failed for %s: %s", worker_name, exc)
            return False

    def _auto_apply(self, worker_name: str, delta: str) -> bool:
        """Apply the delta to the worker's system prompt with safety caps."""
        from kazma_core.swarm.registry import WorkerRegistry

        registry = WorkerRegistry()
        entry = registry.get(worker_name)
        if entry is None:
            logger.warning("[SelfImprovement] Worker '%s' not found — cannot apply delta", worker_name)
            return False

        old_prompt = entry.system_prompt or ""
        new_prompt = _cap_evolution_prompt(old_prompt, delta)
        registry.update(worker_name, system_prompt=new_prompt)

        # Record in mutation log
        import time as _time_apply
        self._mutation_log.append({
            "worker": worker_name,
            "delta": delta[:_MAX_DELTA_CHARS],
            "timestamp": _time_apply.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "applied",
        })
        logger.info("[SelfImprovement] Delta APPLIED to '%s' (prompt: %d → %d chars)",
                     worker_name, len(old_prompt), len(new_prompt))

        # Persist to the 4-layer memory adapter for future retrieval
        try:
            import asyncio
            from kazma_core.swarm.memory.adapter import get_adapter

            adapter = get_adapter()
            if adapter is not None:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(adapter.log_evolution(worker_name, delta))
                else:
                    loop.run_until_complete(adapter.log_evolution(worker_name, delta))
        except Exception:
            logger.debug("[SelfImprovement] log_evolution failed (non-fatal)", exc_info=True)

        return True

    @staticmethod
    def _pending_queue_path() -> Path:
        from pathlib import Path
        return Path.home() / ".kazma" / "pending_evolution.json"

    @classmethod
    def _stage_delta(cls, worker_name: str, delta: str) -> bool:
        """Write delta to the pending HITL approval queue."""
        import json
        import time as _time_stage
        try:
            queue_path = cls._pending_queue_path()
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            pending: list[dict[str, Any]] = []
            if queue_path.exists():
                try:
                    pending = json.loads(queue_path.read_text())
                except json.JSONDecodeError:
                    pending = []
            entry = {
                "id": f"ev_{int(_time_stage.time())}_{worker_name}",
                "worker_name": worker_name,
                "delta": delta,
                "timestamp": _time_stage.strftime("%Y-%m-%dT%H:%M:%S"),
                "status": "pending",
            }
            pending.append(entry)
            queue_path.write_text(json.dumps(pending, indent=2, ensure_ascii=False))
            logger.info("[SelfImprovement] Delta staged for HITL: %s → %s", worker_name, entry["id"])
            return True
        except Exception as exc:
            logger.warning("[SelfImprovement] Stage failed: %s", exc)
            return False

    @classmethod
    def get_pending_deltas(cls) -> list[dict[str, Any]]:
        """Return all pending evolution deltas awaiting HITL approval."""
        import json
        try:
            path = cls._pending_queue_path()
            if not path.exists():
                return []
            return json.loads(path.read_text())
        except Exception:
            return []

    @classmethod
    def approve_delta(cls, delta_id: str) -> dict[str, Any]:
        """Approve a pending delta — apply it to the worker's Soul."""
        import json
        try:
            path = cls._pending_queue_path()
            if not path.exists():
                return {"success": False, "error": "No pending queue"}
            pending = json.loads(path.read_text())
            entry = None
            remaining = []
            for e in pending:
                if e.get("id") == delta_id and e.get("status") == "pending":
                    entry = e
                else:
                    remaining.append(e)
            if entry is None:
                return {"success": False, "error": f"Delta '{delta_id}' not found or not pending"}
            # Apply to worker registry
            from kazma_core.swarm.registry import WorkerRegistry
            registry = WorkerRegistry()
            worker_entry = registry.get(entry["worker_name"])
            if worker_entry is not None:
                new_prompt = _cap_evolution_prompt(worker_entry.system_prompt, entry["delta"])
                registry.update(entry["worker_name"], system_prompt=new_prompt)
            entry["status"] = "approved"
            remaining.append(entry)
            path.write_text(json.dumps(remaining, indent=2, ensure_ascii=False))
            logger.info("[SelfImprovement] Delta APPROVED and applied: %s", delta_id)
            return {"success": True, "delta_id": delta_id, "worker_name": entry["worker_name"]}
        except Exception as exc:
            logger.warning("[SelfImprovement] Approve failed: %s", exc)
            return {"success": False, "error": str(exc)}

    @classmethod
    def reject_delta(cls, delta_id: str) -> dict[str, Any]:
        """Reject a pending delta — remove from queue without applying."""
        import json
        try:
            path = cls._pending_queue_path()
            if not path.exists():
                return {"success": False, "error": "No pending queue"}
            pending = json.loads(path.read_text())
            remaining = []
            found = False
            for e in pending:
                if e.get("id") == delta_id and e.get("status") == "pending":
                    e["status"] = "rejected"
                    found = True
                remaining.append(e)
            if not found:
                return {"success": False, "error": f"Delta '{delta_id}' not found"}
            path.write_text(json.dumps(remaining, indent=2, ensure_ascii=False))
            logger.info("[SelfImprovement] Delta REJECTED: %s", delta_id)
            return {"success": True, "delta_id": delta_id, "status": "rejected"}
        except Exception as exc:
            logger.warning("[SelfImprovement] Reject failed: %s", exc)
            return {"success": False, "error": str(exc)}

    # ── Accessors ───────────────────────────────────────────────────────

    @property
    def mutation_history(self) -> list[dict[str, Any]]:
        """Return the mutation log (most recent first)."""
        return list(reversed(self._mutation_log[-50:]))

    def stats(self) -> dict[str, Any]:
        """Return self-improvement statistics."""
        return {
            "enabled": self._enabled,
            "mutations_applied": len(self._mutation_log),
        }


# Module-level singleton
_self_improvement: SelfImprovementSkill | None = None


def get_self_improvement() -> SelfImprovementSkill:
    """Return the shared SelfImprovementSkill instance."""
    global _self_improvement
    if _self_improvement is None:
        _self_improvement = SelfImprovementSkill()
    return _self_improvement
