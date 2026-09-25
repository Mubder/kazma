"""A provider key saved in Settings must be readable by code with no tenant.

Live, every boot from 2026-09-16 to 2026-09-25 logged::

    Profile provider=deepseek model=deepseek-flash has no usable API key;
    using Z.AI/glm-5.3-flash which has a configured key

and built the agent on Z.AI. The DeepSeek key was in the vault the whole
time -- under tenant ``default``, because Settings saves through a request and
``vault.store`` takes the tenant from the request. ConfigStore has no tenant
dimension; the model registry is one per process and reads its keys with no
tenant bound. ``retrieve_scoped``'s ``default`` rung could have bridged that,
but it is closed when ``KAZMA_PRODUCTION=1`` -- deliberately, since an OIDC
install can have other tenants -- and the live install sets it. Chat turns
escaped only because ``resolve_live_client`` binds ``default`` per call.

Fixed at the storage: provider keys (``INSTALL_SCOPED_CONFIG_SECRETS``) are
written at install scope, and boot brings keys saved the old way up to it
before the registry builds a client. The posture gate is untouched.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kazma_core.security import vault as vault_mod
from kazma_core.tenant_context import tenant_scope

REPO_ROOT = Path(__file__).resolve().parents[1]
NAME = "cfg:providers.list.deepseek.api_key"
DEEPSEEK_URL = "https://api.deepseek.com/v1"
ZAI_URL = "https://api.z.ai/api/paas/v4"


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


@pytest.fixture
def store(tmp_path, vault):
    from kazma_core.config_store import ConfigStore

    cs = ConfigStore(
        db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"),
    )
    yield cs
    cs.close()


@pytest.fixture
def pages(monkeypatch):
    """Capture ops alerts: a substitution in these tests must not page anyone."""
    sent: list[str] = []

    def _alert(key, title, detail="", **_):
        sent.append(key)
        return True

    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", _alert)
    return sent


def _store_as(v, tenant, name, value):
    with tenant_scope(tenant):
        v.store(name, value)


def _registry(store):
    from kazma_core.model_registry import ModelRegistry

    return ModelRegistry(store)


def _providers(registry, deepseek_key):
    registry.upsert_provider({
        "name": "deepseek", "base_url": DEEPSEEK_URL, "api_key": deepseek_key,
        "models": ["deepseek-flash"], "enabled": True,
    })
    registry.upsert_provider({
        "name": "zai", "base_url": ZAI_URL, "api_key": "zai-key",
        "models": ["glm-5.3-flash"], "enabled": True,
    })
    registry.set_active_provider("deepseek", base_url=DEEPSEEK_URL, model="deepseek-flash")


# -- the incident, and the migration that ends it -------------------------


def test_a_key_saved_the_old_way_is_consolidated_before_boot(
    vault, store, production, pages,
):
    """The live vault: the key under tenant 'default' only, the setting a pointer."""
    registry = _registry(store)
    _providers(registry, f"vault://{NAME}")
    _store_as(vault, "default", NAME, "sk-deepseek")

    # Negative control: the incident, reproduced -- a boot-time build with no
    # tenant bound cannot see the key and substitutes Z.AI.
    before = registry.get_client()
    assert before.config.base_url == ZAI_URL

    assert vault.consolidate_install_scoped() == [NAME]
    assert vault.consolidate_install_scoped() == [], "must be idempotent"

    registry._clients.clear()
    after = registry.get_client()
    assert after.config.base_url == DEEPSEEK_URL
    assert after.config.api_key == "sk-deepseek"


def test_a_settings_save_now_stores_the_key_for_the_install(vault, store, production, pages):
    registry = _registry(store)
    with tenant_scope("default"):  # a Settings request
        _providers(registry, "sk-new")

    assert vault.retrieve(NAME) == "sk-new", "no tenant bound: the install's row"
    with tenant_scope("acme"):
        assert vault.retrieve(NAME) == "sk-new"
    assert registry.get_client().config.base_url == DEEPSEEK_URL
    assert pages == [], "nothing fell back, so nothing to announce"


def test_a_resave_rewrites_the_old_tenant_copy(vault, store, production, pages):
    """A copy left under the tenant would shadow the new key for chat turns."""
    _store_as(vault, "default", NAME, "sk-old")
    registry = _registry(store)
    with tenant_scope("default"):
        _providers(registry, "sk-new")

    with tenant_scope("default"):
        assert vault.retrieve(NAME) == "sk-new"
    assert vault.retrieve(NAME) == "sk-new"


def test_other_config_secrets_keep_the_saving_tenant(vault, store, production):
    """Connector credentials are not the install's; their isolation is unchanged."""
    other = "cfg:connectors.x.credentials.password"
    with tenant_scope("default"):
        store.set("connectors.x.credentials", {"password": "pw"})

    assert vault.retrieve(other) is None
    assert vault_mod.retrieve_scoped(other) is None, "the posture gate still holds"
    with tenant_scope("default"):
        assert vault.retrieve(other) == "pw"

    _store_as(vault, "default", "cfg:connectors.x.api_key", "sk-x")
    assert vault.consolidate_install_scoped() == []
    assert vault.retrieve("cfg:connectors.x.api_key") is None


