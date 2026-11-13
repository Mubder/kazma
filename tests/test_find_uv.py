"""Kazma finds the uv its environment is made with, also off PATH
(2026-11-13, ``kazma_core.uv_cli``).

The drill VM: ``kazma setup postgres`` ran in a session whose PATH did not
yet hold ``~/.local/bin`` (setup puts uv there and adds the folder to the
account's PATH for later terminals). The driver step looked on PATH only,
fell back to ``python -m pip``, and Kazma's uv-made environment has no pip:
"No module named pip", and nothing was set up.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from kazma_core import uv_cli

EXE = "uv.exe" if os.name == "nt" else "uv"


def _home_with_uv(tmp_path: Path, *parts: str) -> Path:
    folder = tmp_path.joinpath(*parts)
    folder.mkdir(parents=True)
    (folder / EXE).write_bytes(b"")
    return tmp_path


def test_uv_on_path_is_taken_first(tmp_path):
    home = _home_with_uv(tmp_path, ".local", "bin")
    assert uv_cli.find_uv(lambda name: "/on/path/uv", home=home) == "/on/path/uv"


@pytest.mark.parametrize("parts", [(".local", "bin"), (".cargo", "bin")])
def test_uv_off_path_is_found_where_setup_and_its_installer_put_it(tmp_path, monkeypatch, parts):
    monkeypatch.delenv("UV_INSTALL_DIR", raising=False)
    home = _home_with_uv(tmp_path, *parts)
    assert uv_cli.find_uv(lambda name: None, home=home) == str(home.joinpath(*parts) / EXE)


def test_uv_install_dir_is_honoured(tmp_path, monkeypatch):
    folder = tmp_path / "tools"
    folder.mkdir()
    (folder / EXE).write_bytes(b"")
    monkeypatch.setenv("UV_INSTALL_DIR", str(folder))
    assert uv_cli.find_uv(lambda name: None, home=tmp_path / "empty") == str(folder / EXE)


def test_no_uv_anywhere_is_none(tmp_path, monkeypatch):
    monkeypatch.delenv("UV_INSTALL_DIR", raising=False)
    assert uv_cli.find_uv(lambda name: None, home=tmp_path) is None


def test_negative_control_path_alone_misses_it(tmp_path, monkeypatch):
    """The lookup before 2026-11-13 (PATH only) finds nothing where uv is in
    ~/.local/bin and that folder is not on PATH yet; the finder does."""
    import shutil

    monkeypatch.delenv("UV_INSTALL_DIR", raising=False)
    home = _home_with_uv(tmp_path, ".local", "bin")
    path_without_it = str(tmp_path / "nowhere")
    assert shutil.which("uv", path=path_without_it) is None
    assert uv_cli.find_uv(lambda name: shutil.which(name, path=path_without_it), home=home) is not None


def test_the_postgres_driver_step_uses_uv_off_path(tmp_path, monkeypatch):
    from kazma_cli import setup

    home = _home_with_uv(tmp_path, ".local", "bin")
    monkeypatch.delenv("UV_INSTALL_DIR", raising=False)
    monkeypatch.setattr("kazma_core.db.postgres_pool.driver_missing", lambda modules=None: "psycopg")
    monkeypatch.setattr(uv_cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(uv_cli.Path, "home", classmethod(lambda cls: home))
    step = setup._driver_step()
    assert step.argv[0] == str(home / ".local" / "bin" / EXE) and step.argv[1:3] == ("pip", "install")


def test_no_uv_and_no_pip_is_said_before_anything_is_done(tmp_path, monkeypatch):
    from kazma_cli import setup

    monkeypatch.delenv("UV_INSTALL_DIR", raising=False)
    monkeypatch.setattr("kazma_core.db.postgres_pool.driver_missing", lambda modules=None: "psycopg")
    monkeypatch.setattr(uv_cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(uv_cli.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(uv_cli, "this_python_has_pip", lambda: False)
    with pytest.raises(RuntimeError, match="run setup again"):
        setup._driver_step()
