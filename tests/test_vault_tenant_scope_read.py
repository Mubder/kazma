"""A secret saved in the UI must be readable by code that has no tenant.

Live, 2026-09-16. The operator re-entered the DeepSeek API key in Settings ->
Providers. It encrypted and stored correctly. Then:

    Profile provider=deepseek model=deepseek-flash has no usable API key;
    using Z.AI/glm-5.3-flash which has a configured key
    ...
    HTTP 400 {"error":{"code":"1211","message":"Unknown Model, please check
    the model code."}}

The key was in the right vault, under the right name, decrypting with the
right KAZMA_VAULT_KEY. `auth.py` sets the tenant ContextVar to "default" for
a web request, so `store()` wrote `tenant_id='default'`. `retrieve()` fell
back tenant -> global and stopped, so every caller WITHOUT a tenant context
-- the CLI, cron, the agent turn that services Telegram -- read None. The
registry concluded the provider was keyless and substituted another vendor.

`kazma doctor` then reported the secret "lives in another install's vault",
which was confidently wrong and sent the operator to re-enter a key that was
already there.

Fixed at the call sites, not in the vault: `resolve_live_client` installs the
turn's tenant and `kazma_cli.main` installs the process's, exactly as the cron
scheduler was fixed on 2026-09-12. Widening `retrieve` to fall back
global -> tenant would let any context-less background task read another
tenant's credentials -- see `tests/test_cron_tenant_context.py`.
"""

from __future__ import annotations

import pytest
from kazma_core.security.vault import SecretVault
from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

# The incident above was a provider key; provider keys and mail tokens are
# install-scoped since 2026-09-25 (INSTALL_SCOPED_SECRETS, tested in
# test_provider_key_install_scope.py), so the vault no longer stores them per
# tenant. The mechanism here still guards every per-tenant secret, such as
# the X connector credentials.
NAME = "cfg:connectors.x.api_key"
SECRET = "sk-not-a-real-key-0000"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "a" * 64)
    return SecretVault(db_path=str(tmp_path / "vault.db"))


@pytest.fixture
def as_tenant():
    tokens = []

    def _set(tid):
        tokens.append(set_current_tenant_id(tid))

    yield _set
    for tok in reversed(tokens):
        reset_current_tenant_id(tok)


def test_the_llm_chokepoint_runs_as_the_turns_tenant(vault, as_tenant, monkeypatch):
    """The exact live sequence: saved in a request, read from an agent turn."""
    as_tenant("default")
    vault.store(NAME, SECRET, category="config")
    as_tenant(None)  # an agent turn starts with no tenant context

    assert vault.retrieve(NAME) is None, "the hole itself, still present"

    seen = {}
    from kazma_core.runtime import live_llm
    from kazma_core.tenant_context import get_current_tenant_id

    with live_llm._turn_tenant({"tenant_id": "default"}):
        seen["tenant"] = get_current_tenant_id()
        seen["key"] = vault.retrieve(NAME)

    assert seen["tenant"] == "default"
    assert seen["key"] == SECRET, (
        "the registry builds its client inside this block; reading None here "
        "is what substituted Z.AI and produced the 1211"
    )
    assert get_current_tenant_id() is None, "the tenant must not leak out"


def test_turn_tenant_defaults_when_state_carries_none(vault):
    from kazma_core.runtime import live_llm
    from kazma_core.tenant_context import get_current_tenant_id

    for state in ({}, None, {"tenant_id": ""}):
        with live_llm._turn_tenant(state):
            assert get_current_tenant_id() == "default"


def test_turn_tenant_never_overrides_an_ambient_one(as_tenant):
    """An HTTP request already resolved its tenant; state must not beat it."""
    from kazma_core.runtime import live_llm
    from kazma_core.tenant_context import get_current_tenant_id

    as_tenant("alice")
    with live_llm._turn_tenant({"tenant_id": "bob"}):
        assert get_current_tenant_id() == "alice"


def test_turn_tenant_resets_even_when_the_body_raises():
    from kazma_core.runtime import live_llm
    from kazma_core.tenant_context import get_current_tenant_id

    with pytest.raises(RuntimeError), live_llm._turn_tenant({"tenant_id": "x"}):
        raise RuntimeError("turn blew up")
    assert get_current_tenant_id() is None


def test_resolve_live_client_builds_the_client_inside_the_tenant():
    """Wiring guard: the fix is worthless if the block moves off get_client."""
    import inspect

    from kazma_core.runtime import live_llm

    src = inspect.getsource(live_llm.resolve_live_client)
    assert "with _turn_tenant(state):" in src
    body = src.split("with _turn_tenant(state):", 1)[1]
    assert body.lstrip().startswith("registry_client = get_model_registry().get_client")


def test_the_cli_installs_a_tenant_at_entry():
    """`kazma doctor` must see what the app sees."""
    import inspect

    from kazma_cli import main as cli_main

    src = inspect.getsource(cli_main.main)
    assert "set_current_tenant_id(" in src
    assert "KAZMA_TENANT_ID" in src, "multi-tenant operators need the override"


def test_ambient_tenant_still_wins_over_another_tenants_row(vault, as_tenant):
    """The fallback must not override a caller that HAS a tenant."""
    as_tenant("alice")
    vault.store(NAME, "alice-key")
    as_tenant("bob")
    vault.store(NAME, "bob-key")

    as_tenant("alice")
    assert vault.retrieve(NAME) == "alice-key"


def test_global_row_still_wins_over_a_tenant_row(vault, as_tenant):
    """The new path is a last resort, reached only when global has nothing."""
    vault.store(NAME, "global-key", tenant_id=None)
    as_tenant("default")
    vault.store(NAME, "tenant-key")
    as_tenant(None)

    assert vault.retrieve(NAME) == "global-key"


def test_a_tenant_scoped_secret_never_leaks_to_a_context_less_caller(
    vault, as_tenant
):
    """The property the cron fix chose to preserve. Do not widen this."""
    as_tenant("alice")
    vault.store(NAME, "alice-key")
    as_tenant(None)

    assert vault.retrieve(NAME) is None


def test_missing_name_is_still_none(vault):
    assert vault.retrieve("cfg:nothing.here") is None


def test_explicit_tenant_arg_is_not_widened(vault, as_tenant):
    """Asking for a named tenant means that tenant, not "whoever has one"."""
    as_tenant("alice")
    vault.store(NAME, "alice-key")

    assert vault.retrieve(NAME, tenant_id="bob") is None


def test_describe_secret_reports_scope_without_leaking_the_value(vault, as_tenant):
    """doctor's evidence source: facts about the row, never the row."""
    as_tenant("default")
    vault.store(NAME, SECRET)

    rows = vault.describe_secret(NAME)
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"
    assert rows[0]["decrypts"] is True
    assert SECRET not in repr(rows)
    assert vault.describe_secret("cfg:nothing.here") == []


def test_describe_secret_flags_a_row_written_under_a_different_vault_key(
    tmp_path, monkeypatch, as_tenant
):
    """The case doctor used to assert for every unreadable pointer."""
    monkeypatch.setenv("KAZMA_VAULT_KEY", "a" * 64)
    db = str(tmp_path / "vault.db")
    as_tenant("default")
    SecretVault(db_path=db).store(NAME, SECRET)

    monkeypatch.setenv("KAZMA_VAULT_KEY", "b" * 64)
    rows = SecretVault(db_path=db).describe_secret(NAME)
    assert rows and rows[0]["decrypts"] is False
