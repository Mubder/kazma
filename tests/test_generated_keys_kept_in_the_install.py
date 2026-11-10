"""A first boot keeps the keys it generates in the install's env file, restricted.

Audit 2026-11-10: when ``KAZMA_SECRET`` or ``KAZMA_VAULT_KEY`` was missing the
server made one and wrote it to ``Path(".env")`` -- the folder it was launched
from, whatever that was -- with that folder's default permissions, and a vault
key that could not be written was a WARNING. Now
``kazma_ui.app._ensure_generated_secrets`` writes through
``env_files.persist_generated_secret``: the most specific Kazma-owned file of
the server's ladder (the ``.env`` beside the install when launched there, as
before; ``<kazma home>/.env`` otherwise), restricted to this account before
the key is written; a vault key that cannot be kept is an ERROR saying the
stored secrets will be unreadable after a restart.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kazma_core import env_files
from kazma_core import paths as paths_mod
from kazma_core.security import file_access
from kazma_ui import app as app_mod


@pytest.fixture
def layout(tmp_path: Path, monkeypatch):
    """An install root, Kazma's home inside it, and an unrelated folder."""
    install = tmp_path / "install"
    home = install / ".kazma"
    other = tmp_path / "someones-project"
    for folder in (install, home, other):
        folder.mkdir(parents=True)
    (install / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setenv("KAZMA_USER_HOME", str(home))
    monkeypatch.delenv("KAZMA_ENV_FILE", raising=False)
    monkeypatch.delenv("KAZMA_SECRET", raising=False)
    monkeypatch.delenv("KAZMA_VAULT_KEY", raising=False)
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setattr(paths_mod, "installed_project_root", lambda: install)
    monkeypatch.setattr(paths_mod, "get_project_root", lambda: install)
    restricted: list[Path] = []

    def restrict(path):
        assert path.read_text(encoding="utf-8") == "" or "KAZMA_" in path.read_text(encoding="utf-8")
        restricted.append(Path(path))
        return True

    monkeypatch.setattr(file_access, "restrict_to_owner", restrict)
    return install, home, other, restricted


def _keys(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v
    return out


def test_launched_elsewhere_the_keys_go_to_kazmas_home(layout, monkeypatch):
    install, home, other, restricted = layout
    monkeypatch.chdir(other)
    app_mod._ensure_generated_secrets()
    import os

    kept = _keys(home / ".env")
    assert kept["KAZMA_VAULT_KEY"] == os.environ["KAZMA_VAULT_KEY"]
    assert kept["KAZMA_SECRET"] == os.environ["KAZMA_SECRET"]
    assert not (other / ".env").exists()
    assert home / ".env" in restricted
    # The ladder the server loads reads that file at the next boot.
    assert any(p.resolve() == (home / ".env").resolve() for p in env_files.env_file_ladder())


def test_a_foreign_env_in_the_launch_folder_is_left_alone(layout, monkeypatch):
    install, home, other, _restricted = layout
    (other / ".env").write_text("NODE_ENV=dev\n", encoding="utf-8")
    monkeypatch.chdir(other)
    app_mod._ensure_generated_secrets()
    assert (other / ".env").read_text(encoding="utf-8") == "NODE_ENV=dev\n"
    assert "KAZMA_VAULT_KEY" in _keys(home / ".env")


def test_launched_in_the_install_its_env_file_is_used_as_before(layout, monkeypatch):
    install, home, _other, restricted = layout
    (install / ".env").write_text("OTHER=1\n# KAZMA_VAULT_KEY=\n", encoding="utf-8")
    monkeypatch.chdir(install)
    app_mod._ensure_generated_secrets()
    kept = _keys(install / ".env")
    assert kept["OTHER"] == "1" and len(kept["KAZMA_VAULT_KEY"]) == 64 and "KAZMA_SECRET" in kept
    assert "# KAZMA_VAULT_KEY=" not in (install / ".env").read_text(encoding="utf-8")
    assert install / ".env" in restricted
    assert not (home / ".env").exists()


def test_kazma_env_file_wins(layout, monkeypatch, tmp_path):
    _install, home, other, _restricted = layout
    named = tmp_path / "secrets" / "kazma.env"
    monkeypatch.setenv("KAZMA_ENV_FILE", str(named))
    monkeypatch.chdir(other)
    app_mod._ensure_generated_secrets()
    assert "KAZMA_VAULT_KEY" in _keys(named) and not (home / ".env").exists()


def test_the_file_is_restricted_before_the_key_is_in_it(layout, monkeypatch):
    _install, home, other, _restricted = layout
    seen: list[str] = []
    monkeypatch.setattr(file_access, "restrict_to_owner",
                        lambda path: seen.append(Path(path).read_text(encoding="utf-8")) or True)
    monkeypatch.chdir(other)
    env_files.persist_generated_secret("KAZMA_VAULT_KEY", "k" * 64)
    assert seen and "k" * 64 not in seen[0]


def test_a_vault_key_that_cannot_be_kept_is_an_error(layout, monkeypatch, caplog):
    _install, _home, other, _restricted = layout
    monkeypatch.chdir(other)
    monkeypatch.setenv("KAZMA_SECRET", "already-set")

    def refuse(name, value):
        raise PermissionError("read-only folder")

    monkeypatch.setattr(env_files, "persist_generated_secret", refuse)
    with caplog.at_level(logging.ERROR, logger="kazma_ui.app"):
        app_mod._ensure_generated_secrets()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and r.name == "kazma_ui.app"]
    assert errors and "unreadable after a restart" in errors[0].getMessage()


def test_production_never_invents_a_vault_key(layout, monkeypatch):
    _install, home, other, _restricted = layout
    monkeypatch.chdir(other)
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    with pytest.raises(RuntimeError, match="requires KAZMA_VAULT_KEY"):
        app_mod._ensure_generated_secrets()
    assert "KAZMA_VAULT_KEY" not in (_keys(home / ".env") if (home / ".env").exists() else {})


def test_negative_control_the_old_write_went_to_the_launch_folder(layout, monkeypatch):
    """The old code: ``Path(".env").write_text(...)`` in the launch folder,
    with no restriction."""
    _install, home, other, restricted = layout
    monkeypatch.chdir(other)
    Path(".env").write_text("KAZMA_VAULT_KEY=" + "v" * 64 + "\n", encoding="utf-8")
    assert (other / ".env").is_file() and not (home / ".env").exists()
    assert restricted == []
    # ...and that file is not one of Kazma's: nothing restricts or watches it.
    from kazma_core.store_registry import is_kazma_secret_file

    assert not is_kazma_secret_file(other / ".env")
    assert is_kazma_secret_file(env_files._install_env_file())
