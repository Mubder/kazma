"""Interrupted backups left full-size temp files on disk forever.

Live, 2026-08-29: 3.83 GB of orphaned .tmp dumps in backups/pg -- four
files, one per Kazma restart that happened to land mid-dump. The operator
noticed as "the backup is flooding my drive", and they were right.

Both writers use the same correct pattern: write to .tmp, validate, rename.
Both clean up on an EXCEPTION. Neither survives being killed, because a
killed process runs no handler -- and retention only ever matched finished
files (``pg_shared_*.dump``), so the orphans were invisible to it and grew
without bound.
"""

from __future__ import annotations

import time

import pytest
from kazma_core.backup import universal as uni
from kazma_core.db import pg_backup as pg


def _age(path, seconds):
    old = time.time() - seconds
    import os

    os.utime(path, (old, old))


# ── Postgres dumps ────────────────────────────────────────────────────


def test_an_orphaned_dump_is_swept(tmp_path):
    """The 1.55 GB file a killed process leaves behind."""
    orphan = tmp_path / ".pg_shared_1787920550.dump.tmp"
    orphan.write_bytes(b"PGDMP" + b"\x00" * 1024)
    _age(orphan, 7200)

    assert pg._sweep_orphaned_tmp(tmp_path) == 1
    assert not orphan.exists()


def test_a_dump_in_progress_is_never_touched(tmp_path):
    """A backup running right now writes to exactly this filename. Deleting
    it would turn a leak fix into a broken backup."""
    live = tmp_path / ".pg_shared_1787999999.dump.tmp"
    live.write_bytes(b"PGDMP" + b"\x00" * 1024)

    assert pg._sweep_orphaned_tmp(tmp_path) == 0
    assert live.exists(), "an in-progress dump must survive the sweep"


def test_finished_dumps_are_not_swept(tmp_path):
    keep = tmp_path / "pg_shared_1787920550.dump"
    keep.write_bytes(b"PGDMP" + b"\x00" * 1024)
    _age(keep, 999999)

    assert pg._sweep_orphaned_tmp(tmp_path) == 0
    assert keep.exists()


def test_the_sweep_runs_as_part_of_pruning(tmp_path, monkeypatch):
    """It has to be reachable from the scheduled path, or it never runs --
    which is exactly why these files survived for days."""
    monkeypatch.setattr(pg, "pg_backup_dir", lambda: tmp_path)
    orphan = tmp_path / ".pg_shared_1.dump.tmp"
    orphan.write_bytes(b"x" * 16)
    _age(orphan, 7200)

    assert pg.prune_pg_backups(retention=5) >= 1
    assert not orphan.exists()


def test_the_age_threshold_is_generous(tmp_path):
    """A dump takes seconds to minutes; an hour is far past any legitimate
    in-progress write, so the sweep cannot race a running backup."""
    assert pg._TMP_ORPHAN_AGE_S >= 1800


# ── universal archives, same hole ─────────────────────────────────────


def test_an_orphaned_zip_is_swept(tmp_path):
    orphan = tmp_path / ".1787920550.zip.tmp"
    orphan.write_bytes(b"PK" + b"\x00" * 512)
    _age(orphan, 7200)

    assert uni._sweep_orphaned_tmp(tmp_path) == 1
    assert not orphan.exists()


def test_a_zip_in_progress_survives(tmp_path):
    live = tmp_path / ".1787999999.zip.tmp"
    live.write_bytes(b"PK")
    assert uni._sweep_orphaned_tmp(tmp_path) == 0
    assert live.exists()


def test_a_missing_directory_is_survivable(tmp_path):
    """The sweep runs inside pruning, which runs inside a completed backup.
    It must never be the thing that fails."""
    assert pg._sweep_orphaned_tmp(tmp_path / "nope") == 0
    assert uni._sweep_orphaned_tmp(tmp_path / "nope") == 0


@pytest.mark.parametrize("module", [pg, uni])
def test_both_writers_have_a_sweep(module):
    """The identical hole existed in two places. Fixing one and leaving the
    other is how it comes back."""
    assert callable(module._sweep_orphaned_tmp)


