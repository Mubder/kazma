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

__all__ = [
    "MULTI_USER_REFUSAL",
    "NO_SPACE_REFUSAL",
    "answered_by_caller",
    "for_the_owner",
    "swarm_refusal",
    "task_answered_by_caller",
    "task_for_the_owner",
    "task_of_this_space",
]

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
    return str(_metadata(task).get("tenant_id") or "") == require_space_id()


def _metadata(task: object) -> dict:
    meta = (task.get("metadata") if isinstance(task, dict) else getattr(task, "metadata", None)) or {}
    return meta if isinstance(meta, dict) else {}


def _acts_for(space: str, asker: str | None) -> str:
    """The account work of *space* asked by *asker* acts for: the asker while
    still a member, else the account the space's own work acts for
    (``identity.work_scope``); ``""`` for a space of no install. Blocking."""
    from kazma_core.spaces.identity import work_scope

    found = work_scope(str(space or ""), asker)
    return found[1] if found is not None else ""


def answered_by_caller(space: str, asker: str | None) -> bool:
    """Whether the work this call is bound to may answer work of *space*
    asked by *asker* (decision N4, Spaces S1-15 review): always while Spaces
    is not enforced; once it is, only the person it acts for -- approve,
    reject, cancel or retry a task, decide its worker's approval. Another
    member of a shared space sees it and does not answer it. Blocking (the
    identity store)."""
    from kazma_core.spaces.scope import bound_asker, spaces_enforced

    if not spaces_enforced():
        return True
    caller = bound_asker() or ""
    return bool(caller) and caller == _acts_for(space, asker)


def task_answered_by_caller(task: object) -> bool:
    """:func:`answered_by_caller` for *task* (a ``SwarmTask`` or its dict)."""
    meta = _metadata(task)
    return answered_by_caller(str(meta.get("tenant_id") or ""), meta.get("asker"))


def for_the_owner(space: str, asker: str | None) -> bool:
    """Whether the install's own channels may carry work of *space* asked by
    *asker*: the swarm's chat-app approval cards, its output target, a
    task's chat. Before people link their own chat apps (stage 3) every one
    of them is the owner's, so once Spaces is enforced only work that acts
    for the owner goes there (O1); anyone else's stays in their space. Always
    True while Spaces is not enforced. Blocking (the identity store)."""
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, spaces_enforced

    if not spaces_enforced():
        return True
    return _acts_for(space, asker) == OWNER_ACCOUNT_ID


def task_for_the_owner(task: object) -> bool:
    """:func:`for_the_owner` for *task* (a ``SwarmTask`` or its dict)."""
    meta = _metadata(task)
    return for_the_owner(str(meta.get("tenant_id") or ""), meta.get("asker"))
