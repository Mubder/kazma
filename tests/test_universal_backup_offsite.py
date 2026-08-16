"""Tests for the universal-backup offsite step (zip + single-file upload).

The cloud copy switched from a folder-tree upload (one API call per file) to a
single .zip archive: atomic on the cloud, far fewer API calls, compressed.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

from kazma_core.backup import universal as ub
from kazma_core.backup import cloud_sync as cs


class _FakeProvider:
    """Records what was uploaded so the zip can be inspected at upload time."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.uploaded: list[tuple[Path, str]] = []
        self.zip_names: list[list[str]] = []

    async def upload_file(self, local: Path, remote_name: str) -> dict[str, Any]:
        self.uploaded.append((local, remote_name))
        with zipfile.ZipFile(local) as zf:
            self.zip_names.append(zf.namelist())
        remote = f"google_drive:kazma-backups/{remote_name}"
        if self.ok:
            return {"ok": True, "remote": remote, "files": 1}
        return {"ok": False, "remote": remote, "error": "upload failed"}


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    dest = tmp_path / "backup_20260816_120000"
    (dest / "dbs").mkdir(parents=True)
    (dest / "manifest.json").write_text('{"offsite": {"status": "pending"}}', encoding="utf-8")
    (dest / "dbs" / "memory.db").write_bytes(b"\x00" * 128)
    return dest


def _patch_offsite(monkeypatch: pytest.MonkeyPatch, enabled: bool, provider_name: str) -> None:
    monkeypatch.setattr(
        ub,
        "_offsite_config",
        lambda: {"enabled": enabled, "provider": provider_name, "rclone_remote": ""},
    )


def test_offsite_sync_uploads_single_zip_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, backup_dir: Path
) -> None:
    _patch_offsite(monkeypatch, True, "google_drive")
    fake = _FakeProvider()
    monkeypatch.setattr(cs, "get_sync_provider", lambda: fake)

    result = ub._offsite_sync(backup_dir)

    assert result["ok"] is True
    assert len(fake.uploaded) == 1
    zip_path, remote_name = fake.uploaded[0]
    assert remote_name == "backup_20260816_120000.zip"
    # The archive contains the manifest + the whole tree (relative paths)
    assert sorted(fake.zip_names[0]) == ["dbs/memory.db", "manifest.json"]
    # The transient archive is gone after upload — the local dir is authoritative
    assert not zip_path.exists()
    assert not (backup_dir.parent / ".backup_20260816_120000.zip.tmp").exists()


def test_offsite_sync_disabled_skips(
    monkeypatch: pytest.MonkeyPatch, backup_dir: Path
) -> None:
    _patch_offsite(monkeypatch, False, "google_drive")
    assert ub._offsite_sync(backup_dir) == {"skipped": "offsite sync disabled"}


def test_offsite_sync_upload_failure_is_fail_open(
    monkeypatch: pytest.MonkeyPatch, backup_dir: Path
) -> None:
    _patch_offsite(monkeypatch, True, "google_drive")
    fake = _FakeProvider(ok=False)
    monkeypatch.setattr(cs, "get_sync_provider", lambda: fake)

    result = ub._offsite_sync(backup_dir)
    assert result["ok"] is False
    assert result["error"] == "upload failed"
    # Cleanup still happened — no stray archive or temp file left behind
    assert not (backup_dir.parent / "backup_20260816_120000.zip").exists()


def test_offsite_sync_zip_failure_is_fail_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_offsite(monkeypatch, True, "google_drive")
    fake = _FakeProvider()
    monkeypatch.setattr(cs, "get_sync_provider", lambda: fake)

    empty = tmp_path / "backup_empty"
    empty.mkdir()

    result = ub._offsite_sync(empty)
    assert result["ok"] is False
    assert "zip failed" in result["error"]
    assert fake.uploaded == []
