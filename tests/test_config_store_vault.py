"""ConfigStore + vault: sensitive keys encrypted when KAZMA_VAULT_KEY is set."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kazma_core.config_store import (
    ConfigStore,
    is_sensitive_config_key,
    is_vault_ref,
)
from kazma_core.security import vault as vault_mod


@pytest.fixture
def vault_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Enable vault with a temp DB."""
    key = "unit-test-vault-key-32bytes-long!!"
    monkeypatch.setenv("KAZMA_VAULT_KEY", key)
    vault_mod.reset_vault()
    db = tmp_path / "vault.db"
    v = vault_mod.SecretVault(db_path=str(db))
    vault_mod._vault = v
    vault_mod._vault_init_attempted = True
    yield v
    vault_mod.reset_vault()
    monkeypatch.delenv("KAZMA_VAULT_KEY", raising=False)


def test_is_sensitive_config_key():
    assert is_sensitive_config_key("llm.api_key") is True
    assert is_sensitive_config_key("connectors.telegram.token") is True
    assert is_sensitive_config_key("agent.language") is False
    assert is_sensitive_config_key("token_count") is False  # not a last-segment secret


def test_sensitive_set_stores_vault_ref(vault_env, tmp_path: Path):
    store = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "missing.yaml"))
    try:
        store.set("llm.api_key", "sk-test-secret-value", category="llm")
        # Direct DB read should see vault pointer, not plaintext
        with store._lock:
            row = store._get_conn().execute(
                "SELECT value FROM settings WHERE key = ?", ("llm.api_key",)
            ).fetchone()
        raw = __import__("json").loads(row["value"])
        assert is_vault_ref(raw)
        assert "sk-test" not in raw
        # get() returns plaintext
        assert store.get("llm.api_key") == "sk-test-secret-value"
        # Vault has the secret
        assert vault_env.retrieve("cfg:llm.api_key") == "sk-test-secret-value"
    finally:
        store.close()


def test_masked_placeholder_does_not_overwrite(vault_env, tmp_path: Path):
    store = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "missing.yaml"))
    try:
        store.set("llm.api_key", "sk-real-key-9999", category="llm")
        store.set("llm.api_key", "****9999", category="llm")  # UI re-save
        assert store.get("llm.api_key") == "sk-real-key-9999"
    finally:
        store.close()


def test_plaintext_fallback_without_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("KAZMA_VAULT_KEY", raising=False)
    vault_mod.reset_vault()
    store = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "missing.yaml"))
    try:
        store.set("llm.api_key", "sk-plain", category="llm")
        with store._lock:
            row = store._get_conn().execute(
                "SELECT value FROM settings WHERE key = ?", ("llm.api_key",)
            ).fetchone()
        raw = __import__("json").loads(row["value"])
        assert raw == "sk-plain"
        assert store.get("llm.api_key") == "sk-plain"
    finally:
        store.close()


def test_retrieve_prefers_newest_duplicate_row(vault_env):
    """A rotated credential must win over a stale duplicate row.

    Incident 2026-08-16: an old OAuth client secret kept shadowing the
    rotated one and every token refresh failed with ``invalid_client``.
    Legacy vault DBs (pre unique-index) can hold duplicate rows for the
    same name + scope; retrieve() must return the newest.
    """
    v = vault_env
    v.store("email.gmail.client_secret", "OLD-SECRET", category="email")
    with v._lock:
        # Legacy-shaped DB: drop the unique index, then add a newer dup
        v._conn.execute("DROP INDEX IF EXISTS idx_secrets_name_tenant")
        ct, nonce = v._encrypt("NEW-SECRET")
        v._conn.execute(
            "INSERT INTO secrets (id, name, encrypted_value, nonce, category, metadata, tenant_id, created_at, updated_at)"
            " VALUES ('dup1', 'email.gmail.client_secret', ?, ?, 'email', '{}', NULL, '', '')",
            (ct, nonce),
        )
        v._conn.commit()
    assert v.retrieve("email.gmail.client_secret") == "NEW-SECRET"


def test_retrieve_tenant_isolation(vault_env):
    v = vault_env
    v.store("email.gmail.client_secret", "GLOBAL", category="email")
    v.store("email.gmail.client_secret", "TENANT", category="email", tenant_id="default")
    # Global reads must not leak the tenant-scoped row, and vice versa
    assert v.retrieve("email.gmail.client_secret") == "GLOBAL"
    assert v.retrieve("email.gmail.client_secret", tenant_id="default") == "TENANT"


def test_email_creds_vault_store_forces_global_scope(vault_env):
    """Email/OAuth creds are installation-level — a tenant-scoped request
    must still write them to the global scope (the scope background paths
    like the backup token refresh read from)."""
    from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id
    from kazma_skills.native.email_manager import credentials as creds_mod

    token = set_current_tenant_id("default")
    try:
        creds_mod.vault_store("email.gmail.client_secret", "CURRENT", category="email")
    finally:
        reset_current_tenant_id(token)

    # The background (tenant-less) read finds it — no stale-shadow split
    assert vault_env.retrieve("email.gmail.client_secret") == "CURRENT"
    assert creds_mod.vault_retrieve("email.gmail.client_secret") == "CURRENT"


def test_lazy_migrate_plaintext_on_get(vault_env, tmp_path: Path):
    """Legacy plaintext rows are encrypted on first read when vault is on."""
    store = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "missing.yaml"))
    try:
        # Bypass prepare path: write plaintext directly
        store._write_db_value("llm.api_key", "sk-legacy", category="llm")
        assert store.get("llm.api_key") == "sk-legacy"
        with store._lock:
            row = store._get_conn().execute(
                "SELECT value FROM settings WHERE key = ?", ("llm.api_key",)
            ).fetchone()
        raw = __import__("json").loads(row["value"])
        assert is_vault_ref(raw)
    finally:
        store.close()
