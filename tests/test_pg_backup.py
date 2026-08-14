"""Unit tests for kazma_core.db.pg_backup (no live Postgres needed).

The pg_dump side is faked by monkeypatching the pg_bridge entry point, so
these tests cover the module's own logic: SoT parity, config kill-switches,
atomic+validated dump handling, retention pruning, and schema verification.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.db import pg_backup


class _NoConfigStore:
    """ConfigStore stub — unit tests must never touch the real settings store
    (the dev .env points at a live Postgres, and get_config_store() would
    connect/block on it)."""

    def get(self, key: str, default=None):
        return default


@pytest.fixture(autouse=True)
def _isolated_config_store(monkeypatch: pytest.MonkeyPatch) -> None:
    import kazma_core.config_store as config_store

    monkeypatch.setattr(config_store, "get_config_store", lambda: _NoConfigStore())


# ── fakes ──────────────────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, tables: list[str]) -> None:
        self._tables = tables

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def execute(self, sql: str) -> None:  # noqa: ARG002
        pass

    def fetchall(self) -> list[dict[str, str]]:
        # Mirror the real PostgresPool (psycopg dict_row factory): rows are
        # dict-like keyed by column name, NOT tuple-indexed. Indexing row[0]
        # would raise KeyError: 0 — this is what verify_required_pg_tables
        # must handle.
        return [{"tablename": t} for t in self._tables]


class _FakeConn:
    def __init__(self, tables: list[str]) -> None:
        self._tables = tables

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._tables)


class _FakePool:
    def __init__(self, tables: list[str] | None = None, *, broken: bool = False) -> None:
        self._tables = tables or []
        self._broken = broken

    def connection(self) -> _FakeConn:
        if self._broken:
            raise RuntimeError("pool down")
        return _FakeConn(self._tables)


def _fake_dump_database(dsn: str, out_path, *, progress=None, tables=None, magic: bytes = b"PGDMP") -> None:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(magic + b"\x00" * 2048)


# ── SoT parity ─────────────────────────────────────────────────────────────


def test_kazma_pg_tables_cover_all_shared_state() -> None:
    """Every table that lives in Postgres must be in the backup SoT list."""
    expected = {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
        "kazma_settings",
        "kazma_chat_sessions",
        "kazma_swarm_tasks",
        "kazma_swarm_worker_metrics",
        "kazma_platform_users",
        "kazma_web_sessions",
        "document_jobs",
        "document_job_events",
    }
    assert set(pg_backup.KAZMA_PG_TABLES) == expected
    # No duplicates — a dup would produce redundant pg_dump -t flags.
    assert len(pg_backup.KAZMA_PG_TABLES) == len(set(pg_backup.KAZMA_PG_TABLES))


# ── config ─────────────────────────────────────────────────────────────────


def test_get_pg_backup_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAZMA_PG_BACKUP_ENABLED", raising=False)
    monkeypatch.delenv("KAZMA_PG_BACKUP_RETENTION", raising=False)
    cfg = pg_backup.get_pg_backup_config()
    assert cfg == {"enabled": True, "retention": 7}


def test_get_pg_backup_config_env_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PG_BACKUP_ENABLED", "0")
    monkeypatch.setenv("KAZMA_PG_BACKUP_RETENTION", "3")
    cfg = pg_backup.get_pg_backup_config()
    assert cfg == {"enabled": False, "retention": 3}


def test_get_pg_backup_config_retention_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PG_BACKUP_RETENTION", "0")
    assert pg_backup.get_pg_backup_config()["retention"] == 1


def test_pg_backup_enabled_false_on_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    monkeypatch.delenv("KAZMA_PG_BACKUP_ENABLED", raising=False)
    assert pg_backup.pg_backup_enabled() is False


def test_pg_backup_enabled_true_on_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_DB_BACKEND", "postgres")
    monkeypatch.setenv("KAZMA_DATABASE_URL", "postgresql://u:p@127.0.0.1:5433/kazma")
    monkeypatch.delenv("KAZMA_PG_BACKUP_ENABLED", raising=False)
    assert pg_backup.pg_backup_enabled() is True


def test_pg_backup_enabled_kill_switch_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_DB_BACKEND", "postgres")
    monkeypatch.setenv("KAZMA_PG_BACKUP_ENABLED", "false")
    assert pg_backup.pg_backup_enabled() is False


# ── dump ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def _pg_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAZMA_DB_BACKEND", "postgres")
    monkeypatch.setenv("KAZMA_DATABASE_URL", "postgresql://u:p@127.0.0.1:5433/kazma")
    monkeypatch.delenv("KAZMA_PG_BACKUP_ENABLED", raising=False)
    monkeypatch.setattr(pg_backup, "pg_backup_dir", lambda: tmp_path)
    return tmp_path


def test_perform_pg_backup_writes_atomic_validated_dump(
    _pg_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = _pg_env
    from kazma_core.migration import pg_bridge

    monkeypatch.setattr(pg_bridge, "dump_database", _fake_dump_database)

    result = pg_backup.perform_pg_backup(retention=3)
    assert result is not None and result.exists()
    assert result.name.startswith("pg_shared_") and result.suffix == ".dump"
    assert result.read_bytes()[:5] == b"PGDMP"
    # No .tmp leftovers after the atomic rename.
    assert not list(out_dir.glob(".*.tmp"))
    assert len(list(out_dir.glob("pg_shared_*.dump"))) == 1


def test_perform_pg_backup_discards_corrupt_dump(
    _pg_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kazma_core.migration import pg_bridge

    def _corrupt(dsn: str, out_path, *, progress=None, tables=None) -> None:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"GARBAGE" * 512)

    monkeypatch.setattr(pg_bridge, "dump_database", _corrupt)
    result = pg_backup.perform_pg_backup(retention=3)
    assert result is None
    assert not list(_pg_env.glob("pg_shared_*.dump"))


def test_perform_pg_backup_noop_when_disabled(
    _pg_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAZMA_PG_BACKUP_ENABLED", "0")
    from kazma_core.migration import pg_bridge

    called: list[bool] = []
    monkeypatch.setattr(
        pg_bridge, "dump_database",
        lambda *a, **k: called.append(True),
    )
    assert pg_backup.perform_pg_backup() is None
    assert not called


def test_perform_pg_backup_passes_table_filter(
    _pg_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kazma_core.migration import pg_bridge

    seen: dict[str, object] = {}

    def _capture(dsn: str, out_path, *, progress=None, tables=None) -> None:
        seen["tables"] = tables
        _fake_dump_database(dsn, out_path)

    monkeypatch.setattr(pg_bridge, "dump_database", _capture)
    pg_backup.perform_pg_backup(retention=3)
    # The whole point of the incident fix: dump ONLY Kazma's tables, never
    # a foreign app's tables sharing the same database.
    assert seen["tables"] == pg_backup.KAZMA_PG_TABLES


# ── retention ──────────────────────────────────────────────────────────────


def test_prune_pg_backups_keeps_newest_n(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pg_backup, "pg_backup_dir", lambda: tmp_path)
    for i in range(5):
        f = tmp_path / f"pg_shared_{1000 + i}.dump"
        f.write_bytes(b"PGDMP" + b"\x00" * 128)
        f.touch()  # ensure mtime ordering follows creation order

    deleted = pg_backup.prune_pg_backups(retention=2)
    assert deleted == 3
    remaining = sorted(tmp_path.glob("pg_shared_*.dump"))
    assert [f.name for f in remaining] == ["pg_shared_1003.dump", "pg_shared_1004.dump"]


def test_latest_pg_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pg_backup, "pg_backup_dir", lambda: tmp_path)
    assert pg_backup.latest_pg_backup() is None
    old = tmp_path / "pg_shared_1000.dump"
    old.write_bytes(b"PGDMP" + b"\x00" * 128)
    new = tmp_path / "pg_shared_2000.dump"
    new.write_bytes(b"PGDMP" + b"\x00" * 128)
    new.touch()
    assert pg_backup.latest_pg_backup() == new


# ── schema verification ────────────────────────────────────────────────────


def test_verify_required_pg_tables_reports_missing() -> None:
    present = ["checkpoints", "kazma_settings", "checkpoint_blobs"]
    missing = pg_backup.verify_required_pg_tables(_FakePool(present))
    assert missing is not None
    assert "checkpoints" not in missing
    assert "kazma_chat_sessions" in missing
    assert "document_jobs" in missing
    assert len(missing) == len(pg_backup.KAZMA_PG_TABLES) - len(present)


def test_verify_required_pg_tables_all_present() -> None:
    missing = pg_backup.verify_required_pg_tables(_FakePool(pg_backup.KAZMA_PG_TABLES))
    assert missing == []


def test_verify_required_pg_tables_none_when_pool_down() -> None:
    # "None" means unknown — callers must not interpret it as all-present.
    assert pg_backup.verify_required_pg_tables(_FakePool(broken=True)) is None
