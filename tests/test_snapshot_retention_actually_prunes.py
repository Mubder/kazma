"""Pruning old snapshots must survive the connection closing.

`maintain_snapshots` deleted the right rows and threw them away again. Python's
sqlite3 opens an implicit transaction for a DML statement, and `close()`
without `commit()` ROLLS IT BACK — so the prune ran, reported success, and
undid itself. Every run. For weeks.

Live evidence (2026-09-13/14), three consecutive runs:

    maintenance: deleted=3079 before=905596928 after=905867264
    maintenance: deleted=3083 before=905867264 after=906084352
    maintenance: deleted=3106 before=906084352 after=906158080

The same rows deleted each time, and the file GROWING after every "successful"
prune. snapshots.db reached 864 MB with its oldest row 53 days past a 30-day
retention — while a daily maintenance loop ran on schedule and logged success.

Nothing looked wrong because `rowcount` is what the DELETE *intended*. The
verification here counts what is actually left, which is the only reading that
could have caught it.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest


def _seed(path, *, old: int, fresh: int) -> None:
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE snapshots (id INTEGER PRIMARY KEY, thread_id TEXT, "
            "iteration INTEGER, state_json TEXT, timestamp TEXT, model_used TEXT)"
        )
        now = datetime.now(UTC)
        rows = [
            ("t1", i, '{"messages":[]}', (now - timedelta(days=60)).isoformat(), "m")
            for i in range(old)
        ] + [
            ("t1", 1000 + i, '{"messages":[]}', (now - timedelta(hours=1)).isoformat(), "m")
            for i in range(fresh)
        ]
        con.executemany(
            "INSERT INTO snapshots (thread_id, iteration, state_json, timestamp, "
            "model_used) VALUES (?,?,?,?,?)",
            rows,
        )
        con.commit()
    finally:
        con.close()


def _count(path, *, older_than_days: int | None = None) -> int:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if older_than_days is None:
            return con.execute("SELECT count(*) FROM snapshots").fetchone()[0]
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        return con.execute(
            "SELECT count(*) FROM snapshots WHERE timestamp < ?", (cutoff,)
        ).fetchone()[0]
    finally:
        con.close()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "snapshots.db"
    _seed(path, old=40, fresh=10)
    return path


class TestThePruneSticks:
    def test_old_rows_are_gone_after_maintenance(self, db):
        """The assertion the old code would fail: read the rows back, do not
        trust the return value."""
        from kazma_core.time_travel import maintain_snapshots

        assert _count(db, older_than_days=30) == 40
        maintain_snapshots(db, retention_days=30)
        assert _count(db, older_than_days=30) == 0, "the prune was rolled back"

    def test_fresh_rows_survive(self, db):
        from kazma_core.time_travel import maintain_snapshots

        maintain_snapshots(db, retention_days=30)
        assert _count(db) == 10

    def test_running_twice_deletes_nothing_the_second_time(self, db):
        """The tell. With the rollback, every run deleted the same rows again —
        that is what 3079 / 3083 / 3106 meant."""
        from kazma_core.time_travel import maintain_snapshots

        first = maintain_snapshots(db, retention_days=30)
        second = maintain_snapshots(db, retention_days=30)
        assert first["deleted"] == 40
        assert second["deleted"] == 0, (
            "the same rows were deleted twice — the first prune did not commit"
        )

    def test_it_reports_what_actually_remains(self, db):
        from kazma_core.time_travel import maintain_snapshots

        stats = maintain_snapshots(db, retention_days=30)
        assert stats["remaining_old"] == 0
        assert stats["prune"] == "ok"

    def test_the_file_shrinks(self, db):
        """DELETE alone never shrinks a SQLite file; the VACUUM is what
        reclaims it. 40 of 50 rows removed should be visible on disk."""
        from kazma_core.time_travel import maintain_snapshots

        before = db.stat().st_size
        stats = maintain_snapshots(db, retention_days=30)
        assert stats["size_after"] <= before
        assert db.stat().st_size <= before


class TestItStaysHonestWhenItFails:
    def test_a_rolled_back_prune_is_reported_as_not_sticking(self, db, monkeypatch):
        """Simulate the original bug — commit does nothing — and assert the
        function now says so instead of logging success."""
        from kazma_core import time_travel

        real_connect = sqlite3.connect

        class _NoCommit:
            """sqlite3.Connection.commit is read-only, so wrap rather than
            patch. Everything else delegates, which is the point — only the
            commit is missing, exactly as the bug had it."""

            def __init__(self, con):
                self._con = con

            def commit(self):
                pass

            def __getattr__(self, name):
                return getattr(self._con, name)

        def no_commit_connect(*args, **kwargs):
            return _NoCommit(real_connect(*args, **kwargs))

        monkeypatch.setattr(time_travel.sqlite3, "connect", no_commit_connect)
        stats = time_travel.maintain_snapshots(db, retention_days=30)
        assert stats["remaining_old"] == 40
        assert "did not stick" in stats["prune"]
