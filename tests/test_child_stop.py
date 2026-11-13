"""A backup tool ended by Kazma's own stop is interrupted, never failed.

At an ordinary reload on 2026-11-12 the guard's CTRL_BREAK reached the
server's whole process group, pg_dump in it quit ("pg_dump: terminated by
user", exit 0xC000013A), and Kazma paged "Postgres dump failed" -- twice that
day. ``kazma_core.child_stop.ended_by_kazmas_stop`` is the one answer: a stop's
exit code, and Kazma stopping within a short wait (the child sees the signal a
moment before the server marks its stop). The dump and the weekly base backup
read it. Each case runs a real child; on the old code the interrupted cases
fail (an ended dump was a failure, and it paged), and the cases that must
still fail or page are the other side.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from kazma_core import child_stop, shutdown
from kazma_core.backup import wal_archive
from kazma_core.db import pg_backup
from kazma_core.migration import pg_bridge

_STOP_CHILD = (
    "import os, signal, sys\n"
    "if os.name == 'nt':\n"
    "    os._exit(0xC000013A - 2**32)  # STATUS_CONTROL_C_EXIT, as a C int\n"
    "os.kill(os.getpid(), signal.SIGTERM)\n"
)


def _stopped_child_code() -> int:
    """How a child ends when a console stop reaches it, on this system."""
    return subprocess.run([sys.executable, "-c", _STOP_CHILD], check=False).returncode


@pytest.fixture(autouse=True)
def _no_stop_left_behind():
    shutdown.reset_shutdown()
    yield
    shutdown.reset_shutdown()


def test_a_stopped_child_while_kazma_stops_is_interrupted():
    code = _stopped_child_code()
    assert code in child_stop.STOP_EXIT_CODES, code
    shutdown.signal_shutdown()
    assert child_stop.ended_by_kazmas_stop(code)


def test_the_stop_marked_a_moment_after_the_child_ended_still_counts():
    code = _stopped_child_code()
    threading.Timer(0.2, shutdown.signal_shutdown).start()
    assert child_stop.ended_by_kazmas_stop(code, wait_s=3.0)


def test_a_stop_kazma_did_not_make_is_a_failure():
    assert not child_stop.ended_by_kazmas_stop(_stopped_child_code(), wait_s=0.2)


def test_an_ordinary_failure_while_kazma_stops_is_a_failure():
    shutdown.signal_shutdown()
    assert not child_stop.ended_by_kazmas_stop(1)
    assert not child_stop.ended_by_kazmas_stop(None)


# -- the database dump --------------------------------------------------------------------------


def _fake_pg_dump(tmp_path: Path, monkeypatch, body: str) -> None:
    script = tmp_path / "fake_pg_dump.py"
    script.write_text(body, encoding="utf-8")
    monkeypatch.setattr(pg_bridge, "resolve_pg_dump", lambda: [sys.executable, str(script)])


def test_a_dump_kazma_s_stop_ended_is_interrupted(tmp_path, monkeypatch):
    _fake_pg_dump(tmp_path, monkeypatch, _STOP_CHILD)
    shutdown.signal_shutdown()
    with pytest.raises(pg_bridge.PgBridgeError) as raised:
        pg_bridge.dump_database("postgresql://kazma@127.0.0.1:5432/kazma", tmp_path / "out.dump")
    assert raised.value.interrupted
    assert not (tmp_path / "out.dump").exists()


def test_a_dump_that_failed_is_not_interrupted(tmp_path, monkeypatch):
    _fake_pg_dump(tmp_path, monkeypatch, "import sys; sys.stderr.write('connection refused'); sys.exit(1)\n")
    shutdown.signal_shutdown()
    with pytest.raises(pg_bridge.PgBridgeError) as raised:
        pg_bridge.dump_database("postgresql://kazma@127.0.0.1:5432/kazma", tmp_path / "out.dump")
    assert not raised.value.interrupted


def _backup_ready(tmp_path: Path, monkeypatch, dump) -> None:
    monkeypatch.setattr(pg_backup, "pg_backup_enabled", lambda: True)
    monkeypatch.setattr("kazma_core.db.backend.get_database_url", lambda: "postgresql://k@127.0.0.1/k")
    monkeypatch.setattr(pg_backup, "pg_backup_dir", lambda: tmp_path / "pg")
    monkeypatch.setattr(pg_bridge, "dump_database", dump)


def test_the_scheduled_dump_says_interrupted_and_pages_nobody(tmp_path, monkeypatch):
    def dump(*_a, **_k):
        raise pg_bridge.PgBridgeError("pg_dump failed (exit 3221225786): pg_dump: terminated by user",
                                      interrupted=True)

    _backup_ready(tmp_path, monkeypatch, dump)
    assert pg_backup.perform_pg_backup() is None
    assert pg_backup.last_pg_backup_interrupted()

    import asyncio

    from kazma_core.memory import worker_bootstrap

    pages = []
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", lambda *a, **k: pages.append(a))
    assert asyncio.run(worker_bootstrap._handle_native_pg_backup({})) is False  # runs again
    assert pages == []


def test_negative_control_a_real_dump_failure_still_pages(tmp_path, monkeypatch):
    def dump(*_a, **_k):
        raise pg_bridge.PgBridgeError("pg_dump failed (exit 1): connection refused")

    _backup_ready(tmp_path, monkeypatch, dump)
    import asyncio

    from kazma_core.memory import worker_bootstrap

    pages = []
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", lambda *a, **k: pages.append(a))
    assert asyncio.run(worker_bootstrap._handle_native_pg_backup({})) is False
    assert not pg_backup.last_pg_backup_interrupted()
    assert [p[0] for p in pages] == ["backup.pg_dump"]


# -- the weekly base backup ---------------------------------------------------------------------


def _record(tmp_path: Path) -> dict:
    return {"bin_dir": str(tmp_path), "os": "windows" if os.name == "nt" else "linux", "port": 5432}


def test_a_base_backup_kazma_s_stop_ended_is_interrupted(tmp_path):
    code = _stopped_child_code()
    shutdown.signal_shutdown()

    def run(*_a, **_k):
        return subprocess.CompletedProcess([], code, "", "pg_basebackup: terminated")

    with pytest.raises(child_stop.StoppedByKazma):
        wal_archive._base_backup(_record(tmp_path), "pw", run=run, root=tmp_path / "base")
    assert not list((tmp_path / "base").glob("*"))


def test_a_base_backup_that_failed_still_fails(tmp_path):
    shutdown.signal_shutdown()

    def run(*_a, **_k):
        return subprocess.CompletedProcess([], 1, "", "pg_basebackup: could not connect")

    with pytest.raises(RuntimeError) as raised:
        wal_archive._base_backup(_record(tmp_path), "pw", run=run, root=tmp_path / "base")
    assert not isinstance(raised.value, child_stop.StoppedByKazma)
