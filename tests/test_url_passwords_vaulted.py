"""A password inside any stored URL goes to the vault; an env name ending in
``_KEY`` is a secret (2026-11-15).

Reproduced that day: ``proxy.url`` and ``web.searxng_url`` saved with
``user:pw@`` stayed plaintext in the settings table -- the vault rule took a
URL password only under a name already install-scoped, because a copy under
the saving request's tenant would hide it from the background readers that
use it (AGENTS §38) -- and so did ``providers.list[].base_url`` (the nested
walk judged by key name only). Every Postgres dump and migration bundle
carried them. An MCP server's ``OPENAI_KEY`` / ``NOTION_KEY`` stayed plaintext
in kazma.yaml and the settings, and ``mask_deep``, ``/api/mcp/servers`` and
``config_read`` showed it.

Now: a URL password under a key that is not a credential's goes to
``cfg-url:<key>``, install-scoped, read back by a reader with no tenant; a
nested one too (``cfg:providers.list.<name>.base_url``, install-scoped
already); boot moves what an older build stored; and ``*_KEY`` reads as a
credential to the vault rule, the MCP rule and every masker.
"""

from __future__ import annotations

import pytest

from kazma_core import config_store as cs_mod
from kazma_core.security import vault as vault_mod

PW = "s3cret-" + "pw9"
PROXY = f"http://scraper:{PW}@proxy.example:8080"
SEARX = f"https://u:{PW}@searx.example/"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "get_vault", lambda: v)
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")  # the default rung closed: no tenant fallback
    vault_mod.reset_posture_cache()
    yield v
    vault_mod.reset_posture_cache()
    v.close()


@pytest.fixture
def store(tmp_path, vault):
    s = cs_mod.ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))
    yield s
    s.close()


def _settings_rows(store) -> str:
    """Every row as stored, as one text (what a dump or a bundle carries)."""
    return repr(store.get_all())


@pytest.mark.parametrize(("key", "value", "category"), [
    ("proxy.url", PROXY, "proxy"),
    ("web.searxng_url", SEARX, "web"),
])
def test_a_url_password_under_any_key_goes_to_the_vault(store, key, value, category):
    from kazma_core.tenant_context import tenant_scope

    with tenant_scope("default"):  # saved from a request
        store.set(key, value, category=category)
    assert store._stored_raw(key) == f"vault://cfg-url:{key}"
    assert PW not in _settings_rows(store)
    with tenant_scope(None):  # the proxy, the search chain: no tenant
        store._cache.clear()
        assert store.get(key) == value
    assert vault_mod.is_install_scoped_secret(f"cfg-url:{key}")
    assert vault_mod.is_kazma_owned_secret(f"cfg-url:{key}")  # the agent's vault tools refuse it


def test_a_password_in_a_providers_base_url_goes_to_the_vault(store):
    from kazma_core.tenant_context import tenant_scope

    providers = [{"name": "gw", "base_url": f"https://u:{PW}@gw.example/v1", "api_key": "k-" + "1" * 12},
                 {"name": "plain", "base_url": "https://api.example/v1"}]
    with tenant_scope("default"):
        store.set("providers.list", providers, category="providers")
    raw = store._stored_raw("providers.list")
    assert raw[0]["base_url"] == "vault://cfg:providers.list.gw.base_url"
    assert raw[1]["base_url"] == "https://api.example/v1"  # no password: readable
    assert PW not in _settings_rows(store)
    with tenant_scope(None):
        store._cache.clear()
        assert store.get("providers.list")[0]["base_url"] == f"https://u:{PW}@gw.example/v1"


def test_boot_moves_what_an_older_build_stored(store):
    store._write_db_value("proxy.url", PROXY, category="proxy")
    store._write_db_value("providers.list", [{"name": "gw", "base_url": f"https://u:{PW}@gw.example/v1"}],
                          category="providers")
    assert PW in _settings_rows(store)
    moved, left = store.move_plaintext_secrets()
    assert {"proxy.url", "providers.list"} <= set(moved) and not left
    assert PW not in _settings_rows(store)
    store._cache.clear()
    assert store.get("proxy.url") == PROXY


def test_a_masked_url_posted_back_keeps_the_stored_one(store):
    store.set("proxy.url", PROXY, category="proxy")
    store.set("proxy.url", f"http://scraper:****@proxy.example:8080", category="proxy")
    store._cache.clear()
    assert store.get("proxy.url") == PROXY


