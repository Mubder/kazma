"""Transaction failure and concurrent owners have the same backend contract."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from kazma_core.config_store import ConfigRevisionConflict, ConfigStore

# Run against a real disposable Postgres before admission to the marked suite.
pytestmark = pytest.mark.postgres


@pytest.fixture
def stores(tmp_path):
    pair = [ConfigStore(db_path=str(tmp_path / "settings.db")) for _ in range(2)]
    prefix = "test.recovery." + uuid.uuid4().hex
    yield pair, prefix
    for store in pair:
        for suffix in ("revision", "value", "first", "invalid"):
            store.delete(prefix + "." + suffix)
        store.close()


def test_batch_failure_rolls_back_the_earlier_statement_and_reopens(stores, tmp_path):
    pair, prefix = stores
    first, invalid = prefix + ".first", prefix + ".invalid"
    with pytest.raises(TypeError):
        pair[0].batch_set([(first, "must roll back", "test"), (invalid, object(), "test")])
    assert pair[1].get(first) is None and pair[1].get(invalid) is None
    pair[0].batch_set([(first, "committed", "test"), (invalid, "corrected", "test")])
    reopened = ConfigStore(db_path=str(tmp_path / "settings.db"))
    try:
        assert reopened.get(first) == "committed"
        assert reopened.get(invalid) == "corrected"
    finally:
        reopened.close()


def test_concurrent_conditional_save_commits_one_complete_revision(stores, tmp_path):
    pair, prefix = stores
    revision, value = prefix + ".revision", prefix + ".value"
    barrier = Barrier(2)

    def save(item):
        index, store = item
        barrier.wait(timeout=10)
        try:
            store.batch_set([(value, index, "test"), (revision, 1, "test")], expected=(revision, 0))
            return index
        except ConfigRevisionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, enumerate(pair)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    reopened = ConfigStore(db_path=str(tmp_path / "settings.db"))
    try:
        assert reopened.get(revision) == 1 and reopened.get(value) == winners[0]
    finally:
        reopened.close()
