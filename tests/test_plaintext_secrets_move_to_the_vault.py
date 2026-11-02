"""A secret stored as plaintext becomes a vault pointer (FA-271, FA-206).

The 2026-11 functional audit: the GitHub App private key sat as plaintext in
the settings table beside its vault copy, so in every Postgres dump, restic
snapshot and offsite copy. The lazy migration wrote the row's pointer only
when the vault's value CHANGED; the key had reached the vault before its
pointer did, so every later read found the vault "unchanged" and left the row.
A secret nothing reads was never migrated at all. And the S3 access key id was
not named as a secret, so it was stored, exported and backed up in the clear.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kazma_core.config_store import ConfigStore, _store_config_secret, is_vault_ref
from kazma_core.security import vault as vault_mod

KEY = "connectors.github.app_private_key"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEdemo-not-a-real-key\n-----END RSA PRIVATE KEY-----"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "unit-test-vault-key-32bytes-long!!")
    vault_mod.reset_vault()
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "_vault", v)
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    yield v
    vault_mod.reset_vault()


@pytest.fixture
def store(tmp_path: Path, vault, monkeypatch):
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")  # these cases plant SQLite rows
    s = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))
    yield s
    s.close()


def _plant_plaintext(store: ConfigStore, key: str, value: str) -> None:
    """A row as the old Postgres branch left it: plaintext, the vault already holding it."""
    with store._lock:
        conn = store._get_conn()
        conn.execute("INSERT OR REPLACE INTO settings (key, value, category, updated_at) VALUES (?, ?, ?, ?)",
                     (key, json.dumps(value), "connectors", "2026-07-29T00:00:00+00:00"))
        conn.commit()
        store._invalidate_key(key)


def _raw(store: ConfigStore, key: str):
    with store._lock:
        row = store._get_conn().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"])


def test_a_read_moves_a_row_whose_secret_the_vault_already_holds(store, vault):
    vault.store_install_scoped(f"cfg:{KEY}", PEM, category="config") if hasattr(vault, "store_install_scoped") \
        else vault.store(f"cfg:{KEY}", PEM)
    _plant_plaintext(store, KEY, PEM)
    # The stuck state: the vault holds this exact value, so the old rule
    # (write the pointer only when the vault changed) never wrote it.
    assert _store_config_secret(vault, f"cfg:{KEY}", PEM) is False

    assert store.get(KEY) == PEM
    assert is_vault_ref(_raw(store, KEY))


def test_boot_moves_a_secret_nothing_reads(store):
    _plant_plaintext(store, KEY, PEM)
    _plant_plaintext(store, "backups.offsite.s3.access_key", "AKIADEMONOTREAL")
    moved, left = store.move_plaintext_secrets()
    assert set(moved) == {KEY, "backups.offsite.s3.access_key"} and left == []
    assert is_vault_ref(_raw(store, KEY)) and is_vault_ref(_raw(store, "backups.offsite.s3.access_key"))
    assert store.get(KEY) == PEM  # the read still returns the key
    assert store.get("backups.offsite.s3.access_key") == "AKIADEMONOTREAL"


def test_the_export_carries_references_not_the_key(store):
    store.set("backups.offsite.s3.access_key", "AKIADEMONOTREAL", category="backups")
    assert "AKIADEMONOTREAL" not in store.export_yaml()


def test_offsite_credentials_are_install_scoped():
    """The backup sync reads them with no tenant bound."""
    assert vault_mod.is_install_scoped_secret("cfg:backups.offsite.s3.access_key")


@pytest.mark.postgres
def test_boot_moves_a_plaintext_row_on_postgres(vault, monkeypatch):
    """The same on the live install's backend: a plaintext row in
    ``kazma_settings`` becomes a pointer, and the read still returns the key."""
    import os

    if not os.environ.get("KAZMA_DATABASE_URL") or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (scripts/postgres_suite.py)")
    from kazma_core.config_store import get_config_store, reset_config_store
    from kazma_core.db.postgres_pool import get_postgres_pool

    monkeypatch.setenv("KAZMA_DB_BACKEND", "postgres")
    reset_config_store()
    store = get_config_store()
    pool = get_postgres_pool()
    key = KEY  # a throwaway database: the real name, removed after
    pool.execute("INSERT INTO kazma_settings (key, value, category) VALUES (%s, %s, 'connectors') "
                 "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, json.dumps(PEM)))
    try:
        store._invalidate_key(key)
        moved, _left = store.move_plaintext_secrets()
        assert key in moved
        raw = pool.execute_one("SELECT value FROM kazma_settings WHERE key = %s", (key,))["value"]
        assert is_vault_ref(json.loads(raw))
        assert store.get(key) == PEM
    finally:
        pool.execute("DELETE FROM kazma_settings WHERE key = %s", (key,))
        reset_config_store()
