"""The swarm's published input and storage limits, in one place.

Swarm audit 2026-10-13, SW-S14 and SW-E15: a dispatch took any timeout (0,
negative, NaN, a year), any prompt, context or metadata size, any number of
workers and checkpoints; paused tasks had no cap; and a stored result held
each worker output about four times (worker results, the aggregate and two
blackboard copies). The Web API refuses an out-of-range request with 422
(``kazma_ui.swarm_panel.routes_tasks``), the engine refuses one from any other
entry point before admission (:func:`task_problem`), and the task store keeps
each large output once, referenced from the other places (``TaskStore``).
"""

from __future__ import annotations

import json
import math
from typing import Any

__all__ = [
    "MAX_CHECKPOINTS",
    "MAX_CONTEXT_BYTES",
    "MAX_FALLBACK_CHAIN",
    "MAX_METADATA_BYTES",
    "MAX_PARALLEL_WORKERS",
    "MAX_PAUSED_TASKS",
    "MAX_PROMPT_BYTES",
    "MAX_TIMEOUT_S",
    "MAX_WORKERS",
    "MIN_TIMEOUT_S",
    "payload_problem",
    "task_problem",
]

#: A task's (per-step, for patterns) deadline, as the Web API accepts it.
MIN_TIMEOUT_S = 1.0
MAX_TIMEOUT_S = 3600.0
MAX_PROMPT_BYTES = 64 * 1024
MAX_CONTEXT_BYTES = 256 * 1024
MAX_METADATA_BYTES = 64 * 1024
MAX_WORKERS = 32
MAX_CHECKPOINTS = 32
MAX_FALLBACK_CHAIN = 16
#: Pipelines waiting at a checkpoint at once; a new one with checkpoints is
#: refused past this (a human answers each).
MAX_PAUSED_TASKS = 100
#: Worker dispatches running in parallel across every fan-out, consult and
#: broadcast (each task keeps its own limit under this one).
MAX_PARALLEL_WORKERS = 64


def _size(value: Any) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8"))


def _shape_problem(prompt: Any, context: Any, metadata: Any, workers: Any,
                   fallback_chain: Any) -> tuple[str, str] | None:
    if _size(prompt or "") > MAX_PROMPT_BYTES:
        return "prompt_too_large", f"The task is over {MAX_PROMPT_BYTES // 1024} KiB."
    if _size(context or "") > MAX_CONTEXT_BYTES:
        return "context_too_large", f"The context is over {MAX_CONTEXT_BYTES // 1024} KiB."
    if _size(metadata or {}) > MAX_METADATA_BYTES:
        return "metadata_too_large", f"The metadata is over {MAX_METADATA_BYTES // 1024} KiB."
    if len(list(workers or [])) > MAX_WORKERS:
        return "too_many_workers", f"More than {MAX_WORKERS} workers."
    checkpoints = (metadata or {}).get("hitl_checkpoints") if isinstance(metadata, dict) else None
    if isinstance(checkpoints, list) and len(checkpoints) > MAX_CHECKPOINTS:
        return "too_many_checkpoints", f"More than {MAX_CHECKPOINTS} checkpoints."
    if len(list(fallback_chain or [])) > MAX_FALLBACK_CHAIN:
        return "fallback_chain_too_long", f"More than {MAX_FALLBACK_CHAIN} fallback workers."
    return None


def payload_problem(payload: dict[str, Any]) -> tuple[str, str] | None:
    """(code, message) for a Web dispatch request out of range, else None.

    The timeout must be a finite number from MIN_TIMEOUT_S to MAX_TIMEOUT_S:
    0 and negative ones are refused here (the engine reads <= 0 as "no
    deadline", which only Kazma's own callers may ask for)."""
    raw = payload.get("timeout", 300.0)
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return "timeout_invalid", "The timeout is not a number."
    if isinstance(raw, bool) or not math.isfinite(timeout) or not (MIN_TIMEOUT_S <= timeout <= MAX_TIMEOUT_S):
        return "timeout_out_of_range", f"The timeout must be {MIN_TIMEOUT_S:g} to {MAX_TIMEOUT_S:g} seconds."
    metadata = payload.get("metadata")
    return _shape_problem(payload.get("task"), payload.get("context"),
                          metadata if isinstance(metadata, dict) else {},
                          payload.get("workers"), payload.get("fallback_chain"))


def task_problem(task: Any) -> tuple[str, str] | None:
    """(code, message) for a task out of range, from any entry point."""
    timeout = getattr(task, "timeout", None)
    if timeout is not None:
        try:
            value = float(timeout)
        except (TypeError, ValueError):
            return "timeout_invalid", "The timeout is not a number."
        if not math.isfinite(value) or value > MAX_TIMEOUT_S:
            return "timeout_out_of_range", f"The timeout must be at most {MAX_TIMEOUT_S:g} seconds."
    return _shape_problem(task.prompt, task.context, task.metadata, task.workers,
                          getattr(task, "fallback_chain", None))
