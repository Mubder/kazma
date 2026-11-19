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
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas
from kazma_core.diagnostic_scope import refuse_write
from kazma_core.tenant_context import get_current_tenant_id

__all__ = [
    "INSTALL_SCOPED_SECRETS",
    "SecretVault",
    "VaultScopeError",
    "consolidate_install_scoped_secrets",
    "get_vault",
    "is_install_scoped_secret",
    "is_kazma_owned_secret",
    "reset_posture_cache",
    "reset_vault",
    "retrieve_scoped",
    "retrieve_with_tenant_ladder",
    "secret_scope",
]

logger = logging.getLogger(__name__)

#: Vault names that belong to the install, not to the tenant whose request
#: happened to write them -- and, for copies written before this rule, which
#: copy wins when they disagree. The test for membership: the code that reads
#: the secret is one per process and shared by every tenant. :meth:`SecretVault.store`
#: and :meth:`SecretVault.delete` enforce it for EVERY writer, so no caller can
#: leave a stray per-tenant copy that shadows the install's for callers that
#: have a tenant (``retrieve`` reads the tenant's copy first). Entries are exact
#: names, or prefixes ending in ``.`` or ``:``.
#:
#: * Provider keys: the model registry caches one client per provider for all
#:   callers, and code with no tenant bound builds them. Settings stored the
#:   keys under tenant ``default``, invisible to that code on an install
#:   labelled production, so every boot replaced the configured provider with
#:   another one that had a key (live, 2026-09-16 to 2026-09-25). Those copies
#:   were the operator's saves, so the NEWEST copy wins.
#: * Mail and calendar OAuth clients and tokens: ``email_manager.credentials``
#:   has read and written them in the install scope since 2026-08-16 (an old
#:   tenant copy of a rotated OAuth secret failed every refresh). Other writers
#:   did not: the backup token refresh and the agent's secret tool wrote under
#:   the request's tenant, which is how ``email.gmail.scopes`` held one value
#:   for chat and another for background work (flagged every boot until
#:   2026-09-25). The install copy is the one mail code uses, so the GLOBAL
#:   copy wins.
#: * Memory backend secrets (``memory.backends.*``: the Postgres state DSN,
#:   the vector/graph URLs and passwords, the embedder key): one backend per
#:   process serves every tenant, and the memory worker reads them with no
#:   tenant bound. The state DSN sat in plaintext in the settings table until
#:   2026-09-25 because no rule knew ``state.url`` held a credential; moving
#:   it to the vault under a request's tenant would have hidden it from that
#:   worker. Operator saves, so the NEWEST copy wins.
#: * Chat-platform bot credentials (``connectors.slack.*`` / ``discord.*`` /
#:   ``telegram.*``: bot and app-level tokens, the webhook secret): the
#:   gateway runs ONE adapter per platform for the whole install, built at
#:   boot with no tenant bound. A token saved in Settings landed under the
#:   request's tenant; the Settings save then rebuilt the adapter inside that
#:   request, which read the new copy and connected -- and every restart
#:   rebuilt it at boot from the stale global copy. Live 2026-09-30: Slack
#:   answered ``invalid_auth`` on every reconnect after a reload, with the
#:   boot warning naming ``cfg:connectors.slack.app_token`` as split.
#:   Operator saves, so the NEWEST copy wins.
#:
#: Not here on purpose: X connector credentials (``cfg:connectors.x.*``). The
#: X code reads them as tenant ``default``, and moving an account the agent
#: posts as to the install would let every tenant's requests post with it --
#: an authorization change, not a storage fix.
INSTALL_SCOPED_SECRETS: tuple[tuple[str, str], ...] = (
    ("cfg:providers.list.", "newest"),  # providers.list[<name>].api_key and siblings
    ("cfg:llm.api_key", "newest"),  # the legacy single-provider key
    ("email.", "global"),  # mail OAuth clients, tokens, scopes, IMAP/POP passwords
    ("calendar.", "global"),  # calendar tokens, written through the mail helper
    # A space's own connected accounts (Spaces S1-17,
    # ``spaces.connected_accounts``): read by background refreshes too.
    ("space:", "global"),
    ("cfg:memory.backends.", "newest"),  # state DSN, vector/graph secrets, embedder key
    ("cfg:connectors.slack.", "newest"),  # bot + app-level (Socket Mode) tokens
    ("cfg:connectors.discord.", "newest"),  # bot token
    ("cfg:connectors.telegram.", "newest"),  # bot token, webhook secret
    ("cfg:connectors.whatsapp.", "newest"),  # access token, app secret, webhook verify token
    # MCP servers' keys, tokens and passwords (kazma_core/mcp/secrets.py):
    # the manager connects every server at boot, with no tenant bound.
    ("cfg:mcp.servers.", "newest"),
    # Offsite backup credentials (S3 access key; the NAS passwords are stored
    # global by the route already): the backup sync reads them with no tenant.
    ("cfg:backups.offsite.", "newest"),
    # The Web Push signing key (vault-routed since 2026-11-10): made in the
    # request of the first browser that subscribes, read by whatever finishes
    # a turn, often with no tenant bound.
    ("cfg:notifications.push.", "newest"),
    # The separate alerts bot (Settings -> Alerts, 2026-11-11): saved in an
    # admin's request, read by every alert -- background jobs, the guard's
    # settings lookup -- with no tenant bound.
    ("cfg:notifications.ops.telegram_bot_token", "newest"),
    # The pairing token of the Kazma Watch `kazma observer setup` deployed
    # (2026-11-14): read by the CLI, with no tenant bound.
    ("observer.", "global"),
    # Password hashes inside the identity store's registry (Spaces S1-01):
    # written in an admin's request, read by sign-in before any tenant is
    # bound.
    ("cfg:identity.", "newest"),
    # A URL whose password the settings store moved to the vault under a key
    # that is not a credential's (config_store.URL_SECRET_PREFIX:
    # ``proxy.url``, ``web.searxng_url``, 2026-11-15): read by the proxy, the
    # search chain and the backup sync with no tenant bound.
    ("cfg-url:", "newest"),
)


