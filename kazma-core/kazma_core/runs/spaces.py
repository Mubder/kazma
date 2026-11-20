"""Running work kept to its space (Spaces S1-21).

Every run carries the space it belongs to and the person who asked
(``Run.space``, ``Run.asker``), stamped by its kind's adapter from the kind's
own record: a chat turn's thread record, a swarm task's metadata, a scheduled
job's tenant and creator, a document job's tenant and actor, a research
session's tenant and asker, a crawl's library or working folder, an X
operation's tenant, a memory task's payload. Work of the install itself (a
maintenance sweep, a backup task) names no space, and work from before Spaces
none either: both are the owner's space's (N19).

**Nothing here acts unless Spaces is enforced.** Then:

* a surface shows the runs and incidents of the space bound to its request
  (:func:`visible_here`), never another space's -- the admin's included (O1);
* acting on a run is for its asker or an owner of its space
  (:func:`may_act`, the rule shared work follows: ``spaces.shared_work``),
  and the kind's own control may be stricter (a swarm task answers only its
  asker, N4);
* the run supervisor, Kazma's own work, reads every space's runs and opens
  their incidents, but sends nothing about another space's run to the
  install's channels (:func:`kept_in_its_space`): until people link their own
  chat apps (stage 3) every one of them is the owner's. The incident waits on
  its space's Dashboard and bell;
* work bound to no space (a chat app's turn before its chats are bound to
  one, stage 3) is refused (:data:`NO_SPACE_REFUSAL`).

Not enforced, every surface asks the older single-operator rule
(:func:`runs_refusal`), exactly as before.
"""

from __future__ import annotations

from typing import Any

from kazma_core.spaces.scope import OWNER_SPACE_ID, current_scope, spaces_enforced

__all__ = [
    "NO_SPACE_REFUSAL",
    "kept_in_its_space",
    "may_act",
    "runs_refusal",
    "space_of",
    "visible_here",
]

#: Spaces enforced and the work tied to no space.
NO_SPACE_REFUSAL = "Running work is shown inside a space, and this work is not tied to one yet."


def space_of(space: Any) -> str:
    """The space a run or incident belongs to: its own, else the owner's
    (the install's own work, and work from before Spaces, N19)."""
    return str(space or "").strip() or OWNER_SPACE_ID


def runs_refusal(multi_user_message: str) -> str | None:
    """Why a running-work surface refuses this work, or None.

    Enforced: :data:`NO_SPACE_REFUSAL` with no space bound, else None. Not
    enforced: *multi_user_message* on an install of the older multi-user
    mode (``security.single_operator``). Blocking (a settings read when not
    enforced): async callers use ``asyncio.to_thread``."""
    if spaces_enforced():
        return None if current_scope() is not None else NO_SPACE_REFUSAL
    from kazma_core.security.single_operator import single_operator_refusal

    return single_operator_refusal(multi_user_message)


def visible_here(space: Any) -> bool:
    """Whether work of *space* is shown to the work bound now. No I/O.

    Always while Spaces is not enforced; once it is, only in its own space,
    and to nothing bound to no space."""
    if not spaces_enforced():
        return True
    bound = current_scope()
    return bound is not None and space_of(space) == bound.space_id


def kept_in_its_space(space: Any) -> bool:
    """Whether news of work of *space* stays off the install's channels (the
    run's chat address, the ops alerts): once Spaces is enforced, every
    space's but the owner's (O1). No I/O."""
    return spaces_enforced() and space_of(space) != OWNER_SPACE_ID


def may_act(space: Any, asker: Any) -> bool:
    """Whether the person bound now may act on work of *space* asked by
    *asker*: always while Spaces is not enforced; once it is, only in its own
    space, and only its asker or an owner of that space. Blocking (the
    identity store); ``IdentityUnavailable`` when it cannot say."""
    if not spaces_enforced():
        return True
    if not visible_here(space):
        return False
    from kazma_core.spaces.shared_work import may_change

    return may_change(space_of(space), str(asker or "") or None)