def test_deleting_the_setting_removes_its_vault_copy(store, vault):
    store.set("proxy.url", PROXY, category="proxy")
    assert vault.retrieve("cfg-url:proxy.url") == PROXY
    assert store.delete("proxy.url")
    assert vault.retrieve("cfg-url:proxy.url") is None


def test_a_crawl_journal_keeps_the_urls_it_recorded(store):
    """Kazma's records of pages it read are read back raw: a pointer there
    would change the record (``durable_state.refuse_vaulted`` would refuse it)."""
    record = {"pages": [{"url": f"https://u:{PW}@site.example/a"}]}
    assert cs_mod.vaulted_paths("acquisition.crawl.abc", record) == []
    store.set("acquisition.crawl.abc", record, category="acquisition_crawl")
    assert store._stored_raw("acquisition.crawl.abc") == record


def test_control_the_old_rule_left_these_in_plaintext():
    """The rule before 2026-11-15: a URL password moved only under a name
    already install-scoped, and the nested walk judged by key name only."""
    def old_rule(key, value):
        if cs_mod.is_sensitive_config_key(key):
            return True
        if not cs_mod.url_has_credentials(value):
            return False
        return vault_mod.is_install_scoped_secret(f"cfg:{key}")

    assert not old_rule("proxy.url", PROXY)
    assert not old_rule("web.searxng_url", SEARX)
    assert not cs_mod.is_sensitive_config_key("providers.list.gw.base_url")
    assert cs_mod._is_sensitive_config_value("proxy.url", PROXY)
    assert cs_mod._is_sensitive_config_value("providers.list.gw.base_url", f"https://u:{PW}@gw/v1")


# ── *_KEY names ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["OPENAI_KEY", "NOTION_KEY", "STRIPE_SECRET_KEY", "MY_SERVICE_KEY"])
def test_an_env_name_ending_in_key_is_a_secret_everywhere(name):
    from kazma_core.mcp.secrets import _secret_name
    from kazma_core.security.config_mask import env_name_is_secret, key_is_secret, mask_deep

    assert env_name_is_secret(name) and key_is_secret(name)
    assert cs_mod.is_sensitive_config_key(f"mcp.servers.notion.env.{name}")
    assert _secret_name(name) and _secret_name(name.lower())
    value = "plain-" + "value-" + "7" * 10
    assert value not in str(mask_deep({"env": {name: value}}))


@pytest.mark.parametrize("name", ["public_key", "sort_key", "primary_key", "tenant_key"])
def test_a_settings_key_ending_in_key_is_not_taken_for_one(name):
    """Upper case only: the push key's public half and ordinary settings stay readable."""
    from kazma_core.security.config_mask import env_name_is_secret, mask_deep

    assert not env_name_is_secret(name)
    assert mask_deep({name: "visible"}) == {name: "visible"}


def test_an_mcp_servers_key_is_vaulted_and_masked(monkeypatch):
    from kazma_core.mcp import secrets as mcp_secrets

    stored: dict[str, str] = {}

    class _Vault:
        def retrieve(self, name, tenant_id=None):
            return stored.get(name)

        def store(self, name, value, category="general", tenant_id=None):
            stored[name] = value

        def store_install_scoped(self, name, value, category="general"):
            stored[name] = value
            return 1

    monkeypatch.setattr(vault_mod, "get_vault", lambda: _Vault())
    value = "notion-" + "secret-" + "8" * 10
    token = "gh" + "p_" + "Ab3x" * 8
    server = {"name": "notion", "command": ["npx", "server", "--header", f"Authorization: Bearer {token}"],
              "env": {"NOTION_KEY": value, "ODD_NAME": token}, "headers": {"X-Custom": token}}
    shown = mcp_secrets.masked(server)
    assert value not in str(shown) and token not in str(shown)
    out = mcp_secrets.externalize(server)
    assert value not in str(out) and token not in str(out)
    assert out["env"]["NOTION_KEY"].startswith("vault://")
    assert mcp_secrets.resolve(out)["env"]["NOTION_KEY"] == value


def test_control_the_old_mcp_rule_did_not_read_a_bare_key_ending():
    from kazma_core.mcp.secrets import _EXTRA_SECRET_NAMES

    def old(name):
        lowered = name.strip().lower().replace("-", "_")
        return lowered in _EXTRA_SECRET_NAMES or cs_mod.is_sensitive_config_key(lowered)

    assert not old("OPENAI_KEY") and not old("NOTION_KEY")
