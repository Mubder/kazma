"""A file the backup could not copy makes the backup incomplete, and says so.

Audit 2026-10-26 AUD-021: ``_robust_copytree`` logged every ``OSError``
(``PermissionError`` included) at DEBUG and counted nothing, the summary was
always ``"ok": True``, and ``_prune`` counted partial backups toward
retention. A locked document blob was missing from every backup without a
word, and seven partial backups in a row rotated out the last complete one
(AGENTS.md §27: a backup that says "complete" when it is not is no backup).

Now: only a file that vanished (``FileNotFoundError``) is skipped quietly;
any other failure is counted into the manifest and the gap alert, the
summary is ``ok: False``, and retention always keeps the newest complete
backup. Negative controls: the old copier and the old prune, replayed.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest
from kazma_core.backup import universal


def _tree(root: Path) -> Path:
    src = root / "document-store"
    (src / "originals" / "ab").mkdir(parents=True)
    (src / "originals" / "ab" / "locked.bin").write_bytes(b"held by another program")
    (src / "originals" / "ab" / "fine.bin").write_bytes(b"copies")
    (src / "cache.bin").write_bytes(b"vanishes")
    return src


def _copy2_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    real = shutil.copy2

    def copy2(src, dst, *a, **kw):
        name = Path(src).name
        if name == "locked.bin":
            raise PermissionError(13, "The process cannot access the file", str(src))
        if name == "cache.bin":
            raise FileNotFoundError(2, "No such file", str(src))
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(universal.shutil, "copy2", copy2)


def test_a_locked_file_is_counted_and_a_vanished_one_is_not(tmp_path, monkeypatch):
    src = _tree(tmp_path)
    _copy2_failing(monkeypatch)
    failures: list[dict[str, str]] = []

    copied = universal._robust_copytree(src, tmp_path / "out", failures)

    assert copied == 1
    assert [f["path"] for f in failures] == ["originals/ab/locked.bin"]
    assert "PermissionError" in failures[0]["error"]


def test_the_backup_is_incomplete_and_the_gap_is_alerted(tmp_path, monkeypatch):
    data = tmp_path / "kazma-data"
    data.mkdir()
    _tree(data)
    base = data / "backups" / "universal"
    base.mkdir(parents=True)
    _copy2_failing(monkeypatch)
    monkeypatch.setattr(universal, "_data_dir", lambda: data)
    monkeypatch.setattr(universal, "_universal_dir", lambda: base)
    monkeypatch.setattr(universal, "_backup_progress", {"phase": "idle"})
    monkeypatch.setattr(universal, "_offsite_sync", lambda dest: {"skipped": "test"})
    monkeypatch.setattr(universal, "_snapshot_to_restic", lambda dest: None)
    monkeypatch.setattr(universal, "_pg_dump_state", lambda: {})
    monkeypatch.setattr(universal, "_rebuilt_dirs", lambda: set())
    import kazma_core.backup.neo4j_backup as neo

    monkeypatch.setattr(neo, "export_graph", lambda dest: type("R", (), {"as_dict": lambda self: {}})())
    gaps: list[dict] = []
    monkeypatch.setattr(
        universal, "_alert_on_backup_gaps",
        lambda offsite, db_fail, failed_dbs=None, files_failed=0, file_examples=None:
            gaps.append({"files_failed": files_failed, "examples": file_examples}),
    )

    summary = universal.perform_universal_backup(retention=5, trigger="manual")

    assert summary["ok"] is False
    assert summary["complete"] is False
    assert summary["files_failed"] == 1
    assert "1 file(s) not saved" in summary["error"]
    manifest = json.loads((Path(summary["backup_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] is False
    assert manifest["files"]["failed"] == 1
    assert manifest["files"]["examples"] == ["document-store/originals/ab/locked.bin"]
    assert gaps == [{"files_failed": 1, "examples": ["document-store/originals/ab/locked.bin"]}]


def _backup(base: Path, ts: int, *, complete: bool | None, legacy_failed_dbs: int = 0) -> Path:
    folder = base / str(ts)
    folder.mkdir()
    manifest: dict = {"timestamp": ts, "databases": {"ok": 3, "failed": legacy_failed_dbs}}
    if complete is not None:
        manifest["complete"] = complete
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    os.utime(folder, (ts, ts))
    return folder


def test_retention_keeps_the_newest_complete_backup(tmp_path, monkeypatch):
    base = tmp_path / "universal"
    base.mkdir()
    monkeypatch.setattr(universal, "_universal_dir", lambda: base)
    now = int(time.time())
    complete = _backup(base, now - 400, complete=True)
    older_complete = _backup(base, now - 500, complete=True)
    partial = [_backup(base, now - 300 + i, complete=False) for i in range(3)]

    universal._prune(2)

    left = {p.name for p in base.iterdir()}
    assert complete.name in left, "the newest complete backup was pruned"
    assert older_complete.name not in left
    assert {partial[2].name, partial[1].name} <= left
    assert partial[0].name not in left


def test_an_older_manifest_is_judged_by_its_failed_databases(tmp_path, monkeypatch):
    base = tmp_path / "universal"
    base.mkdir()
    monkeypatch.setattr(universal, "_universal_dir", lambda: base)
    now = int(time.time())
    whole = _backup(base, now - 400, complete=None)
    _backup(base, now - 300, complete=None, legacy_failed_dbs=1)
    _backup(base, now - 200, complete=None, legacy_failed_dbs=1)

    universal._prune(1)

    assert whole.is_dir()


def test_negative_control_the_old_copier_and_prune_lose_the_gap(tmp_path, monkeypatch):
    # The old copier: every OSError skipped at DEBUG, nothing counted.
    src = _tree(tmp_path)
    _copy2_failing(monkeypatch)
    dest = tmp_path / "old"
    dest.mkdir()
    counted = 0
    for item in sorted((src / "originals" / "ab").iterdir()):
        try:
            shutil.copy2(item, dest / item.name)
        except (FileNotFoundError, OSError):
            pass  # the old handler: logged at DEBUG, never counted
    assert counted == 0 and not (dest / "locked.bin").exists()

    # The old prune: retention by age alone deletes the last complete backup.
    base = tmp_path / "universal"
    base.mkdir()
    now = int(time.time())
    complete = _backup(base, now - 400, complete=True)
    for i in range(3):
        _backup(base, now - 300 + i, complete=False)
    backups = sorted(base.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in backups[2:]:
        shutil.rmtree(stale)
    assert not complete.exists()
