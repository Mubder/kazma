"""Two first updates of one settings key keep both (Spaces S1-10 review).

``ConfigStore.atomic_update`` reads its key with ``SELECT ... FOR UPDATE``
on Postgres, and that locks nothing while the row does not exist yet: two
processes making the first update of a key each read nothing, and the second
write replaced the first. The two spaces' first lessons were the case found
(the spaces' Soul document is one key), and every caller of
``atomic_update`` on a new key had it. The key itself is now locked for the
transaction first (``pg_advisory_xact_lock``), so the second update waits
and reads the first.

Two ``ConfigStore`` instances stand for two processes: each has its own
write lock, as each process does. SQLite's ``BEGIN IMMEDIATE`` serialized
the two already; its case runs everywhere.
"""

from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from kazma_core.config_store import ConfigStore

_HOLD_S = 0.5  # each update holds its transaction this long, after its read


def _two_first_updates(stores: list[ConfigStore], key: str) -> None:
    barrier = Barrier(len(stores))

    def add(store: ConfigStore, name: str) -> None:
        barrier.wait(timeout=30)

        def updater(current):
            items = list(current or [])
            time.sleep(_HOLD_S)  # the other update runs meanwhile
            return [*items, name]

        store.atomic_update(key, updater, "test")

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        list(pool.map(add, stores, [f"writer-{i}" for i in range(len(stores))]))


def test_on_sqlite_two_first_updates_keep_both(tmp_path) -> None:
    db = str(tmp_path / "settings.db")
    stores = [ConfigStore(db_path=db, yaml_path=str(tmp_path / "none.yaml")) for _ in range(2)]
    try:
        if stores[0]._use_postgres():
            pytest.skip("the SQLite case: this run's settings store is Postgres")
        key = "test.atomic.first." + uuid.uuid4().hex
        _two_first_updates(stores, key)
        assert sorted(stores[0].get(key)) == ["writer-0", "writer-1"]
    finally:
        for store in stores:
            store.close()


def _postgres_or_skip() -> None:
    from kazma_core.db.backend import is_postgres

    if not is_postgres():
        pytest.skip("Requires the isolated CI Postgres service, never an operator database.")


@pytest.mark.postgres
def test_on_postgres_two_first_updates_keep_both(tmp_path) -> None:
    _postgres_or_skip()
    key = "test.atomic.first." + uuid.uuid4().hex
    stores = [ConfigStore(db_path=str(tmp_path / f"unused-{i}.db")) for i in range(2)]
    try:
        _two_first_updates(stores, key)
        reader = ConfigStore(db_path=str(tmp_path / "unused-reader.db"))
        try:
            assert sorted(reader.get(key)) == ["writer-0", "writer-1"]
        finally:
            reader.delete(key)
            reader.close()
    finally:
        for store in stores:
            store.close()


@pytest.mark.postgres
def test_control_for_update_alone_locks_nothing_on_a_new_key(tmp_path) -> None:
    """The order ``atomic_update`` had: ``SELECT ... FOR UPDATE``, the
    change, the upsert. On a key with no row, both read nothing and one
    writer's item is lost."""
    import json

    import psycopg

    _postgres_or_skip()
    ConfigStore(db_path=str(tmp_path / "unused-schema.db")).close()  # the table exists
    key = "test.atomic.first." + uuid.uuid4().hex
    dsn = os.environ["KAZMA_DATABASE_URL"]
    barrier = Barrier(2)

    def add(name: str) -> None:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            barrier.wait(timeout=30)
            cur.execute("SELECT value FROM kazma_settings WHERE key = %s FOR UPDATE", (key,))
            row = cur.fetchone()
            items = json.loads(row[0]) if row and row[0] else []
            time.sleep(_HOLD_S)
            cur.execute(
                "INSERT INTO kazma_settings (key, value, category, updated_at) VALUES (%s, %s, 'test', now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, json.dumps([*items, name])),
            )
            conn.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(add, ["writer-0", "writer-1"]))
    reader = ConfigStore(db_path=str(tmp_path / "unused-reader.db"))
    try:
        assert len(reader.get(key)) == 1  # one writer's item lost
    finally:
        reader.delete(key)
        reader.close()
