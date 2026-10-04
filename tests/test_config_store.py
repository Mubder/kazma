"""Tests for the ConfigStore — runtime settings persistence."""

from __future__ import annotations

import sqlite3

from kazma_core.config_store import ConfigStore

import pytest

# Verified against a real Postgres; the CI Postgres job runs every test
# carrying this marker (scripts/postgres_suite.py).
pytestmark = pytest.mark.postgres


class TestConfigStoreInit:
    def test_default_init(self) -> None:
        store = ConfigStore()
        assert store is not None
        store.close()

    def test_get_default(self) -> None:
        store = ConfigStore()
        val = store.get("nonexistent.key", "default_val")
        assert val == "default_val"
        store.close()

    def test_set_and_get(self) -> None:
        store = ConfigStore()
        store.set("test.my_key", "hello", category="test")
        val = store.get("test.my_key", "")
        assert val == "hello"
        store.close()

    def test_set_and_get_int(self) -> None:
        store = ConfigStore()
        store.set("test.count", 42, category="test")
        val = store.get("test.count", 0)
        assert val == 42
        store.close()

    def test_set_and_get_float(self) -> None:
        store = ConfigStore()
        store.set("test.rate", 0.75, category="test")
        val = store.get("test.rate", 0.0)
        assert val == 0.75
        store.close()

    def test_set_and_get_bool(self) -> None:
        store = ConfigStore()
        store.set("test.enabled", True, category="test")
        val = store.get("test.enabled", False)
        assert val is True
        store.close()

    def test_overwrite(self) -> None:
        store = ConfigStore()
        store.set("test.key", "old", category="test")
        store.set("test.key", "new", category="test")
        val = store.get("test.key", "")
        assert val == "new"
        store.close()

    def test_delete(self) -> None:
        store = ConfigStore()
        store.set("test.key", "value", category="test")
        store.delete("test.key")
        val = store.get("test.key", "not_found")
        assert val == "not_found"
        store.close()

    def test_get_category(self) -> None:
        store = ConfigStore()
        store.set("test.a", "1", category="test")
        store.set("test.b", "2", category="test")
        cat = store.get_category("test")
        assert isinstance(cat, dict)
        # Keys may include prefix - check values by any key
        values = list(cat.values())
        assert "1" in values or "2" in values
        store.close()

    def test_get_all(self) -> None:
        store = ConfigStore()
        store.set("test.key1", "val1", category="test")
        store.set("other.key2", "val2", category="other")
        all_settings = store.get_all()
        assert "test" in all_settings
        assert "other" in all_settings
        store.close()

    def test_reset_all(self) -> None:
        store = ConfigStore()
        store.set("test.key", "value", category="test")
        count = store.reset_all()
        assert count >= 1
        val = store.get("test.key", "not_found")
        assert val == "not_found"
        store.close()


class TestConfigStoreYaml:
    def test_export_yaml(self) -> None:
        store = ConfigStore()
        yaml_str = store.export_yaml()
        assert isinstance(yaml_str, str)
        assert len(yaml_str) > 0
        store.close()

    def test_an_export_leaves_the_defaults_as_they_are(self, tmp_path) -> None:
        """export_yaml merged the rows into the store's cached kazma.yaml: after
        a backup download, a setting deleted later read its old value instead
        of the shipped one until a restart (2026-10-01)."""
        (tmp_path / "kazma.yaml").write_text("agent:\n  language: en\n", encoding="utf-8")
        store = ConfigStore(db_path=str(tmp_path / "s.db"), yaml_path=str(tmp_path / "kazma.yaml"))
        try:
            store.set("agent.language", "ar", category="agent")
            assert "language: ar" in store.export_yaml()
            store.delete("agent.language")
            assert store.get("agent.language") == "en"
            copy = store.yaml_defaults()
            copy["agent"]["language"] = "changed"
            assert store.get("agent.language") == "en", "yaml_defaults hands out a copy"
        finally:
            store.close()

    def test_import_yaml(self) -> None:
        store = ConfigStore()
        test_yaml = """
test:
  imported_key: imported_val
"""
        count = store.import_yaml(test_yaml)
        assert count >= 1
        store.close()


# ── Volatile-fallback honesty (2026-09-04) ───────────────────────────────
# Live incident: a transient settings.db failure at boot latched the
# in-memory fallback for the whole process lifetime. Hours of 200-OK
# settings saves went to RAM; the failure reason was logged before file
# logging came up, so nobody saw it.


class TestVolatileFallback:
    @pytest.fixture(autouse=True)
    def sqlite_boot(self, monkeypatch):
        monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")

    def test_init_retries_then_succeeds(self, monkeypatch, tmp_path):
        import kazma_core.config_store as cs_mod

        calls = {"n": 0}
        real_cls = cs_mod.ConfigStore

        class FlakyStore:
            def __init__(self, *a, **kw):
                calls["n"] += 1
                if calls["n"] <= 2:
                    raise sqlite3.OperationalError("disk I/O error (transient)")
                self._real = real_cls(
                    db_path=str(tmp_path / "s.db"), *a, **kw
                )

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr(cs_mod, "ConfigStore", FlakyStore)
        monkeypatch.setattr(cs_mod, "_config_store", None)
        monkeypatch.setattr(cs_mod, "_INIT_RETRY_BACKOFF_S", 0.0)
        try:
            store = cs_mod.get_config_store()
            assert calls["n"] == 3  # failed twice, third attempt held
            assert not isinstance(store, cs_mod._InMemoryStore)
            assert cs_mod.is_config_store_volatile() is False
        finally:
            cs_mod.set_config_store(None)  # type: ignore[arg-type]

    def test_persistent_failure_refuses_boot_and_can_recover(self, monkeypatch):
        import kazma_core.config_store as cs_mod

        class DeadStore:
            def __init__(self, *a, **kw):
                raise sqlite3.OperationalError("unable to open database file")

        monkeypatch.setattr(cs_mod, "ConfigStore", DeadStore)
        monkeypatch.setattr(cs_mod, "_config_store", None)
        monkeypatch.setattr(cs_mod, "_INIT_RETRY_BACKOFF_S", 0.0)
        try:
            from kazma_core.config_availability import ConfigStoreUnavailableError

            with pytest.raises(ConfigStoreUnavailableError):
                cs_mod.get_config_store()
            assert cs_mod.peek_config_store() is None
        finally:
            cs_mod.set_config_store(None)  # type: ignore[arg-type]

    def test_deep_health_fails_on_volatile_store(self, monkeypatch):
        from kazma_ui.health import check_config_store

        monkeypatch.setattr(
            "kazma_core.config_store.is_config_store_volatile", lambda: True
        )
        result = check_config_store()
        assert result["status"] == "failed"
        assert "VOLATILE" in result["error"]
