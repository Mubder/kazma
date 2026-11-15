"""Every secret file Kazma writes or copies is readable by its owner only.

Measured 2026-11-15 on real files (``readable_by_others``), in a folder that
lets ``Users`` read what is made in it (``open_folder`` below):

* ``restrict_to_owner`` (and the guard's copy) ran ``icacls /inheritance:r
  /grant:r <account>:(F)``, which keeps another account's EXPLICIT entry: a
  file with an explicit ``Users:R`` stayed readable by Users and the call
  answered True. Both now set the whole access list in one step and read it
  back (``security/owner_only.py``, one file the guard loads by path).
* ``kazma migrate import`` rewrote a restricted ``.env`` (the vault key)
  readable by Users; setup wrote first and restricted after. Every env file
  is now written by ``env_files.set_env_values`` (restricted before the key
  is in it).
* The universal backup copied ``.env`` beside ``dbs/vault.db`` with
  ``shutil.copy2`` (readable by Users), a downloaded archive and a migration
  bundle -- the vault and its key in one file -- likewise.

Planted values only; every file is in a temporary folder.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kazma_core.security import file_access, owner_only
from kazma_core.security.file_access import readable_by_others

REPO = Path(__file__).resolve().parents[1]


def _load_guard():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_owner_only_guard", REPO / "scripts" / "service" / "kazma_guard.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()
KEY = "v" * 32 + "-planted-vault-key-" + "9" * 16
USERS = "*S-1-5-32-545"


@pytest.fixture
def open_folder(tmp_path):
    """A folder whose new files other accounts can read (Users on Windows,
    group/others on POSIX), like the backups folder that was measured."""
    folder = tmp_path / "shared"
    folder.mkdir()
    if os.name == "nt":
        subprocess.run(["icacls", str(folder), "/grant", f"{USERS}:(OI)(CI)(R)"],
                       capture_output=True, check=True)
        yield folder
    else:
        folder.chmod(0o755)
        old = os.umask(0o022)
        try:
            yield folder
        finally:
            os.umask(old)


def _readable(path: Path) -> list[str]:
    readers = readable_by_others(path)
    assert readers is not None, f"could not read the permissions of {path}"
    return readers


def _plain_file(folder: Path, name: str, text: str = "x\n") -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


# ── item 6: the restriction removes explicit entries, and says so honestly ──

def _grant_users_explicitly(path: Path) -> None:
    if os.name == "nt":
        subprocess.run(["icacls", str(path), "/grant", f"{USERS}:(R)"], capture_output=True, check=True)
    else:
        path.chmod(0o644)


@pytest.mark.parametrize("which", ["kazma", "guard"])
def test_an_explicit_entry_for_another_account_is_removed(tmp_path, which):
    path = _plain_file(tmp_path, "secret.txt")
    _grant_users_explicitly(path)
    assert _readable(path)
    if which == "kazma":
        assert file_access.restrict_to_owner(path) is True
    else:
        assert guard._restrict_to_owner(path) is True
    assert _readable(path) == []


@pytest.mark.skipif(os.name != "nt", reason="the access list is a Windows ACL; POSIX gets mode 600")
def test_control_the_old_command_kept_the_explicit_entry(tmp_path):
    path = _plain_file(tmp_path, "secret.txt")
    _grant_users_explicitly(path)
    sid = owner_only.current_user_sid()
    done = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
                          capture_output=True, check=False)
    assert done.returncode == 0  # what the old code returned as "restricted"
    assert "Users" in _readable(path)


def test_a_failed_restriction_is_never_reported_as_success(tmp_path, monkeypatch):
    """The answer is read back: a change that did not take is False."""
    path = _plain_file(tmp_path, "secret.txt")
    monkeypatch.setattr(owner_only, "restrict", lambda p: True)  # claims success, changes nothing
    _grant_users_explicitly(path)
    assert file_access.restrict_to_owner(path) is False


@pytest.mark.skipif(os.name != "nt", reason="inheritance is a Windows ACL")
def test_a_restricted_folder_gives_its_files_the_same(open_folder):
    folder = open_folder / "keys"
    folder.mkdir()
    assert _readable(_plain_file(folder, "before.pem"))
    assert file_access.restrict_to_owner(folder) is True
    assert _readable(_plain_file(folder, "after.pem")) == []


def test_the_guard_and_kazma_share_one_implementation():
    """The guard loads kazma_core/security/owner_only.py by path; that file
    stays standard library only (the guard never imports the app)."""
    loaded = guard._owner_only()
    assert Path(loaded.__file__).resolve() == Path(owner_only.__file__).resolve()
    tree = ast.parse(Path(owner_only.__file__).read_text(encoding="utf-8"))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported
    guard_source = (REPO / "scripts" / "service" / "kazma_guard.py").read_text(encoding="utf-8")
    assert '"/inheritance:r"' not in guard_source  # no second copy of the rule


# ── item 1: one env-file writer, restricted before the key is in it ──────

def _restricted_env(folder: Path) -> Path:
    env = _plain_file(folder, ".env", "OTHER=1\nKAZMA_VAULT_KEY=old\n")
    assert file_access.restrict_to_owner(env)
    return env


def _write_vault_pairing(env: Path) -> None:
    from kazma_core.migration.vault_pairing import _set_env_key

    _set_env_key(env, "KAZMA_VAULT_KEY", KEY)


def _write_setup(env: Path) -> None:
    from kazma_cli.setup import _set_env_value

    _set_env_value(env, "KAZMA_VAULT_KEY", KEY)


def _write_generated(env: Path) -> None:
    from kazma_core.env_files import persist_generated_secret

    persist_generated_secret("KAZMA_VAULT_KEY", KEY, path=env)


def _write_rollback(env: Path) -> None:
    from kazma_core.migration.importer import _VaultUndo

    _VaultUndo(env, f"OTHER=1\nKAZMA_VAULT_KEY={KEY}\n", None, None, env.parent / "vault.db").restore()


WRITERS = {"migrate import": _write_vault_pairing, "setup": _write_setup,
           "first boot": _write_generated, "import rollback": _write_rollback}


@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_every_env_writer_keeps_the_file_private(open_folder, writer, monkeypatch):
    monkeypatch.delenv("KAZMA_VAULT_KEY", raising=False)
    env = _restricted_env(open_folder)
    WRITERS[writer](env)
    text = env.read_text(encoding="utf-8")
    assert f"KAZMA_VAULT_KEY={KEY}" in text and "OTHER=1" in text
    assert text.count("KAZMA_VAULT_KEY=") == 1
    assert _readable(env) == []
    assert [p.name for p in open_folder.iterdir()] == [".env"]  # no temporary file left


@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_the_new_file_is_restricted_before_the_key_is_in_it(open_folder, writer, monkeypatch):
    monkeypatch.delenv("KAZMA_VAULT_KEY", raising=False)
    env = open_folder / ".env"
    seen: list[str] = []
    real = file_access.restrict_to_owner

    def record(path):
        seen.append(Path(path).read_text(encoding="utf-8") if Path(path).is_file() else "")
        return real(path)

    monkeypatch.setattr(file_access, "restrict_to_owner", record)
    WRITERS[writer](env)
    assert seen and KEY not in seen[0]
    assert _readable(env) == []


def test_control_the_old_import_writer_left_the_key_readable(open_folder):
    """``vault_pairing._set_env_key`` before 2026-11-15."""
    env = _restricted_env(open_folder)
    tmp = env.with_suffix(env.suffix + ".tmp")
    tmp.write_text(f"OTHER=1\nKAZMA_VAULT_KEY={KEY}\n", encoding="utf-8")
    os.replace(tmp, env)
    assert _readable(env)


def test_an_env_line_is_set_once_and_the_rest_is_kept():
    from kazma_core.env_files import _env_text_with

    text = "# KAZMA_PORT=9090\nA=1\nexport KAZMA_PORT=1\nKAZMA_PORT=2\n# note: KAZMA_PORT=x\n"
    out = _env_text_with(text, {"KAZMA_PORT": "9191", "NEW": "y"})
    assert out == "KAZMA_PORT=9191\nA=1\n# note: KAZMA_PORT=x\nNEW=y\n"


# ── item 2: copies in a backup, archives, bundles ────────────────────────

def test_a_backups_env_copy_and_its_vault_are_private(open_folder, tmp_path, monkeypatch):
    from kazma_core.backup import universal

    install = tmp_path / "install"
    (install / "kazma-data").mkdir(parents=True)
    _plain_file(install, ".env", f"KAZMA_VAULT_KEY={KEY}\n")
    monkeypatch.setattr(universal, "_data_dir", lambda: install / "kazma-data")
    monkeypatch.setattr("kazma_core.config_store.get_config_store",
                        lambda: type("S", (), {"get": lambda self, k, d=None: "none-here"})())
    dest = open_folder / "1700000000"
    (dest / "dbs").mkdir(parents=True)
    vault_copy = _plain_file(dest / "dbs", "vault.db", "encrypted")
    result = universal._copy_root_artifacts(dest)
    assert result["env"]["restricted"] is True
    assert _readable(dest / ".env") == []
    assert _readable(vault_copy)  # copied by the database step: still open here
    assert file_access.restrict_secret_files(dest) == []
    assert _readable(vault_copy) == []
    assert _readable(_plain_file(dest, "manifest.json")) != []  # an ordinary file is left as it is


def test_control_a_plain_copy_of_the_env_is_readable(open_folder, tmp_path):
    src = _plain_file(tmp_path, ".env", f"KAZMA_VAULT_KEY={KEY}\n")
    shutil.copy2(src, open_folder / ".env")  # the backup's copy before 2026-11-15
    assert _readable(open_folder / ".env")


def test_a_downloaded_backup_archive_is_private(open_folder, monkeypatch):
    from kazma_core.backup import universal

    backup = open_folder / "1700000000"
    (backup / "dbs").mkdir(parents=True)
    _plain_file(backup, ".env", f"KAZMA_VAULT_KEY={KEY}\n")
    _plain_file(backup / "dbs", "vault.db", "encrypted")
    monkeypatch.setattr(universal, "_universal_dir", lambda: open_folder)
    result = universal.archive_universal_backup("1700000000")
    assert result["ok"], result
    assert _readable(Path(result["archive"])) == []
    assert not list(open_folder.glob(".*.partial"))
    zipped = universal._zip_backup_dir(backup)  # the offsite copy's archive
    assert _readable(zipped) == []


def test_an_archive_that_cannot_be_made_private_is_not_written(open_folder, monkeypatch):
    from kazma_core.backup import universal

    backup = open_folder / "1700000000"
    backup.mkdir()
    _plain_file(backup, ".env", f"KAZMA_VAULT_KEY={KEY}\n")
    monkeypatch.setattr(universal, "_universal_dir", lambda: open_folder)
    monkeypatch.setattr(file_access, "restrict_to_owner", lambda p: False)
    result = universal.archive_universal_backup("1700000000")
    assert result["ok"] is False
    assert not (open_folder / "1700000000.zip").exists()
    assert not list(open_folder.glob(".*.partial"))


def _staging(tmp_path: Path) -> Path:
    from kazma_core.env_files import write_env_text

    staging = tmp_path / "staging"
    (staging / "data").mkdir(parents=True)
    _plain_file(staging / "data", "vault.db", "encrypted")
    write_env_text(staging / "meta.env", f"KAZMA_VAULT_KEY={KEY}\n")
    return staging


def test_a_migration_bundle_is_private(open_folder, tmp_path):
    from kazma_core.migration.bundle import Manifest
    from kazma_core.migration.exporter import _finish_bundle

    staging = _staging(tmp_path)
    assert _readable(staging / "meta.env") == []
    out = open_folder / "kazma-bundle-test.zip"
    _finish_bundle(staging, out, Manifest(), {"KAZMA_VAULT_KEY": KEY}, lambda m: None)
    assert out.is_file() and _readable(out) == []
    assert [p.name for p in open_folder.iterdir()] == ["kazma-bundle-test.zip"]


def test_a_bundle_that_cannot_be_made_private_is_refused(open_folder, tmp_path, monkeypatch):
    from kazma_core.migration.bundle import Manifest
    from kazma_core.migration.exporter import ExportRefused, _finish_bundle

    staging = _staging(tmp_path)
    monkeypatch.setattr(file_access, "restrict_to_owner", lambda p: False)
    with pytest.raises(ExportRefused, match="vault key"):
        _finish_bundle(staging, open_folder / "b.zip", Manifest(), {"KAZMA_VAULT_KEY": KEY}, lambda m: None)
    assert list(open_folder.iterdir()) == []


def test_the_default_bundle_folder_is_never_a_git_checkout(tmp_path, monkeypatch):
    from kazma_cli import migrate

    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: checkout / "home"))
    folder = migrate._default_bundle_dir()
    assert migrate._inside_git_checkout(folder) is None
    assert migrate._inside_git_checkout(checkout / "home" / "kazma-bundles") == checkout
    plain_home = tmp_path / "plain"
    plain_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: plain_home))
    assert migrate._default_bundle_dir() == plain_home / "kazma-bundles"


def test_the_export_command_says_the_bundle_holds_the_key(tmp_path, monkeypatch, capsys):
    from kazma_cli import migrate

    written: list[str] = []

    def export_bundle(out_path, **kw):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"PK")
        written.append(str(out_path))
        return Path(out_path)

    monkeypatch.setattr("kazma_core.migration.export_bundle", export_bundle)
    monkeypatch.setattr(migrate, "_default_bundle_dir", lambda: tmp_path / "bundles")
    migrate._cmd_export([])
    said = capsys.readouterr().out
    assert "KAZMA_VAULT_KEY" in said and "Only this account can read it" in said
    assert written and Path(written[0]).parent == tmp_path / "bundles"
    # --out inside a git checkout is warned about.
    checkout = tmp_path / "repo"
    (checkout / ".git").mkdir(parents=True)
    migrate._cmd_export(["--out", str(checkout / "b.zip")])
    assert "inside the git checkout" in capsys.readouterr().out


def test_git_ignores_a_bundle_in_the_checkout():
    ignored = subprocess.run(["git", "check-ignore", "-q", "kazma-bundle-20261115-120000.zip"],
                             cwd=REPO, capture_output=True, check=False)
    assert ignored.returncode == 0
