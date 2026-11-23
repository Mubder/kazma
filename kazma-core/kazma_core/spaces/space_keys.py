"""A key per space (Spaces S1-26, plan 4.9).

Every space but the owner's has a key of its own, and once Spaces is enforced
its secrets are sealed with it. The keys live in the vault's own file
(``vault.db``, table ``space_keys``), each wrapped by a key derived from the
install's vault key: the salt, the wrapped keys and the rows they seal are
one file, carried whole by a backup, a migration bundle and a restore. The
vault does the sealing (``security.vault``: ``sealing_space`` says which rows,
``SecretVault.seal_space`` / ``unseal_space`` / ``rotate_space_key`` move
them); this module is what the identity store and the switch-on check ask.

* :func:`ensure_keys` -- called by ``identity.update_registry`` for every
  space a change made: "made when the space is made". Best effort: a space
  whose key could not be made gets it with its first sealed secret.
* :func:`report` -- what ``kazma spaces migrate --check`` (S1-33) reads:
  spaces with no key, secrets of a space still under the install's vault key,
  rows whose key is lost, keys of a space the install does not hold.
* :func:`change_key` -- a space gets a new key (S4-07, ``kazma spaces keys
  change``): its sealed secrets are sealed again with it, its old keys are
  destroyed (``SecretVault.rotate_space_key``), and the change is recorded
  (``space_key_changed``).
* :func:`destroy_erased_keys` -- a deleted space's key is destroyed (S4-07):
  an erase takes a space's keys and secrets itself (``erase_space``), and
  this maintenance sweep destroys them again when they come back -- an older
  vault file restored over an identity store that recorded the erase.

The owner's space has no key (decision N22): its secrets stay under the
install's vault key until stage 4. What this keeps apart is one space from
another; it keeps nothing from the person who runs the machine, who holds the
vault key that wraps every space's.

Blocking (SQLite, and :func:`report` reads the identity store): call off the
event loop.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from typing import Any

from kazma_core.spaces.scope import OWNER_SPACE_ID, valid_space_id

__all__ = ["KeyChangeRefused", "change_key", "destroy_erased_keys", "ensure_keys", "report"]

logger = logging.getLogger(__name__)


def ensure_keys(space_ids: Iterable[str]) -> list[str]:
    """Make a key for each of *space_ids* that has none (never the owner's).
    Returns the spaces that now have one. Never raises: the space stands
    without its key, and the vault makes it with the space's first sealed
    secret."""
    wanted = [s for s in space_ids if s != OWNER_SPACE_ID and valid_space_id(s)]
    if not wanted:
        return []
    from kazma_core.diagnostic_scope import DiagnosticWriteRefused
    from kazma_core.security.vault import get_vault

    vault = get_vault()
    if vault is None:
        logger.info(
            "[spaces] the vault is off: %d new space(s) get their key with their first secret", len(wanted))
        return []
    keyed: list[str] = []
    for space in wanted:
        try:
            vault.ensure_space_key(space)
            keyed.append(space)
        except (sqlite3.Error, ValueError, DiagnosticWriteRefused) as exc:
            logger.warning(
                "[spaces] the key of space %s could not be made (%s); it is made with its first secret",
                space, type(exc).__name__)
    return keyed


def report() -> dict[str, Any]:
    """The state of the space keys, with no secret and no key: ``no_key``
    (spaces of the install with none), ``unsealed`` (space -> secrets still
    under the install's vault key), ``broken`` (secrets whose key is lost),
    ``unknown_spaces`` (keys of a space the install does not hold) and
    ``vault`` (False when the vault is off)."""
    from kazma_core.security.vault import get_vault
    from kazma_core.spaces import identity

    spaces = sorted(s for s in identity.read_registry()["spaces"] if s != OWNER_SPACE_ID)
    vault = get_vault()
    if vault is None:
        return {"vault": False, "no_key": spaces, "unsealed": {}, "broken": 0, "unknown_spaces": []}
    found = vault.key_report()
    return {
        "vault": True,
        "no_key": [s for s in spaces if s not in found["keys"]],
        "unsealed": found["unsealed"],
        "broken": found["broken"],
        "unknown_spaces": sorted(s for s in found["keys"] if s not in spaces),
    }


# ── Key care (S4-07) ────────────────────────────────────────────────────


class KeyChangeRefused(ValueError):  # noqa: N818 -- named for what happened, as people.PeopleRefused
    """A key change that is refused; nothing was changed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def change_key(space_id: str, *, by: str) -> dict[str, Any]:
    """Give *space_id* a new key: its sealed secrets are sealed again with
    it (each read back first), its old keys are destroyed, and the change
    is recorded. One vault transaction. Refused (:class:`KeyChangeRefused`)
    for the owner's space (its secrets are the install's vault key's, N22),
    a space the install does not hold or is erasing, and with the vault
    off. Returns ``{"space", "key_id", "sealed"}`` (no key). A running
    Kazma that unwrapped the old key keeps it in its memory until it
    reloads; the file holds it no more. Blocking."""
    from kazma_core.security.vault import get_vault
    from kazma_core.spaces import identity, people

    if space_id == OWNER_SPACE_ID:
        raise KeyChangeRefused(
            "owners_space", "The owner's space has no key of its own: its secrets are the install's vault key's.")
    space = identity.read_registry()["spaces"].get(space_id)
    if not isinstance(space, dict) or space.get("erasing_at"):
        raise KeyChangeRefused("no_such_space", f"There is no space {space_id!r}.")
    vault = get_vault()
    if vault is None:
        raise KeyChangeRefused("vault_off", "The vault is off (KAZMA_VAULT_KEY is not set): no space has a key.")
    key_id = vault.rotate_space_key(space_id)
    try:
        people.note_key_changed(space_id, by=by)
    except (people.PeopleRefused, identity.IdentityUnavailable) as exc:
        # The key was changed: the record missing it is said, never the change undone.
        logger.warning("[spaces] the key of space %s was changed but the change could not be recorded (%s)",
                       space_id, type(exc).__name__)
    sealed = vault.key_report()["sealed"].get(space_id, 0)
    logger.info("[spaces] the key of space %s was changed by %s (%d sealed secret(s))", space_id, by, sealed)
    return {"space": space_id, "key_id": key_id, "sealed": sealed}


def destroy_erased_keys() -> list[str]:
    """Destroy what the vault still holds of a space the identity record
    says was erased (``space_erased``) and the install no longer holds: its
    keys and its secrets, as the erase did (``SecretVault.erase_space``).
    They come back when an older vault file is restored over an identity
    store that recorded the erase. Only a space recorded erased: one merely
    missing from the registry (a restore the other way, a stale copy) is
    left, and :func:`report` names it. Raises when the identity store cannot
    be read (never taken as "nothing erased"). Returns the spaces whose
    keys were destroyed. Blocking."""
    from kazma_core.spaces import identity

    registry = identity.read_registry()
    erased = {
        str(e["space"]) for e in registry.get("events") or ()
        if isinstance(e, dict) and e.get("kind") == "space_erased" and e.get("space")
    }
    gone = erased - set(registry["spaces"]) - {OWNER_SPACE_ID}
    if not gone:
        return []
    from kazma_core.security.vault import get_vault

    vault = get_vault()
    if vault is None:
        return []
    destroyed: list[str] = []
    for space in sorted(gone & vault.held_spaces()):
        vault.erase_space(space)
        logger.warning(
            "[spaces] the vault held keys or secrets of space %s, which was erased; they came back "
            "(an older vault file restored?) and are destroyed again", space)
        destroyed.append(space)
    return destroyed
