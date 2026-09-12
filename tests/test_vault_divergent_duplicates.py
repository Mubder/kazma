"""The same secret under two scopes, holding two different values.

`Vault.retrieve` tries the tenant scope, then the global one, and returns the
first hit. That is correct isolation. What it means in practice is that when a
name exists under BOTH scopes with DIFFERENT values, the credential a caller
gets depends on whether it happens to have a tenant context — so the same
service can work in chat and fail in a scheduled task, with nothing in either
log saying why.

Found on a live install 2026-09-12, five of them:

    cfg:connectors.github.oauth_token   global 07-18 vs default 07-28
    email.gmail.client_secret           default 08-04 vs global 08-29
    email.gmail.scopes                  default 08-16 vs global 09-12
    email.microsoft.access_token        default 08-16 vs global 08-29
    email.microsoft.refresh_token       default 08-16 vs global 08-29

`email.gmail.client_secret` is the same key as the 2026-08-16 `invalid_client`
incident, and the docstring written after that incident claimed "the most
recently written row wins — a stale row must never shadow a newer value". It
never did across scopes and cannot: handing a tenant a global value because the
global one is newer defeats the isolation the tenant argument exists for. So
the claim is corrected and the real hazard is detected instead.
"""

from __future__ import annotations

import logging

import pytest
from kazma_core.security.vault import SecretVault
from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
    return SecretVault(db_path=str(tmp_path / "v.db"))


def test_a_divergent_duplicate_is_reported(vault):
    vault.store("svc.token", "OLD-VALUE", tenant_id="default")
    vault.store("svc.token", "NEW-VALUE", tenant_id=None)

    found = vault.find_divergent_duplicates()
    assert [d["name"] for d in found] == ["svc.token"]
    assert {s["tenant_id"] for s in found[0]["scopes"]} == {"default", None}


def test_an_agreeing_duplicate_is_not_reported(vault):
    """Two copies of the same value are untidy, not dangerous. Reporting them
    would bury the five that matter under the fifty that do not."""
    vault.store("svc.token", "SAME", tenant_id="default")
    vault.store("svc.token", "SAME", tenant_id=None)
    assert vault.find_divergent_duplicates() == []


def test_a_single_scope_is_not_a_duplicate(vault):
    vault.store("only.global", "v", tenant_id=None)
    vault.store("only.tenant", "v", tenant_id="default")
    assert vault.find_divergent_duplicates() == []


def test_the_report_never_contains_the_secret(vault):
    """It is meant to be safe to log, so it carries names and timestamps and
    compares digests. A diagnostic that prints credentials is a worse bug than
    the one it diagnoses."""
    vault.store("svc.token", "SUPER-SECRET-A", tenant_id="default")
    vault.store("svc.token", "SUPER-SECRET-B", tenant_id=None)

    blob = repr(vault.find_divergent_duplicates())
    assert "SUPER-SECRET-A" not in blob
    assert "SUPER-SECRET-B" not in blob


def test_the_warning_names_every_offender(vault, caplog):
    vault.store("a.token", "1", tenant_id="default")
    vault.store("a.token", "2", tenant_id=None)
    vault.store("b.token", "1", tenant_id="default")
    vault.store("b.token", "2", tenant_id=None)

    with caplog.at_level(logging.WARNING):
        n = vault.warn_on_divergent_duplicates()

    assert n == 2
    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert "a.token" in msg and "b.token" in msg
    assert "background task" in msg, "say what it costs, not just that it exists"


def test_a_clean_vault_says_nothing(vault, caplog):
    vault.store("svc.token", "v", tenant_id="default")
    with caplog.at_level(logging.WARNING):
        assert vault.warn_on_divergent_duplicates() == 0
    assert caplog.records == []


# ── the behaviour the docstring now describes ───────────────────────────────


def test_scope_beats_age(vault):
    """The corrected claim, pinned. A tenant-scoped row wins even when the
    global row is newer -- otherwise a caller with its own credential could be
    handed somebody else's."""
    vault.store("svc.token", "TENANT-OLDER", tenant_id="default")
    vault.store("svc.token", "GLOBAL-NEWER", tenant_id=None)

    token = set_current_tenant_id("default")
    try:
        assert vault.retrieve("svc.token") == "TENANT-OLDER"
    finally:
        reset_current_tenant_id(token)

    assert vault.retrieve("svc.token") == "GLOBAL-NEWER"


def test_the_docstring_no_longer_claims_newest_wins_across_scopes():
    """The old wording sent a reader looking for a guarantee that was not
    implemented, which is how this survived since 2026-08-16."""
    import inspect

    doc = inspect.getdoc(SecretVault.retrieve) or ""
    assert "Scope wins over age" in doc
    assert "find_divergent_duplicates" in doc
