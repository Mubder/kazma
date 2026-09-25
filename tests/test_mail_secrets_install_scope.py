"""Mail and calendar secrets have one copy, whoever writes them.

Every live boot logged::

    [Vault] 1 secret name(s) differ between tenant and global scope:
    email.gmail.scopes. Which value a caller gets depends on whether it has
    a tenant context ... Reconcile them.

The live vault held ``email.gmail.scopes`` globally (rewritten 2026-09-25 by
a Gmail reconnect) and under tenant ``default`` (from 2026-09-12), with
different values. ``email_manager.credentials`` has read and written mail
secrets in the global scope since 2026-08-16, but not every writer used it:
the backup token refresh (``backup/cloud_sync._write_vault``) and the agent's
secret tool called ``vault.store`` with the request's tenant, and a caller
with a tenant reads its tenant's copy first. So the fix is in the vault:
``email.*`` and ``calendar.*`` are install-scoped (``INSTALL_SCOPED_SECRETS``),
``store`` and ``delete`` enforce it for every writer, and boot reconciles the
old copies with the GLOBAL copy winning -- it is the one mail code uses.
"""

from __future__ import annotations

import asyncio
import secrets as _secrets

import pytest

from kazma_core.security import vault as vault_mod
from kazma_core.tenant_context import tenant_scope

SCOPES = "email.gmail.scopes"
LIVE_GLOBAL = "https://mail.google.com/ https://www.googleapis.com/auth/calendar"
LIVE_TENANT = "https://mail.google.com/"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "m" * 64)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "get_vault", lambda: v)
    yield v
    v.close()


def _legacy_row(v, tenant, name, value, stamp):
    """A row as a writer before 2026-09-25 left it (``store`` no longer can)."""
    ct, nonce = v._encrypt(value)
    v._conn.execute(
        "INSERT INTO secrets (id, name, encrypted_value, nonce, category, metadata, "
        "tenant_id, created_at, updated_at) VALUES (?, ?, ?, ?, 'email', '{}', ?, ?, ?)",
        (_secrets.token_hex(16), name, ct, nonce, tenant, stamp, stamp),
    )


def _every_reader_sees(v, name) -> set[str | None]:
    seen = set()
    for tenant in (None, "default", "acme"):
        with tenant_scope(tenant):
            seen.add(v.retrieve(name))
    return seen


# -- the live divergence, reproduced and reconciled ---------------------------


def test_the_live_scopes_split_is_reconciled_to_the_global_copy(vault):
    _legacy_row(vault, None, SCOPES, LIVE_GLOBAL, "2026-09-25T14:07:25+00:00")
    _legacy_row(vault, "default", SCOPES, LIVE_TENANT, "2026-09-12T16:18:08+00:00")
    # Negative control: the boot warning's condition, and the split it causes.
    assert [d["name"] for d in vault.find_divergent_duplicates()] == [SCOPES]
    assert _every_reader_sees(vault, SCOPES) == {LIVE_GLOBAL, LIVE_TENANT}

    assert vault.consolidate_install_scoped() == [SCOPES]
    assert vault.find_divergent_duplicates() == []
    assert _every_reader_sees(vault, SCOPES) == {LIVE_GLOBAL}
    assert vault.consolidate_install_scoped() == [], "idempotent"


def test_the_global_copy_wins_even_against_a_newer_stray(vault):
    """Mail code reads the global copy; a stray tenant copy is never the truth."""
    _legacy_row(vault, None, "email.gmail.client_secret", "real", "2026-08-29T18:31:07+00:00")
    _legacy_row(vault, "default", "email.gmail.client_secret", "stray", "2026-09-12T16:18:08+00:00")
    vault.consolidate_install_scoped()
    assert _every_reader_sees(vault, "email.gmail.client_secret") == {"real"}


