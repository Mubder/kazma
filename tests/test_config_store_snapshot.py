"""Coherent detached prefix reads, bypassing stale per-key caches."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from kazma_core.config_availability import ConfigStoreUnavailableError
from kazma_core.config_store import ConfigStore, _InMemoryStore, get_config_store


def test_snapshot_preserves_get_precedence_yaml_and_detached_values(tmp_path):
    yaml = tmp_path / "defaults.yaml"
    yaml.write_text("documents:\n  limits:\n    pages: 11\n  enabled: true\n", encoding="utf-8")
    store = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(yaml))
    try:
        store.batch_set([("documents.limits.cells", 20, "general"),
                         ("documents.labels", ["first"], "general"),
                         ("documents.sibling", {"child": "parent"}, "general"),
                         ("documents.sibling.child", "exact", "general"),
                         ("documentsX.secret", "decoy", "general")])
        snap = store.snapshot("documents")
        assert snap.get("documents.limits") == store.get("documents.limits") == {"cells": 20}
        assert snap.get("documents.limits.pages") == store.get("documents.limits.pages") == 11
        assert snap.get("documents.sibling") == {"child": "parent"}
        assert snap.get("documents.sibling.child") == "exact"
        assert snap.get("documents.enabled") is True
        snap.get("documents.labels").append("changed")
        assert snap.get("documents.labels") == ["first"]
        store.set("documents.labels", ["later"])
        assert snap.get("documents.labels") == ["first"]
        with pytest.raises(ValueError):
            snap.get("documentsX.secret")
    finally:
        store.close()


def test_snapshot_is_one_statement_and_sees_another_connection_commit():
    store = get_config_store()
    store.set("documents.limit", 1)
    assert store.get("documents.limit") == 1
    with sqlite3.connect(store._db_path) as other:
        other.execute("UPDATE settings SET value = '2' WHERE key = 'documents.limit'")
    assert store.get("documents.limit") == 1  # old TTL cache, negative control
    statements = []
    with store._lock:
        store._get_conn().set_trace_callback(statements.append)
    try:
        assert store.snapshot("documents").get("documents.limit") == 2
    finally:
        with store._lock:
            store._get_conn().set_trace_callback(None)
    assert len([sql for sql in statements if sql.startswith("SELECT key, value FROM settings")]) == 1


def test_cross_connection_atomic_batches_never_produce_mixed_snapshot(tmp_path):
    db = tmp_path / "snapshot.db"
    reader = ConfigStore(db_path=str(db), yaml_path=str(tmp_path / "absent.yaml"))
    writer = ConfigStore(db_path=str(db), yaml_path=str(tmp_path / "absent.yaml"))
    reader.batch_set([("documents.left", 0, "general"), ("documents.right", 0, "general")])
    started, done = Event(), Event()

    def write():
        started.set()
        try:
            for i in range(1, 101):
                writer.batch_set([("documents.left", i, "general"), ("documents.right", i, "general")])
        finally:
            done.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(write)
            assert started.wait(5)
            for _ in range(100):
                snap = reader.snapshot("documents")
                assert snap.get("documents.left") == snap.get("documents.right")
            future.result(timeout=10)
        assert done.is_set()
        assert reader.snapshot("documents").get("documents.left") == 100
    finally:
        writer.close()
        reader.close()


def test_snapshot_literal_prefix_and_configured_pg_outage(monkeypatch):
    store = get_config_store()
    store.set("doc_%.limit", 7)
    store.set("docXY.limit", 8)
    assert store.snapshot("doc_%").get("doc_%") == {"limit": 7}
    monkeypatch.setattr(store, "_use_postgres", lambda: True)
    monkeypatch.setattr(store, "_pg_pool", lambda: None)
    with pytest.raises(ConfigStoreUnavailableError):
        store.snapshot("documents")


def test_pg_snapshot_reads_once_and_resolves_json_without_cache(monkeypatch):
    store = get_config_store()
    statements = []

    class Pool:
        def execute(self, sql, params):
            statements.append((sql, params))
            return [{"key": "documents.first", "value": json.dumps(4)},
                    {"key": "documents.second", "value": 4}]

    monkeypatch.setattr(store, "_use_postgres", lambda: True)
    monkeypatch.setattr(store, "_pg_pool", lambda: Pool())
    snapshot = store.snapshot("documents")
    assert snapshot.get("documents.first") == snapshot.get("documents.second") == 4
    assert len(statements) == 1
    assert statements[0][1] == ("documents", 10, "documents.")


def test_snapshot_keeps_native_select_inside_close_lock():
    store = get_config_store()
    store.set("documents.limit", 7)
    observed = []

    def trace(sql):
        if sql.startswith("SELECT key, value FROM settings"):
            observed.append(store._lock._is_owned())

    with store._lock:
        store._get_conn().set_trace_callback(trace)
    try:
        assert store.snapshot("documents").get("documents.limit") == 7
    finally:
        with store._lock:
            store._get_conn().set_trace_callback(None)
    assert observed == [True]


def test_volatile_snapshot_is_detached_and_readonly_sentinel_refuses():
    store = _InMemoryStore(writable=True)
    store.set("documents.limit", [7])
    snap = store.snapshot("documents")
    store.set("documents.limit", [8])
    assert snap.get("documents.limit") == [7]
    with pytest.raises(ConfigStoreUnavailableError):
        _InMemoryStore().snapshot("documents")


@pytest.mark.postgres
def test_real_pg_snapshot_observes_committed_batches_without_stale_cache(tmp_path, monkeypatch):
    import os
    import uuid

    if os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("requires an explicitly isolated PostgreSQL qualification database")
    from kazma_core.db.postgres_pool import get_postgres_pool

    if get_postgres_pool() is None:
        pytest.skip("Postgres pool not configured")
    prefix = "documents.snapshot_" + uuid.uuid4().hex
    reader = get_config_store()
    monkeypatch.setattr(reader, "_CACHE_TTL_SECONDS", 3600.0)
    writer = ConfigStore(db_path=str(tmp_path / "unused.db"), yaml_path=str(tmp_path / "absent.yaml"))
    assert reader._use_postgres() and writer._use_postgres()
    keys = [prefix + ".left", prefix + ".right"]
    reader.batch_set([(key, 0, "general") for key in keys])
    assert reader.get(keys[0]) == 0
    started = Event()

    def write():
        started.set()
        for i in range(1, 101):
            writer.batch_set([(key, i, "general") for key in keys])

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(write)
            assert started.wait(5)
            for _ in range(100):
                snap = reader.snapshot(prefix)
                assert snap.get(keys[0]) == snap.get(keys[1])
            future.result(timeout=30)
        # Independent store writes do not invalidate the reader's TTL cache.
        assert reader.get(keys[0]) == 0
        assert reader.snapshot(prefix).get(keys[0]) == 100
    finally:
        for key in keys:
            writer.delete(key)
        writer.close()


def test_a_key_stored_nowhere_reads_as_missing_like_get(tmp_path):
    """``snapshot(k).get(k)`` answers what ``get(k)`` answers for a key held
    neither in the database nor in kazma.yaml: None (or the default). It
    answered ``{}`` -- the YAML walk defaulted every missing level to a dict
    -- and the crawl journal read a removed job as a broken row (2026-11-13)."""
    yaml_path = tmp_path / "kazma.yaml"
    yaml_path.write_text("acquisition:\n  crawl: {}\ndocuments:\n  intake:\n    max_files: 3\n", encoding="utf-8")
    store = ConfigStore(str(tmp_path / "settings.db"), yaml_path=str(yaml_path))
    try:
        key = "acquisition.crawl.0123456789abcdef"
        assert store.get(key) is None
        assert store.snapshot(key).get(key) is None
        assert store.snapshot(key).get(key, "fallback") == "fallback"
        # What the YAML does hold is still read through the snapshot.
        assert store.snapshot("documents").get("documents.intake.max_files") == 3
        store.set(key + ".rev", 4)
        assert store.snapshot(key).get(key) == {"rev": 4}  # stored children still merge, as in get
    finally:
        store.close()


def test_negative_control_the_old_walk_answered_an_empty_dict():
    """The YAML walk as it was: every missing level a dict."""
    from kazma_core.config_store import SettingsSnapshot

    key = "acquisition.crawl.0123456789abcdef"
    yaml_value = {"acquisition": {"crawl": {}}}
    for part in key.split("."):
        yaml_value = yaml_value.get(part, {}) if isinstance(yaml_value, dict) else {}
    yaml_data = {"acquisition": {"crawl": {"0123456789abcdef": yaml_value}}}
    assert SettingsSnapshot(key, {}, yaml_data).get(key) == {}
