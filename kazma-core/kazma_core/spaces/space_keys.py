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

__all__ = ["ensure_keys", "report"]

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
