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
from typing import Any

logger = logging.getLogger(__name__)


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
        elif status in ("failed", "partial") or success_rate < 0.5:
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
                        f"  [{layer}] {text[:200]}" for text, _, layer, _, _ in results[:3]
                    )
        except Exception:
            pass

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
                        f"  [{layer}] {text[:200]}" for text, _, layer, _, _ in results[:3]
                    )
        except Exception:
            pass

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
        """Write the Soul delta to the worker's system prompt via WorkerRegistry.

        Returns True if the mutation was applied.
        """
        if not delta or not self._enabled:
            return False

        try:
            from kazma_core.swarm.registry import WorkerRegistry

            registry = WorkerRegistry()
            entry = registry.get(worker_name)
            if entry is None:
                logger.warning("[SelfImprovement] Worker '%s' not in registry", worker_name)
                return False

            # Cap deltas — keep only the last 5 improvements
            existing = entry.system_prompt
            # Strip old SelfImprovement blocks to prevent unbounded growth
            import re as _re_si
            cleaned = _re_si.sub(r'\\n\\n\\[SelfImprovement\\].*?(?=\\n\\n\\[SelfImprovement\\]|$)', '', existing, count=max(0, len(_re_si.findall(r'\\[SelfImprovement\\]', existing)) - 4))
            new_prompt = cleaned + delta
            registry.update(worker_name, system_prompt=new_prompt)

            self._mutation_log.append({
                "worker": worker_name,
                "delta": delta[:200],
                "timestamp": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
            })
            logger.info("[SelfImprovement] Soul mutated for %s", worker_name)
            return True
        except Exception as exc:
            logger.warning("[SelfImprovement] Mutation failed for %s: %s", worker_name, exc)
            return False

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