# -- the vault operations themselves --------------------------------------


def test_store_install_scoped_syncs_every_copy_and_skips_an_unchanged_value(vault):
    _store_as(vault, "default", NAME, "old")
    _store_as(vault, "acme", NAME, "older")

    assert vault.store_install_scoped(NAME, "new") == 3  # global + two tenants
    for tenant in (None, "default", "acme"):
        with tenant_scope(tenant):
            assert vault.retrieve(NAME) == "new"
    assert vault.store_install_scoped(NAME, "new") == 0


def _age(vault, tenant, stamp):
    vault._conn.execute(
        "UPDATE secrets SET updated_at = ? WHERE name = ? AND "
        "COALESCE(tenant_id, '__global__') = COALESCE(?, '__global__')",
        (stamp, NAME, tenant),
    )


@pytest.mark.parametrize("newer, value", [("default", "tenant-save"), (None, "global-save")])
def test_consolidation_keeps_the_newest_save(vault, newer, value):
    _store_as(vault, None, NAME, "global-save")
    _store_as(vault, "default", NAME, "tenant-save")
    older = "default" if newer is None else None
    _age(vault, older, "2026-09-01T00:00:00+00:00")
    _age(vault, newer, "2026-09-20T00:00:00+00:00")

    assert vault.consolidate_install_scoped() == [NAME]
    for tenant in (None, "default"):
        with tenant_scope(tenant):
            assert vault.retrieve(NAME) == value


def test_an_undecryptable_newest_copy_is_left_alone(vault, caplog):
    _store_as(vault, "default", NAME, "sk-real")
    vault._conn.execute(
        "INSERT INTO secrets (id, name, encrypted_value, nonce, category, metadata, "
        "tenant_id, created_at, updated_at) VALUES "
        "('x', ?, ?, ?, 'config', '{}', 'acme', '2099-01-01', '2099-01-01')",
        (NAME, b"not-a-ciphertext", b"0" * 12),
    )
    assert vault.consolidate_install_scoped() == []
    assert "does not decrypt" in caplog.text
    assert vault.retrieve(NAME) is None, "nothing was guessed into the global row"


def test_a_diagnostic_cannot_write_install_scope(vault):
    from kazma_core.diagnostic_scope import DiagnosticWriteRefused, read_only_diagnostic

    with read_only_diagnostic("doctor"), pytest.raises(DiagnosticWriteRefused):
        vault.store_install_scoped(NAME, "x")


def test_the_membership_rule():
    assert vault_mod.is_install_scoped_secret(NAME)
    assert vault_mod.is_install_scoped_secret("cfg:providers.list.zai.api_key")
    assert vault_mod.is_install_scoped_secret("cfg:llm.api_key")
    assert not vault_mod.is_install_scoped_secret("cfg:llm.api_key_backup")
    assert not vault_mod.is_install_scoped_secret("cfg:connectors.x.api_key")
    assert not vault_mod.is_install_scoped_secret("providers.list.deepseek.api_key")


# -- wiring: boot consolidates before the registry builds a client --------


def _calls(func: ast.FunctionDef) -> list[str]:
    found: list[tuple[int, int, str]] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            found.append((node.lineno, node.col_offset, name))
    return [name for _, _, name in sorted(found)]


def _consolidates_before_the_registry(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "_bootstrap_services":
            calls = _calls(node)
            if "consolidate_install_scoped_secrets" not in calls:
                return False
            if "initialize_model_registry" not in calls:
                return False
            return calls.index("consolidate_install_scoped_secrets") < calls.index(
                "initialize_model_registry"
            )
    return False


def test_boot_consolidates_before_the_registry_is_built():
    source = (REPO_ROOT / "kazma-ui" / "kazma_ui" / "app.py").read_text(encoding="utf-8")
    assert _consolidates_before_the_registry(source)


def test_the_boot_order_check_catches_a_late_or_missing_call():
    """Negative control (§28)."""
    late = (
        "def _bootstrap_services(self):\n"
        "    self.registry = initialize_model_registry(self.config_store)\n"
        "    consolidate_install_scoped_secrets()\n"
    )
    missing = (
        "def _bootstrap_services(self):\n"
        "    self.registry = initialize_model_registry(self.config_store)\n"
    )
    good = (
        "def _bootstrap_services(self):\n"
        "    consolidate_install_scoped_secrets()\n"
        "    self.registry = initialize_model_registry(self.config_store)\n"
    )
    assert not _consolidates_before_the_registry(late)
    assert not _consolidates_before_the_registry(missing)
    assert _consolidates_before_the_registry(good)
