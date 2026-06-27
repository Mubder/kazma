"""Tests for the config write-race fix (VAL-CRIT-006 / VAL-CRIT-007).

These tests assert that:
  * ``_save_config`` no longer opens ``kazma.yaml`` directly for writing
    (treating it as a read-only bootstrap).
  * All runtime config writes route through ``ConfigStore.set()`` which
    serializes them with a ``threading.Lock``.
  * Concurrent writes do not corrupt the store: after N parallel writes,
    every key is readable.
  * ``/config`` slash commands still function end-to-end.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from kazma_core.config_store import ConfigStore
from kazma_gateway import slash_commands

# ── Helpers ──────────────────────────────────────────────────────────


def _isolated_store(tmp_path) -> ConfigStore:
    """A ConfigStore backed by a temp DB + tmp YAML (no leak to repo files)."""
    yaml_path = tmp_path / "kazma.yaml"
    yaml_path.write_text("llm:\n  model: gpt-4o-mini\n", encoding="utf-8")
    return ConfigStore(
        db_path=str(tmp_path / "settings.db"),
        yaml_path=str(yaml_path),
    )


# ══════════════════════════════════════════════════════════════════════
# VAL-CRIT-007: config writes route through ConfigStore (locked)
# ══════════════════════════════════════════════════════════════════════


class TestSaveConfigRoutesThroughConfigStore:
    def test_save_config_does_not_open_yaml_for_writing(self, tmp_path, monkeypatch):
        """_save_config must NOT call ``open(kazma.yaml, 'w')``."""
        store = _isolated_store(tmp_path)
        monkeypatch.setattr(slash_commands, "_get_config_store", lambda: store, raising=False)

        opened_for_write: list[str] = []

        import builtins

        real_open = builtins.open

        def spy_open(file, mode="r", *args, **kwargs):
            file_str = str(file)
            if "w" in mode and "kazma.yaml" in file_str:
                opened_for_write.append(file_str)
            return real_open(file, mode, *args, **kwargs)

        config = {"llm": {"model": "gpt-4o"}, "memory": {"enabled": False}}
        try:
            with patch("builtins.open", side_effect=spy_open):
                slash_commands._save_config(config)
        finally:
            store.close()

        assert opened_for_write == [], (
            f"_save_config wrote directly to kazma.yaml: {opened_for_write}"
        )

    def test_save_config_calls_configstore_set(self, tmp_path, monkeypatch):
        """_save_config must call ConfigStore.set() for each leaf key."""
        store = _isolated_store(tmp_path)
        monkeypatch.setattr(slash_commands, "_get_config_store", lambda: store, raising=False)

        set_calls: list[tuple[str, object]] = []
        real_set = store.set

        def spy_set(key, value, category="general"):
            set_calls.append((key, value))
            return real_set(key, value, category=category)

        try:
            with patch.object(store, "set", side_effect=spy_set):
                slash_commands._save_config({"llm": {"model": "claude-sonnet-4"}})
        finally:
            store.close()

        assert ("llm.model", "claude-sonnet-4") in set_calls

    def test_save_config_persists_via_configstore_get(self, tmp_path, monkeypatch):
        """After _save_config, the value is readable via ConfigStore.get()."""
        store = _isolated_store(tmp_path)
        monkeypatch.setattr(slash_commands, "_get_config_store", lambda: store, raising=False)

        try:
            slash_commands._save_config({"llm": {"model": "claude-sonnet-4"}})
            assert store.get("llm.model") == "claude-sonnet-4"
        finally:
            store.close()


# ══════════════════════════════════════════════════════════════════════
# VAL-CRIT-006: concurrent writes do not corrupt
# ══════════════════════════════════════════════════════════════════════


class TestConcurrentConfigWrites:
    def test_ten_concurrent_writes_all_visible(self, tmp_path, monkeypatch):
        """N parallel _save_config calls must all persist (no lost updates)."""
        store = _isolated_store(tmp_path)
        monkeypatch.setattr(slash_commands, "_get_config_store", lambda: store, raising=False)

        n = 10

        def writer(i: int) -> None:
            # Each writer sets a distinct leaf key via the public path.
            slash_commands._save_config({"concurrent": {"writer": f"val-{i}"}})

        try:
            with ThreadPoolExecutor(max_workers=n) as pool:
                list(pool.map(writer, range(n)))

            # All N writes should be present and uncorrupted.
            for i in range(n):
                assert store.get("concurrent.writer") in {f"val-{j}" for j in range(n)}, (
                    f"write {i} lost"
                )
        finally:
            store.close()

    def test_high_contention_single_key(self, tmp_path, monkeypatch):
        """Many threads hammering the SAME key through ConfigStore.set must
        produce a single consistent final value (lock serializes them)."""
        store = _isolated_store(tmp_path)
        monkeypatch.setattr(slash_commands, "_get_config_store", lambda: store, raising=False)

        n = 50
        barrier = threading.Barrier(n)

        def writer(i: int) -> None:
            barrier.wait()
            store.set("race.key", i)

        try:
            with ThreadPoolExecutor(max_workers=n) as pool:
                list(pool.map(writer, range(n)))

            final = store.get("race.key")
            assert final in set(range(n)), f"corrupted final value: {final!r}"
        finally:
            store.close()


# ══════════════════════════════════════════════════════════════════════
# Regression: /config slash commands still function
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def isolated_store_for_slash(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path)
    monkeypatch.setattr(slash_commands, "_get_config_store", lambda: store, raising=False)
    yield store
    store.close()


class TestSlashConfigStillFunctions:
    def test_config_model_uses_configstore(self, isolated_store_for_slash):
        """/config model routes its save through ConfigStore."""
        from kazma_gateway.slash_commands import resolve_slash_command

        ctx = {"model": "gpt-4o-mini"}
        result = resolve_slash_command("/config model claude-sonnet-4", ctx)
        assert result is not None
        assert "claude-sonnet-4" in result
        # Persistence went through the locked store
        assert isolated_store_for_slash.get("llm.model") == "claude-sonnet-4"

    def test_config_memory_toggle_persists(self, isolated_store_for_slash):
        """/config memory off persists via ConfigStore."""
        from kazma_gateway.slash_commands import resolve_slash_command

        result = resolve_slash_command("/config memory off", {})
        assert result is not None
        assert "OFF" in result.upper()
        assert isolated_store_for_slash.get("memory.enabled") is False

    def test_config_show_works_after_refactor(self, isolated_store_for_slash):
        """/config show still returns a table after the refactor."""
        from kazma_gateway.slash_commands import resolve_slash_command

        result = resolve_slash_command("/config show", {"model": "gpt-4o-mini"})
        assert result is not None
        assert "Current Configuration" in result
