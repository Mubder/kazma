"""Encrypted Secret Vault — AES-256-GCM encrypted storage for API keys, tokens, and secrets.

The vault uses a separate encrypted SQLite database (``kazma-data/vault.db``),
distinct from the plaintext ``settings.db``.  Secrets are encrypted with
AES-256-GCM; the encryption key is derived from the ``KAZMA_VAULT_KEY``
environment variable via PBKDF2-HMAC-SHA256.

If ``KAZMA_VAULT_KEY`` is not set, the vault is disabled and all operations
return a graceful error.

Usage::

    from kazma_core.security.vault import get_vault

    vault = get_vault()
    if vault is None:
        print("Vault disabled — set KAZMA_VAULT_KEY")

    vault.store("openai_key", "sk-...", category="llm")
    value = vault.retrieve("openai_key")  # → "sk-..."
    secrets = vault.list_secrets()        # → [{"name": "openai_key", "category": "llm"}]
    vault.delete("openai_key")
"""

from __future__ import annotations

import json
import logging
import os
import secrets as _secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas
from kazma_core.tenant_context import get_current_tenant_id

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

_VAULT_DB = "kazma-data/vault.db"
_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 32
_KEY_BYTES = 32  # AES-256
_NONCE_BYTES = 12  # GCM standard

_SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    encrypted_value BLOB NOT NULL,
    nonce BLOB NOT NULL,
    category TEXT DEFAULT 'general',
    metadata TEXT DEFAULT '{}',
    tenant_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_secrets_name_tenant ON secrets(name, COALESCE(tenant_id, '__global__'));
