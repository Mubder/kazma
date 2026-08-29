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
