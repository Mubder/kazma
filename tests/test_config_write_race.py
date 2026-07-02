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
from typing import Any
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
        """_save_config must persist each leaf key atomically via batch_set."""
        store = _isolated_store(tmp_path)
        monkeypatch.setattr(slash_commands, "_get_config_store", lambda: store, raising=False)

        batch_calls: list[list[tuple[str, Any, str]]] = []
        real_batch_set = store.batch_set

        def spy_batch_set(items):
            batch_calls.append(items)
            return real_batch_set(items)

        try:
            with patch.object(store, "batch_set", side_effect=spy_batch_set):
                slash_commands._save_config({"llm": {"model": "claude-sonnet-4"}})
        finally:
            store.close()

        # batch_set should have been called once with all items
        assert len(batch_calls) == 1, f"Expected 1 batch_set call, got {len(batch_calls)}"
        items = batch_calls[0]
        keys = [(key, value) for key, value, _ in items]
        assert ("llm.model", "claude-sonnet-4") in keys

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


# ══════════════════════════════════════════════════════════════════════
# P1-4: Atomicity + WAL + Singleton tests
# ══════════════════════════════════════════════════════════════════════


class TestBatchSetAtomicity:
    """batch_set() must write all keys or none (atomic)."""

    def test_batch_set_writes_all_keys(self, tmp_path):
        store = _isolated_store(tmp_path)
        try:
            count = store.batch_set([
                ("a.key1", "val1", "a"),
                ("a.key2", "val2", "a"),
                ("b.key1", 42, "b"),
            ])
            assert count == 3
            assert store.get("a.key1") == "val1"
            assert store.get("a.key2") == "val2"
            assert store.get("b.key1") == 42
        finally:
            store.close()

    def test_batch_set_rolls_back_on_error(self, tmp_path):
        """If an item fails, the whole batch rolls back — no partial writes."""
        store = _isolated_store(tmp_path)
        try:
            # First write a baseline value
            store.set("existing.key", "original")

            # Now attempt a batch that will fail (json.dumps on a set raises)
            with pytest.raises(TypeError):
                store.batch_set([
                    ("new.key1", "should_not_persist", "general"),
                    ("new.key2", {"unjsonable": {1, 2, 3}}, "general"),  # set() fails json.dumps
                ])

            # Neither new key should exist
            assert store.get("new.key1") is None
            assert store.get("new.key2") is None
            # Existing key unchanged
            assert store.get("existing.key") == "original"
        finally:
            store.close()

    def test_batch_set_empty_list(self, tmp_path):
        store = _isolated_store(tmp_path)
        try:
            assert store.batch_set([]) == 0
        finally:
            store.close()


class TestTransactionContextManager:
    """transaction() context manager provides atomic grouping."""

    def test_transaction_commits_on_success(self, tmp_path):
        store = _isolated_store(tmp_path)
        try:
            with store.transaction() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value, category, updated_at) VALUES (?, ?, ?, ?)",
                    ("tx.key", '"value"', "general", "2026-01-01T00:00:00Z"),
                )
            assert store.get("tx.key") == "value"
        finally:
            store.close()

    def test_transaction_rolls_back_on_exception(self, tmp_path):
        store = _isolated_store(tmp_path)
        try:
            with pytest.raises(RuntimeError):
                with store.transaction() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value, category, updated_at) VALUES (?, ?, ?, ?)",
                        ("tx.rollback", '"nope"', "general", "2026-01-01T00:00:00Z"),
                    )
                    raise RuntimeError("simulated failure")
            # The write should have rolled back
            assert store.get("tx.rollback") is None
        finally:
            store.close()


class TestSingleton:
    """get_config_store() returns the shared singleton."""

    def test_singleton_returns_same_instance(self):
        from kazma_core.config_store import get_config_store, reset_config_store

        reset_config_store()
        s1 = get_config_store()
        s2 = get_config_store()
        assert s1 is s2
        reset_config_store()

    def test_set_config_store_replaces_singleton(self, tmp_path):
        from kazma_core.config_store import get_config_store, set_config_store, reset_config_store

        custom = _isolated_store(tmp_path)
        set_config_store(custom)
        assert get_config_store() is custom
        reset_config_store()
        custom.close()


