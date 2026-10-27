"""The process-wide episode writer is set up once, whoever writes first
(audit 2026-10-26 AUD-117).

``DualWriteMirror._ensure`` had no lock: two first writers both found it not
ready, both ran the schema scripts at once on two connections, and the second
overwrote the first's connections (one leaked; a write could run on a
connection replaced under it).
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from kazma_core.memory import dual_write, schema_v2


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    return tmp_path


def _first_writers(monkeypatch, ensure, writers: int = 4) -> tuple[int, int]:
    """*writers* threads call *ensure* on one fresh mirror at the same moment.
    Returns how many times the primary schema ran, and how many of the
    connections it ran on are not the one the mirror kept (replaced, leaked)."""
    schema_conns: list[sqlite3.Connection] = []
    real = schema_v2.ensure_primary_schema

    def slow_schema(conn):
        schema_conns.append(conn)
        time.sleep(0.2)  # the window two first writers both used to enter
        return real(conn)

    monkeypatch.setattr(schema_v2, "ensure_primary_schema", slow_schema)
    mirror = dual_write.DualWriteMirror()
    start = threading.Barrier(writers)

    def write():
        start.wait()
        ensure(mirror)

    threads = [threading.Thread(target=write) for _ in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    replaced = [c for c in schema_conns if c is not mirror._primary]
    for conn in replaced:
        conn.close()
    mirror.close()
    return len(schema_conns), len(replaced)


def test_first_writers_set_up_one_connection_once(paths, monkeypatch):
    runs, replaced = _first_writers(monkeypatch, dual_write.DualWriteMirror._ensure)
    assert (runs, replaced) == (1, 0)


def test_a_failed_setup_closes_what_it_opened(paths, monkeypatch):
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def connect(*a, **k):
        conn = real_connect(*a, **k)
        opened.append(conn)
        return conn

    def broken(conn):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(dual_write.sqlite3, "connect", connect)
    monkeypatch.setattr(schema_v2, "ensure_ops_schema", broken)
    mirror = dual_write.DualWriteMirror()
    assert mirror._ensure() is False
    assert mirror._primary is None and mirror._ops is None and not mirror._ready
    assert len(opened) == 2
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):  # closed
            conn.execute("SELECT 1")


def test_negative_control_the_unlocked_setup_ran_twice(paths, monkeypatch):
    """The old ``_ensure``: no lock, connections published as they opened."""

    def old_ensure(self):
        if self._ready:
            return True
        self._primary = sqlite3.connect(str(paths / "memory_state.db"), check_same_thread=False)
        schema_v2.ensure_primary_schema(self._primary)
        self._ops = sqlite3.connect(str(paths / "memory_ops.db"), check_same_thread=False)
        schema_v2.ensure_ops_schema(self._ops)
        self._ready = True
        return True

    runs, replaced = _first_writers(monkeypatch, old_ensure)
    assert runs > 1 and replaced >= 1
