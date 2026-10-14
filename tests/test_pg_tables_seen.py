"""A table missing at boot was dropped, or this build adds it: they differ.

Live 2026-10-14: the first boot of the Documents hardening logged

    CRITICAL [PG-BACKUP] REQUIRED POSTGRES TABLES MISSING:
    document_intake_reservations, document_intake_mutex ... Restore the latest
    backup with: python scripts/pg_backup.py restore --latest

over two tables that had never existed. The boot check runs before the stores
start (a store recreates a dropped table empty, which would hide the drop the
check exists for), so a table a new build adds is missing at that moment and
created by its store seconds later. Restoring would have rolled back every
other table. The install now keeps a record of Kazma's tables it has found in
its Postgres; only a recorded table that is gone is a drop.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from kazma_core.db import pg_backup

_LOGGER = "kazma_core.db.pg_backup"
_NEW = ["document_intake_reservations", "document_intake_mutex"]


class _NoConfigStore:
    def get(self, key: str, default=None):
        return default


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    import kazma_core.config_store as config_store

    monkeypatch.setattr(config_store, "get_config_store", lambda: _NoConfigStore())
    record = tmp_path / "pg_tables_seen.json"
    monkeypatch.setattr(pg_backup, "_seen_path", lambda: record)
    monkeypatch.setattr(pg_backup, "latest_pg_backup", lambda: tmp_path / "pg_shared_1.dump")
    return record


class _Pool:
    """The shared pool's shape: dict rows keyed by column name."""

    def __init__(self, tables, *, broken: bool = False) -> None:
        self.tables = list(tables)
        self.broken = broken

    def connection(self):
        if self.broken:
            raise RuntimeError("pool down")
        pool = self

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                class _Cur:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                    def execute(self, sql):
                        pass

                    def fetchall(self):
                        return [{"tablename": t} for t in pool.tables]

                return _Cur()

        return _Conn()


def _before_the_upgrade() -> list[str]:
    return [t for t in pg_backup.KAZMA_PG_TABLES if t not in _NEW]


def _records(caplog, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _LOGGER and r.levelno == level]


def test_the_check_records_the_kazma_tables_it_finds() -> None:
    pg_backup.verify_required_pg_tables(_Pool(["documents", "kca_users"]))
    assert pg_backup._tables_seen() == {"documents"}  # a foreign app's table is not recorded
    pg_backup.verify_required_pg_tables(_Pool(["kazma_settings"]))
    assert pg_backup._tables_seen() == {"documents", "kazma_settings"}  # never removes one


def test_a_table_this_build_adds_is_not_a_drop(caplog) -> None:
    """The live witness: a record from before the upgrade, the two new tables missing."""
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    missing = pg_backup.verify_required_pg_tables(_Pool(_before_the_upgrade()))
    assert missing == _NEW
    caplog.clear()
    later = pg_backup.report_missing_tables(missing)
    assert later == _NEW
    assert not _records(caplog, logging.CRITICAL)
    assert any("tables this build adds" in m for m in _records(caplog, logging.INFO))


def _old_report(missing: list[str]) -> list[str]:
    """The boot code before 2026-10-14: every missing table a drop."""
    logging.getLogger(_LOGGER).critical(
        "[PG-BACKUP] REQUIRED POSTGRES TABLES MISSING: %s.", ", ".join(missing))
    return []


def test_the_old_check_called_a_new_table_a_drop(caplog) -> None:
    """Negative control: the same scenario through the old rule."""
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    missing = pg_backup.verify_required_pg_tables(_Pool(_before_the_upgrade()))
    caplog.clear()
    _old_report(missing)
    assert _records(caplog, logging.CRITICAL)


def test_a_recorded_table_that_is_gone_was_dropped(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    pg_backup.verify_required_pg_tables(_Pool(pg_backup.KAZMA_PG_TABLES))
    missing = pg_backup.verify_required_pg_tables(
        _Pool([t for t in pg_backup.KAZMA_PG_TABLES if t != "kazma_chat_sessions"]))
    caplog.clear()
    assert pg_backup.report_missing_tables(missing) == []
    critical = _records(caplog, logging.CRITICAL)
    assert len(critical) == 1
    assert "kazma_chat_sessions" in critical[0] and "restore --latest" in critical[0]


def test_without_a_record_it_warns_and_looks_again(caplog) -> None:
    """The first boot of this code, or a new machine: no way to tell."""
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    assert pg_backup.report_missing_tables(["documents"]) == ["documents"]
    assert not _records(caplog, logging.CRITICAL)
    assert any("no record" in m for m in _records(caplog, logging.WARNING))


def test_an_unreadable_record_is_no_record(_isolated: Path, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    _isolated.write_text("{not json", encoding="utf-8")
    assert pg_backup._tables_seen() is None
    assert any("unreadable" in m for m in _records(caplog, logging.WARNING))


def test_the_second_look_finds_what_the_stores_created(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    still = asyncio.run(pg_backup.recheck_new_tables(_Pool(pg_backup.KAZMA_PG_TABLES), _NEW, delay=0))
    assert still == []
    assert not _records(caplog, logging.WARNING)
    assert set(_NEW) <= (pg_backup._tables_seen() or set())


def test_the_second_look_warns_about_a_table_still_missing(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    still = asyncio.run(pg_backup.recheck_new_tables(_Pool(_before_the_upgrade()), _NEW, delay=0))
    assert still == _NEW
    assert any("still missing" in m for m in _records(caplog, logging.WARNING))


def test_the_second_look_says_when_it_could_not_check(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    assert asyncio.run(pg_backup.recheck_new_tables(_Pool([], broken=True), _NEW, delay=0)) is None
    assert any("could not check again" in m for m in _records(caplog, logging.WARNING))


def test_the_boot_code_reports_through_the_one_rule() -> None:
    """app.py hands the finding to ``report_missing_tables``; it does not
    decide "dropped" itself again."""
    src = (Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "app.py").read_text(
        encoding="utf-8")
    assert "REQUIRED POSTGRES TABLES MISSING" not in src
    assert "report_missing_tables" in src and "recheck_new_tables" in src
