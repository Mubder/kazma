"""The graph's HITL authority, for one tool call at a time (Swarm audit SW-S01).

Two places decide a danger tool call's approval: the graph's interrupt() for a
chat turn (AGENTS §7A) and the swarm bus everywhere else (§7B). The registry
must not ask the bus about a call the graph already decided -- and it must ask
about everything else. Until 2026-10-14 the graph said so with boolean
ContextVars set for its whole tool-worker step; ``asyncio.create_task``
copies the context, so the swarm task ``dispatch_swarm`` started inside that
step ran its worker's danger tools (``file_write``, ``git_push``,
``vault_retrieve``, ``send_message``) with no card on any surface.

Now the authority names the one tool call it covers and is consumed by its
first execution (:func:`claim_call_authority`): work that call starts -- a
detached task, a thread, another tool, the same tool again -- finds it spent
and goes to the bus. Detached starters also run with none at all
(:func:`detached_context`), so an authority a path never consumed cannot
travel either.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

__all__ = [
    "CallAuthority",
    "call_authority",
    "claim_call_authority",
    "detached_context",
    "without_call_authority",
]


@dataclass(eq=False)
class CallAuthority:
    """What the graph decided about ONE tool call, for that call only.

    The graph classifies each call of a turn and asks the human about the
    danger ones; for those calls it is the HITL authority and the bus must
    not ask again. That authority used to be a boolean ContextVar set for
    the whole tool-worker step (Swarm audit SW-S01, 2026-10-13): a call it
    covered -- ``dispatch_swarm``, which the graph never gates -- started the
    swarm task with ``asyncio.create_task``, which copies the context, and
    the swarm worker's danger tools skipped the bus with no card anywhere.
    Now the graph binds an authority to the tool it decided about, the first
    execution of that tool consumes it (:func:`claim_call_authority`), and
    anything after -- the work that call starts, detached or not, sharing
    this same object through every context copy -- reaches the bus.
    """

    tool: str
    consumed: bool = False


#: Set by the graph's interrupt() gate around ONE approved call. This is the
#: ONLY trusted source for "already approved" -- the ``_hitl_approved`` key
#: in LLM-supplied tool arguments is always stripped and never honored
#: (prompt-injection bypass).
_hitl_approved_ctx: ContextVar[CallAuthority | None] = ContextVar("_hitl_approved", default=None)

#: Set by the supervisor graph's tool_worker_node around ONE call it
#: classified, when it was compiled WITH a HITL config (its interrupt() gate
#: is the authority for single-agent chat): ``execute()`` then skips the
#: SwarmMessageBus ``safety.check()`` (mechanism B) for that call so a
#: graph-gated danger tool is not prompted twice. The bus stays the
#: authority for everything else -- /swarm dispatch, IDE paths, and the work
#: a graph call starts.
_graph_hitl_gate_ctx: ContextVar[CallAuthority | None] = ContextVar("_graph_hitl_gate", default=None)


def claim_call_authority(var: ContextVar[CallAuthority | None], tool_name: str) -> bool:
    """True once: for the first execution of the tool *var*'s authority
    names, which consumes it. Every later execution in any copy of this
    context -- detached work the call started, another tool, the same tool
    again -- answers False and goes to the bus."""
    auth = var.get()
    if not isinstance(auth, CallAuthority) or auth.consumed or auth.tool != tool_name:
        return False
    auth.consumed = True
    return True


@contextmanager
def call_authority(var: ContextVar[CallAuthority | None], tool_name: str) -> Iterator[CallAuthority]:
    """Bind *var*'s authority to one call of *tool_name* for this block."""
    auth = CallAuthority(str(tool_name))
    token = var.set(auth)
    try:
        yield auth
    finally:
        var.reset(token)


def detached_context() -> contextvars.Context:
    """A copy of this context with no call authority, for work started from
    it that outlives or runs beside the call (``asyncio.create_task``,
    ``spawn_background``, ``SwarmEngine.dispatch``): it asks the bus itself."""
    ctx = contextvars.copy_context()
    ctx.run(_hitl_approved_ctx.set, None)
    ctx.run(_graph_hitl_gate_ctx.set, None)
    return ctx


@contextmanager
def without_call_authority() -> Iterator[None]:
    """No call authority inside this block: for work entered by awaiting
    (``SwarmEngine.dispatch`` awaited from a tool) rather than started."""
    approved = _hitl_approved_ctx.set(None)
    graph = _graph_hitl_gate_ctx.set(None)
    try:
        yield
    finally:
        _graph_hitl_gate_ctx.reset(graph)
        _hitl_approved_ctx.reset(approved)
