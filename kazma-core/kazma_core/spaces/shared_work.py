"""Who may change a space's shared work (decision N32, Spaces S1-16 follow-up).

In a shared space every member reads the space's research -- a research
session, a research task -- and two people may change it (archive, restore,
delete, cancel, resume): its author, and the space's owner. Another member
reads it and answers ``not_yours`` when they try to change it.

While Spaces is not enforced nothing is refused here: one operator, as
before. Work a person did not start (Kazma's own sweeps) does not ask.
"""

from __future__ import annotations

__all__ = ["NOT_YOURS", "may_change"]

#: The answer to a change the caller may only read.
NOT_YOURS = "not_yours"


def may_change(space_id: str, author: str | None) -> bool:
    """Whether the person this work is for may change work of *space_id*
    whose author is *author*: always while Spaces is not enforced; once it
    is, the author or an owner of the space. Work from before Spaces (no
    space recorded) is the owner's space's. No person bound: False.
    Blocking (the identity store); ``IdentityUnavailable`` when it cannot
    say."""
    from kazma_core.spaces.identity import owns_space
    from kazma_core.spaces.scope import OWNER_SPACE_ID, bound_asker, spaces_enforced

    if not spaces_enforced():
        return True
    caller = bound_asker() or ""
    if not caller:
        return False
    if author and caller == author:
        return True
    return owns_space(caller, str(space_id or "") or OWNER_SPACE_ID)
