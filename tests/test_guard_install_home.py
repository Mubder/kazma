"""Guard / stall dumps stay inside the Kazma install, not ~/.kazma."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_GUARD_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "service" / "kazma_guard.py"
)


def _load_guard():
    spec = importlib.util.spec_from_file_location("kazma_guard_home", _GUARD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_guard_file_uses_install_not_user_home(tmp_path: Path) -> None:
    install_home = tmp_path / "install" / ".kazma"
    legacy_home = tmp_path / "userhome" / ".kazma"
    legacy_home.mkdir(parents=True)
    (legacy_home / "guard.state.json").write_text('{"child_pid": 1}', encoding="utf-8")

    guard = _load_guard()
    guard._kazma_home = lambda: install_home  # type: ignore[method-assign]
    guard._legacy_home = lambda: legacy_home  # type: ignore[method-assign]

    dest = guard._guard_file("guard.state.json")
    assert dest == install_home / "guard.state.json"
    assert dest.read_text(encoding="utf-8") == '{"child_pid": 1}'
    dest.write_text('{"child_pid": 2}', encoding="utf-8")
    assert (legacy_home / "guard.state.json").read_text(encoding="utf-8") == '{"child_pid": 1}'


def test_write_fallbacks_are_not_user_home() -> None:
    """Exception fallbacks must not recreate ~/.kazma."""
    root = Path(__file__).resolve().parents[1] / "kazma-core" / "kazma_core"
    for rel in ("cli/wizard.py", "hub/cli.py", "system/installer.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert 'Path.home() / ".kazma"' not in text, rel
    # Negative control: the grep catches the real pattern (legacy helper).
    assert 'Path.home() / ".kazma"' in (root / "paths.py").read_text(encoding="utf-8")


def test_stall_dump_dir_uses_project_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kazma_core.observability import loop_stall

    project_home = tmp_path / "kazma-home"
    monkeypatch.setattr("kazma_core.paths.user_home", lambda: project_home)
    d = loop_stall.stall_dump_dir()
    assert d == project_home
    assert d != Path.home() / ".kazma"
