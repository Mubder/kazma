"""An empty source is not a lost backup, and the drill must say which it is.

THE ALERT (operator's install, 2026-09-15)

    [Ops] A backup cannot be restored -- the drill failed.
    FAIL: 32/34 checks passed. Failed: sqlite:kazma.db (opens, but contains no
    tables at all), sqlite:ops.db (opens, but contains no tables at all).

Both files are 0 bytes on that install, created months ago and never written
to. Nothing was lost, because there was never anything in them. The backup was
fine; the drill was describing a faithful copy of nothing in the language of
catastrophe, every night.

WHY THE CHECK COULD NOT TELL

A truncated backup of a real database and a faithful copy of an empty file are
byte-identical, and `PRAGMA integrity_check` passes on both. From the backup
side the two cases are genuinely indistinguishable. The only thing that
separates them is what the SOURCE held, and only at the moment of copying --
an offsite drill may run on a host with no live install, and the live file has
moved on anyway.

So the manifest now records `source_tables` per database, and the drill reads
it. Three outcomes instead of one:

    source had 0 tables    -> PASS, "empty at source"
    source had N > 0       -> FAIL, "THE SOURCE HAD N TABLES"    (the real one)
    not recorded           -> FAIL, and says the backup predates the check

The middle case is the one that matters and it is now LOUDER than before, not
softer. The point is not to quieten the drill; it is that an alert firing every
night for a non-problem trains you to ignore the night it is right.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from kazma_core.backup.restore_drill import (
    DrillResult,
    _check_sqlite,
    _norm,
    _source_table_counts,
)


def _db(path: Path, *, tables: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        for i in range(tables):
            conn.execute(f"CREATE TABLE t{i} (a INTEGER)")
        conn.commit()
    finally:
        conn.close()
    return path


def _result() -> DrillResult:
    return DrillResult(backup_dir="/tmp/x")


def _scratch(tmp_path: Path) -> Path:
    """`_check_sqlite` copies INTO this; the real caller is what creates it."""
    d = tmp_path / "scratch"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _verdict(res: DrillResult, name: str) -> dict:
    hits = [c for c in res.checks if c["check"] == f"sqlite:{name}"]
    assert hits, [c["check"] for c in res.checks]
    return hits[0]


class TestAnEmptySourceIsNotALoss:
    def test_empty_source_and_empty_backup_passes(self, tmp_path: Path) -> None:
        """kazma.db and ops.db: 0 bytes then, 0 bytes now."""
        db = _db(tmp_path / "kazma.db", tables=0)
        res = _result()
        _check_sqlite(db, _scratch(tmp_path), res, source_tables=0)
        v = _verdict(res, "kazma.db")
        assert v["ok"] is True, v
        assert "empty at source" in v["detail"]

    def test_a_truncated_backup_of_real_data_still_fails(self, tmp_path: Path) -> None:
        """The case the check exists for. It must get LOUDER, not quieter."""
        db = _db(tmp_path / "chat_sessions.db", tables=0)
        res = _result()
        _check_sqlite(db, _scratch(tmp_path), res, source_tables=12)
        v = _verdict(res, "chat_sessions.db")
        assert v["ok"] is False, v
        assert "12" in v["detail"], v
        assert "SOURCE HAD" in v["detail"], v

    def test_an_unrecorded_source_fails_and_says_why(self, tmp_path: Path) -> None:
        """Not knowing must never be reported as knowing there was nothing."""
        db = _db(tmp_path / "legacy.db", tables=0)
        res = _result()
        _check_sqlite(db, _scratch(tmp_path), res, source_tables=None)
        v = _verdict(res, "legacy.db")
        assert v["ok"] is False, v
        assert "does not record" in v["detail"], v

    def test_a_populated_backup_passes_regardless(self, tmp_path: Path) -> None:
        db = _db(tmp_path / "memory_state.db", tables=3)
        res = _result()
        _check_sqlite(db, _scratch(tmp_path), res, source_tables=3)
        v = _verdict(res, "memory_state.db")
        assert v["ok"] is True, v
        assert "3 tables" in v["detail"]


class TestTheManifestCarriesTheEvidence:
    def test_counts_are_read_from_the_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text(
            json.dumps(
                {
                    "databases": {
                        "items": [
                            {"path": "kazma.db", "size": 0, "source_tables": 0},
                            {"path": "sub/deep.db", "size": 9, "source_tables": 4},
                            {"path": "old.db", "size": 9},
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        counts = _source_table_counts(tmp_path)
        assert counts["kazma.db"] == 0
        assert counts["sub/deep.db"] == 4
        assert "old.db" not in counts, "an absent key must stay absent, not become 0"

    def test_windows_and_posix_paths_agree(self) -> None:
        assert _norm("sub\\deep.db") == _norm("sub/deep.db") == "sub/deep.db"

    def test_a_missing_manifest_is_not_an_exception(self, tmp_path: Path) -> None:
        assert _source_table_counts(tmp_path) == {}


class TestTheBackupRecordsIt:
    def test_table_count_reads_a_real_database(self, tmp_path: Path) -> None:
        from kazma_core.backup.universal import _table_count

        assert _table_count(_db(tmp_path / "a.db", tables=2)) == 2
        assert _table_count(_db(tmp_path / "b.db", tables=0)) == 0

    def test_an_unreadable_file_is_none_not_zero(self, tmp_path: Path) -> None:
        """"I could not tell" must never be recorded as "there was nothing"."""
        from kazma_core.backup.universal import _table_count

        junk = tmp_path / "corrupt.db"
        junk.write_bytes(b"this is not a sqlite file at all, not even close")
        assert _table_count(junk) is None

    def test_the_manifest_item_carries_source_tables(self) -> None:
        src = (
            Path(__file__).resolve().parents[1]
            / "kazma-core" / "kazma_core" / "backup" / "universal.py"
        ).read_text(encoding="utf-8")
        assert '"source_tables": _table_count(db),' in src
