"""Connected accounts belong to a space (Spaces S1-17).

A connected account -- a mailbox, a calendar, and after them the X account
and the GitHub sign-in -- keeps its secrets in the vault under names its code
chooses (``email.gmail.refresh_token``) and, for the mail code, some of its
settings in the process environment (an IMAP host a connect sets, the
``EMAIL_*`` lines of ``.env``).

The owner's space keeps both exactly as they are: the owner's existing
connections stay the owner space's, unchanged (decision N26). Every other
space keeps its own under the same names inside its space's prefix
(``space:<id>:email.gmail.refresh_token``), install-scoped like the owner's so
a refresh that runs with no request bound reads the space it was told; the
environment is the install's, so another space's environment values are kept
in the vault too (``space:<id>:env.EMAIL_IMAP_HOST``) and the process
environment is never read or written for it.

The GitHub sign-in keeps its tokens in settings keys (vaulted by their name):
the owner's space under today's keys, any other under ``tenant.<id>.``
(:func:`space_setting_key`, the form X's settings take). What the process
environment holds for a connected account (``GITHUB_TOKEN``) is the owner's
sign-in, read for the owner's space only (:func:`owners_environment`).

What an account's code reads and writes is the space the work is bound to
(:func:`connected_space`): the owner's while Spaces is not enforced, as
before; once it is, the bound space, and ``NoSpaceBound`` with none bound --
a connection is never looked up for no one, nor the owner's lent to it.
"""

from __future__ import annotations

from kazma_core.spaces.scope import OWNER_SPACE_ID

__all__ = [
    "INSTALL_ACCOUNT_SECRETS", "OWNER_ACCOUNT_PREFIXES", "SPACE_SECRET_PREFIX", "connected_space", "env_secret_name", "owners_environment", "space_secret_name",
    "space_setting_key",
]

#: The prefix of a space's connected-account secrets (install-scoped in the
#: vault, ``security.vault.INSTALL_SCOPED_SECRETS``).
SPACE_SECRET_PREFIX = "space:"


#: The install's app registration with Google and Microsoft: one for every
#: space, read by each space's sign-in and refresh. The one list (the mail
#: code and the vault's space scope both read it).
INSTALL_ACCOUNT_SECRETS = frozenset({
    "email.gmail.client_id", "email.gmail.client_secret",
    "email.microsoft.client_id", "email.microsoft.client_secret",
})

#: Vault names of the owner's space's own mailbox and calendar (its tokens,
#: scopes, passwords, extra accounts), kept under the names they always had
#: (N26). Everything under them but :data:`INSTALL_ACCOUNT_SECRETS`.
OWNER_ACCOUNT_PREFIXES = ("email.", "calendar.")


def connected_space() -> str:
    """The space whose connected accounts this work uses: the owner's while
    Spaces is not enforced; once it is, the bound space (``NoSpaceBound``
    with none bound). A ContextVar read: no I/O."""
    from kazma_core.spaces.scope import require_space_id, spaces_enforced

    if not spaces_enforced():
        return OWNER_SPACE_ID
    return require_space_id()


def space_secret_name(name: str, space: str | None = None) -> str:
    """The vault name of a connected account's secret *name* in *space*
    (the bound one when None): *name* itself in the owner's space, else
    ``space:<space>:<name>``."""
    space = connected_space() if space is None else space
    if space == OWNER_SPACE_ID:
        return name
    return f"{SPACE_SECRET_PREFIX}{space}:{name}"


def space_setting_key(key: str, space: str | None = None) -> str:
    """The settings key of a connected account's setting *key* in *space*
    (the bound one when None): *key* itself in the owner's space, else
    ``tenant.<space>.<key>`` -- the form X's settings already take
    (``x_api.ownership.x_config_key``). The GitHub sign-in keeps its tokens
    this way (S1-17 part c)."""
    space = connected_space() if space is None else space
    return key if space == OWNER_SPACE_ID else f"tenant.{space}.{key}"


def owners_environment(variable: str) -> str:
    """The process environment's *variable*, for work of the owner's space
    only: the environment is the install's, and what it holds for a
    connected account (``GITHUB_TOKEN``) is the owner's -- another space
    reads ``""``, never the owner's sign-in (N26). ``NoSpaceBound`` with
    Spaces enforced and none bound."""
    import os

    return os.environ.get(variable, "") if connected_space() == OWNER_SPACE_ID else ""


def env_secret_name(variable: str, space: str) -> str:
    """Where a space other than the owner's keeps what the owner's keeps in
    the process environment variable *variable*."""
    if space == OWNER_SPACE_ID:
        raise ValueError("the owner's space keeps its environment in the process environment")
    return space_secret_name(f"env.{variable}", space)
