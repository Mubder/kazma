"""Pure dispatch helpers extracted from SwarmEngine (S3/S5)."""

from __future__ import annotations

from typing import Any

from kazma_core.swarm.blackboard import BlackboardStore, SwarmDispatchContext
from kazma_core.swarm.task import SwarmTask, WorkerResult

__all__ = ["NO_DEADLINE_SENTINEL", "WORKER_TYPE_ALIASES", "aggregate_outputs", "build_dispatch_context", "build_handoff_context", "build_result_metadata", "normalize_worker_type", "overall_status", "pattern_dispatch_context", "resolve_max_concurrent", "wait_timeout"]

# Map free-form worker type aliases to canonical WorkerConfig.type values
WORKER_TYPE_ALIASES: dict[str, str] = {
    "in-process": "in_process",
    "in_process": "in_process",
    "telegram": "telegram_bot",
    "telegram_bot": "telegram_bot",
}


def normalize_worker_type(worker_type: str) -> str:
    """Normalize worker type aliases to canonical config values."""
    return WORKER_TYPE_ALIASES.get(worker_type, worker_type)


def wait_timeout(timeout: float | None) -> float | None:
    """Normalize ``SwarmTask.timeout`` for ``asyncio.wait_for``.

    The engine treats ``timeout <= 0`` as "no timeout"; patterns previously
    passed the raw value to ``wait_for``, where ``0`` meant an INSTANT
    ``TimeoutError`` for every fan-out/pipeline/consultation worker.
    """
    try:
        t = float(timeout)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return t if t > 0 else None


# Sentinel expressing "no deadline" to :class:`TimeoutGuard`, whose
# ``execute()`` raises ``ValueError`` for ``timeout <= 0`` and treats
# ``None`` as its own default (300s) — so neither can carry the documented
# "timeout <= 0 means no timeout" semantics through the guard. One year:
# effectively unbounded, still finite for ``asyncio.wait_for`` scheduling
# and log formatting.
NO_DEADLINE_SENTINEL: float = 86_400.0 * 365.0


def aggregate_outputs(worker_results: list[WorkerResult]) -> str | None:
    """Join successful worker outputs for multi-worker tasks."""
    successful = [
        result for result in worker_results if result.status == "success" and result.output
    ]
    if not successful:
        return None
    if len(successful) == 1:
        return successful[0].output
    return "\n\n".join(f"[{result.worker}] {result.output}" for result in successful)


def overall_status(worker_results: list[WorkerResult]) -> str:
    """Derive overall task status from per-worker results."""
    if not worker_results:
        return "success"

    successes = [result for result in worker_results if result.status == "success"]
    timeouts = [result for result in worker_results if result.status == "timeout"]

    if len(successes) == len(worker_results):
        return "success"
    if successes:
        return "partial"
    if timeouts and len(timeouts) == len(worker_results):
        return "timeout"
    return "failed"


def build_handoff_context(
    *,
    original_prompt: str,
    original_context: str | SwarmDispatchContext,
    intermediate_results: str,
    blackboard: BlackboardStore | None = None,
) -> str | SwarmDispatchContext:
    """Build accumulated context for a handoff target worker."""
    sections: list[str] = []
    original_ctx_text = str(original_context).strip()
    if original_ctx_text:
        sections.append(f"Original context:\n{original_ctx_text}")
    sections.append(f"Original prompt:\n{original_prompt}")
    if intermediate_results:
        sections.append(f"Intermediate results:\n{intermediate_results}")

    context_text = "\n\n".join(sections)

    # Forward the original dispatch metadata (Phase 5: so a commitment scope-token
    # and workspace_id survive A→B handoffs). Only the blackboard path can carry
    # metadata; the plain-str path has none by design.
    meta = None
    if isinstance(original_context, SwarmDispatchContext):
        meta = dict(original_context.metadata or {})
    if blackboard is not None:
        return SwarmDispatchContext(
            context_text,
            blackboard=blackboard,
            metadata=meta,
        )
    return context_text


def pattern_dispatch_context(
    task: SwarmTask,
    *,
    blackboard: BlackboardStore | None = None,
    extra: dict[str, Any] | None = None,
    context_text: str | None = None,
    system_prompt: str = "",
) -> SwarmDispatchContext:
    """One context builder for every swarm pattern (audit H-10).

    Always injects ``workspace_id`` (and keeps ``commitment_scope`` already
    on ``task.metadata``). Patterns must not assemble metadata by hand.
    """
    meta = dict(task.metadata or {})
    ws = getattr(task, "workspace_id", None)
    if ws and "workspace_id" not in meta:
        meta["workspace_id"] = ws
    if extra:
        meta.update(extra)
    return SwarmDispatchContext(
        task.context if context_text is None else context_text,
        blackboard=blackboard,
        metadata=meta,
        task_id=task.id,
        task_type=task.type.value,
        system_prompt=system_prompt,
    )


def build_dispatch_context(
    task: SwarmTask,
    *,
    blackboard: BlackboardStore | None = None,
    system_prompt: str = "",
) -> str | SwarmDispatchContext:
    """Build context passed to worker.dispatch()."""
    if blackboard is None:
        return task.context
    return pattern_dispatch_context(
        task, blackboard=blackboard, system_prompt=system_prompt
    )


async def build_result_metadata(
    blackboard: BlackboardStore | None = None,
) -> dict[str, Any]:
    """Snapshot blackboard into result metadata when present."""
    if blackboard is None:
        return {}
    snapshot = await blackboard.snapshot()
    return {
        "blackboard": snapshot,
        "blackboard_snapshot": snapshot,
    }


def resolve_max_concurrent(task: SwarmTask, config_default: int = 5) -> int:
    """Resolve fan-out concurrency limit for a task."""
    configured_default = max(1, int(config_default or 5))
    configured_value = task.metadata.get("max_concurrent", configured_default)
    try:
        resolved = int(configured_value)
    except (TypeError, ValueError):
        return configured_default
    return max(1, resolved)