CREATE TABLE IF NOT EXISTS vault_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SecretVault:
    """Encrypted-at-rest secret store backed by AES-256-GCM.

    The master encryption key is derived from the ``KAZMA_VAULT_KEY``
    environment variable using PBKDF2-HMAC-SHA256 with a per-installation
    random salt (stored in ``vault_meta``).  Each secret record gets its own
    random 12-byte GCM nonce.
    """

    def __init__(self, db_path: str = _VAULT_DB) -> None:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        self._AESGCM = AESGCM
        self._PBKDF2HMAC = PBKDF2HMAC
        self._hashes = hashes

        self._db_path = str(Path(db_path).resolve())
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self._db_path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        apply_sqlite_pragmas(self._conn)
        self._conn.executescript(_SCHEMA)

        # Derive the encryption key from KAZMA_VAULT_KEY.
        raw_key = os.environ.get("KAZMA_VAULT_KEY", "").encode("utf-8")
        if not raw_key:
            raise ValueError(
                "KAZMA_VAULT_KEY is not set. The vault is disabled. "
                "Set it in your .env or environment to enable the secret vault."
            )

        # Load or create the per-installation salt.
        row = self._conn.execute(
            "SELECT value FROM vault_meta WHERE key = 'pbkdf2_salt'"
        ).fetchone()
        if row:
            salt = bytes.fromhex(row["value"])
        else:
            salt = _secrets.token_bytes(_SALT_BYTES)
            self._conn.execute(
                "INSERT OR REPLACE INTO vault_meta (key, value) VALUES ('pbkdf2_salt', ?)",
                (salt.hex(),),
            )

        # Derive the AES-256 key.
        kdf = PBKDF2HMAC(
            algorithm=self._hashes.SHA256(),
            length=_KEY_BYTES,
            salt=salt,
            iterations=_PBKDF2_ITERATIONS,
        )
        self._key = kdf.derive(raw_key)
        logger.info("[Vault] Initialized at %s (AES-256-GCM, PBKDF2 %d iters)", self._db_path, _PBKDF2_ITERATIONS)

    # ── Core encrypt / decrypt ──────────────────────────────────────────

    def _encrypt(self, plaintext: str) -> tuple[bytes, bytes]:
        """Encrypt a string. Returns (ciphertext, nonce)."""
        nonce = _secrets.token_bytes(_NONCE_BYTES)
        aesgcm = self._AESGCM(self._key)
        ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return ct, nonce

    def _decrypt(self, ciphertext: bytes, nonce: bytes) -> str:
        """Decrypt ciphertext with the given nonce."""
        aesgcm = self._AESGCM(self._key)
        pt = aesgcm.decrypt(nonce, ciphertext, None)
        return pt.decode("utf-8")

    # ── Tenant resolution ───────────────────────────────────────────────

    @staticmethod
    def _tenant_filter(tenant_id: str | None = None) -> str | None:
        """Resolve tenant: explicit arg → ContextVar → None (global)."""
        if tenant_id is not None:
            return tenant_id
        return get_current_tenant_id()

    # ── Public API ──────────────────────────────────────────────────────

    def store(
        self,
        name: str,
        value: str,
        category: str = "general",
        metadata: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Store (or update) a secret in the vault.

        Args:
            name: A human-readable name (e.g. "openai_key"). Unique per tenant.
            value: The secret value to encrypt and store.
            category: A grouping tag (e.g. "llm", "database", "payment").
            metadata: Optional non-sensitive metadata dict.
            tenant_id: Optional tenant override. Defaults to ContextVar.

        Returns:
            The secret ID.
        """
        ct, nonce = self._encrypt(value)
        tid = self._tenant_filter(tenant_id)
        now = datetime.now(timezone.utc).isoformat()
        sid = _secrets.token_hex(16)
        meta = json.dumps(metadata or {})

        with self._lock:
            # Upsert: if name+tenant exists, replace.
            self._conn.execute(
                "DELETE FROM secrets WHERE name = ? AND COALESCE(tenant_id, '__global__') = COALESCE(?, '__global__')",
                (name, tid),
            )
            self._conn.execute(
                """INSERT INTO secrets (id, name, encrypted_value, nonce, category, metadata, tenant_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sid, name, ct, nonce, category, meta, tid, now, now),
            )
        logger.info("[Vault] Stored secret '%s' (category=%s, tenant=%s)", name, category, tid or "global")
        return sid

    def retrieve(self, name: str, tenant_id: str | None = None) -> str | None:
        """Retrieve and decrypt a secret by name.

        Falls back to global (tenant_id IS NULL) if the tenant-scoped
        secret doesn't exist.

        Returns:
            The decrypted secret value, or None if not found.
        """
        tid = self._tenant_filter(tenant_id)
        with self._lock:
            # Try tenant-specific first, then global.
            for query_tid in ([tid] if tid else []) + [None]:
                row = self._conn.execute(
                    """SELECT encrypted_value, nonce FROM secrets
                       WHERE name = ? AND COALESCE(tenant_id, '__global__') = COALESCE(?, '__global__')""",
                    (name, query_tid),
                ).fetchone()
                if row:
                    return self._decrypt(row["encrypted_value"], row["nonce"])
        return None

    def list_secrets(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """List all secrets (names + categories, NOT values)."""
        tid = self._tenant_filter(tenant_id)
        with self._lock:
            if tid:
                cursor = self._conn.execute(
                    """SELECT name, category, tenant_id, created_at, updated_at
                       FROM secrets WHERE tenant_id = ? OR tenant_id IS NULL ORDER BY name""",
                    (tid,),
                )
            else:
                cursor = self._conn.execute(
                    """SELECT name, category, tenant_id, created_at, updated_at
                       FROM secrets ORDER BY name"""
                )
            return [
                {
                    "name": r["name"],
                    "category": r["category"],
                    "tenant": r["tenant_id"] or "global",
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in cursor
            ]

    def delete(self, name: str, tenant_id: str | None = None) -> bool:
        """Delete a secret. Returns True if a row was deleted."""
        tid = self._tenant_filter(tenant_id)
        with self._lock:
            cur = self._conn.execute(
                """DELETE FROM secrets
                   WHERE name = ? AND COALESCE(tenant_id, '__global__') = COALESCE(?, '__global__')""",
                (name, tid),
            )
        deleted = cur.rowcount > 0
        if deleted:
            logger.info("[Vault] Deleted secret '%s' (tenant=%s)", name, tid or "global")
        return deleted

    @property
    def count(self) -> int:
        """Total number of stored secrets."""
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM secrets").fetchone()[0]


# ── Singleton ──────────────────────────────────────────────────────────────

_vault: SecretVault | None = None
_vault_init_attempted = False


def get_vault() -> SecretVault | None:
    """Return the shared SecretVault singleton.

    Returns None if KAZMA_VAULT_KEY is not set (vault disabled).
    Once attempted, does not retry — avoids repeated PBKDF2 on every call.
    """
    global _vault, _vault_init_attempted
    if _vault_init_attempted:
        return _vault
    _vault_init_attempted = True
    try:
        _vault = SecretVault()
    except ValueError:
        logger.debug("[Vault] KAZMA_VAULT_KEY not set — vault disabled")
    except Exception as exc:
        logger.warning("[Vault] Init failed: %s", exc)
    return _vault


def reset_vault() -> None:
    """Reset the singleton (for tests)."""
    global _vault, _vault_init_attempted
    _vault = None
    _vault_init_attempted = False
