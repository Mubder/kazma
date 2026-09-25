"""The restore rehearsal: off by default, scratch-only, self-cleaning, honest.

Reading every block of a dump is not restoring it. ``restore_rehearsal``
restores the newest dump into ``kazma_restore_rehearsal_<epoch>`` on the same
server, checks Kazma's tables and settings came back, and drops it. Because it
is the one backup check that writes to the database server, its guard rails
are tested as hard as its happy path.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from kazma_core.backup import restore_rehearsal as rr
from kazma_core.backup.restore_drill import DrillResult

NOW = 1_790_000_000.0


# ── opt-in ────────────────────────────────────────────────────────────────


class _Store:
    def __init__(self, value):
        self.value = value

    def get(self, key, default=None):
        return self.value if key == "backups.pg.restore_rehearsal" else default


@pytest.fixture
def on_postgres(monkeypatch):
    monkeypatch.setattr("kazma_core.db.backend.is_postgres", lambda: True)
    monkeypatch.delenv("KAZMA_PG_RESTORE_REHEARSAL", raising=False)


def test_off_by_default(on_postgres, monkeypatch):
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Store(None))
    assert rr.rehearsal_enabled() is False


def test_the_setting_opts_in_and_the_env_can_veto_it(on_postgres, monkeypatch):
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Store(True))
    assert rr.rehearsal_enabled() is True
    monkeypatch.setenv("KAZMA_PG_RESTORE_REHEARSAL", "0")
    assert rr.rehearsal_enabled() is False


def test_the_env_alone_opts_in(on_postgres, monkeypatch):
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Store(False))
    monkeypatch.setenv("KAZMA_PG_RESTORE_REHEARSAL", "1")
    assert rr.rehearsal_enabled() is True


def test_never_on_sqlite(monkeypatch):
    monkeypatch.setattr("kazma_core.db.backend.is_postgres", lambda: False)
    monkeypatch.setenv("KAZMA_PG_RESTORE_REHEARSAL", "1")
    assert rr.rehearsal_enabled() is False


# ── guard rails ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("name,live", [
    ("kazma", "kazma"),
    ("postgres", "kazma"),
    ("kazma_restore_rehearsal_", "kazma"),
    ("kazma_restore_rehearsal_12345", "kazma"),
    ("kazma_restore_rehearsal_1790000000; DROP DATABASE kazma", "kazma"),
    ("kazma_restore_rehearsal_1790000000", "kazma_restore_rehearsal_1790000000"),
])
def test_the_guard_refuses_anything_but_a_scratch_name(name, live):
    with pytest.raises(rr._RehearsalRefused):
        rr._assert_scratch(name, live)


def test_the_guard_accepts_a_scratch_name():
    rr._assert_scratch(rr._scratch_name(NOW), "kazma")


class _Info:
    server_version = 160000


class _Conn:
    """Records statements; serves pg_database rows."""

    def __init__(self, names):
        self.names = names
        self.executed: list[str] = []
        self.info = _Info()

    def execute(self, query, params=None):
        self.executed.append(repr(query))
        rows = [(n,) for n in self.names]

        class _R:
            def fetchall(self_inner):
                return rows

        return _R()


def test_stale_cleanup_drops_only_old_rehearsal_databases():
    old = rr._scratch_name(NOW - 3 * 86400)
    fresh = rr._scratch_name(NOW - 60)
    conn = _Conn(["kazma", old, fresh, "kazma_restore_rehearsal_x", "other_app"])
    dropped = rr._drop_stale(conn, "kazma", NOW)
    assert dropped == [old]
    drops = [q for q in conn.executed if "DROP DATABASE" in q]
    assert len(drops) == 1 and old in drops[0] and "FORCE" in drops[0]


def test_the_scratch_dsn_changes_only_the_database():
    dsn = "postgresql://u:p@db.example:5433/kazma?sslmode=require"
    out = rr._with_dbname(dsn, rr._scratch_name(NOW))
    assert out == f"postgresql://u:p@db.example:5433/{rr._scratch_name(NOW)}?sslmode=require"
    assert rr._dbname(dsn) == "kazma"


def test_missing_createdb_is_unverified_not_a_failed_backup(monkeypatch, tmp_path):
    import psycopg
    from contextlib import contextmanager

    class _Denied(_Conn):
        def execute(self, query, params=None):
            if "CREATE DATABASE" in repr(query):
                raise psycopg.errors.InsufficientPrivilege("permission denied to create database")
            return super().execute(query, params)

    @contextmanager
    def admin(_dsn):
        yield _Denied([])

    monkeypatch.setattr(rr, "_admin", admin)
    res = DrillResult(backup_dir="(test)")
    rr.rehearse_pg_restore(tmp_path / "x.dump", "postgresql://kazma:k@h:5432/kazma", res, now=NOW)
    (check,) = res.checks
    assert check["check"] == "postgres:restore" and check["status"] == "unverified"
    assert "CREATEDB" in check["detail"]


# ── wiring into the weekly deep drill ─────────────────────────────────────


def _deep(monkeypatch, tmp_path, *, data_ok: bool, enabled: bool) -> list[str]:
    from kazma_core.backup import restore_drill

    calls: list[str] = []
    dump = tmp_path / "pg_shared_1.dump"
    dump.write_bytes(b"PGDMP")
    monkeypatch.setattr(restore_drill, "_check_pg_data_section",
                        lambda d, res: res.add("postgres:data", data_ok, "stub"))
    monkeypatch.setattr(restore_drill, "_check_restic_data", lambda res: None)
    monkeypatch.setattr(rr, "rehearsal_enabled", lambda: enabled)
    monkeypatch.setattr(rr, "rehearse_pg_restore", lambda d, dsn, res: calls.append(str(d)))
    monkeypatch.setattr("kazma_core.db.backend.get_database_url", lambda: "postgresql://u:p@h/kazma")
    restore_drill.run_deep_drill(pg_dump=dump)
    return calls


def test_the_deep_drill_does_not_rehearse_when_off(monkeypatch, tmp_path):
    assert _deep(monkeypatch, tmp_path, data_ok=True, enabled=False) == []


def test_the_deep_drill_rehearses_a_clean_archive_when_on(monkeypatch, tmp_path):
    assert len(_deep(monkeypatch, tmp_path, data_ok=True, enabled=True)) == 1


def test_a_corrupt_archive_is_not_rehearsed(monkeypatch, tmp_path):
    assert _deep(monkeypatch, tmp_path, data_ok=False, enabled=True) == []


# ── the real thing, against a real Postgres ───────────────────────────────


def _live_dsn() -> str:
    from kazma_core.db.backend import get_database_url, is_postgres

    dsn = get_database_url() or ""
    if not (is_postgres() and dsn.startswith(("postgres://", "postgresql://"))):
        pytest.skip("needs a real Postgres (the CI Postgres job, or a throwaway container)")
    return dsn


def _rehearsal_dbs(dsn: str) -> list[str]:
    with rr._admin(dsn) as conn:
        return [r[0] for r in conn.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE %s", (rr.SCRATCH_PREFIX + "%",)
        ).fetchall()]


@pytest.mark.postgres
def test_a_real_dump_restores_into_scratch_and_the_scratch_is_dropped(tmp_path):
    dsn = _live_dsn()
    from kazma_core.config_store import get_config_store
    from kazma_core.db.pg_backup import KAZMA_PG_TABLES
    from kazma_core.migration.pg_bridge import PgBridgeError, PgToolNotFound, dump_database

    get_config_store().set("rehearsal.probe", "present")  # kazma_settings is not empty
    try:
        dump = dump_database(dsn, tmp_path / "pg_shared_test.dump", tables=list(KAZMA_PG_TABLES))
    except PgToolNotFound as exc:
        pytest.skip(f"pg_dump unavailable: {exc}")
    except PgBridgeError as exc:
        if "version mismatch" in str(exc):
            pytest.skip(f"pg_dump is older than the server: {exc}")
        raise

    res = DrillResult(backup_dir="(test)")
    rr.rehearse_pg_restore(Path(dump), dsn, res)
    (check,) = res.checks
    assert check["status"] == "passed", check
    assert "settings rows" in check["detail"]
    assert _rehearsal_dbs(dsn) == [], "the scratch database must be dropped"


@pytest.mark.postgres
def test_a_leftover_scratch_database_is_removed_and_nothing_else():
    dsn = _live_dsn()
    from psycopg import sql

    old = rr._scratch_name(time.time() - 3 * 86400)
    bystander = "kazma_rehearsal_bystander_" + os.urandom(3).hex()
    with rr._admin(dsn) as conn:
        for name in (old, bystander):
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        with rr._admin(dsn) as conn:
            dropped = rr._drop_stale(conn, rr._dbname(dsn), time.time())
        assert old in dropped
        with rr._admin(dsn) as conn:
            names = {r[0] for r in conn.execute("SELECT datname FROM pg_database").fetchall()}
        assert old not in names and bystander in names
    finally:
        with rr._admin(dsn) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(bystander)))
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(old)))
