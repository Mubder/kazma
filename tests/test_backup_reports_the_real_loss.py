"""A backup alert must say how much is missing, not how many.

Live, 2026-09-13:

    [universal-backup] DB copy failed snapshots.db: database is locked
    [universal-backup] complete: 25 DBs, 440.3 MB     (previous run: 26, 1304.0 MB)

The operator got: *"1 database(s) failed to back up."* That reads as 96% of 26
covered. In fact `snapshots.db` is 864 MB of a 1304 MB backup — two thirds of
the data by volume, gone, behind a sentence that sounded minor.

A count is the wrong unit for blast radius.
"""

from __future__ import annotations

import sqlite3
import threading
import time


class TestTheHeadlineNamesTheDamage:
    def test_it_names_the_database(self):
        from kazma_core.backup.universal import _failed_db_headline

        text = _failed_db_headline(1, [{"path": "snapshots.db", "missing_bytes": 0}])
        assert "snapshots.db" in text

    def test_it_reports_the_volume_not_just_the_count(self):
        from kazma_core.backup.universal import _failed_db_headline

        text = _failed_db_headline(
            1, [{"path": "snapshots.db", "missing_bytes": 905_596_928}]
        )
        assert "864 MB" in text, text

    def test_many_failures_stay_readable(self):
        from kazma_core.backup.universal import _failed_db_headline

        rows = [{"path": f"{i}.db", "missing_bytes": 1 << 20} for i in range(6)]
        text = _failed_db_headline(6, rows)
        assert "+3 more" in text
        assert text.count(".db") == 3, f"expected three names, got: {text}"
        assert "6 MB" in text

    def test_an_unknown_size_does_not_invent_one(self):
        from kazma_core.backup.universal import _failed_db_headline

        text = _failed_db_headline(1, [{"path": "x.db"}])
        assert "MB" not in text


class TestTheCopySurvivesAConcurrentWriter:
    """`pages=100` let SQLite restart the copy on every write, so a store under
    continuous write could never finish. One step has no restart window."""

    def test_a_database_being_written_still_backs_up(self, tmp_path):
        from kazma_core.backup.universal import _backup_one_db

        src = tmp_path / "busy.db"
        con = sqlite3.connect(str(src))
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, blob TEXT)")
        con.executemany(
            "INSERT INTO t (blob) VALUES (?)", [("x" * 4096,) for _ in range(2000)]
        )
        con.commit()
        con.close()

        stop = threading.Event()

        def hammer() -> None:
            w = sqlite3.connect(str(src), timeout=30)
            try:
                while not stop.is_set():
                    w.execute("INSERT INTO t (blob) VALUES (?)", ("y" * 4096,))
                    w.commit()
                    time.sleep(0.001)
            finally:
                w.close()

        writer = threading.Thread(target=hammer, daemon=True)
        writer.start()
        try:
            assert _backup_one_db(src, tmp_path / "copy.db") is True
        finally:
            stop.set()
            writer.join(timeout=10)

        out = sqlite3.connect(str(tmp_path / "copy.db"))
        try:
            assert out.execute("SELECT count(*) FROM t").fetchone()[0] >= 2000
        finally:
            out.close()

    def test_the_copy_is_not_batched(self):
        """The regression that matters: re-introducing `pages=` brings the
        restart window back, and the symptom is a backup that silently drops
        the busiest store."""
        import ast
        import inspect

        from kazma_core.backup.universal import _backup_one_db

        # Parsed, not grepped. A first version matched `pages=` inside the
        # docstring that EXPLAINS why batching is wrong — the same mistake the
        # provider conformance suite made with the word "raise".
        tree = ast.parse(inspect.getsource(_backup_one_db).lstrip())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]

        backups = [
            c for c in calls
            if isinstance(c.func, ast.Attribute) and c.func.attr == "backup"
        ]
        assert backups, "the Online Backup API call is gone"
        for call in backups:
            assert not [k for k in call.keywords if k.arg == "pages"], (
                "batched copy restarts on every write to the source"
            )

        connects = [
            c for c in calls
            if isinstance(c.func, ast.Attribute) and c.func.attr == "connect"
        ]
        assert connects, "no sqlite connection is opened"
        for call in connects:
            assert [k for k in call.keywords if k.arg == "timeout"], (
                "no busy timeout: the copy fails instead of waiting for a writer"
            )
