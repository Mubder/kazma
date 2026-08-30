"""The manifest must report the Postgres dump, not assert it.

Two defects, found 2026-08-30 by an operator asking why their main database
did not appear in a backup:

1. ``perform_universal_backup`` skips the Postgres dump on purpose -- the
   ``native_pg_backup`` task owns it -- but only the 24-hourly sweep ever
   enqueued that task. ``POST /api/backup/now`` therefore backed up 25 SQLite
   databases, reported "Done", and never touched Postgres.

2. The manifest recorded ``{"ok": True, "note": "handled by
   native_pg_backup task"}`` as a literal. It said the main database was
   healthy whether the newest dump was minutes old, days old, or absent.

The second is the dangerous one: it is indistinguishable from a working
backup right up until a restore.
"""

from __future__ import annotations

import time

import pytest
from kazma_core.backup import universal

from tests._module_source import module_source


@pytest.fixture
def pg_dir(tmp_path, monkeypatch):
    """An isolated backups/pg directory with Postgres reported as in use."""
    d = tmp_path / "backups" / "pg"
    d.mkdir(parents=True)
    monkeypatch.setattr(universal, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "kazma_core.db.pg_backup.pg_backup_enabled", lambda: True, raising=False
    )
    return d


def _dump(d, name: str, *, age_hours: float, size: int = 4096):
    f = d / name
    f.write_bytes(b"x" * size)
    when = time.time() - age_hours * 3600
    import os

    os.utime(f, (when, when))
    return f


def test_a_missing_dump_is_not_ok(pg_dir):
    """The case the hardcoded literal could never express."""
    state = universal._pg_dump_state()
    assert state["ok"] is False
    assert "no Postgres dump" in state["error"]


def test_a_fresh_dump_reports_its_identity(pg_dir):
    _dump(pg_dir, "pg_shared_1788032484.dump", age_hours=2.0, size=8192)
    state = universal._pg_dump_state()
    assert state["ok"] is True
    assert state["dump"] == "pg_shared_1788032484.dump"
    assert state["size"] == 8192
    assert state["age_hours"] == pytest.approx(2.0, abs=0.2)
    assert state["generations"] == 1


def test_a_stale_dump_fails_and_says_why(pg_dir):
    """A dump older than the sweep interval means a run was missed."""
    _dump(pg_dir, "pg_shared_old.dump", age_hours=40.0)
    state = universal._pg_dump_state()
    assert state["ok"] is False
    assert "stale" in state["error"]
    assert "40" in state["error"] or "39" in state["error"]


def test_the_newest_dump_is_the_one_reported(pg_dir):
    _dump(pg_dir, "pg_shared_older.dump", age_hours=30.0)
    _dump(pg_dir, "pg_shared_newer.dump", age_hours=1.0)
    state = universal._pg_dump_state()
    assert state["dump"] == "pg_shared_newer.dump"
    assert state["ok"] is True
    assert state["generations"] == 2


def test_sqlite_installs_are_skipped_not_failed(tmp_path, monkeypatch):
    """No Postgres to dump is not a broken backup."""
    monkeypatch.setattr(universal, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "kazma_core.db.pg_backup.pg_backup_enabled", lambda: False, raising=False
    )
    state = universal._pg_dump_state()
    assert state["ok"] is True
    assert "skipped" in state


def test_the_state_is_never_raised_into_the_backup(pg_dir, monkeypatch):
    """A reporting failure must not take the whole backup down."""
    monkeypatch.setattr(
        universal, "_data_dir", lambda: (_ for _ in ()).throw(OSError("disk gone"))
    )
    state = universal._pg_dump_state()
    assert state["ok"] is False
    assert "could not inspect" in state["error"]


def test_the_hardcoded_ok_literal_is_gone():
    """Regression guard: the manifest must not assert success again."""
    src = module_source(universal.__file__)
    assert '{"ok": True, "note": "handled by native_pg_backup task"}' not in src, (
        "the Postgres manifest entry is hardcoded again -- it must be measured"
    )
    assert "_pg_dump_state()" in src


def test_manual_backup_enqueues_the_postgres_dump():
    """POST /api/backup/now must cover the main database too."""
    from kazma_ui.routes_direct import backup as backup_routes

    src = module_source(backup_routes.__file__)
    assert "native_pg_backup" in src, (
        "the manual backup button does not enqueue the Postgres dump, so it "
        "reports success without touching the main database"
    )
    assert "pg_backup_enabled" in src, "it must self-disable on SQLite installs"
