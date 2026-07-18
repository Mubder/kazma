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
