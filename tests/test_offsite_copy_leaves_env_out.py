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
    monkeypatch.setattr(universal, "_offsite_leaves_out", lambda rel: False)
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


# -- every secret file, at any depth (audit 2026-11-10) ----------------------
#
# A failed ``kazma migrate`` import or export left ``<data dir>/.migrate-*/``
# with the bundle's ``meta.env`` (the vault key in clear) beside a vault.db.
# The asset copy kept out exact names only, so the registry's ``.migrate-*``
# pattern matched nothing, and the offsite filter left out the ROOT ``.env``
# only: the 6-hourly universal backup sent the key offsite, plain.

KEY_LINE = "KAZMA_VAULT_KEY=demo-planted-not-a-key\n"


@pytest.fixture
def data_dir(tmp_path):
    src = tmp_path / "kazma-data"
    staging = src / ".migrate-staging-1700000000"
    (staging / "data").mkdir(parents=True)
    (staging / "meta.env").write_text(KEY_LINE, encoding="utf-8")
    (staging / "data" / "vault.db").write_bytes(b"SQLite format 3\x00")
    (src / "sub").mkdir()
    (src / "sub" / ".env").write_text(KEY_LINE, encoding="utf-8")
    (src / "sub" / ".env.example").write_text("KAZMA_VAULT_KEY=\n", encoding="utf-8")
    (src / "sub" / "notes.txt").write_text("mine", encoding="utf-8")
    (src / "restic.pass").write_text("demo-pass", encoding="utf-8")
    return src


def _backup_of(src, tmp_path):
    dest = tmp_path / "universal-2026-11-10"
    dest.mkdir()
    universal._copy_assets(src, dest)
    (dest / ".env").write_text(KEY_LINE, encoding="utf-8")  # the root copy, as before
    return dest


def _old_rules(monkeypatch):
    """The asset copy's exact-name set and the root-only offsite filter."""
    from kazma_core import store_registry

    def exact_names_only(name):
        entry = store_registry.DATA_DIR_ENTRIES.get(name)
        return entry is not None and not entry.backed_up

    monkeypatch.setattr(store_registry, "kept_out_of_backups", exact_names_only)
    monkeypatch.setattr(universal, "_offsite_leaves_out", lambda rel: rel == ".env")


def _secret_names(names):
    from kazma_core.store_registry import is_secret_file_name

    return {n for n in names if is_secret_file_name(n.rsplit("/", 1)[-1])}


def test_a_migration_folder_is_not_backed_up(data_dir, tmp_path):
    dest = _backup_of(data_dir, tmp_path)
    assert not any(p.name.startswith(".migrate-") for p in dest.iterdir())
    assert (dest / "sub" / "notes.txt").is_file()
    assert all(".migrate-" not in str(db) for db in universal.selectable_databases(data_dir))


def test_no_secret_file_reaches_the_offsite_zip(data_dir, tmp_path):
    dest = _backup_of(data_dir, tmp_path)
    names = _names(universal._zip_backup_dir(dest))
    assert _secret_names(names) == set(), names
    assert {"sub/notes.txt", "sub/.env.example"} <= names  # a template is no secret
    assert (dest / "sub" / ".env").is_file(), "the local backup keeps its copies"


def test_every_secret_file_is_excluded_from_the_rclone_copy(data_dir, tmp_path):
    dest = _backup_of(data_dir, tmp_path)
    argv = universal._offsite_rclone_excludes(dest)
    assert set(argv[0::2]) == {"--exclude"}
    excluded = set(argv[1::2])
    on_disk = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    assert excluded == {"/" + n for n in _secret_names(on_disk)}
    assert {"/.env", "/sub/.env", "/restic.pass"} <= excluded


def test_negative_control_the_old_rules_send_the_key_offsite(data_dir, tmp_path, monkeypatch):
    _old_rules(monkeypatch)
    dest = _backup_of(data_dir, tmp_path)
    names = _names(universal._zip_backup_dir(dest))
    assert ".migrate-staging-1700000000/meta.env" in names
    assert "sub/.env" in names


def test_negative_control_the_old_walk_backs_up_the_staged_vault(data_dir, monkeypatch):
    from kazma_core import store_registry

    monkeypatch.setattr(store_registry, "kept_out_of_backups", lambda name: False)
    assert any(".migrate-" in str(db) for db in universal.selectable_databases(data_dir))


def test_an_rclone_exclude_matches_only_its_own_file(tmp_path):
    dest = tmp_path / "b"
    (dest / "a[1]").mkdir(parents=True)
    (dest / "a[1]" / ".env").write_text("x", encoding="utf-8")
    assert universal._offsite_rclone_excludes(dest) == ["--exclude", "/a\\[1\\]/.env"]
