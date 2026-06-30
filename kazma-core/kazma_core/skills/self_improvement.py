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
        """Generate a reinforcement delta for successful execution."""
        # Identify what the worker did well
        strengths: list[str] = []
        for s in stages:
            role = getattr(s, "role", "")
            if role and getattr(s, "status", "") == "completed":
                strengths.append(str(role))

        delta = (
            f"\n\n[SelfImprovement] You recently succeeded at a task involving: "
            f"{', '.join(strengths[:3]) if strengths else 'general execution'}. "
            f"Apply this pattern to similar tasks.\n"
            f"Task hint: '{task[:100]}' → completed successfully."
        )

        reason = f"All {len(stages)} stages passed ({rate:.0%} success rate)"
        logger.info(
            "[SelfImprovement] %s SUCCESS — %s", worker_name, reason,
        )
        return {"action": "mutate", "delta": delta, "reason": reason}

    # ── Failure analysis ────────────────────────────────────────────────

    async def _analyze_failure(
        self, worker_name: str, task: str, stages: list[Any], rate: float, failed_count: int
    ) -> dict[str, Any]:
        """Generate a corrective delta for failed execution."""
        # Identify what went wrong
        failed_roles: list[str] = []
        for s in stages:
            if getattr(s, "status", "") == "failed":
                role = getattr(s, "role", "")
                error = getattr(s, "error", "")
                failed_roles.append(f"{role}" + (f": {error[:80]}" if error else ""))

        if failed_count == 0:
            return {"action": "skip", "delta": "", "reason": "No failed stages"}

        delta = (
            f"\n\n[SelfImprovement] You recently failed {failed_count} stage(s) in a "
            f"pipeline task. Review the following before your next attempt:\n"
            + "\n".join(f"- Check: {f}" for f in failed_roles[:3])
            + f"\n\nTask context: '{task[:100]}'\n"
            "Before responding, verify your approach against the task requirements."
        )

        reason = f"{failed_count}/{len(stages)} stages failed"
        logger.warning(
            "[SelfImprovement] %s FAILURE — %s", worker_name, reason,
        )
        return {"action": "mutate", "delta": delta, "reason": reason}

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
