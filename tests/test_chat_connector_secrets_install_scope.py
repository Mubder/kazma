"""A chat app's token saved in Settings is the token the gateway boots with.

Live 2026-09-30, after a reload: Slack answered ``invalid_auth`` on every
Socket Mode reconnect, and boot warned ``1 secret name(s) differ between
tenant and global scope: cfg:connectors.slack.app_token``. The operator had
saved a new app-level token in Settings, which ``vault.store`` put under the
REQUEST's tenant; the Settings save then rebuilt the adapter inside that
request, so it read the new copy and connected. The gateway runs one adapter
per platform for the whole install and builds it at boot with no tenant
bound, so every restart read the stale GLOBAL copy -- a revoked token. The
owner had hit it earlier the same day ("reloaded the server and tested got
error") and it looked like a bad paste.

Fixed at the storage, like provider keys and mail (AGENTS §38): the chat
platforms' credentials are in ``INSTALL_SCOPED_SECRETS``; a save writes the
install's copy, and boot brings a split pair to the NEWEST save before any
adapter reads it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kazma_core.security import vault as vault_mod
from kazma_core.tenant_context import tenant_scope

REPO = Path(__file__).resolve().parents[1]
APP_TOKEN = "cfg:connectors.slack.app_token"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "get_vault", lambda: v)
    yield v
    v.close()


@pytest.fixture
def production(monkeypatch):
    """The live posture: the ``default`` rung is closed to context-less reads."""
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    vault_mod.reset_posture_cache()
    yield
    vault_mod.reset_posture_cache()


def _row(v, tenant, name, value, stamp):
    """A row as the old writers left it (``store`` no longer writes this shape)."""
    import secrets as _secrets

    ct, nonce = v._encrypt(value)
    v._conn.execute(
        "INSERT INTO secrets (id, name, encrypted_value, nonce, category, metadata, "
        "tenant_id, created_at, updated_at) VALUES (?, ?, ?, ?, 'config', '{}', ?, ?, ?)",
        (_secrets.token_hex(16), name, ct, nonce, tenant, stamp, stamp),
    )


def test_the_live_split_boots_with_the_saved_token(vault, production) -> None:
    """The live vault: an old global copy, the operator's newer save under
    ``default``. Consolidation (run at boot before the gateway starts) makes
    the no-tenant read return the save."""
    _row(vault, None, APP_TOKEN, "xapp-revoked", "2026-09-23T10:00:00+00:00")
    _row(vault, "default", APP_TOKEN, "xapp-saved", "2026-09-30T12:21:21+00:00")

    # Negative control: the incident -- a boot-time read gets the revoked
    # token, while the Settings request's own read got the new one.
    assert vault.retrieve(APP_TOKEN) == "xapp-revoked"
    with tenant_scope("default"):
        assert vault.retrieve(APP_TOKEN) == "xapp-saved"

    assert vault.consolidate_install_scoped() == [APP_TOKEN]
    assert vault.consolidate_install_scoped() == [], "must be idempotent"

    assert vault.retrieve(APP_TOKEN) == "xapp-saved", "boot reads the operator's save"
    with tenant_scope("default"):
        assert vault.retrieve(APP_TOKEN) == "xapp-saved"


def test_a_settings_save_stores_the_token_for_the_install(vault, production) -> None:
    with tenant_scope("default"):  # a Settings request
        vault.store("cfg:connectors.slack.token", "xoxb-new")
        vault.store("cfg:connectors.discord.token", "discord-new")
    assert vault.retrieve("cfg:connectors.slack.token") == "xoxb-new"
    assert vault.retrieve("cfg:connectors.discord.token") == "discord-new"


@pytest.mark.parametrize("name", [
    "cfg:connectors.slack.token",
    "cfg:connectors.slack.app_token",
    "cfg:connectors.discord.token",
    "cfg:connectors.telegram.token",
    "cfg:connectors.telegram.webhook_secret",
])
def test_every_gateway_credential_is_install_scoped(name: str) -> None:
    assert vault_mod.is_install_scoped_secret(name)


def test_x_credentials_stay_per_tenant() -> None:
    """X is an account the agent posts AS -- an authorization question, so it
    stays per tenant (see the note above ``INSTALL_SCOPED_SECRETS``)."""
    for name in ("cfg:connectors.x.api_key", "cfg:connectors.x.access_token"):
        assert not vault_mod.is_install_scoped_secret(name)


_BOOT_READ = re.compile(r'config_store\.get\(\s*"(connectors\.[a-z_]+\.[a-z_]+)"')


def test_every_credential_the_boot_reads_is_install_scoped() -> None:
    """The adapters are built in app.py at boot, with no tenant bound. Every
    sensitive ``connectors.*`` key read there must be install-scoped, or the
    next platform added boots from a stale copy the same way."""
    from kazma_core.config_store import is_sensitive_config_key

    src = (REPO / "kazma-ui" / "kazma_ui" / "app.py").read_text(encoding="utf-8")
    sensitive = sorted(
        k for k in set(_BOOT_READ.findall(src)) if is_sensitive_config_key(k)
    )
    assert "connectors.slack.app_token" in sensitive, f"the scan is blind: {sensitive}"
    missing = [k for k in sensitive if not vault_mod.is_install_scoped_secret("cfg:" + k)]
    assert not missing, (
        "credentials the gateway reads at boot but the vault keeps per tenant "
        f"(a Settings save would not reach the next boot): {missing}"
    )


def test_the_boot_scan_would_flag_a_new_platform() -> None:
    """Negative control: a new platform's token read at boot, not yet declared,
    is sensitive and not install-scoped -- exactly what the gate refuses."""
    from kazma_core.config_store import is_sensitive_config_key

    line = 'tok = self.config_store.get("connectors.whatsapp.token", "")'
    (key,) = _BOOT_READ.findall(line)
    assert is_sensitive_config_key(key)
    assert not vault_mod.is_install_scoped_secret("cfg:" + key)
