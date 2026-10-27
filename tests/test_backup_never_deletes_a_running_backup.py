"""A backup that finds another one running leaves its folder alone.

Audit 2026-10-26 AUD-020: inside ``_backup_lock`` the loop that deletes
every backup folder without ``manifest.json`` ran BEFORE the "already
running" check. A live run's folder has no manifest until it finishes, so
the 6-hour queued backup arriving while a manual "Back Up Now" was copying
deleted the running backup's folder and then answered "already running";
the first run went on to record as saved databases whose files were gone.

Negative control: the old order (cleanup first) deletes the folder.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from kazma_core.backup import universal


@pytest.fixture
def backups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "universal"
    base.mkdir()
    monkeypatch.setattr(universal, "_universal_dir", lambda: base)
    monkeypatch.setattr(universal, "_data_dir", lambda: tmp_path / "data")
    return base


def _running_copy(base: Path, monkeypatch: pytest.MonkeyPatch, *, started: float) -> Path:
    """A backup mid-copy: its folder, no manifest yet, progress in a mid phase."""
    folder = base / "1790000000"
    (folder / "dbs").mkdir(parents=True)
    (folder / "dbs" / "settings.db").write_bytes(b"copied so far")
    monkeypatch.setattr(
        universal, "_backup_progress",
        {"phase": "databases", "detail": "DB 3/26", "started_ts": started},
    )
    return folder


def test_a_queued_backup_leaves_the_running_backup_alone(backups, monkeypatch):
    running = _running_copy(backups, monkeypatch, started=time.time())

    result = universal.perform_universal_backup(trigger="auto")

    assert result["ok"] is False
    assert "already running" in result["error"]
    assert running.is_dir(), "the running backup's folder was deleted"
    assert (running / "dbs" / "settings.db").read_bytes() == b"copied so far"


def test_a_crashed_run_is_still_cleaned_up(backups, monkeypatch):
    # The other half: a mid phase older than the stale threshold is a crash,
    # and its incomplete folder still goes before the new run starts.
    crashed = _running_copy(backups, monkeypatch, started=time.time() - 10 * 3600)
    removed: list[Path] = []
    monkeypatch.setattr(universal, "_rmtree_force", lambda path: removed.append(path))
    # Stop the new run right after its guard: the rest is not under test.
    monkeypatch.setattr(universal, "_set_progress", _stop_after_guard)

    with pytest.raises(_GuardPassed):
        universal.perform_universal_backup(trigger="auto")

    assert crashed in removed


class _GuardPassed(Exception):
    pass


def _stop_after_guard(phase: str, **_kw) -> None:
    raise _GuardPassed(phase)


def test_negative_control_cleaning_first_deletes_the_running_backup(backups, monkeypatch):
    # The old order, replayed: the cleanup loop before the running check.
    running = _running_copy(backups, monkeypatch, started=time.time())
    for old in backups.iterdir():
        if old.is_dir() and not (old / "manifest.json").is_file():
            universal._rmtree_force(old)
    assert not running.exists()
