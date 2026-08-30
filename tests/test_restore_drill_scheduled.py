"""The drill has to run, reach the main database, and be heard when it fails.

``restore_drill.py`` could verify a backup from the day it was written, and
never did. Its only non-test reference was the resilience manifest, which
listed it as a mechanism protecting this system. Three defects had therefore
gone unnoticed since it was written:

* ``run_drill`` resolved the backup as ``list_universal_backups()["dir"]``,
  which is a NAME, not a path -- so every hand-run failed instantly with
  "not a directory".
* ``_latest_pg_dump`` imported ``pg_backup._dump_dir``, which does not exist.
  The bare ``except`` turned that AttributeError into None, so the Postgres
  check never ran and the drill reported "29/29 passed" while never looking
  at the 1.67 GB main database.
* Nothing scheduled it, so neither defect could surface.

The last one is why the other two survived.
"""

from __future__ import annotations

import time

import pytest
from kazma_core.backup import restore_drill

from tests._module_source import module_source


def test_the_drill_is_actually_scheduled():
    """The defect that hid the other two."""
    from kazma_core.memory import worker_bootstrap

    src = module_source(worker_bootstrap.__file__)
    assert "drill_scheduler" in src, (
        "nothing starts the restore drill, so it is a documented mechanism "
        "that never fires -- which is indistinguishable from a broken one"
    )
    assert hasattr(restore_drill, "drill_scheduler")
    assert restore_drill.DRILL_INTERVAL_HOURS > 0


def test_the_ledger_can_notice_the_drill_going_quiet():
    from kazma_core.observability.firing_ledger import FIRING_SIGNATURES

    assert any(s.mechanism == "restore drill" for s in FIRING_SIGNATURES), (
        "the weekly sweep cannot report a drill that stopped firing"
    )


def test_latest_pg_dump_uses_a_name_that_exists():
    """The import that silently became 'there is no Postgres dump'."""
    src = module_source(restore_drill.__file__)
    # The import statement specifically -- the name appears in prose above,
    # explaining why it must never be imported again.
    assert "import _dump_dir" not in src, (
        "pg_backup has no _dump_dir; importing it makes the Postgres check "
        "vanish behind an except"
    )
    assert "import pg_backup_dir" in src

    from kazma_core.db import pg_backup

    assert hasattr(pg_backup, "pg_backup_dir")


def test_latest_pg_dump_picks_the_newest(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(
        "kazma_core.db.pg_backup.pg_backup_dir", lambda: tmp_path, raising=False
    )
    for name, age in (("pg_shared_old.dump", 40.0), ("pg_shared_new.dump", 1.0)):
        f = tmp_path / name
        f.write_bytes(b"PGDMP" + b"0" * 2048)
        when = time.time() - age * 3600
        os.utime(f, (when, when))

    assert restore_drill._latest_pg_dump().name == "pg_shared_new.dump"


def test_no_pg_dump_on_a_postgres_install_is_a_failure(tmp_path, monkeypatch):
    """Absence must not pass by omission when Postgres IS the backend."""
    monkeypatch.setattr(restore_drill, "_latest_pg_dump", lambda: None)
    monkeypatch.setattr(
        "kazma_core.db.pg_backup.pg_backup_enabled", lambda: True, raising=False
    )
    monkeypatch.setattr(
        "kazma_core.backup.universal.latest_universal_backup",
        lambda: {"dir": str(tmp_path)},
    )
    (tmp_path / "manifest.json").write_text('{"version": 1}', encoding="utf-8")
    (tmp_path / ".env").write_text("X=1", encoding="utf-8")

    res = restore_drill.run_drill()
    failed = [c["check"] for c in res.failures]
    assert "postgres:dump" in failed, (
        "a Postgres install with no dump reported a clean drill"
    )
    assert res.ok is False


def test_no_pg_dump_on_sqlite_is_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(restore_drill, "_latest_pg_dump", lambda: None)
    monkeypatch.setattr(
        "kazma_core.db.pg_backup.pg_backup_enabled", lambda: False, raising=False
    )
    monkeypatch.setattr(
        "kazma_core.backup.universal.latest_universal_backup",
        lambda: {"dir": str(tmp_path)},
    )
    (tmp_path / "manifest.json").write_text('{"version": 1}', encoding="utf-8")
    (tmp_path / ".env").write_text("X=1", encoding="utf-8")

    res = restore_drill.run_drill()
    assert "postgres:dump" not in [c["check"] for c in res.checks]


def test_a_bare_directory_name_still_resolves(tmp_path, monkeypatch):
    """list_universal_backups reports "dir" as a name, not a path."""
    backup = tmp_path / "1788049169"
    backup.mkdir()
    (backup / "manifest.json").write_text('{"version": 1}', encoding="utf-8")
    (backup / ".env").write_text("X=1", encoding="utf-8")

    monkeypatch.setattr(restore_drill, "_latest_pg_dump", lambda: None)
    monkeypatch.setattr(
        "kazma_core.db.pg_backup.pg_backup_enabled", lambda: False, raising=False
    )
    monkeypatch.setattr(
        "kazma_core.backup.universal.latest_universal_backup",
        lambda: {"dir": "1788049169"},
    )
    monkeypatch.setattr(
        "kazma_core.backup.universal._universal_dir", lambda: tmp_path
    )

    res = restore_drill.run_drill()
    assert "backup:exists" not in [c["check"] for c in res.failures], (
        "a bare directory name must resolve against the universal backup dir"
    )


def test_a_failed_drill_raises_a_critical_alert(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(
        "kazma_core.observability.ops_alerts.alert",
        lambda key, title, detail="", **kw: sent.append((key, title, kw)) or True,
    )
    res = restore_drill.DrillResult(backup_dir="x")
    res.add("sqlite:kazma.db", False, "integrity_check failed")

    restore_drill._alert_failure(res)

    assert sent, "a backup that cannot be restored must reach the operator"
    key, _title, kw = sent[0]
    assert key == "backup.restore_drill_failed"
    assert kw.get("severity") == "critical"


def test_alerting_never_raises_into_the_scheduler(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", boom)
    res = restore_drill.DrillResult(backup_dir="x")
    res.add("whatever", False)
    restore_drill._alert_failure(res)  # must not raise
