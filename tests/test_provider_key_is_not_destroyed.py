"""Saving a provider key must survive every other provider write.

Found on a live install where four configured providers all reported "no API
key". The keys were not missing because nobody saved them — they were deleted
by the act of checking them.

Every provider mutation is a read-modify-write over the *whole* list::

    providers = self.list_providers()   # vault-RESOLVED
    providers[i]["health"] = status     # change one field
    self._save_providers(providers)     # write ALL of them back

``list_providers()`` resolves each ``vault://`` pointer, and
``ConfigStore._resolve_vault_value`` returns ``None`` for any pointer it cannot
decrypt — which becomes ``""``. So a single write from a process that cannot
decrypt replaces every stored pointer with an empty string. Permanently.

``set_provider_health`` is called by the Test button. Pressing Test was enough.

The only symptom was one ``WARNING`` line, after which the UI truthfully
reported that no key was stored, and the operator — who had saved one — was
told to paste it again.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import kazma_core.config_store as config_store_module


@pytest.fixture
def store(tmp_path):
    from kazma_core.config_store import ConfigStore

    return ConfigStore(
        db_path=str(tmp_path / "providers.db"),
        yaml_path=str(tmp_path / "nonexistent.yaml"),
    )


@pytest.fixture
def registry(store):
    from kazma_core.model_registry import ModelRegistry

    reg = ModelRegistry(store)
    reg.upsert_provider({
        "name": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": "gsk_REAL_SECRET_VALUE",
        "enabled": True,
    })
    return reg


def _stored_api_key(store, name: str):
    """What is actually on disk, with no vault resolution."""
    for values in store.get_all().values():
        if "providers.list" in values:
            entries = values["providers.list"]
            if isinstance(entries, str):
                entries = json.loads(entries)
            for entry in entries:
                if entry.get("name") == name:
                    return entry.get("api_key") or ""
    return None


def _no_vault():
    """A process that cannot decrypt — a missing or rotated KAZMA_SECRET."""
    return patch.object(config_store_module, "_try_get_vault", return_value=None)


class TestTheKeySurvivesAWriteItDidNotAuthor:
    def test_the_key_is_on_disk_to_begin_with(self, registry, store):
        """At-rest form depends on whether a vault is configured -- a pointer
        when it is, plaintext when it is not. Both are destroyed the same way,
        so these tests assert the key SURVIVES rather than what shape it has;
        asserting the shape only tests which environment pytest is running in.
        """
        assert _stored_api_key(store, "groq")

    def test_pressing_test_does_not_delete_it(self, registry, store):
        """`set_provider_health` is what the Test button writes. This is the
        exact call that destroyed four keys on a live install."""
        from kazma_core.model_registry import ModelRegistry

        with _no_vault():
            ModelRegistry(store).set_provider_health("groq", "healthy")

        assert _stored_api_key(store, "groq"), "the Test button deleted the key"

    def test_toggling_a_provider_does_not_delete_it(self, registry, store):
        from kazma_core.model_registry import ModelRegistry

        with _no_vault():
            ModelRegistry(store).toggle_provider("groq", False)

        assert _stored_api_key(store, "groq"), "the toggle deleted the key"

    def test_editing_another_provider_does_not_delete_it(self, registry, store):
        """The write is over the whole list, so a provider can be destroyed by
        an edit to a completely different one."""
        from kazma_core.model_registry import ModelRegistry

        with _no_vault():
            ModelRegistry(store).upsert_provider(
                {"name": "deepseek", "base_url": "https://api.deepseek.com/v1"}
            )

        assert _stored_api_key(store, "groq"), (
            "editing deepseek deleted groq's key"
        )

    def test_the_key_still_reads_back_afterwards(self, registry, store):
        """Surviving on disk is only half of it — it has to still read back as
        the value that was saved."""
        from kazma_core.model_registry import ModelRegistry

        with _no_vault():
            ModelRegistry(store).set_provider_health("groq", "healthy")

        assert (
            ModelRegistry(store).get_provider("groq") or {}
        ).get("api_key") == "gsk_REAL_SECRET_VALUE"

    def test_resolve_credentials_still_finds_it(self, registry, store):
        """The end the product actually uses."""
        from kazma_core.model_registry import ModelRegistry

        with _no_vault():
            ModelRegistry(store).toggle_provider("groq", True)

        _, key = ModelRegistry(store).resolve_provider_credentials("groq")
        assert key == "gsk_REAL_SECRET_VALUE"


class TestAKeyCanStillBeChanged:
    """The guard must not freeze a key in place — only refuse to blank one."""

    def test_a_new_key_overwrites_the_old_one(self, registry, store):
        registry.upsert_provider({"name": "groq", "api_key": "gsk_A_DIFFERENT_KEY"})
        assert (
            registry.get_provider("groq") or {}
        ).get("api_key") == "gsk_A_DIFFERENT_KEY"

    def test_deleting_the_provider_removes_it_entirely(self, registry, store):
        registry.delete_provider("groq")
        assert _stored_api_key(store, "groq") is None

    def test_a_provider_with_no_key_is_unaffected(self, registry, store):
        registry.upsert_provider(
            {"name": "ollama", "base_url": "http://127.0.0.1:11434/v1"}
        )
        assert _stored_api_key(store, "ollama") == ""


class TestAnUndecryptableKeySaysSo:
    """"No key" and "a key you cannot read" both arrive as an empty string and
    need opposite actions. Telling an operator who saved a key to paste it
    again is the one action that cannot help — the stored value is fine, the
    vault key is not."""

    def test_a_readable_key_is_not_reported_as_undecryptable(self, registry, store):
        assert registry.stored_key_is_undecryptable("groq") is False

    def test_a_provider_with_no_key_is_not_reported_as_undecryptable(
        self, registry, store
    ):
        registry.upsert_provider(
            {"name": "ollama", "base_url": "http://127.0.0.1:11434/v1"}
        )
        assert registry.stored_key_is_undecryptable("ollama") is False

    def test_an_unreadable_pointer_is_detected(self, store, monkeypatch):
        """The state a rotated or missing KAZMA_VAULT_KEY leaves behind: the
        pointer survives on disk (thanks to the save guard) and resolves to
        nothing."""
        import json

        from kazma_core.model_registry import ModelRegistry

        reg = ModelRegistry(store)
        reg.upsert_provider(
            {"name": "groq", "base_url": "https://api.groq.com/openai/v1",
             "api_key": "gsk_REAL_SECRET_VALUE"}
        )
        # Force the at-rest form to a pointer regardless of whether this test
        # environment has a vault, then make it unreadable.
        entries = json.loads(json.dumps(
            [dict(e) for e in
             __import__("kazma_core.model_registry_store", fromlist=["x"])
             .load_providers_unresolved(store)]
        ))
        for e in entries:
            if e.get("name") == "groq":
                e["api_key"] = "vault://cfg:providers.list.groq.api_key"
        store.set("providers.list", entries, category="providers")

        with _no_vault():
            assert ModelRegistry(store).stored_key_is_undecryptable("groq") is True

    def test_the_test_route_says_which_problem_it_is(self):
        import inspect

        from kazma_ui import providers as ui_providers

        src = inspect.getsource(ui_providers)
        assert "stored_key_is_undecryptable" in src
        assert "KAZMA_VAULT_KEY" in src
