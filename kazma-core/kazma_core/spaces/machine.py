"""Work run on the machine itself acts as the admin (Spaces S1-20, left 5).

``kazma ask``, ``kazma mcp`` and the terminal app run on the machine with no
sign-in: whoever runs them can read the install's files, so they are the
admin. Once Spaces is enforced, work that binds no space reads no space's data
and has no host tool (S1-01, S1-20). These three act as the admin in the
admin's own spaces (plan 4.4): each binds the owner's account in the owner's
personal space, or in a space it names that the owner is a member of. A
command naming any other space is refused before anything runs: the admin
does not open a member's space (decision O1).

An install that does not enforce Spaces binds nothing, exactly as before; a
command naming a space other than the owner's is refused there too, since
there is no other space to act in.

``machine_space`` reads the identity store: blocking, so async callers run it
in a thread and bind its answer themselves.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from kazma_core.spaces.scope import (
    OWNER_ACCOUNT_ID,
    OWNER_SPACE_ID,
    Scope,
    bind_scope,
    spaces_enforced,
    valid_space_id,
)

__all__ = ["SpaceRefused", "bound_on_the_machine", "machine_scope", "machine_space"]


class SpaceRefused(ValueError):
    """A command named a space it may not act in; nothing ran."""


def machine_space(requested: str | None = None) -> str | None:
    """The space work on the machine runs in: the one *requested*, else the
    owner's personal space; None when Spaces is not enforced and nothing (or
    the owner's space) was named. Raises :class:`SpaceRefused` for a space
    the owner is not a member of, and when the identity store cannot say."""
    from kazma_core.spaces import identity

    named = str(requested or "").strip()
    if not spaces_enforced():
        if named and named != OWNER_SPACE_ID:
            raise SpaceRefused(
                f"There is no space {named!r} to act in: Spaces is not on for this install."
            )
        return None
    space = named or OWNER_SPACE_ID
    if not valid_space_id(space):
        raise SpaceRefused(f"{space!r} is not a space id.")
    try:
        member = identity.is_member(OWNER_ACCOUNT_ID, space)
    except identity.IdentityUnavailable as exc:
        raise SpaceRefused(f"The spaces could not be read ({type(exc).__name__}); nothing ran.") from exc
    if not member:
        raise SpaceRefused(
            f"Kazma on this machine acts as the admin in the admin's own spaces only; "
            f"the admin is not a member of {space!r}."
        )
    return space


@contextmanager
def bound_on_the_machine(space: str | None) -> Iterator[Scope | None]:
    """Bind the owner's account in *space* (an answer of
    :func:`machine_space`); bind nothing for None."""
    if space is None:
        yield None
        return
    with bind_scope(space, OWNER_ACCOUNT_ID) as bound:
        yield bound


@contextmanager
def machine_scope(requested: str | None = None) -> Iterator[Scope | None]:
    """:func:`machine_space` and :func:`bound_on_the_machine` in one, for
    synchronous callers (a command's ``main``)."""
    with bound_on_the_machine(machine_space(requested)) as bound:
        yield bound
