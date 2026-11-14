"""A point-in-time restore on a real Postgres (2026-11-14).

The owner's first point-in-time restore of PostgreSQL 18 replayed nothing
past its base backup: ``wal_archive.restore_to`` wrote the Windows copy
command into ``postgresql.auto.conf`` with single backslashes, the file's
reader took them as escapes, and every archived segment was "not found".
The unit tests only ever wrote a Linux ``cp`` command.

Here the product's own pieces run on real servers: a throwaway cluster
(``initdb``, trust sign-in, loopback, a private port) archives its WAL with
``wal_archive.archive_command``, ``wal_archive._base_backup`` takes its base
backup, rows are written before and after a moment, and
``wal_archive.restore_to`` lays the backup into a new folder set to replay to
that moment. The restored server must hold the rows written before it and
none written after. The negative control (Windows) writes the old way: the
restored server cannot start.

Opt-in: ``KAZMA_TEST_PG_UPGRADE_BIN`` (a Postgres ``bin`` folder, 16 or
newer, run as an account that is not an administrator); skipped without.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest
from kazma_core import postgres_upgrade as pgu
from kazma_core.backup import wal_archive
from kazma_core.postgres_service import Platform, _sql_literal

pytestmark = [pytest.mark.timeout(600)]

BIN = (os.environ.get("KAZMA_TEST_PG_UPGRADE_BIN") or "").strip()
OS_NAME = "windows" if os.name == "nt" else "debian"


def _exe(name: str) -> str:
    return str(Path(BIN) / (f"{name}.exe" if os.name == "nt" else name))


def _start(data: Path, port: int) -> bool:
    """``pg_ctl start`` on *port*; True once a connection is accepted. No
    pipes: the server would inherit them, and the wait would never end. Not
    pg_ctl's exit code: it said "started" for a server whose recovery failed
    a moment later (measured here)."""
    subprocess.run([_exe("pg_ctl"), "-D", str(data), "-l", str(_log_path(data)),
                    "-o", f"-p {port} -c listen_addresses=localhost", "-w", "-t", "120", "start"],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    psycopg = pytest.importorskip("psycopg")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with _connect(port) as conn:
                conn.execute("SELECT 1")
            return True
        except psycopg.Error:
            if not (data / "postmaster.pid").exists():
                return False
            time.sleep(0.5)
    return False


def _log_path(data: Path) -> Path:
    return data.parent / f"{data.name}.log"


def _stop(data: Path) -> None:
    if (data / "postmaster.pid").exists():
        subprocess.run([_exe("pg_ctl"), "-D", str(data), "-m", "fast", "-w", "-t", "60", "stop"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def _connect(port: int):
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(f"postgresql://postgres@127.0.0.1:{port}/postgres", autocommit=True, connect_timeout=10)


@pytest.fixture
def archived(tmp_path):
    """A cluster that archived its WAL, its base backup, and the moment to
    restore to: rows ``before`` and ``kept`` were written before it, ``after``
    after it."""
    if not BIN or not Path(BIN).is_dir():
        pytest.skip("set KAZMA_TEST_PG_UPGRADE_BIN to a Postgres bin folder (16 or newer)")
    pytest.importorskip("psycopg")
    primary, archive, bases = tmp_path / "primary", tmp_path / "wal", tmp_path / "base"
    archive.mkdir()
    subprocess.run([_exe("initdb"), "-D", str(primary), "-U", "postgres", "--auth=trust", "-E", "UTF8"],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
    port = pgu._free_port(57100)
    command = wal_archive.archive_command(Platform(os=OS_NAME, manager=None), archive)
    try:
        assert _start(primary, port)
        with _connect(port) as conn:
            conn.execute("ALTER SYSTEM SET archive_mode = 'on'")
            conn.execute(f"ALTER SYSTEM SET archive_command = {_sql_literal(command)}")
        _stop(primary)
        assert _start(primary, port)
        with _connect(port) as conn:
            conn.execute("CREATE TABLE rows_written (label text, at timestamptz DEFAULT now())")
            conn.execute("INSERT INTO rows_written (label) VALUES ('before')")
        base = wal_archive._base_backup({"bin_dir": BIN, "os": OS_NAME, "port": port}, "", root=bases)
        with _connect(port) as conn:
            conn.execute("INSERT INTO rows_written (label) VALUES ('kept')")
            time.sleep(1.5)
            moment = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            time.sleep(1.5)
            conn.execute("INSERT INTO rows_written (label) VALUES ('after')")
            switched = conn.execute("SELECT pg_walfile_name(pg_switch_wal())").fetchone()[0]
            deadline = time.monotonic() + 120
            while not (archive / switched).is_file():
                assert time.monotonic() < deadline, f"segment {switched} was never archived"
                time.sleep(0.5)
        _stop(primary)
        yield {"base": base, "bases": bases, "archive": archive, "moment": moment, "tmp": tmp_path}
    finally:
        _stop(primary)


def test_a_real_restore_replays_the_archive_to_the_moment(archived):
    restored = archived["tmp"] / "restored"
    wal_archive.restore_to(archived["moment"], restored, archived["archive"], os_name=OS_NAME,
                           base_root=archived["bases"])
    port = pgu._free_port(57200)
    try:
        assert _start(restored, port), _log_path(restored).read_text(errors="replace")[-2000:]
        with _connect(port) as conn:
            labels = sorted(r[0] for r in conn.execute("SELECT label FROM rows_written"))
            archiving = conn.execute("SHOW archive_mode").fetchone()[0]
        assert labels == ["before", "kept"], labels
        assert archiving == "off", "a restored copy must never write into the live archive"
    finally:
        _stop(restored)


@pytest.mark.skipif(os.name != "nt", reason="the old writer's fault is a Windows path's backslashes")
def test_negative_control_the_old_writer_cannot_replay(archived, monkeypatch):
    monkeypatch.setattr(wal_archive, "_conf_string", lambda value: f"'{value}'")
    restored = archived["tmp"] / "restored_old"
    wal_archive.restore_to(archived["moment"], restored, archived["archive"], os_name=OS_NAME,
                           base_root=archived["bases"])
    port = pgu._free_port(57300)
    try:
        assert not _start(restored, port)
        log = _log_path(restored).read_text(errors="replace")
        assert "recovery ended before configured recovery target was reached" in log, log[-2000:]
    finally:
        _stop(restored)
