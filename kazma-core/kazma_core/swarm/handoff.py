"""Handoff mechanism for swarm workers.

Workers can delegate execution mid-task to another worker by calling
:func:`request_handoff`.  This raises a :class:`HandoffRequest` exception
that the :class:`~kazma_core.swarm.engine.SwarmEngine` catches during
dispatch, records a :class:`~kazma_core.swarm.task.HandoffRecord`, and
transfers control (with accumulated context) to the target worker.

Multi-hop chains (A -> B -> C) and return handoffs (A -> B -> A) are
supported. This module itself does no cycle detection — it only raises
:class:`HandoffRequest` for the engine to catch. Cycle/depth guarding is
enforced by the caller: ``SwarmEngine._handle_handoff()`` tracks per-worker
visit counts and hop depth (max depth 5, each worker revisitable up to
``_MAX_VISITS=2`` times) to allow legitimate A -> B -> A returns while still
stopping runaway A -> B -> A -> B ... cycles.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class HandoffRequest(Exception):  # noqa: N818
    """Raised by a worker to request handoff to another worker.

    Attributes:
        target_worker: Name of the worker to hand off to.
        task:          Task description for the target worker.
        context:       Additional context to transfer.
    """

    def __init__(
        self,
        target_worker: str,
        task: str,
        context: str = "",
    ) -> None:
        super().__init__(f"Handoff to '{target_worker}': {task}")
        self.target_worker = target_worker
        self.task = task
        self.context = context


# Module-level callback set by SwarmEngine during dispatch.
_handoff_callback: Any = None


def set_handoff_callback(callback: Any) -> None:
    """Set the module-level handoff callback (engine sets this during dispatch)."""
    global _handoff_callback
    _handoff_callback = callback


def get_handoff_callback() -> Any:
    """Return the current handoff callback."""
    return _handoff_callback


def request_handoff(
    target_worker: str,
    task: str,
    context: str = "",
) -> None:
    """Request a handoff to another worker.

    Call this from within a worker's dispatch method to delegate execution
    to *target_worker*.  Raises :class:`HandoffRequest` which the engine
    intercepts.

    Args:
        target_worker: Name of the target worker.
        task:          Task description for the target.
        context:       Additional context to transfer (optional).
    """
    logger.info(
        "[Handoff] requesting handoff to '%s' with task: %s",
        target_worker,
        task[:80],
    )
    raise HandoffRequest(
        target_worker=target_worker,
        task=task,
        context=context,
    )