def test_a_mail_secret_only_stored_per_tenant_is_promoted(vault):
    _legacy_row(vault, "default", "email.microsoft.refresh_token", "rt", "2026-08-04T16:57:44+00:00")
    assert vault.consolidate_install_scoped() == ["email.microsoft.refresh_token"]
    assert _every_reader_sees(vault, "email.microsoft.refresh_token") == {"rt"}


# -- no writer can make a stray copy again ------------------------------------


def test_the_vault_stores_mail_for_the_install_whatever_the_tenant(vault):
    vault.store("email.gmail.access_token", "t1", tenant_id="default")
    with tenant_scope("default"):
        vault.store("calendar.google.refresh_token", "r1")
    assert _every_reader_sees(vault, "email.gmail.access_token") == {"t1"}
    assert _every_reader_sees(vault, "calendar.google.refresh_token") == {"r1"}
    assert vault.find_divergent_duplicates() == []


def test_the_backup_token_refresh_cannot_leave_a_stray_copy(vault):
    """cloud_sync wrote refreshed tokens under the request's tenant."""
    from kazma_core.backup import cloud_sync

    _legacy_row(vault, "default", "email.gmail.access_token", "old", "2026-09-12T00:00:00+00:00")
    with tenant_scope("default"):
        cloud_sync._write_vault("email.gmail.access_token", "refreshed", category="email")
    assert _every_reader_sees(vault, "email.gmail.access_token") == {"refreshed"}


def test_the_agents_secret_tool_cannot_either(vault):
    from kazma_skills.native.secret_vault.tools import vault_store

    with tenant_scope("default"):
        reply = asyncio.run(vault_store("email.gmail.client_secret", "from-chat", "email"))
    assert "stored securely" in reply
    assert _every_reader_sees(vault, "email.gmail.client_secret") == {"from-chat"}


def test_mail_code_and_the_backup_code_read_the_same_copy(vault):
    from kazma_core.backup import cloud_sync
    from kazma_skills.native.email_manager.credentials import vault_retrieve

    _legacy_row(vault, None, "email.gmail.refresh_token", "global-rt", "2026-09-25T14:07:25+00:00")
    _legacy_row(vault, "default", "email.gmail.refresh_token", "stray-rt", "2026-09-12T16:18:08+00:00")
    vault.consolidate_install_scoped()
    with tenant_scope("default"):  # a Settings "sync now" request
        assert cloud_sync._read_vault("email.gmail.refresh_token") == "global-rt"
        assert vault_retrieve("email.gmail.refresh_token") == "global-rt"


# -- delete, and the names that stay per tenant -------------------------------


def test_disconnecting_removes_every_copy(vault):
    _legacy_row(vault, None, "email.gmail.access_token", "g", "2026-09-25T00:00:00+00:00")
    _legacy_row(vault, "default", "email.gmail.access_token", "t", "2026-09-12T00:00:00+00:00")
    assert vault.delete("email.gmail.access_token") is True
    assert _every_reader_sees(vault, "email.gmail.access_token") == {None}


def test_per_tenant_names_keep_their_scope(vault):
    """Negative control: X credentials are not the install's."""
    name = "cfg:connectors.x.api_key"
    with tenant_scope("default"):
        vault.store(name, "x-default")
    with tenant_scope("acme"):
        vault.store(name, "x-acme")
    assert vault.retrieve(name) is None
    assert vault.consolidate_install_scoped() == []
    with tenant_scope("default"):
        assert vault.delete(name) is True
    with tenant_scope("acme"):
        assert vault.retrieve(name) == "x-acme", "a per-tenant delete stays per tenant"


def test_the_membership_rule_covers_mail_and_calendar():
    for name in ("email.gmail.scopes", "email.microsoft.client_secret",
                 "email.imap.password", "calendar.google.access_token"):
        assert vault_mod.is_install_scoped_secret(name), name
    for name in ("cfg:connectors.x.api_key", "emails.archive", "calendarx.token"):
        assert not vault_mod.is_install_scoped_secret(name), name
