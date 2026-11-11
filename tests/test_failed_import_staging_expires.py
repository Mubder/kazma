"""A failed import's staging folder expires after 30 days, never in use.

Until 2026-11-11 the importer swept earlier staging folders with
``max_age_s=inf``: a failed import's ``.migrate-staging-<ts>`` (the files it
could not restore, its vault key already taken out, AGENTS.md §62) was kept
forever, one more for every failed import. Now one older than
``STALE_STAGING_S`` (30 days) is removed at the next import, and never one a
running import marks as its own (``IN_USE_MARKER``: host, pid, start time).
Each test has its negative control.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import psutil
import pytest

from kazma_core.migration import bundle as bundle_mod
from kazma_core.migration import importer
from kazma_core.migration.bundle import (
    BUNDLE_VERSION,
    IN_USE_MARKER,
    STALE_STAGING_S,
    Manifest,
    mark_working_folder,
    sweep_working_folders,
    _working_folder_in_use,
)

DAY = 86400


def _sqlite_bytes(tmp: Path) -> bytes:
    path = tmp / "src-cron.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


def _bundle(path: Path) -> Path:
    files: dict[str, str | bytes] = {
        "meta.env": "KAZMA_VAULT_KEY=\n", "config.yaml": "{}\n", "pathmap.json": "{}\n",
        "data/cron.db": _sqlite_bytes(path.parent),
    }
    hashes = {name: hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()
              for name, data in files.items()}
    manifest = Manifest(bundle_version=BUNDLE_VERSION, source_workspace_root="",
                        source_data_dir="", file_hashes=hashes)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", manifest.to_json())
        for name, content in files.items():
            zf.writestr(name, content)
    return path


@pytest.fixture
def data(tmp_path: Path, monkeypatch) -> Path:
    folder = tmp_path / "kazma-data"
    folder.mkdir()
    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: folder)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KAZMA_VAULT_KEY", raising=False)
    return folder


def _left_folder(data: Path, age_days: float) -> Path:
    folder = data / f".migrate-staging-{int(time.time() - age_days * DAY)}"
    folder.mkdir()
    (folder / "kept.txt").write_text("a file the import could not restore", encoding="utf-8")
    return folder


def _failed_import(tmp_path: Path, monkeypatch) -> None:
    def stop(*args, **kwargs):
        raise RuntimeError("stopped after staging")

    monkeypatch.setattr(importer, "_apply_path_rewrite", stop)
    monkeypatch.setattr(importer, "_live_server_detected", lambda: (False, ""))
    with pytest.raises(RuntimeError, match="stopped after staging"):
        importer.import_bundle(_bundle(tmp_path / "b.zip"), target_workspace_root=str(tmp_path))


def test_the_age_is_thirty_days():
    assert STALE_STAGING_S == 30 * DAY


def test_a_staging_folder_older_than_thirty_days_is_removed(tmp_path, data, monkeypatch):
    old = _left_folder(data, 31)
    young = _left_folder(data, 29)

    _failed_import(tmp_path, monkeypatch)

    assert not old.exists()
    assert (young / "kept.txt").is_file()
    runs = [p for p in data.glob(".migrate-staging-*") if p not in (old, young)]
    assert len(runs) == 1, runs  # this run's own, kept on failure
    # ... and its mark is taken off, so it expires in its turn.
    assert not (runs[0] / IN_USE_MARKER).exists()


def test_negative_control_the_old_age_keeps_it_forever(tmp_path, data, monkeypatch):
    old = _left_folder(data, 3650)
    monkeypatch.setattr(importer, "STALE_STAGING_S", float("inf"))

    _failed_import(tmp_path, monkeypatch)

    assert (old / "kept.txt").is_file()


def test_a_folder_a_running_import_holds_is_never_removed(tmp_path, data, monkeypatch):
    held = _left_folder(data, 400)
    mark_working_folder(held)  # this process: a run still going

    _failed_import(tmp_path, monkeypatch)

    assert (held / "kept.txt").is_file()
    assert _working_folder_in_use(held)


def test_negative_control_without_the_in_use_check_it_is_removed(tmp_path, data, monkeypatch):
    held = _left_folder(data, 400)
    mark_working_folder(held)
    monkeypatch.setattr(bundle_mod, "_working_folder_in_use", lambda folder: False)

    _failed_import(tmp_path, monkeypatch)

    assert not held.exists()


def _ended_process_owner() -> dict[str, object]:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        started = f"{psutil.Process(proc.pid).create_time():.3f}"
    finally:
        proc.kill()
        proc.wait(timeout=30)
    import socket

    return {"host": socket.gethostname(), "pid": proc.pid, "started": started}


def test_a_mark_of_an_ended_process_does_not_hold_the_folder(tmp_path):
    folder = _left_folder(tmp_path, 31)
    (folder / IN_USE_MARKER).write_text(json.dumps(_ended_process_owner()), encoding="utf-8")

    assert not _working_folder_in_use(folder)
    assert sweep_working_folders(tmp_path, ".migrate-staging-",
                                 max_age_s=STALE_STAGING_S) == [folder.name]


def test_a_mark_that_cannot_be_read_holds_the_folder(tmp_path):
    folder = _left_folder(tmp_path, 31)
    (folder / IN_USE_MARKER).write_text("{not json", encoding="utf-8")
    other_host = _left_folder(tmp_path, 32)
    (other_host / IN_USE_MARKER).write_text(
        json.dumps({"host": "another-machine.invalid", "pid": 1, "started": "0.000"}),
        encoding="utf-8")

    assert sweep_working_folders(tmp_path, ".migrate-staging-", max_age_s=STALE_STAGING_S) == []
    assert folder.exists() and other_host.exists()
