"""Handoff mechanism for swarm workers.

A worker delegates the rest of its task to another worker by raising
:class:`HandoffRequest`. :func:`~kazma_core.swarm.worker_dispatch.dispatch_worker`
catches it, and ``SwarmEngine._handle_handoff()`` records a
:class:`~kazma_core.swarm.task.HandoffRecord` and dispatches the target with
the accumulated context; the target's result becomes the task's.

Shipped workers reach it through a tool (Swarm audit SW-E13, owner decision
D6: handoff is a product feature). Every dispatch the engine runs binds a
:class:`HandoffOffer` (:func:`handoff_offer`): who else the worker may hand
off to, and the engine's own cycle guard. The LLM-backed ``InProcessWorker``
offers the model :data:`HANDOFF_TOOL_NAME` while an offer is bound and raises
a :class:`HandoffRequest` for a call :func:`handoff_call` accepts. The tool
is the worker's, never the tool registry's: the main agent, the MCP server and
scheduled tasks never see it. Handing off approves nothing -- every tool the
target runs goes through the registry and the swarm bus as any worker's does
(AGENTS.md §7B).

Multi-hop chains (A -> B -> C) and return handoffs (A -> B -> A) are
supported. The cycle guard (``handoff_guards``: depth 5, each worker run at
most ``MAX_VISITS=2`` times) is enforced by ``SwarmEngine._handle_handoff()``;
the offer asks the same guard before the request is raised, so a refused hop
reaches the model as the tool's answer and the worker finishes the task
itself instead of failing the chain.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

__all__ = [
    "HANDOFF_TOOL_DESCRIPTION",
    "HANDOFF_TOOL_NAME",
    "MAX_HANDOFF_TARGETS",
    "HandoffCall",
    "HandoffOffer",
    "HandoffRequest",
    "HandoffTarget",
    "current_handoff_offer",
    "handoff_call",
    "handoff_offer",
    "handoff_policy_refusal",
    "handoff_tool_definition",
    "request_handoff",
]

logger = logging.getLogger(__name__)

#: The tool an LLM-backed swarm worker calls to hand its task to another worker.
HANDOFF_TOOL_NAME = "handoff_to_worker"

#: Most workers one offer names (the tool's enum stays small).
MAX_HANDOFF_TARGETS = 32

#: Longest reason, task and context a handoff call carries. The context is the
#: handing-off worker's own findings: the target reads it fenced
#: (``dispatch_helpers.build_handoff_context``).
_MAX_REASON_CHARS = 1_000
_MAX_TASK_CHARS = 8_000
_MAX_CONTEXT_CHARS = 16_000


class HandoffRequest(Exception):  # noqa: N818
    """Raised by a worker to request handoff to another worker.

    Attributes:
        target_worker: Name of the worker (or worker template) to hand off to.
        task:          Task description for the target worker.
        context:       Additional context to transfer.
        reason:        Why the worker hands off (recorded on the handoff).
        tokens_used:   Tokens the handing-off worker spent before it handed off.
        cost:          What those calls cost, in USD.
    """

    def __init__(
        self,
        target_worker: str,
        task: str,
        context: str = "",
        *,
        reason: str = "",
        tokens_used: int = 0,
        cost: float = 0.0,
    ) -> None:
        super().__init__(f"Handoff to '{target_worker}': {task}")
        self.target_worker = target_worker
        self.task = task
        self.context = context
        self.reason = reason
        self.tokens_used = int(tokens_used or 0)
        self.cost = float(cost or 0.0)


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


# ---------------------------------------------------------------------------
# The offer: who a dispatched worker may hand off to
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HandoffTarget:
    """A worker a handoff may name: a registered worker or a worker template."""

    name: str
    role: str = ""


@dataclass(frozen=True)
class HandoffOffer:
    """What the engine offers one dispatch of one worker.

    ``targets`` lists the workers (and templates) the worker may hand off to,
    computed when the worker asks; ``refusal(target)`` is the engine's cycle
    guard for a hop to *target* from this dispatch (None = allowed).
    """

    source: str
    targets: Callable[[], tuple[HandoffTarget, ...]]
    refusal: Callable[[str], str | None]


_offer: ContextVar[HandoffOffer | None] = ContextVar("kazma_swarm_handoff_offer", default=None)


@contextmanager
def handoff_offer(offer: HandoffOffer | None) -> Iterator[None]:
    """Bind *offer* for the duration of one worker dispatch (None: no offer)."""
    token = _offer.set(offer)
    try:
        yield
    finally:
        _offer.reset(token)


def current_handoff_offer() -> HandoffOffer | None:
    """The offer of the dispatch running now, or None (no engine dispatch)."""
    return _offer.get()


#: What the tool says it does (the tools catalog lists it from here).
HANDOFF_TOOL_DESCRIPTION = (
    "Hand the rest of this task to another swarm worker whose role or tools fit it "
    "better. You stop when you call this: the worker you name continues the task with "
    "what you pass in `context`, and its answer becomes the result. Call it on its own, "
    "not together with other tools."
)


def handoff_tool_definition(targets: tuple[HandoffTarget, ...]) -> dict[str, Any]:
    """The OpenAI-format definition of :data:`HANDOFF_TOOL_NAME` for *targets*."""
    listed = "; ".join(
        f"{t.name} ({t.role})" if t.role and t.role != t.name else t.name for t in targets
    )
    return {
        "type": "function",
        "function": {
            "name": HANDOFF_TOOL_NAME,
            "description": f"{HANDOFF_TOOL_DESCRIPTION} Workers you can hand off to: {listed}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "worker": {
                        "type": "string",
                        "enum": [t.name for t in targets],
                        "description": "The worker to hand the task to.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why that worker should continue, in one sentence.",
                    },
                    "task": {
                        "type": "string",
                        "description": (
                            "What the worker should do. Leave empty to give it the whole "
                            "original task."
                        ),
                    },
                    "context": {
                        "type": "string",
                        "description": "What you found so far that the worker needs.",
                    },
                },
                "required": ["worker", "reason"],
                "additionalProperties": False,
            },
        },
    }


def handoff_policy_refusal(arguments: dict[str, Any]) -> str | None:
    """The commitment layer's answer for a handoff call (None = allowed).

    The same ``authorize_effect`` the tool registry asks for every tool, so a
    worker scope that does not allow the ``delegate`` act refuses a handoff as
    it refuses ``dispatch_swarm`` (AGENTS.md §20). It reads settings: call it
    off the event loop. The gate guards its own reads (a scope it cannot
    verify denies); anything else it raises ends the worker's dispatch with
    that error, so no handoff happens unchecked."""
    from kazma_core.safety.commitment import authorize_effect
    from kazma_core.safety.commitment.config import get_commitment_config

    decision = authorize_effect(
        HANDOFF_TOOL_NAME,
        dict(arguments),
        enforce_unknown_mutators=bool(get_commitment_config().get("enforce_unknown_mutators")),
        context={"source": "swarm_handoff"},
    )
    if decision.decision == "deny":
        return decision.reason or "denied by the commitment layer"
    return None


@dataclass(frozen=True)
class HandoffCall:
    """A :data:`HANDOFF_TOOL_NAME` call that names a worker the offer allows."""

    worker: str
    reason: str
    task: str
    context: str


def handoff_call(
    offer: HandoffOffer,
    targets: tuple[HandoffTarget, ...],
    arguments: Any,
    *,
    task: str,
) -> HandoffCall | str:
    """Read one :data:`HANDOFF_TOOL_NAME` call, or say why it cannot be made.

    Returns the call, or the text the tool answers the model with: an unknown
    worker, a missing reason, or the engine's cycle guard refusing the hop
    (the worker then finishes the task itself). *task* is the worker's own
    task: the target's when the call names none."""
    args = arguments if isinstance(arguments, dict) else {}
    names = [t.name for t in targets]
    worker = str(args.get("worker") or "").strip()
    if worker not in names:
        return (
            f"Error: '{worker}' is not a worker you can hand off to. "
            f"Choose one of: {', '.join(names)}."
        )
    reason = str(args.get("reason") or "").strip()[:_MAX_REASON_CHARS]
    if not reason:
        return "Error: say in `reason` why that worker should continue the task."
    refused = offer.refusal(worker)
    if refused:
        logger.info("[Handoff] %s -> %s refused: %s", offer.source, worker, refused)
        return (
            f"Handoff refused: {refused.rstrip('.')}. Finish the task yourself with what you have."
        )
    return HandoffCall(
        worker=worker,
        reason=reason,
        task=str(args.get("task") or "").strip()[:_MAX_TASK_CHARS] or task,
        context=str(args.get("context") or "")[:_MAX_CONTEXT_CHARS],
    )