def _install_scope_rule(name: str) -> str | None:
    """Which copy wins for *name* (``"newest"``/``"global"``), or ``None`` if per-tenant."""
    for entry, authority in INSTALL_SCOPED_SECRETS:
        if name.startswith(entry) if entry.endswith((".", ":")) else name == entry:
            return authority
    return None


def is_install_scoped_secret(name: str) -> bool:
    """True if *name* (a vault name) belongs to the install: :data:`INSTALL_SCOPED_SECRETS`."""
    return _install_scope_rule(name) is not None


#: The prefix of every vault row the settings store writes for a secret
#: setting (``cfg:<key path>``): provider keys, bot tokens, the memory
#: mirror's address, X credentials. Settings owns them.
CONFIG_SECRET_PREFIX = "cfg:"


def is_kazma_owned_secret(name: str) -> bool:
    """True if *name* is one of Kazma's own vault rows, never the agent's.

    A settings secret (``cfg:``) or an install-scoped one
    (:data:`INSTALL_SCOPED_SECRETS`: mail and calendar sign-ins among them).
    The agent's vault tools refuse these names both ways and leave them out of
    their list: a retrieve put a provider key or a bot token into the model's
    context, and a store overwrote a setting Settings guards
    (``safety.protected_config``) -- the memory mirror's address, say.
    """
    clean = str(name or "").strip()
    return clean.startswith(CONFIG_SECRET_PREFIX) or is_install_scoped_secret(clean)


# ── The space scope (Spaces S1-18) ─────────────────────────────────────────
#
# Once Spaces is enforced every vault name is the install's or one space's,
# and one function says which: :func:`secret_scope`. While it is not enforced
# nothing below is asked and the vault behaves as it always did.

#: The prefix of a space's connected-account secrets
#: (``spaces.connected_accounts.SPACE_SECRET_PREFIX``): the name carries its
#: space, ``space:<id>:<name>``.
_SPACE_NAME_PREFIX = "space:"

#: A settings key of one space (``tenant.<id>.<key>``): its secret's vault
#: name is ``cfg:tenant.<id>.<key>``.
_SPACE_SETTING_PREFIX = CONFIG_SECRET_PREFIX + "tenant."

#: Settings whose secrets are a space's connected account, kept under the
#: plain key in the owner's space (``spaces.connected_accounts``): the X
#: account and the GitHub sign-in. Every other setting is the install's until
#: settings are classed key by key (S1-23).
_SPACE_ACCOUNT_SETTINGS: tuple[str, ...] = (
    CONFIG_SECRET_PREFIX + "connectors.x.",
    CONFIG_SECRET_PREFIX + "connectors.github.",
)

#: Names the install's own work reads with no space bound, beside
#: :data:`INSTALL_SCOPED_SECRETS`: the offsite backup's passwords, which its
#: route stores with no tenant.
_INSTALL_NAMES_ONCE_ENFORCED: tuple[str, ...] = ("backups.offsite.",)


class VaultScopeError(PermissionError):
    """A write named a secret of another space than the one the work is in."""


def secret_scope(name: str) -> tuple[str, str | None]:
    """Whose secret *name* is once Spaces is enforced: ``("install", None)``,
    or ``("space", <id>)`` when the name itself carries its space, or
    ``("space", None)`` for a secret of whichever space stores it (its row's
    ``tenant_id``).

    * ``space:<id>:...`` and ``cfg:tenant.<id>....`` name their space.
    * :data:`INSTALL_SCOPED_SECRETS`, the offsite backup's passwords and
      every settings secret that is not a space's connected account are the
      install's: one copy, read by every space and by the install's own work.
    * Everything else -- a secret the agent stored, the X account and GitHub
      sign-in of the owner's space, the legacy X names -- is a space's.
    """
    clean = str(name or "")
    if clean.startswith(_SPACE_NAME_PREFIX):
        space, sep, _rest = clean[len(_SPACE_NAME_PREFIX):].partition(":")
        return "space", (space if sep else "")
    if clean.startswith(_SPACE_SETTING_PREFIX):
        space, sep, _rest = clean[len(_SPACE_SETTING_PREFIX):].partition(".")
        return "space", (space if sep else "")
    if is_install_scoped_secret(clean) or clean.startswith(_INSTALL_NAMES_ONCE_ENFORCED):
        return "install", None
    if clean.startswith(CONFIG_SECRET_PREFIX) and not clean.startswith(_SPACE_ACCOUNT_SETTINGS):
        return "install", None
    return "space", None


def _enforced() -> bool:
    from kazma_core.spaces.scope import spaces_enforced

    return spaces_enforced()


def _owner_space() -> str:
    from kazma_core.spaces.scope import OWNER_SPACE_ID

    return OWNER_SPACE_ID


