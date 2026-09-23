"""`kazma docs` finds the docs site and npm the way a user would expect.

Regression for the 2026-09-22 audit: the docs directory was
``Path(__file__).parents[2] / "docs"`` — inside site-packages for an installed
wheel — and ``["npm", ...]`` cannot start ``npm.cmd`` on Windows without a
shell.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kazma_cli import main as cli


def test_docs_in_the_current_directory_win(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli._find_docs_dir() == tmp_path / "docs"


def test_the_source_checkout_is_the_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)  # no docs/ here
    found = cli._find_docs_dir()
    assert (found / "package.json").is_file()
    assert found == Path(cli.__file__).resolve().parents[2] / "docs"


def test_missing_npm_is_a_clear_exit(monkeypatch: pytest.MonkeyPatch, capsys):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        cli._npm_executable()
    assert "npm not found" in capsys.readouterr().out


def test_npm_is_resolved_to_a_path(monkeypatch: pytest.MonkeyPatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: r"C:\Program Files\nodejs\npm.cmd")
    assert cli._npm_executable().endswith("npm.cmd")
