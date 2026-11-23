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

import logging

__all__ = [
    "ADMIN_ONLY_REFUSAL",
    "MULTI_USER_REFUSAL",
    "NO_SPACE_REFUSAL",
    "OWNER_ANSWER_WAIT_S",
    "answered_by_caller",
    "for_the_owner",
    "swarm_refusal",
    "swarm_write_refusal",
    "task_answered_by_caller",
    "task_is_callers",
    "task_of_this_space",
    "wait_for_owner_answer",
]

logger = logging.getLogger(__name__)

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


#: Spaces enforced and the caller not the install's admin: the swarm's
#: writes are the admin's until feature grants give the swarm to others
#: (S2-11), on every surface -- the Swarm page's routes and the agent's
#: ``dispatch_swarm`` alike (Spaces S1-16 follow-up).
ADMIN_ONLY_REFUSAL = (
    "Starting swarm work is for the install's admin until the swarm is given to others."
)


def swarm_write_refusal() -> str | None:
    """Why the swarm refuses new work from this caller, or None:
    :func:`swarm_refusal`, then -- once Spaces is enforced -- the admin rule
    the Swarm page's write routes follow (``kazma_ui.auth.admin_decision``):
    with sign-in off there is no admin role to require; with it on, only the
    install's admin, who is the owner. Blocking (the install's secret):
    async callers use ``asyncio.to_thread``."""
    from kazma_core.config_store import get_kazma_secret
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bound_asker, spaces_enforced

    refusal = swarm_refusal()
    if refusal or not spaces_enforced() or not get_kazma_secret():
        return refusal
    return None if bound_asker() == OWNER_ACCOUNT_ID else ADMIN_ONLY_REFUSAL


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
    from kazma_core.spaces.identity import AskerLeft, acting_account, work_scope

    try:
        found = work_scope(str(space or ""), asker)
    except AskerLeft:
        # Their task runs for no one (the S2-12 review, finding 6); the
        # space's own account may still cancel or reject it.
        return acting_account(str(space or ""))
    return found[1] if found is not None else ""


def answered_by_caller(space: str, asker: str | None, tools: tuple[str, ...] = (), *,
                       caller: str | None = None) -> bool:
    """Whether the work this call is bound to may answer work of *space*
    asked by *asker* about *tools* (decision N4, Spaces S1-15 review): always
    while Spaces is not enforced; once it is, only the people the one rule of
    who answers names (``spaces.approvals``, S2-05) for the person the work
    acts for -- approve, reject, cancel or retry a task, decide its worker's
    approval. That is the person themself, unless the admin set approvers on
    their account (N4) or the card is a Development holder's always-ask
    action in a shared space with a project folder (N21: the admin). Another
    member of a shared space sees it and does not answer it. Blocking (the
    identity store)."""
    from kazma_core.spaces.approvals import answer_refusal
    from kazma_core.spaces.scope import bound_asker, spaces_enforced

    if not spaces_enforced():
        return True
    caller = caller or bound_asker() or ""
    if not caller:
        return False
    return answer_refusal(space, _acts_for(space, asker), tools, caller=caller) is None


def task_answered_by_caller(task: object) -> bool:
    """:func:`answered_by_caller` for *task* (a ``SwarmTask`` or its dict)."""
    meta = _metadata(task)
    return answered_by_caller(str(meta.get("tenant_id") or ""), meta.get("asker"))


def task_is_callers(task: object) -> bool:
    """Whether *task* (a ``SwarmTask`` or its dict) acts for the person this
    call is bound to: the person who asked for it while still a member, else
    the account the space's own work acts for (a departed asker's task,
    ``_acts_for``). Cancel and Retry of one's own task ask this, never the
    rule of who answers approvals (the S2-05 review): approvers answer a
    person's approval cards (N4), they do not take the person's tasks from
    them. Always True while Spaces is not enforced. Blocking (the identity
    store)."""
    from kazma_core.spaces.scope import bound_asker, spaces_enforced

    if not spaces_enforced():
        return True
    caller = bound_asker() or ""
    if not caller:
        return False
    meta = _metadata(task)
    return _acts_for(str(meta.get("tenant_id") or ""), meta.get("asker")) == caller


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


def _task_for_the_owner(task: object) -> bool:
    """:func:`for_the_owner` for *task* (a ``SwarmTask`` or its dict)."""
    meta = _metadata(task)
    return for_the_owner(str(meta.get("tenant_id") or ""), meta.get("asker"))


#: How long a finished task's result waits for the identity store to say
#: whose it is before it stays in its space (the Swarm panel holds it).
OWNER_ANSWER_WAIT_S = 6 * 3600.0
_OWNER_ANSWER_STEPS = (5.0, 15.0, 60.0, 300.0)


async def wait_for_owner_answer(task: object, *, label: str) -> bool:
    """:func:`_task_for_the_owner`, read off the loop, waiting while the
    identity store cannot answer (Spaces S1-16 follow-up): a database outage
    holds the owner's result until it can be read, it is never dropped. True
    when the owner's channels may carry it; False for someone else's, and
    after :data:`OWNER_ANSWER_WAIT_S` or when Kazma stops (the result stays
    in the Swarm panel). Not enforced: True at once, nothing read."""
    import asyncio
    import time

    from kazma_core.db import availability
    from kazma_core.shutdown import sleep_or_shutdown
    from kazma_core.spaces.identity import IdentityUnavailable

    give_up = time.monotonic() + OWNER_ANSWER_WAIT_S
    attempt = 0
    while True:
        try:
            return await asyncio.to_thread(_task_for_the_owner, task)
        except IdentityUnavailable as exc:
            outage = availability.note_failure("swarm", exc.__cause__ or exc)
            if time.monotonic() >= give_up:
                logger.warning("[swarm] %s: whose it is could not be read for %.0f h; its result stays in "
                               "the Swarm panel", label, OWNER_ANSWER_WAIT_S / 3600)
                return False
            if attempt == 0:
                logger.warning("[swarm] %s: whose it is cannot be read; its result waits", label)
        attempt += 1
        if outage and availability.is_away():
            # The database is away: wait for it, then read again at once.
            if await availability.wait_while_away():
                return False
            continue
        if await sleep_or_shutdown(_OWNER_ANSWER_STEPS[min(attempt - 1, len(_OWNER_ANSWER_STEPS) - 1)]):
            return False
