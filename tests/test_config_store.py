"""Tests for the ConfigStore — runtime settings persistence."""

from __future__ import annotations

from kazma_core.config_store import ConfigStore


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

    def test_import_yaml(self) -> None:
        store = ConfigStore()
        test_yaml = """
test:
  imported_key: imported_val
"""
        count = store.import_yaml(test_yaml)
        assert count >= 1
        store.close()