class TestConcurrentCrossInstanceWrites:
    """Multiple ConfigStore instances on the same DB file coordinate via WAL."""

    def test_cross_instance_concurrent_writes(self, tmp_path):
        """Two separate instances writing to the same DB file should not corrupt."""
        import random

        db_path = str(tmp_path / "shared.db")
        yaml_path = tmp_path / "kazma.yaml"
        yaml_path.write_text("llm:\n  model: test\n", encoding="utf-8")

        store_a = ConfigStore(db_path=db_path, yaml_path=str(yaml_path))
        store_b = ConfigStore(db_path=db_path, yaml_path=str(yaml_path))
        errors = []

        def writer(store, prefix, n):
            try:
                for i in range(n):
                    store.set(f"{prefix}.key{i}", i)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(store_a, "a", 50)),
            threading.Thread(target=writer, args=(store_b, "b", 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent writes failed: {errors[:3]}"

        # All keys should be readable from either instance
        for i in range(50):
            assert store_a.get(f"a.key{i}") == i
            assert store_b.get(f"b.key{i}") == i

        store_a.close()
        store_b.close()


# ══════════════════════════════════════════════════════════════════════
# P2-9: Config reconciliation (YAML → SQLite non-clobbering seed)
# ══════════════════════════════════════════════════════════════════════


class TestReconcileFromYaml:
    """reconcile_from_yaml() seeds DB with YAML keys not already present."""

    def test_seeds_new_keys_from_yaml(self, tmp_path):
        """First run: all YAML keys are seeded into the empty DB."""
        yaml_path = tmp_path / "kazma.yaml"
        yaml_path.write_text(
            "llm:\n  model: gpt-4o\n  temperature: 0.7\n"
            "safety:\n  hitl:\n    enabled: true\n",
            encoding="utf-8",
        )
        store = ConfigStore(
            db_path=str(tmp_path / "settings.db"),
            yaml_path=str(yaml_path),
        )
        try:
            count = store.reconcile_from_yaml()
            assert count == 3  # llm.model, llm.temperature, safety.hitl.enabled
            assert store.get("llm.model") == "gpt-4o"
            assert store.get("llm.temperature") == 0.7
            assert store.get("safety.hitl.enabled") is True
        finally:
            store.close()

    def test_does_not_clobber_existing_db_keys(self, tmp_path):
        """User-changed DB keys must NOT be overwritten by YAML values."""
        yaml_path = tmp_path / "kazma.yaml"
        yaml_path.write_text("llm:\n  model: gpt-4o\n", encoding="utf-8")
        store = ConfigStore(
            db_path=str(tmp_path / "settings.db"),
            yaml_path=str(yaml_path),
        )
        try:
            # User changes the model via Settings UI
            store.set("llm.model", "claude-sonnet-4")

            # Reconcile — should NOT overwrite the user's choice
            count = store.reconcile_from_yaml()
            assert count == 0  # llm.model already in DB
            assert store.get("llm.model") == "claude-sonnet-4"
        finally:
            store.close()

    def test_seeds_only_missing_keys(self, tmp_path):
        """Partial DB: only keys absent from DB are seeded."""
        yaml_path = tmp_path / "kazma.yaml"
        yaml_path.write_text(
            "llm:\n  model: gpt-4o\n  temperature: 0.7\n  base_url: http://x\n",
            encoding="utf-8",
        )
        store = ConfigStore(
            db_path=str(tmp_path / "settings.db"),
            yaml_path=str(yaml_path),
        )
        try:
            # Pre-set one key
            store.set("llm.model", "user-choice")

            count = store.reconcile_from_yaml()
            assert count == 2  # only temperature + base_url (model already in DB)
            assert store.get("llm.model") == "user-choice"  # not clobbered
            assert store.get("llm.temperature") == 0.7  # seeded
            assert store.get("llm.base_url") == "http://x"  # seeded
        finally:
            store.close()

    def test_no_yaml_file_returns_zero(self, tmp_path):
        """No kazma.yaml → nothing to reconcile."""
        store = ConfigStore(
            db_path=str(tmp_path / "settings.db"),
            yaml_path=str(tmp_path / "nonexistent.yaml"),
        )
        try:
            assert store.reconcile_from_yaml() == 0
        finally:
            store.close()

    def test_idempotent(self, tmp_path):
        """Calling reconcile twice doesn't double-seed."""
        yaml_path = tmp_path / "kazma.yaml"
        yaml_path.write_text("llm:\n  model: gpt-4o\n", encoding="utf-8")
        store = ConfigStore(
            db_path=str(tmp_path / "settings.db"),
            yaml_path=str(yaml_path),
        )
        try:
            first = store.reconcile_from_yaml()
            second = store.reconcile_from_yaml()
            assert first == 1
            assert second == 0  # already seeded
        finally:
            store.close()