def _callers_space(tenant_id: str | None) -> str | None:
    """The space the calling work is in: the one it names, else its bound
    scope's (the mail code pins the tenant to none while its scope stays
    bound), else a tenant bound by older code, else none."""
    if tenant_id:
        return tenant_id
    from kazma_core.spaces.scope import current_scope

    bound = current_scope()
    if bound is not None:
        return bound.space_id
    return get_current_tenant_id() or None

# ── Constants ──────────────────────────────────────────────────────────────

def _default_vault_db() -> str:
    from kazma_core.paths import vault_db_path
    return vault_db_path()

_VAULT_DB = None  # Resolved lazily via paths.py
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

    def __init__(self, db_path: str | None = None) -> None:
        from kazma_core.paths import vault_db_path
        if db_path is None:
            db_path = vault_db_path()
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        self._AESGCM = AESGCM
        self._PBKDF2HMAC = PBKDF2HMAC
        self._hashes = hashes

        self._db_path = str(Path(db_path).resolve())
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        # Names already reported by _note_scoped_miss: once per name per process.
        self._scoped_miss_warned: set[str] = set()
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

        A name in :data:`INSTALL_SCOPED_SECRETS` is stored for the whole
        install whatever *tenant_id* or the request says (see
        :meth:`store_install_scoped`): the backup token refresh and the agent's
        secret tool wrote mail tokens under the request's tenant, and that
        stray copy is what ``retrieve`` hands a caller that has a tenant.
        """
        refuse_write("vault", name)
        if _enforced():
            kind, _named = secret_scope(name)
            if kind == "install" or name.startswith(_SPACE_NAME_PREFIX):
                self._space_for_write(name, tenant_id)  # a named space must be the caller's
                return self._write_install_scoped(name, value, category, metadata)[1]
            tid: str | None = self._space_for_write(name, tenant_id)
        elif is_install_scoped_secret(name):
            return self._write_install_scoped(name, value, category, metadata)[1]
        else:
            tid = self._tenant_filter(tenant_id)
        ct, nonce = self._encrypt(value)
        now = datetime.now(UTC).isoformat()
        sid = _secrets.token_hex(16)
        meta = json.dumps(metadata or {})

        with self._lock:
            # Atomic upsert: use BEGIN/COMMIT so a crash between DELETE and
            # INSERT doesn't lose the secret.
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "DELETE FROM secrets WHERE name = ? AND COALESCE(tenant_id, '__global__') = COALESCE(?, '__global__')",
                    (name, tid),
                )
                self._conn.execute(
                    """INSERT INTO secrets (id, name, encrypted_value, nonce, category, metadata, tenant_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (sid, name, ct, nonce, category, meta, tid, now, now),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        logger.info("[Vault] Stored secret '%s' (category=%s, tenant=%s)", name, category, tid or "global")
        return sid

    @staticmethod
    def _space_for_write(name: str, tenant_id: str | None) -> str | None:
        """The space a write of *name* lands in once Spaces is enforced.

        The install's secret: none. A space's: the calling work's space,
        which must be the one the name carries when it carries one
        (:class:`VaultScopeError`), and ``NoSpaceBound`` with none bound --
        a secret written for no one would be the owner's.
        """
        kind, named = secret_scope(name)
        if kind == "install":
            return None
        space = _callers_space(tenant_id)
        if space is None:
            from kazma_core.spaces.scope import require_space_id

            space = require_space_id()  # raises NoSpaceBound
        if named is not None and named != space:
            raise VaultScopeError(
                f"secret {name!r} belongs to another space than the one this work is in"
            )
        return space

    def _scopes_to_read(self, name: str, tenant_id: str | None) -> list[str | None] | None:
        """The rows (by ``tenant_id``) a read or delete of *name* may touch
        once Spaces is enforced, in order; None when the calling work may not
        touch it at all (no space bound, or another space's name)."""
        kind, named = secret_scope(name)
        owner = _owner_space()
        if kind == "install":
            return [owner, None]
        space = _callers_space(tenant_id)
        if space is None or (named is not None and named != space):
            return None
        if name.startswith(_SPACE_NAME_PREFIX):
            return [None]  # kept as one row, told apart by its name
        # What an install stored with no tenant before Spaces is the owner's.
        return [space, None] if space == owner else [space]

    def for_space(self, space_id: str) -> _SpaceVault:
        """This vault for one space: every call names *space_id*."""
        return _SpaceVault(self, space_id)

    def store_install_scoped(
        self, name: str, value: str, category: str = "general",
    ) -> int:
        """Store *name* for the whole install. Returns the rows written (0 = unchanged).

        Writes the global row, and rewrites every tenant-scoped copy of the same
        name to the same value, in one transaction: a reader with a tenant sees
        its copy first (see :meth:`retrieve`), so a copy left behind would keep
        serving the old value to exactly the callers that have a tenant.
        Nothing is deleted; a copy already equal to *value* is left alone, so
        re-saving an unchanged setting writes nothing.

        :meth:`store` routes every :data:`INSTALL_SCOPED_SECRETS` name here.
        """
        refuse_write("vault", name)
        return self._write_install_scoped(name, value, category, None)[0]

    def _write_install_scoped(
        self, name: str, value: str, category: str, metadata: dict[str, Any] | None,
    ) -> tuple[int, str]:
        """(rows written, id of the global row) -- see :meth:`store_install_scoped`."""
        from cryptography.exceptions import InvalidTag

        meta = json.dumps(metadata or {})
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, tenant_id, encrypted_value, nonce FROM secrets "
                "WHERE name = ? ORDER BY rowid DESC",
                (name,),
            ).fetchall()
            newest: dict[str | None, str | None] = {}
            ids: dict[str | None, str] = {}
            for row in rows:
                if row["tenant_id"] in newest:
                    continue
                ids[row["tenant_id"]] = row["id"]
                try:
                    newest[row["tenant_id"]] = self._decrypt(
                        row["encrypted_value"], row["nonce"],
                    )
                except (InvalidTag, ValueError):
                    newest[row["tenant_id"]] = None  # unreadable: rewrite it
            scopes = [None, *sorted(t for t in newest if t is not None)]
            stale = [s for s in scopes if s not in newest or newest[s] != value]
            if not stale:
                return 0, ids[None]
            now = datetime.now(UTC).isoformat()
            self._conn.execute("BEGIN")
            committed = False
            try:
                for scope in stale:
                    ct, nonce = self._encrypt(value)
                    ids[scope] = _secrets.token_hex(16)
                    self._conn.execute(
                        "DELETE FROM secrets WHERE name = ? AND "
                        "COALESCE(tenant_id, '__global__') = COALESCE(?, '__global__')",
                        (name, scope),
                    )
                    self._conn.execute(
                        """INSERT INTO secrets (id, name, encrypted_value, nonce, category, metadata, tenant_id, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (ids[scope], name, ct, nonce, category, meta, scope, now, now),
                    )
                self._conn.execute("COMMIT")
                committed = True
            finally:
                if not committed:
                    self._conn.execute("ROLLBACK")
        logger.info(
            "[Vault] Stored secret '%s' at install scope (%d row(s) written: %s)",
            name, len(stale), ", ".join(s or "global" for s in stale),
        )
        return len(stale), ids[None]

    def consolidate_install_scoped(self) -> list[str]:
        """Bring install-scoped secrets written under a tenant up to install scope.

        Until 2026-09-25 a provider key saved through Settings was stored under
        the tenant of the request that saved it. The model registry reads it
        with no tenant bound, and on an install labelled production that read
        may not fall back to tenant ``default`` (it cannot tell ``default`` is
        the operator), so every boot substituted another provider for the
        configured one. Mail tokens gained stray tenant copies the same way.

        For each install-scoped name with a tenant-scoped row, the winning copy
        (per :data:`INSTALL_SCOPED_SECRETS`: the NEWEST in any scope, or the
        GLOBAL one when there is one) is stored at install scope with
        :meth:`store_install_scoped`. Returns the names changed. Idempotent:
        once every copy agrees there is nothing to do.
        """
        from cryptography.exceptions import InvalidTag

        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid, name, tenant_id, category, updated_at, "
                "encrypted_value, nonce FROM secrets"
            ).fetchall()
        by_name: dict[str, list[Any]] = {}
        for row in rows:
            if is_install_scoped_secret(row["name"]):
                by_name.setdefault(row["name"], []).append(row)
        changed: list[str] = []
        for name, group in sorted(by_name.items()):
            if all(r["tenant_id"] is None for r in group):
                continue
            pool = group
            if _install_scope_rule(name) == "global":
                pool = [r for r in group if r["tenant_id"] is None] or group
            latest = max(pool, key=lambda r: (r["updated_at"] or "", r["rowid"]))
            try:
                value = self._decrypt(latest["encrypted_value"], latest["nonce"])
            except (InvalidTag, ValueError):
                logger.warning(
                    "[Vault] '%s': the winning copy does not decrypt with this "
                    "KAZMA_VAULT_KEY -- left as it is; save it again (Settings, "
                    "or reconnect the account)",
                    name,
                )
                continue
            if self.store_install_scoped(name, value, category=latest["category"] or "config"):
                changed.append(name)
        if changed:
            logger.info(
                "[Vault] %d secret(s) stored under a tenant are now "
                "install-scoped, so every caller reads the same copy: %s",
                len(changed), ", ".join(changed),
            )
        return changed

    def retrieve(self, name: str, tenant_id: str | None = None) -> str | None:
        """Retrieve and decrypt a secret by name.

        Falls back to global (tenant_id IS NULL) if the tenant-scoped secret
        doesn't exist. **Scope wins over age:** a tenant-scoped row is returned
        even when the global row is newer. Within one scope the newest row
        wins, which is what protects a rotation from a stale duplicate
        (incident 2026-08-16: an old OAuth client secret kept winning over the
        rotated one and every token refresh failed with ``invalid_client``).

        This docstring used to claim "the most recently written row wins" flatly,
        across scopes. It never did, and could not: returning a global value to
        a caller that has its own tenant-scoped one would break the isolation
        this argument exists for. The real hazard is a *divergent duplicate* —
        the same name under both scopes with different values, where the answer
        then depends on whether the caller happens to have a tenant context.
        That is a data problem, not a lookup problem, so it is surfaced by
        :meth:`find_divergent_duplicates` rather than papered over here. The
        operator's install had five when this was written, including
        ``email.gmail.client_secret`` — the very key from that incident.

        Returns:
            The decrypted secret value, or None if not found.
        """
        if _enforced():
            return self._retrieve_enforced(name, tenant_id)
        tid = self._tenant_filter(tenant_id)
        with self._lock:
            # Try tenant-specific first, then global. Newest-first within a
            # scope so a rotated credential always wins over a stale copy.
            #
            # The reverse fallback (global -> tenant) is deliberately absent:
            # it would let any context-less background task read another
            # tenant's credentials. A caller that cannot see a secret it owns
            # is missing its tenant context, and that is fixed at the call
            # site -- see `resolve_live_client` and the CLI entrypoint, both
            # of which install one. `describe_secret` exists so a diagnostic
            # can tell "absent" from "present, one scope over" without
            # widening this lookup.
            for query_tid in ([tid] if tid else []) + [None]:
                row = self._conn.execute(
                    """SELECT encrypted_value, nonce FROM secrets
                       WHERE name = ? AND COALESCE(tenant_id, '__global__') = COALESCE(?, '__global__')
                       ORDER BY rowid DESC LIMIT 1""",
                    (name, query_tid),
                ).fetchone()
                if row:
                    return self._decrypt(row["encrypted_value"], row["nonce"])
            self._note_scoped_miss(name, tid)
        return None

    def _retrieve_enforced(self, name: str, tenant_id: str | None) -> str | None:
        """:meth:`retrieve` once Spaces is enforced (S1-18).

        The install's secret is read by anyone. A space's is read only by
        work in that space: another space, and work with no space bound, get
        None -- never the owner's copy (the ``default`` rung), never a row
        with no tenant unless the caller is the owner's space, whose rows
        from before Spaces those are.
        """
        scopes = self._scopes_to_read(name, tenant_id)
        if scopes is None:
            if name not in self._scoped_miss_warned:
                self._scoped_miss_warned.add(name)
                logger.warning(
                    "[Vault] '%s' is a space's secret and was read %s: nothing is "
                    "returned. The entry point must bind the space it works for.",
                    name,
                    "with no space bound" if _callers_space(tenant_id) is None
                    else "from another space",
                )
            return None
        with self._lock:
            for query_tid in scopes:
                row = self._conn.execute(
                    """SELECT encrypted_value, nonce FROM secrets
                       WHERE name = ? AND COALESCE(tenant_id, '__global__') = COALESCE(?, '__global__')
                       ORDER BY rowid DESC LIMIT 1""",
                    (name, query_tid),
                ).fetchone()
                if row:
                    return self._decrypt(row["encrypted_value"], row["nonce"])
        return None

    def _note_scoped_miss(self, name: str, tid: str | None) -> None:
        """Say once when a miss is really a missing tenant context.

        ``None`` from :meth:`retrieve` reads as "not configured", so a caller
        that forgot to bind a tenant fails silently and gets diagnosed wrong.
        It shipped three times, each found by a symptom far from the cause:
        09:00 reminders failing ``no usable API key``, a DeepSeek key read as
        absent and Z.AI substituted, and ``kazma doctor`` blaming another
        install's vault for a key that decrypted fine. A static list of entry
        points is the gate that let all of those through; the miss is where
        every caller passes.

        Only a read with NO tenant bound is reported. A caller that has its
        own tenant and misses another tenant's secret is isolation working,
        not a bug. Once per name per process, tenant names capped, never a
        value. The probe is one indexed query and runs only on a miss.
        Caller holds ``self._lock``.

        (First landed 2026-09-17 and reverted when CI hung — wrongly: the hang
        was ``DocumentWorker.stop()``'s unbounded wait, found and fixed
        2026-09-20.)
        """
        if tid or name in self._scoped_miss_warned:
            return
        try:
            rows = self._conn.execute(
                "SELECT DISTINCT tenant_id FROM secrets WHERE name = ? AND tenant_id IS NOT NULL",
                (name,),
            ).fetchall()
        except sqlite3.Error:
            return
        scopes = sorted(r["tenant_id"] for r in rows)
        if not scopes:
            return
        self._scoped_miss_warned.add(name)
        shown = ", ".join(scopes[:3]) + (f" (+{len(scopes) - 3} more)" if len(scopes) > 3 else "")
        logger.warning(
            "[Vault] '%s' was read with no tenant bound and is not stored globally, "
            "but it is stored for tenant(s): %s. This None means 'not visible from "
            "this scope', not 'not configured' — bind the tenant with "
            "tenant_scope(...) or read through retrieve_scoped().",
            name, shown,
        )

    def describe_secret(self, name: str) -> list[dict[str, Any]]:
        """Per-scope facts about `name` WITHOUT returning the secret.

        For diagnostics (``kazma doctor``): which scopes hold this name, when
        each was written, and whether it decrypts with this install's key.
        A tool that reports on credentials must not print them, and a tool
        that says "cannot decrypt" must have actually tried.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT tenant_id, updated_at, encrypted_value, nonce
                   FROM secrets WHERE name = ? ORDER BY rowid DESC""",
                (name,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                self._decrypt(r["encrypted_value"], r["nonce"])
                decrypts = True
            except Exception:  # noqa: BLE001 — a failure IS the answer here
                decrypts = False
            out.append({
                "tenant_id": r["tenant_id"],
                "updated_at": r["updated_at"],
                "decrypts": decrypts,
            })
        return out

    def find_divergent_duplicates(self) -> list[dict[str, Any]]:
        """Names stored under more than one scope with DIFFERENT values.

        A duplicate that agrees is harmless. A duplicate that disagrees means
        the value a caller gets depends on whether it happens to have a tenant
        context -- a background task reading global and a request reading
        tenant-scoped will use different credentials for the same service, and
        neither one is obviously wrong from where it stands.

        Returns one entry per divergent name with the scopes and their
        ``updated_at``, and **never the values**: this is meant to be safe to
        log. Comparison is over SHA-256 digests for the same reason.

        Found in the wild 2026-09-12: five divergent names on a single install,
        among them ``email.gmail.client_secret`` (global copy 25 days newer than
        the tenant copy) and both Microsoft mail tokens.
        """
        import hashlib
        from collections import defaultdict

        # Each scope's newest row, decrypted here: once Spaces is enforced
        # ``retrieve`` answers by the caller's space, not by the row asked.
        by_name: dict[str, dict[str | None, tuple[Any, Any, Any]]] = defaultdict(dict)
        with self._lock:
            for row in self._conn.execute(
                "SELECT name, tenant_id, updated_at, encrypted_value, nonce "
                "FROM secrets ORDER BY rowid DESC"
            ).fetchall():
                by_name[row["name"]].setdefault(
                    row["tenant_id"], (row["updated_at"], row["encrypted_value"], row["nonce"])
                )
        enforced = _enforced()

        out: list[dict[str, Any]] = []
        for name, scopes in by_name.items():
            if len(scopes) < 2:
                continue
            # The same name in two spaces is two secrets, not a duplicate:
            # once enforced only the install's names can disagree.
            if enforced and secret_scope(name)[0] != "install":
                continue
            entries = [(tid, updated) for tid, (updated, _ct, _nonce) in scopes.items()]
            seen: dict[str, str] = {}
            for tid, (_updated, ct, nonce) in scopes.items():
                try:
                    value = self._decrypt(ct, nonce)
                except Exception:  # pragma: no cover - unreadable row
                    value = None
                seen[str(tid)] = hashlib.sha256((value or "").encode()).hexdigest()
            if len(set(seen.values())) > 1:
                out.append({
                    "name": name,
                    "scopes": [
                        {"tenant_id": tid, "updated_at": updated}
                        for tid, updated in entries
                    ],
                })
        return sorted(out, key=lambda d: d["name"])

    def warn_on_divergent_duplicates(self) -> int:
        """Log one warning naming every divergent duplicate. Returns the count.

        Best-effort: a diagnostic must never be the reason a boot fails.
        """
        try:
            found = self.find_divergent_duplicates()
        except Exception:  # pragma: no cover - defensive
            logger.debug("[Vault] divergence scan skipped", exc_info=True)
            return 0
        if found:
            logger.warning(
                "[Vault] %d secret name(s) differ between tenant and global "
                "scope: %s. Which value a caller gets depends on whether it has "
                "a tenant context, so the same credential can work in chat and "
                "fail in a background task. Reconcile them.",
                len(found), ", ".join(d["name"] for d in found),
            )
        return len(found)

    def list_secrets(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """List all secrets (names + categories, NOT values)."""
        if _enforced():
            return self._list_enforced(tenant_id)
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

    def _list_enforced(self, tenant_id: str | None) -> list[dict[str, Any]]:
        """:meth:`list_secrets` once Spaces is enforced: a space's own rows
        (the owner's space also the rows with no tenant, as before); work
        with no space bound, the install's names only."""
        space = _callers_space(tenant_id)
        owner = _owner_space()
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, category, tenant_id, created_at, updated_at FROM secrets ORDER BY name"
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            kind, named = secret_scope(r["name"])
            if space is None:
                shown = kind == "install"
            elif named is not None:
                shown = named == space
            elif r["tenant_id"] is None:
                shown = space == owner
            else:
                shown = r["tenant_id"] == space
            if shown:
                out.append({
                    "name": r["name"],
                    "category": r["category"],
                    "tenant": r["tenant_id"] or "global",
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                })
        return out

    def sample_opens(self, limit: int = 5) -> list[tuple[str, bool]]:
        """Up to *limit* rows, each with whether it decrypts with this key;
        never a value. For a restore drill, which runs with no space bound
        and must judge every row as the row it is."""
        from cryptography.exceptions import InvalidTag

        with self._lock:
            rows = self._conn.execute(
                "SELECT name, encrypted_value, nonce FROM secrets ORDER BY name LIMIT ?",
                (int(limit),),
            ).fetchall()
        out: list[tuple[str, bool]] = []
        for r in rows:
            try:
                self._decrypt(r["encrypted_value"], r["nonce"])
                out.append((r["name"], True))
            except (InvalidTag, ValueError):  # the wrong key: that is the answer
                out.append((r["name"], False))
        return out

    def delete(self, name: str, tenant_id: str | None = None, *, every_scope: bool = False) -> bool:
        """Delete a secret. Returns True if a row was deleted.

        A name in :data:`INSTALL_SCOPED_SECRETS` is deleted in EVERY scope:
        disconnecting an account removes its tokens globally, and a tenant copy
        left behind would keep the account looking connected to any caller
        that has a tenant. *every_scope* does the same for a name the caller
        knows has no other owner (a GitHub sign-in's, Spaces S1-17's review:
        Disconnect left an encrypted copy in the scope it did not run in).
        """
        refuse_write("vault", name)
        if _enforced() and secret_scope(name)[0] != "install":
            # A space's secret: only that space's own rows, whatever
            # *every_scope* says -- the same name is another space's too.
            scopes = self._scopes_to_read(name, tenant_id)
            if scopes is None:
                return False
            with self._lock:
                marks = ",".join("?" for _ in scopes)
                cur = self._conn.execute(
                    f"DELETE FROM secrets WHERE name = ? AND COALESCE(tenant_id, '__global__') IN ({marks})",  # noqa: S608 -- placeholders only
                    (name, *[s if s is not None else "__global__" for s in scopes]),
                )
            if cur.rowcount > 0:
                logger.info("[Vault] Deleted a space's secret '%s' (%d row(s))", name, cur.rowcount)
            return cur.rowcount > 0
        if every_scope or is_install_scoped_secret(name) or _enforced():
            with self._lock:
                cur = self._conn.execute("DELETE FROM secrets WHERE name = ?", (name,))
            if cur.rowcount > 0:
                logger.info(
                    "[Vault] Deleted secret '%s' in every scope (%d row(s))", name, cur.rowcount,
                )
            return cur.rowcount > 0
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

    def close(self) -> None:
        """Close the SQLite connection. Safe to call multiple times."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    # Safe to ignore (audit O3): the handle is dropped either
                    # way, and close() on an already-closed/broken connection
                    # has no recoverable failure mode.
                    logger.debug("[Vault] close() failed", exc_info=True)
                self._conn = None


class _SpaceVault:
    """A :class:`SecretVault` for one space (``SecretVault.for_space``):
    every call names the space, so nothing depends on what is bound."""

    def __init__(self, vault: SecretVault, space_id: str) -> None:
        from kazma_core.spaces.scope import valid_space_id

        if not valid_space_id(space_id):
            raise ValueError(f"not a space id: {space_id!r}")
        self._vault = vault
        self.space_id = space_id

    def store(self, name: str, value: str, category: str = "general",
              metadata: dict[str, Any] | None = None) -> str:
        return self._vault.store(name, value, category, metadata, tenant_id=self.space_id)

    def retrieve(self, name: str) -> str | None:
        return self._vault.retrieve(name, tenant_id=self.space_id)

    def delete(self, name: str) -> bool:
        return self._vault.delete(name, tenant_id=self.space_id)

    def list_secrets(self) -> list[dict[str, Any]]:
        return self._vault.list_secrets(tenant_id=self.space_id)


#: Re-entrancy guard for the posture probe. ``multi_user_enabled()`` reads
#: ``platform.users`` through ConfigStore, and this function is called FROM
#: ConfigStore's own resolver — so without this a probe triggered by one
#: config read could trigger another. Nested probes fail closed (no rung),
#: which is the same answer an unknown posture gets.
_probing = threading.local()

#: Memoized posture. The probe reads ``platform.users`` through ConfigStore,
#: so running it per read made every vaulted config read re-enter
#: ``ConfigStore.get`` — measured at nesting depth 2 on the provider-key path,
#: which is read constantly. That is the re-entrancy class that once
#: deadlocked the whole application from one swarm approval.
#:
#: Caching a *posture* has one unsafe direction: a stale "single-tenant" after
#: an install turns multi-user would leave the ``default`` rung on, which is a
#: cross-tenant read. So the cache is asymmetric — multi-user STICKS for the
#: process (its failure mode is a secret looking missing, exactly the
#: pre-2026-09-17 behaviour, fail-closed), while single-tenant is re-probed
#: every ``_POSTURE_TTL_S``. The env switches are checked live inside
#: ``multi_user_or_production`` on every re-probe, so flipping
#: ``KAZMA_PRODUCTION`` or ``KAZMA_MULTI_USER`` takes effect within one TTL.
_POSTURE_TTL_S = 30.0
_posture_lock = threading.Lock()
_posture_cache: dict[str, float | bool] = {"allowed": False, "at": 0.0}


def reset_posture_cache() -> None:
    """Drop the memoized posture (tests, and an explicit operator reset)."""
    with _posture_lock:
        _posture_cache["allowed"] = False
        _posture_cache["at"] = 0.0


def _operator_default_rung_allowed() -> bool:
    """True only where tenant ``default`` IS the operator.

    The ladder below adds a ``default`` rung so a context-less background
    reader can see Settings-saved secrets. On a single-operator install that
    is correct — ``default`` is them. On a multi-tenant one it is a
    cross-tenant read: tenant B asks for a key, misses, and gets whatever
    tenant ``default`` has.

    That is the same leak ``tests/test_cron_tenant_context.py`` refuses to
    put in ``Vault.retrieve``'s fallback, moved one layer up, so it gets the
    same answer. ``multi_user_or_production()`` fails CLOSED (True on a probe
    error), which here means: when we cannot tell, do not add the rung.
    """
    if getattr(_probing, "active", False):
        return False

    now = time.time()
    with _posture_lock:
        allowed = bool(_posture_cache["allowed"])
        at = float(_posture_cache["at"])
        # Multi-user is sticky: once we have seen it, never re-probe back to
        # the permissive answer within this process.
        if at and not allowed:
            return False
        if at and (now - at) < _POSTURE_TTL_S:
            return True

    _probing.active = True
    try:
        from kazma_core.tenant_isolation import multi_user_or_production

        allowed = not multi_user_or_production()
    except Exception:  # noqa: BLE001
        logger.warning(
            "[Vault] posture probe failed — omitting the 'default' tenant rung"
        )
        allowed = False
    finally:
        _probing.active = False

    with _posture_lock:
        _posture_cache["allowed"] = allowed
        _posture_cache["at"] = time.time()
    return allowed


def retrieve_scoped(name: str, vault: Any | None = None) -> str | None:
    """Decrypt *name* walking current tenant → ``default`` → global.

    Returns the RAW value (no strip) or ``None``, so callers that store
    structured or whitespace-significant values are unaffected.

    Settings-saved secrets live under the operator's tenant — measured on the
    live install 2026-09-17, 34 of 67 vault rows are scoped to ``default``.
    ``retrieve()`` with no tenant sees ONLY global rows, so a background loop,
    cron job, CLI command or standalone script reads every one of them as
    missing. That shipped three times (cron 09-12, the agent turn and
    ``kazma doctor`` 09-16, connector-health/backup 09-17) before it was
    fixed here rather than at the next call site.

    The ``default`` rung is posture-gated — see
    :func:`_operator_default_rung_allowed`. ``Vault.retrieve``'s own
    tenant→global fallback is untouched.
    """
    # *vault* lets a caller pass the handle it already holds. ConfigStore
    # obtains one through its own `_try_get_vault` and must not have this
    # function quietly resolve a second (possibly different) instance behind
    # its back -- which also silently bypassed the patch point its tests use.
    if vault is None:
        vault = get_vault()
    if vault is None:
        return None

    def _try(tid: str | None) -> str | None:
        try:
            val = vault.retrieve(name, tid)
        except Exception:  # noqa: BLE001
            return None
        return val if (val is not None and str(val) != "") else None

    if _enforced():
        # One rule, the vault's own (S1-18): the install's secret for anyone,
        # a space's for work in that space. No ``default`` rung: work with no
        # space bound never reads the owner's.
        return _try(None)

    current = get_current_tenant_id()

    if current:
        # The caller knows its own scope. Vault.retrieve already falls back
        # tenant -> global in one query, so this rung covers both and the
        # posture probe never runs on the hot path.
        hit = _try(current)
        if hit is not None:
            return hit
        if current == "default":
            return None  # tried, and it covered global too
        # A caller with its OWN tenant does not get the operator's rung —
        # that is the cross-tenant read, and the posture gate below would
        # allow it on a single-operator box where it is harmless. Keep it
        # for context-less callers only, which is what the rung is for.
        return None

    # Context-less: `default` BEFORE global. Order is not cosmetic — when a
    # name exists in both scopes the tenant-scoped row must win, which is
    # what Vault.retrieve itself does and what the whole notion of scoping
    # means. Getting this backwards returns the stale global copy of a
    # credential the operator re-saved through Settings.
    #
    # The probe behind this rung reads `platform.users` through ConfigStore,
    # and this function is called FROM ConfigStore's resolver — so it is
    # memoized (see _operator_default_rung_allowed) rather than run per read.
    # Eager per-read probing measured at nesting depth 2 on the provider-key
    # path, the re-entrancy class that once deadlocked the application.
    if _operator_default_rung_allowed():
        hit = _try("default")
        if hit is not None:
            return hit
    return _try(None)


def retrieve_with_tenant_ladder(name: str) -> str:
    """:func:`retrieve_scoped`, stripped, with ``""`` for a miss."""
    val = retrieve_scoped(name)
    return str(val).strip() if val is not None else ""


# ── Singleton ──────────────────────────────────────────────────────────────

_vault: SecretVault | None = None
_vault_init_attempted = False
#: Held while the vault is built (PBKDF2, 600,000 rounds): a second caller
#: waits for the one being built instead of reading "no vault" (FA-103).
_vault_lock = threading.Lock()
#: When a build that failed for another reason than a missing key may be
#: tried again, and how long the next wait is.
_vault_retry_at = 0.0
_vault_retry_wait = 0.0
_VAULT_RETRY_FIRST_S = 5.0
_VAULT_RETRY_MAX_S = 300.0


def get_vault() -> SecretVault | None:
    """Return the shared SecretVault singleton.

    Returns None if KAZMA_VAULT_KEY is not set or invalid (vault disabled):
    that is decided once. Built under a lock (double-checked): a caller that
    arrived while the first was still deriving the key got None, and ConfigStore
    then kept the secret it was saving in plaintext (FA-103). A build that
    fails for another reason (a locked or unreadable vault.db) is tried again
    after a wait that doubles from 5 s to 5 minutes -- one transient failure
    left the process without a vault until a restart.
    """
    global _vault, _vault_init_attempted, _vault_retry_at, _vault_retry_wait
    if _vault_init_attempted:
        return _vault
    with _vault_lock:
        if _vault_init_attempted:
            return _vault
        if time.monotonic() < _vault_retry_at:
            return None
        try:
            _vault = SecretVault()
        except ValueError:
            logger.debug("[Vault] KAZMA_VAULT_KEY not set — vault disabled")
        except Exception as exc:
            _vault_retry_wait = min(max(_VAULT_RETRY_FIRST_S, _vault_retry_wait * 2), _VAULT_RETRY_MAX_S)
            _vault_retry_at = time.monotonic() + _vault_retry_wait
            logger.warning("[Vault] Init failed (%s): %s -- trying again in %.0f s",
                           type(exc).__name__, exc, _vault_retry_wait)
            return None
        _vault_init_attempted = True
        _vault_retry_at = _vault_retry_wait = 0.0
        return _vault


def reset_vault() -> None:
    """Reset the singleton (for tests). Closes the existing connection first."""
    global _vault, _vault_init_attempted, _vault_retry_at, _vault_retry_wait
    with _vault_lock:
        if _vault is not None:
            _vault.close()
        _vault = None
        _vault_init_attempted = False
        _vault_retry_at = _vault_retry_wait = 0.0


def consolidate_install_scoped_secrets() -> list[str]:
    """Run :meth:`SecretVault.consolidate_install_scoped` on the shared vault.

    Called once at server boot, before the model registry builds its first
    client. Returns the names changed; empty when the vault is disabled.
    """
    vault = get_vault()
    if vault is None:
        return []
    return vault.consolidate_install_scoped()
