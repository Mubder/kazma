"""A password in a space's own setting is that space's secret.

The settings store moves a URL's password out of a row into the vault. For a
space's own setting (``tenant.<id>.<key>``) it goes under the space's name
(``cfg:tenant.<id>.<key>``): sealed with the space's key, read in that space
alone, and erased, exported and counted with it. A name in the other
spelling (``cfg-url:tenant.<id>.``) is read as the same space's wherever it
is found, never the install's; ``vault.space_name_prefixes`` is the one list
of names that carry a space.

Controls: the naming of before put the password under an install-wide name
that every space reads and no erase reaches; the scope rule of before read
``cfg-url:tenant.<id>.`` as the install's.
"""

from __future__ import annotations

import uuid

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.security import vault as vault_mod
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

PASSWORD = "Pa55-" + uuid.uuid4().hex[:10]


def test_a_spaces_url_password_goes_under_the_spaces_name() -> None:
    from kazma_core.config_store import _secret_vault_name

    url = f"https://user:{PASSWORD}@mirror.example/x"
    assert _secret_vault_name("tenant.s1.web.mirror_url", url) == "cfg:tenant.s1.web.mirror_url"
    # The install's own settings keep their install-wide name.
    assert _secret_vault_name("web.searxng_url", url) == "cfg-url:web.searxng_url"


def test_every_spelling_of_a_spaces_setting_secret_is_that_spaces() -> None:
    assert vault_mod.secret_scope("cfg:tenant.s1.web.mirror_url") == ("space", "s1")
    assert vault_mod.secret_scope("cfg-url:tenant.s1.web.mirror_url") == ("space", "s1")
    assert not vault_mod.is_install_scoped_secret("cfg-url:tenant.s1.web.mirror_url")
    assert vault_mod.is_install_scoped_secret("cfg-url:web.searxng_url")
    assert vault_mod.sealing_space("cfg-url:tenant.s1.web.mirror_url", None) == "s1"
    assert "cfg-url:tenant.s1." in vault_mod.space_name_prefixes("s1")


def test_control_the_scope_rule_of_before_read_it_as_the_installs(monkeypatch) -> None:
    monkeypatch.setattr(vault_mod, "_SPACE_URL_SETTING_PREFIX", "never-matches:")
    original = vault_mod._install_scope_rule

    def _before(name):
        return "newest" if name.startswith("cfg-url:") else original(name)

    monkeypatch.setattr(vault_mod, "_install_scope_rule", _before)
    assert vault_mod.secret_scope("cfg-url:tenant.s1.web.mirror_url") == ("install", None)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "c" * 64)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "_vault", v)
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    monkeypatch.setattr("kazma_core.paths.vault_db_path", lambda: str(tmp_path / "vault.db"))
    vault_mod.reset_posture_cache()
    yield v
    v.close()
    vault_mod.reset_posture_cache()


@pytest.fixture
def world(vault, monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)
    person = people.create_person("url-one", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    try:
        yield {"person": person, "space": person["personal_space"]}
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)


def test_a_spaces_url_password_is_read_in_its_space_alone_and_erased_with_it(world, vault) -> None:
    from kazma_core.spaces import space_data

    space = world["space"]
    key = f"tenant.{space}.web.mirror_url"
    store = get_config_store()
    with scope.bind_scope(space, world["person"]["id"]):
        store.set(key, f"https://user:{PASSWORD}@mirror.example/x")
        assert PASSWORD in str(store.get(key))
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert vault.retrieve(f"cfg:{key}") is None
        assert vault.retrieve(f"cfg-url:{key}") is None
    names = [name for name, _tenant in space_data._vault_rows(space)]
    assert f"cfg:{key}" in names
    assert vault.erase_space(space) >= 1
    assert not [n for n, _t in space_data._vault_rows(space) if n.endswith(key)]
    store.delete(key)


def test_erase_reaches_the_other_spelling_too(world, vault) -> None:
    space = world["space"]
    name = f"cfg-url:tenant.{space}.web.mirror_url"
    with scope.bind_scope(space, world["person"]["id"]):
        vault.store(name, PASSWORD)
    assert vault.erase_space(space) >= 1
    with scope.bind_scope(space, world["person"]["id"]):
        assert vault.retrieve(name) is None


def test_every_space_name_list_is_the_one_list() -> None:
    """Gate: the space's name prefixes are built in one place; erase and
    the count/export reader both ask it."""
    import inspect

    from kazma_core.spaces import space_data

    assert "space_name_prefixes(" in inspect.getsource(vault_mod.SecretVault.erase_space)
    assert "space_name_prefixes(" in inspect.getsource(space_data._vault_rows)
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    hits = []
    for path in (root / "kazma-core" / "kazma_core").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if 'f"cfg:tenant.{' in text or "f'cfg:tenant.{" in text:
            hits.append(path.name)
    assert hits == ["vault.py"] or hits == [], hits
