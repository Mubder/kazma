"""HA ownership refuses split state and stops on a lost database session."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from kazma_core.runtime_ownership import _PostgresRuntimeLease, RuntimeOwnership, _volume_identity
from kazma_core.runtime_writer import RuntimeWriterBusy


def test_ha_requires_postgres_and_releases_local_fence(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_RUNTIME_HA", "1")
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    with pytest.raises(RuntimeWriterBusy, match="requires a direct Postgres"):
        RuntimeOwnership(tmp_path).acquire()
    # A failed HA admission must not leave a process-local reference behind.
    import kazma_core.runtime_writer as writer
    assert tmp_path.resolve() not in writer._owners


def test_volume_identity_is_stable_and_corruption_refuses_boot(tmp_path):
    first = _volume_identity(tmp_path)
    assert _volume_identity(tmp_path) == first
    (tmp_path / ".runtime-state-id").write_text("partial", encoding="ascii")
    with pytest.raises(RuntimeWriterBusy, match="Invalid runtime state identity"):
        _volume_identity(tmp_path)


@pytest.fixture
def pg_dsn():
    from kazma_core.db.backend import get_database_url, is_postgres

    dsn = get_database_url()
    if not is_postgres() or not dsn:
        pytest.skip("requires disposable Postgres with KAZMA_TEST_ALLOW_REAL_DB=1")
    psycopg = pytest.importorskip("psycopg")

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS kazma_runtime_volume")
    try:
        yield dsn
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS kazma_runtime_volume")


@pytest.mark.postgres
def test_database_refuses_second_root_then_wrong_volume(pg_dsn, tmp_path):
    root = tmp_path / "owner"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    owner = _PostgresRuntimeLease(pg_dsn, root)
    owner.acquire()
    try:
        with pytest.raises(RuntimeWriterBusy, match="Another Kazma runtime"):
            _PostgresRuntimeLease(pg_dsn, other).acquire()
    finally:
        owner.release()
    with pytest.raises(RuntimeWriterBusy, match="different state volume"):
        _PostgresRuntimeLease(pg_dsn, other).acquire()
    # Complete restore carries the pairing file with the rest of the volume.
    (other / ".runtime-state-id").write_bytes((root / ".runtime-state-id").read_bytes())
    successor = _PostgresRuntimeLease(pg_dsn, other)
    successor.acquire()
    successor.release()


@pytest.mark.postgres
def test_session_loss_calls_fail_stop_without_reconnecting(pg_dsn, tmp_path):
    psycopg = pytest.importorskip("psycopg")

    lost = threading.Event()
    owner = _PostgresRuntimeLease(pg_dsn, tmp_path, on_loss=lost.set)
    owner.acquire()
    original_connection = owner._conn
    backend_pid = original_connection.info.backend_pid
    try:
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            assert conn.execute("SELECT pg_terminate_backend(%s)", (backend_pid,)).fetchone()[0]
        assert lost.wait(12)
        assert owner._lost.is_set()
        assert owner._conn is original_connection
        assert original_connection.closed
    finally:
        owner.release()


@pytest.mark.postgres
def test_process_death_allows_paired_successor(pg_dsn, tmp_path):
    core = str(Path(__file__).resolve().parents[1] / "kazma-core")
    child_code = """
import os, sys
from pathlib import Path
from kazma_core.runtime_ownership import RuntimeOwnership
owner = RuntimeOwnership(Path(sys.argv[1]))
owner.acquire()
Path(sys.argv[1], 'owner-ready').write_text('ready')
print('owned', flush=True)
sys.stdin.readline()
owner.release()
"""
    env = dict(os.environ, PYTHONPATH=core, KAZMA_RUNTIME_HA="1", KAZMA_DATABASE_URL=pg_dsn, KAZMA_DB_BACKEND="postgres")
    child = subprocess.Popen([sys.executable, "-c", child_code, str(tmp_path)], env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        # Poll through communicate with a deadline, never an unbounded readline.
        import time
        deadline = time.monotonic() + 12
        identity = tmp_path / "owner-ready"
        while not identity.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        contender = _PostgresRuntimeLease(pg_dsn, tmp_path)
        with pytest.raises(RuntimeWriterBusy, match="Another Kazma runtime"):
            contender.acquire()
        child.kill()
        out, err = child.communicate(timeout=12)
        assert "owned" in out, err
        successor = RuntimeOwnership(tmp_path)
        # RuntimeOwnership itself is also tested in the marked suite.
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("KAZMA_RUNTIME_HA", "1")
            successor.acquire()
            successor.release()
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=12)


@pytest.mark.postgres
def test_default_loss_action_exits_entire_process(pg_dsn, tmp_path):
    psycopg = pytest.importorskip("psycopg")

    core = str(Path(__file__).resolve().parents[1] / "kazma-core")
    code = """
import sys, time
from pathlib import Path
from kazma_core.runtime_ownership import _PostgresRuntimeLease
owner = _PostgresRuntimeLease(sys.argv[1], Path(sys.argv[2]))
owner.acquire()
Path(sys.argv[2], 'owner-ready').write_text(str(owner._conn.info.backend_pid))
print(owner._conn.info.backend_pid, flush=True)
time.sleep(30)
"""
    child = subprocess.Popen([sys.executable, "-c", code, pg_dsn, str(tmp_path)],
                             env=dict(os.environ, PYTHONPATH=core), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        # Wait for completed admission, avoiding a blocking pipe read.
        import time
        deadline = time.monotonic() + 12
        ready = tmp_path / "owner-ready"
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists()
        pid = int(ready.read_text())
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute("SELECT pg_terminate_backend(%s)", (pid,))
        _, err = child.communicate(timeout=12)
        assert child.returncode == 75, err
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=12)
