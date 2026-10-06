"""First-owner acquisition must have one winner across Postgres connections."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from threading import Barrier
from uuid import uuid4

import pytest


@pytest.mark.postgres
def test_postgres_absent_key_has_exactly_one_first_writer(monkeypatch):
    from kazma_core.config_store import ConfigStore

    first, second = ConfigStore(), ConfigStore()
    if not first._use_postgres():
        first.close()
        second.close()
        pytest.skip("requires a disposable Postgres database")
    pool = first._pg_pool()
    key = f"session.owner.race-{uuid4().hex}"
    barrier = Barrier(2)

    class RacingCursor:
        def __init__(self, cursor):
            self.cursor = cursor
            self.owner_lookup = False

        def __enter__(self):
            self.cursor.__enter__()
            return self

        def __exit__(self, *args):
            return self.cursor.__exit__(*args)

        def execute(self, query, args):
            self.owner_lookup = query.strip().startswith("SELECT value, updated_at") and args == (key,)
            return self.cursor.execute(query, args)

        def fetchone(self):
            row = self.cursor.fetchone()
            if self.owner_lookup and row is None:
                # Force both independent connections to observe the absent
                # row before either attempts to insert it.
                barrier.wait(timeout=15)
            return row

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class RacingConnection:
        def __init__(self, connection):
            self.connection = connection

        def cursor(self):
            return RacingCursor(self.connection.cursor())

        def __getattr__(self, name):
            return getattr(self.connection, name)

    class RacingPool:
        @contextmanager
        def connection(self):
            with pool.connection() as connection:
                yield RacingConnection(connection)

    racing_pool = RacingPool()
    for store in (first, second):
        monkeypatch.setattr(store, "_pg_pool", lambda: racing_pool)
    owners = ["discord:42:900", "discord:84:900"]
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(store.set_if_absent, key, owner, category="session")
                       for store, owner in zip((first, second), owners)]
            acquired = [future.result(timeout=25) for future in futures]
        assert acquired.count(True) == 1, "both processes acquired the same missing owner key"
        with pool.connection() as connection:
            row = connection.execute("SELECT value FROM kazma_settings WHERE key = %s", (key,)).fetchone()
        assert json.loads(row["value"]) == owners[acquired.index(True)]
    finally:
        with pool.connection() as connection:
            connection.execute("DELETE FROM kazma_settings WHERE key = %s", (key,))
            connection.commit()
        first.close()
        second.close()
