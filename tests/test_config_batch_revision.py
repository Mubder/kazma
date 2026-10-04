"""Conditional setting batches keep policy revisions consistent across writers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from kazma_core.config_store import ConfigRevisionConflict, ConfigStore, _InMemoryStore


def test_independent_connections_allow_exactly_one_reviewed_batch(tmp_path):
    stores = [ConfigStore(db_path=str(tmp_path / "settings.db")) for _ in range(2)]
    barrier = Barrier(2)
    def save(store, value):
        barrier.wait()
        try:
            store.batch_set([("policy.revision", 1, "test"), ("policy.subject", value, "test")], expected=("policy.revision", 0))
            return value
        except ConfigRevisionConflict:
            return None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(save, stores, ["first", "second"]))
        winner = next(value for value in outcomes if value)
        assert outcomes.count(None) == 1
        with stores[0].transaction() as conn:
            assert conn.execute("SELECT value FROM settings WHERE key = 'policy.revision'").fetchone()[0] == "1"
            assert conn.execute("SELECT value FROM settings WHERE key = 'policy.subject'").fetchone()[0] == '"' + winner + '"'
    finally:
        for store in stores:
            store.close()


@pytest.mark.parametrize("in_memory", [False, True])
def test_refused_batch_writes_nothing_and_announces_nothing(tmp_path, in_memory):
    store = _InMemoryStore(writable=True) if in_memory else ConfigStore(db_path=str(tmp_path / "settings.db"))
    observed = []
    store.add_change_listener(observed.append)
    try:
        store.batch_set([("revision", 1, "test"), ("subject", "reviewed", "test")], expected=("revision", 0))
        observed.clear()
        with pytest.raises(ConfigRevisionConflict):
            store.batch_set([("revision", 1, "test"), ("subject", "stale", "test")], expected=("revision", 0))
        assert store.get("subject") == "reviewed" and store.get("revision") == 1
        assert not observed
        with pytest.raises(ValueError):
            store.batch_set([("subject", "unguarded", "test")], expected=("revision", 1))
        assert store.get("subject") == "reviewed"
    finally:
        store.close()


@pytest.mark.postgres
def test_real_postgres_first_revision_has_one_winner(tmp_path):
    import uuid

    from kazma_core.db.backend import is_postgres

    if not is_postgres():
        pytest.skip("Requires the isolated CI Postgres service, never an operator database.")
    prefix = "test.x.cas." + uuid.uuid4().hex
    revision_key, value_key = prefix + ".revision", prefix + ".value"
    stores = [ConfigStore(db_path=str(tmp_path / f"unused-{index}.db")) for index in range(2)]
    barrier = Barrier(2)

    def save(store, value):
        barrier.wait(timeout=30)
        try:
            store.batch_set([(revision_key, 1, "test"), (value_key, value, "test")], expected=(revision_key, 0))
            return value
        except ConfigRevisionConflict:
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(save, stores, ["first", "second"]))
        assert outcomes.count(None) == 1
        reader = ConfigStore(db_path=str(tmp_path / "unused-reader.db"))
        try:
            assert reader.get(revision_key) == 1
            assert reader.get(value_key) == next(value for value in outcomes if value)
        finally:
            reader.delete(revision_key)
            reader.delete(value_key)
            reader.close()
    finally:
        for store in stores:
            store.close()
