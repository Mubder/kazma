"""The offsite backup copies never carry ``.env`` (2026-11, FA-215).

The universal backup copies the install's ``.env`` -- the vault's key -- into
the backup folder, and both offsite paths sent the folder as it was: the
zip uploaded to a native provider and the rclone folder copy, both plain.
The owner's decision: leave ``.env`` out of the offsite copies; the local
backup keeps it (it is the vault's recovery key), the restic snapshot is
encrypted, and the vault key is escrowed apart.

The zip is read back; the rclone call is captured. With nothing left out
(the old copy) ``.env`` is in the zip: the negative control.
"""

from __future__ import annotations

import subprocess
import zipfile

import pytest

from kazma_core.backup import universal


@pytest.fixture
def backup(tmp_path):
    dest = tmp_path / "universal-2026-11-02"
    (dest / "dbs").mkdir(parents=True)
    (dest / ".env").write_text("KAZMA_VAULT_KEY=demo-not-a-key\n", encoding="utf-8")
    (dest / "manifest.json").write_text("{}", encoding="utf-8")
    (dest / "dbs" / "settings.db").write_bytes(b"SQLite format 3\x00")
    return dest


def _names(zip_path) -> set[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return set(zf.namelist())


def test_the_offsite_zip_leaves_env_out(backup):
    names = _names(universal._zip_backup_dir(backup))
    assert ".env" not in names and {"manifest.json", "dbs/settings.db"} <= names
    assert (backup / ".env").is_file(), "the local backup keeps it"


def test_negative_control_with_nothing_left_out_env_is_in_the_zip(backup, monkeypatch):
    monkeypatch.setattr(universal, "OFFSITE_LEAVES_OUT", ())
    assert ".env" in _names(universal._zip_backup_dir(backup))


def test_the_rclone_copy_excludes_env(backup, monkeypatch):
    seen: list[list[str]] = []

    def run(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(universal, "_offsite_config",
                        lambda: {"enabled": True, "provider": "", "rclone_remote": "r2:kazma-backups"})
    monkeypatch.setattr(universal.shutil, "which", lambda name: "rclone")
    monkeypatch.setattr(subprocess, "run", run)
    result = universal._offsite_sync(backup)
    assert result.get("ok") is True, result
    # The copy, not the listing of old copies that follows it.
    argv = next(a for a in seen if a[:2] == ["rclone", "copy"])
    assert argv[argv.index("--exclude") + 1] == "/.env"
    assert argv.index("--exclude") < argv.index("--")
