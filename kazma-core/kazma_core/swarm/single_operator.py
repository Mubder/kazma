"""The swarm on a single-operator install (Swarm audit SW-S16, decision D1),
and per space once Spaces is enforced (Spaces S1-15).

Swarm tasks are not separated per user: a task read, streamed, approved,
cancelled or retried by id is the install's, the active-task list is every
task, and a chat-dispatched task carries the chat and the sender it came
from. Kazma is one operator per install (``docs/THREAT_MODEL.md``), so the
swarm stays whole there; on an install with platform users -- or
``KAZMA_MULTI_USER`` -- every swarm surface (the Web API, the chat ``/swarm``
command, the agent's swarm tools) refuses with :data:`MULTI_USER_REFUSAL`.
A settings store that cannot answer refuses too: the swarm never acts for
everyone by accident.

Once Spaces is enforced a task carries its space and the account that asked
for it, its workers run as them, and every surface keeps to the caller's
space: work bound to a space is served there, work bound to none is refused
(:data:`NO_SPACE_REFUSAL`).
"""

from __future__ import annotations

__all__ = ["MULTI_USER_REFUSAL", "NO_SPACE_REFUSAL", "swarm_refusal", "task_of_this_space"]

MULTI_USER_REFUSAL = (
    "The swarm works on a single-operator install. This install has platform "
    "users, and swarm tasks are not separated per user yet, so the swarm is "
    "off here."
)

#: Spaces enforced and the work tied to no space (a chat app's turn before
#: its chats are bound to one, stage 3): a task would belong to no one.
NO_SPACE_REFUSAL = "The swarm works inside a space, and this chat is not tied to one yet."


def swarm_refusal() -> str | None:
    """Why the swarm refuses this work, or None. Blocking: async callers use
    ``asyncio.to_thread``."""
    from kazma_core.spaces.scope import current_scope, spaces_enforced

    if spaces_enforced():
        return None if current_scope() is not None else NO_SPACE_REFUSAL
    from kazma_core.security.single_operator import single_operator_refusal

    return single_operator_refusal(MULTI_USER_REFUSAL)


def task_of_this_space(task: object) -> bool:
    """True when *task* (a ``SwarmTask`` or its dict) may be read or changed
    by this work: always while Spaces is not enforced; once it is, only a
    task of the space this work is bound to (Spaces S1-15). Every surface
    that takes a task id asks it, and answers another space's task as an
    unknown one. ``NoSpaceBound`` with no space bound."""
    from kazma_core.spaces.scope import require_space_id, spaces_enforced

    if not spaces_enforced():
        return True
    meta = (task.get("metadata") if isinstance(task, dict) else getattr(task, "metadata", None)) or {}
    return str(meta.get("tenant_id") or "") == require_space_id()
