"""A module loaded from a file path is registered before it runs.

``@dataclass`` looks its class's module up in ``sys.modules`` while the class
is being defined. A module loaded with ``spec_from_file_location`` and
``exec_module`` and never registered raises there. On the drill VM
(2026-11-09) ``kazma update``'s "fresh process" step loaded ``update.py`` that
way. Its first ``@dataclass`` raised, so every update fell back to the
pre-pull installer -- the case the step exists to avoid.

Two halves:
- the updater's child really runs a module holding a frozen dataclass, with
  the code as written as the negative control;
- every ``exec_module(`` in product code and scripts registers the module in
  ``sys.modules`` in the lines before it, with a planted load as the control.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_STUB = '''from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Release:
    tag: str
    page: str = ""


def _reinstall_local(cwd):
    return Release("v1").tag == "v1"
'''


def _child_code(monkeypatch, cwd: Path) -> str:
    """The code ``_reinstall_via_subprocess`` hands its child interpreter."""
    from kazma_cli import update

    seen: list[list[str]] = []

    def fake_run(cmd, cwd=None, timeout=None):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(update, "_run_cmd", fake_run)
    assert update._reinstall_via_subprocess(str(cwd)) is True
    return seen[0][2]


def _run(code: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60, check=False)


def test_the_updaters_fresh_process_runs_a_module_with_a_dataclass(tmp_path, monkeypatch):
    root = tmp_path / "install"
    (root / "kazma-cli" / "kazma_cli").mkdir(parents=True)
    (root / "kazma-cli" / "kazma_cli" / "update.py").write_text(_STUB, encoding="utf-8")
    code = _child_code(monkeypatch, root)
    done = _run(code, root)
    assert done.returncode == 0, done.stderr
    # Control: the child as it was, without the registration, fails there.
    old = code.replace("sys.modules[spec.name] = mod\n", "")
    assert old != code
    failed = _run(old, root)
    assert failed.returncode != 0 and "dataclasses.py" in failed.stderr, failed.stderr


def _unregistered_loads(files: list[Path]) -> list[str]:
    found = []
    for path in files:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for number, line in enumerate(lines, 1):
            if "exec_module(" not in line:
                continue
            before = "\n".join(lines[max(0, number - 8):number - 1])
            if "sys.modules[" not in before:
                found.append(f"{path.relative_to(REPO).as_posix()}:{number}")
    return found


def _product_files() -> list[Path]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "--cached", "--others", "--exclude-standard",
                          "*.py"], capture_output=True, text=True, check=True).stdout.splitlines()
    return [REPO / f for f in out
            if (f.startswith("kazma-") or f.startswith("scripts/"))
            and "/tests/" not in f and (REPO / f).is_file()]


def test_every_load_by_path_registers_the_module():
    assert _unregistered_loads(_product_files()) == []


def test_an_unregistered_load_is_found(tmp_path, monkeypatch):
    """The negative control: a load with no registration before it."""
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    planted = tmp_path / "loader.py"
    planted.write_text(
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('x', 'x.py')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n", encoding="utf-8")
    kept = tmp_path / "kept.py"
    kept.write_text(
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('x', 'x.py')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = mod\n"
        "spec.loader.exec_module(mod)\n", encoding="utf-8")
    assert _unregistered_loads([planted, kept]) == ["loader.py:4"]