# ── the Postgres dump must reach the offsite repository ───────────────


def test_the_pg_dump_is_snapshotted_to_every_repo(monkeypatch, tmp_path):
    """Until 2026-08-29 the Postgres database -- chat sessions, memory
    vectors, shared state -- had NO offsite copy. The universal sweep
    excludes the whole backups/ directory and the nightly dump wrote only to
    local disk, so a disk failure lost the primary datastore while every
    other component was protected."""
    from kazma_core.backup import restic_repo as rr
    from kazma_core.memory.worker_bootstrap import _snapshot_pg_to_restic

    seen: list[str] = []
    monkeypatch.setattr(rr, "restic_available", lambda: True)
    monkeypatch.setattr(rr, "ensure_password", lambda **kw: ("pw", False))
    monkeypatch.setattr(rr, "repo_paths",
                        lambda: {"local": "/l", "remote": "rclone:r/restic"})
    monkeypatch.setattr(
        rr, "backup",
        lambda repo, pw, paths, tags=None: seen.append(repo)
        or rr.ResticResult(ok=True, action="backup", repo=repo),
    )

    dump = tmp_path / "pg_shared_1.dump"
    dump.write_bytes(b"PGDMP")
    _snapshot_pg_to_restic(dump)

    assert seen == ["/l", "rclone:r/restic"], (
        "the offsite repository is the one that was missing"
    )


def test_a_snapshot_failure_never_fails_the_dump(monkeypatch, tmp_path):
    """The dump already succeeded by this point."""
    from kazma_core.backup import restic_repo as rr
    from kazma_core.memory.worker_bootstrap import _snapshot_pg_to_restic

    monkeypatch.setattr(rr, "restic_available", lambda: True)
    monkeypatch.setattr(rr, "ensure_password", lambda **kw: ("pw", False))
    monkeypatch.setattr(rr, "repo_paths", lambda: {"local": "/l"})

    def _boom(*a, **k):
        raise RuntimeError("repository unreachable")

    monkeypatch.setattr(rr, "backup", _boom)
    dump = tmp_path / "pg_shared_2.dump"
    dump.write_bytes(b"PGDMP")
    _snapshot_pg_to_restic(dump)  # must not raise


def test_the_nightly_handler_actually_calls_it():
    """Wiring that nothing invokes is the failure this whole audit is about."""
    import inspect

    from kazma_core.memory import worker_bootstrap

    src = inspect.getsource(worker_bootstrap._handle_native_pg_backup)
    assert "_snapshot_pg_to_restic" in src


# ── the boot sweep that produced 29 generations in two days ───────────


def test_a_fresh_backup_skips_the_boot_sweep(monkeypatch, tmp_path):
    """"Back up shortly after boot" is right for a machine that has been off
    for a week and wrong for one restarted five times in an evening."""
    import time as _t

    from kazma_core.memory import worker_bootstrap as wb

    base = tmp_path / "backups" / "universal" / "1787963091"
    base.mkdir(parents=True)
    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: str(tmp_path))
    assert wb._backup_ran_recently() is True

    old = _t.time() - 9 * 3600
    import os
    os.utime(base, (old, old))
    assert wb._backup_ran_recently() is False, "a stale backup must still run"


def test_no_backups_at_all_means_take_one(monkeypatch, tmp_path):
    """A fresh install must not skip its first backup."""
    from kazma_core.memory import worker_bootstrap as wb

    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: str(tmp_path))
    assert wb._backup_ran_recently() is False


def test_an_unreadable_backup_dir_never_skips(monkeypatch):
    """Unknown age must mean "take the backup". Skipping on doubt is how a
    machine ends up with no recent copy at all."""
    from kazma_core.memory import worker_bootstrap as wb

    def _boom():
        raise OSError("no such directory")

    monkeypatch.setattr("kazma_core.paths.data_dir", _boom)
    assert wb._backup_ran_recently() is False


def test_the_guard_is_actually_used_by_the_scheduler():
    import inspect

    from kazma_core.memory import worker_bootstrap

    src = inspect.getsource(worker_bootstrap)
    assert "_backup_ran_recently()" in src
