"""No write may erase a stored secret by being empty.

This is the defect that destroyed every provider API key on a live install:

    a vault pointer that cannot be decrypted resolves to None
      -> becomes ""
      -> is written straight back over the pointer
      -> permanently, behind one WARNING line

`save_providers` got a guard at its own writer. That fixed `providers.list`
and nothing else, which is the wrong shape for a defect that belongs to the
STORAGE, not to one caller: any config value holding a secret — flat, or
nested inside a JSON blob — can be blanked the same way, and a JSON blob
nobody has audited yet is exactly where it would happen next.

The guard now sits in `_prepare_value_for_storage`, where every writer already
passes: `set`, `batch_set`, and the nested walk underneath them.

Clearing on purpose still works — through `delete(key)`, which is what the
connector-removal path already uses. Nothing legitimate writes an empty secret.
"""

from __future__ import annotations

import pytest

VAULT_REF = "vault://cfg:connectors.telegram.token"


@pytest.fixture
def store(tmp_path):
    from kazma_core.config_store import ConfigStore

    return ConfigStore(
        db_path=str(tmp_path / "secrets.db"),
        yaml_path=str(tmp_path / "nonexistent.yaml"),
    )


class TestAFlatSecret:
    def test_an_empty_write_does_not_erase_it(self, store):
        store.set("connectors.telegram.token", "123456:REAL-TOKEN", category="connectors")
        store.set("connectors.telegram.token", "", category="connectors")
        assert store.get("connectors.telegram.token") == "123456:REAL-TOKEN"

    def test_a_none_write_does_not_erase_it(self, store):
        store.set("connectors.slack.token", "xoxb-REAL", category="connectors")
        store.set("connectors.slack.token", None, category="connectors")
        assert store.get("connectors.slack.token") == "xoxb-REAL"

    def test_a_batch_write_does_not_erase_it(self, store):
        """`PUT /api/settings` sends a list. One empty field in a form submit
        must not take a credential with it."""
        store.set("connectors.discord.token", "REAL-DISCORD", category="connectors")
        store.batch_set([("connectors.discord.token", "", "connectors")])
        assert store.get("connectors.discord.token") == "REAL-DISCORD"

    def test_a_vault_pointer_is_protected_too(self, store):
        """The actual live shape: what is on disk is a pointer, and the
        process writing cannot decrypt it."""
        store.set("connectors.telegram.token", VAULT_REF, category="connectors")
        store.set("connectors.telegram.token", "", category="connectors")
        assert store._stored_raw("connectors.telegram.token") == VAULT_REF

    def test_a_real_new_secret_still_replaces_the_old_one(self, store):
        store.set("connectors.telegram.token", "OLD", category="connectors")
        store.set("connectors.telegram.token", "NEW", category="connectors")
        assert store.get("connectors.telegram.token") == "NEW"

    def test_delete_still_clears_it(self, store):
        """The deliberate path. If this broke, the guard would be a lock with
        no key."""
        store.set("connectors.telegram.token", "REAL", category="connectors")
        store.delete("connectors.telegram.token")
        assert not store.get("connectors.telegram.token")

    def test_a_non_secret_key_can_still_be_emptied(self, store):
        """The guard is scoped to secrets. Ordinary settings clear normally."""
        store.set("ui.theme", "dark", category="general")
        store.set("ui.theme", "", category="general")
        assert store.get("ui.theme") == ""


class TestASecretNestedInABlob:
    """One level down — the providers.list wipe."""

    def test_an_empty_leaf_does_not_erase_the_stored_one(self, store):
        store.set(
            "providers.list",
            [{"name": "groq", "api_key": "gsk_REAL", "enabled": True}],
            category="providers",
        )
        store.set(
            "providers.list",
            [{"name": "groq", "api_key": "", "enabled": False}],
            category="providers",
        )
        rows = store.get("providers.list")
        assert rows[0]["api_key"] == "gsk_REAL", "the read-modify-write blanked it"
        assert rows[0]["enabled"] is False, "the field being edited did not save"

    def test_it_matches_list_items_by_identity_not_position(self, store):
        """Reordering must not pair one provider's secret with another's row."""
        store.set(
            "providers.list",
            [{"name": "a", "api_key": "KEY-A"}, {"name": "b", "api_key": "KEY-B"}],
            category="providers",
        )
        store.set(
            "providers.list",
            [{"name": "b", "api_key": ""}, {"name": "a", "api_key": ""}],
            category="providers",
        )
        by_name = {r["name"]: r["api_key"] for r in store.get("providers.list")}
        assert by_name == {"a": "KEY-A", "b": "KEY-B"}

    def test_a_nested_secret_in_a_dict_is_protected(self, store):
        """Covers the blobs nobody has audited — swarm.output_target and
        whatever is added next."""
        store.set("swarm.output_target", {"kind": "webhook", "token": "REAL"}, category="swarm")
        store.set("swarm.output_target", {"kind": "webhook", "token": ""}, category="swarm")
        assert store.get("swarm.output_target")["token"] == "REAL"

    def test_a_new_nested_secret_still_saves(self, store):
        store.set("providers.list", [{"name": "groq", "api_key": "OLD"}], category="providers")
        store.set("providers.list", [{"name": "groq", "api_key": "NEW"}], category="providers")
        assert store.get("providers.list")[0]["api_key"] == "NEW"

    def test_removing_the_whole_entry_still_works(self, store):
        """Deleting a provider is not the same as blanking its key."""
        store.set(
            "providers.list",
            [{"name": "groq", "api_key": "REAL"}, {"name": "zai", "api_key": "REAL2"}],
            category="providers",
        )
        store.set("providers.list", [{"name": "zai", "api_key": ""}], category="providers")
        rows = store.get("providers.list")
        assert [r["name"] for r in rows] == ["zai"]
        assert rows[0]["api_key"] == "REAL2"
