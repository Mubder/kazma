"""Vault-key pairing — migration invariant A.

``vault.db`` and ``KAZMA_VAULT_KEY`` are an atomic pair: the vault's
per-installation PBKDF2 salt lives *inside* ``vault.db``, and the key
derivation depends on both. Copying the encrypted DB to a machine with a
*different* ``KAZMA_VAULT_KEY`` makes every secret undecryptable — the #1
silent-breakage mode of a naive copy-paste migration.

This module encapsulates the check/sync logic so the importer has one
clear chokepoint.

States reported by :func:`check_vault_key`:

  - ``MATCH``     : target key matches the bundle (safe to import vault.db)
  - ``EMPTY``     : target has no key yet (import will write the bundle's key)
  - ``MISMATCH``  : target key differs — import must abort unless the caller
                    passes ``--reset-vault-key`` (which overwrites the target
                    ``.env``'s ``KAZMA_VAULT_KEY`` with the bundle's).
  - ``NO_VAULT``  : bundle has no vault.db (source had no secrets) — nothing
                    to do; the key check is skipped.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from kazma_core.migration.bundle import current_vault_key, vault_key_fingerprint

logger = logging.getLogger(__name__)

__all__ = ["VaultKeyStatus", "VaultPairing", "check_vault_key", "sync_vault_key"]


class VaultKeyStatus(str, Enum):
    MATCH = "match"
    EMPTY = "empty"
    MISMATCH = "mismatch"
    NO_VAULT = "no_vault"
    INCONSISTENT = "inconsistent"


@dataclass
class VaultPairing:
    status: VaultKeyStatus
    bundle_key: str  # the key from meta.env (may be empty)
    target_key: str  # the live KAZMA_VAULT_KEY (may be empty)
    message: str


def check_vault_key(
    *,
    bundle_vault_key: str,
    bundle_has_vault_db: bool,
    bundle_fingerprint: str,
) -> VaultPairing:
    """Compare the bundle's vault key against the live environment's key.

    Args:
        bundle_vault_key: the ``KAZMA_VAULT_KEY`` read from the bundle's
            ``meta.env`` (the source machine's key).
        bundle_has_vault_db: whether ``vault.db`` is present in the bundle.
        bundle_fingerprint: the fingerprint recorded in ``manifest.json``
            (cross-check that meta.env wasn't tampered with).
    """
    if not bundle_has_vault_db:
        return VaultPairing(
            status=VaultKeyStatus.NO_VAULT,
            bundle_key="",
            target_key="",
            message="bundle has no vault.db — source had no encrypted secrets",
        )

    # Cross-check meta.env against the manifest fingerprint. If they
    # disagree, the bundle is internally inconsistent — refuse.
    if bundle_fingerprint and vault_key_fingerprint(bundle_vault_key) != bundle_fingerprint:
        # Distinct from a legitimate MISMATCH: the bundle itself is internally
        # inconsistent (meta.env key disagrees with the manifest fingerprint),
        # which signals tampering. This MUST NOT be bypassable by
        # --reset-vault-key (which would overwrite the target's legitimate key
        # with the tampered bundle's key). The importer hard-aborts on
        # INCONSISTENT regardless of the reset flag.
        return VaultPairing(
            status=VaultKeyStatus.INCONSISTENT,
            bundle_key=bundle_vault_key,
            target_key=current_vault_key(),
            message=(
                "bundle internal inconsistency: meta.env vault key does not match "
                "manifest fingerprint (bundle may be tampered with)"
            ),
        )

    target_key = current_vault_key()
    if not target_key:
        return VaultPairing(
            status=VaultKeyStatus.EMPTY,
            bundle_key=bundle_vault_key,
            target_key="",
            message=(
                "target has no KAZMA_VAULT_KEY set; import will write the bundle's "
                "key into .env so vault.db decrypts"
            ),
        )

    if target_key == bundle_vault_key:
        return VaultPairing(
            status=VaultKeyStatus.MATCH,
            bundle_key=bundle_vault_key,
            target_key=target_key,
            message="target vault key matches the bundle — vault.db will decrypt",
        )

    return VaultPairing(
        status=VaultKeyStatus.MISMATCH,
        bundle_key=bundle_vault_key,
        target_key=target_key,
        message=(
            "target KAZMA_VAULT_KEY differs from the bundle's. vault.db cannot "
            "decrypt with the target's key. Re-run with --reset-vault-key to "
            "overwrite the target's key with the bundle's (the existing target "
            "vault.db, if any, will be backed up first)."
        ),
    )


def sync_vault_key(
    *,
    bundle_vault_key: str,
    env_path: Path,
    target_data_dir: Path,
) -> Path | None:
    """Write the bundle's ``KAZMA_VAULT_KEY`` into the target ``.env``.

    Backs up any pre-existing target ``vault.db`` to
    ``<data_dir>/.migrate-vault-backup-<ts>.db`` first (so the target's own
    secrets aren't lost), then writes the key line.

    Returns the backup path if one was made, else None.
    """
    backup_path: Path | None = None
    target_vault = target_data_dir / "vault.db"
    if target_vault.exists():
        import time

        ts = int(time.time())
        backup_path = target_data_dir / f".migrate-vault-backup-{ts}.db"
        target_vault.replace(backup_path)
        logger.info("[migrate] backed up existing target vault.db -> %s", backup_path.name)

    _set_env_key(env_path, "KAZMA_VAULT_KEY", bundle_vault_key)
    # Make the running process see it immediately (importer uses vault after).
    os.environ["KAZMA_VAULT_KEY"] = bundle_vault_key
    return backup_path


def _set_env_key(env_path: Path, key: str, value: str) -> None:
    """Set one KEY=value in a .env file, creating or updating in place.

    Preserves other lines and comment formatting. Idempotent.
    """
    env_path = Path(env_path)
    lines: list[str] = []
    found = False
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _, _ = stripped.partition("=")
                if k.strip() == key:
                    lines.append(f"{key}={value}")
                    found = True
                    continue
            lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    # Atomic write (temp sibling + os.replace): a crash/power loss mid-
    # write_text left a truncated .env that could drop KAZMA_VAULT_KEY (or
    # other lines) — same corruptibility class §11A flags for JSON stores
    # (audit finding).
    tmp = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, env_path)
