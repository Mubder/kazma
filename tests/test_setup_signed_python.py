"""A new Windows install runs on a signed interpreter (2026-11-26).

Windows' file check (Smart App Control) judges the interpreter's own files.
python.org's Python is signed by the Python Software Foundation; the builds
uv downloads are not (measured: python.org 3.14 has no unsigned interpreter
file outside Tcl/Tk, uv's 3.12 build 41 of 49). ``setup.ps1`` therefore makes
a new environment from a python.org Python (``Find-SignedPython``, installed
with winget when none is there) and passes it to ``uv sync --python``.

The function is run as setup runs it, in Windows PowerShell, against
interpreters on this PC shown to it through a folder junction (``py`` and
Program Files out of its reach): a python.org one is chosen, uv's unsigned
build is passed over (the control). Windows only; skipped where this PC has
no such interpreter.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell and Authenticode")


def _function(script: str, name: str) -> str:
    """The text of ``function <name> { ... }`` in *script* (braces counted)."""
    start = script.index(f"function {name} ")
    depth, i = 0, script.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(script[i], 0)
        if depth == 0:
            return script[start:i + 1]
        i += 1


def _find(tmp_path: Path, target: Path) -> str:
    """Run Find-SignedPython with *target* as the only interpreter it can see."""
    setup = (REPO / "setup.ps1").read_text(encoding="utf-8-sig")
    local = tmp_path / "local"
    (local / "Programs" / "Python").mkdir(parents=True)
    link = local / "Programs" / "Python" / "Python312"
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    (tmp_path / "pf").mkdir()
    script = _function(setup, "Invoke-Native") + "\n" + _function(setup, "Find-SignedPython") + "\n" \
        + "$r = Find-SignedPython; if ($r) { $r } else { 'NONE' }\n"
    system32 = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    env = {**os.environ, "LOCALAPPDATA": str(local), "ProgramFiles": str(tmp_path / "pf"),
           "PATH": system32 + os.pathsep + str(Path(system32) / "WindowsPowerShell" / "v1.0")}
    out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                          "-Command", script], capture_output=True, text=True, env=env, timeout=120)
    return (out.stdout.strip().splitlines() or [""])[-1]


def _pythons() -> tuple[Path | None, Path | None]:
    """A python.org interpreter folder (3.11.5-3.14) and uv's own build folder on this PC."""
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
    signed = next((d for d in sorted(local.glob("Python31[1-4]"), reverse=True) if (d / "python.exe").is_file()), None)
    home = None
    cfg = Path(sys.prefix) / "pyvenv.cfg"  # this run's environment and the interpreter it was made from
    if cfg.is_file():
        match = re.search(r"^home\s*=\s*(.+)$", cfg.read_text(encoding="utf-8"), re.M)
        if match:
            home = Path(match.group(1).strip())
    unsigned = home if home is not None and "uv" in str(home).lower() and (home / "python.exe").is_file() else None
    return signed, unsigned


def test_a_python_org_interpreter_is_chosen(tmp_path):
    signed, _ = _pythons()
    if signed is None:
        pytest.skip("no python.org Python 3.11-3.14 on this PC")
    chosen = _find(tmp_path, signed)
    assert chosen.lower().endswith(r"python312\python.exe"), chosen  # the junction to it


def test_control_uvs_unsigned_build_is_passed_over(tmp_path):
    _, unsigned = _pythons()
    if unsigned is None:
        pytest.skip("this run's Python is not a uv-managed build")
    assert _find(tmp_path, unsigned) == "NONE"


def test_setup_uses_it_for_a_new_environment_only():
    setup = (REPO / "setup.ps1").read_text(encoding="utf-8-sig")
    block = setup[setup.index('if (-not (Test-Path ".venv")) {'):]
    assert "$signedPython = Find-SignedPython" in block
    assert '$syncArgs += @("--python", $signedPython)' in block
    assert "--id Python.Python.3.12" in block
    assert setup.index("function Find-SignedPython") < setup.index("uv sync @syncArgs")
