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
    assert "$signedPython = Install-SignedPython" in block
    assert '$syncArgs += @("--python", $signedPython)' in block
    assert setup.index("function Find-SignedPython") < setup.index("uv sync @syncArgs")
    assert setup.index("function Install-SignedPython") < setup.index("uv sync @syncArgs")


# ── the newest signed Python, no probe installs one, winget only with a yes ──
# (review M-b, M-c, L-7, 2026-11-27). The functions run as setup runs them;
# py, winget, Read-Host, the signature and each interpreter's version are
# stand-ins, so every rule is exercised on any Windows PC.

_STUBS = r"""
$script:Calls = [System.Collections.Generic.List[string]]::new()
$script:Said = [System.Collections.Generic.List[string]]::new()
function Log-Info { param($m) $script:Said.Add("INFO $m") }
function Log-Warn { param($m) $script:Said.Add("WARN $m") }
function Get-AuthenticodeSignature { param($FilePath)
    [pscustomobject]@{ Status = "Valid"; SignerCertificate = [pscustomobject]@{ Subject = "CN=Python Software Foundation" } } }
function Invoke-Native { param([scriptblock]$Command, [switch]$StdoutOnly)
    $global:LASTEXITCODE = 0
    if ("$Command" -match 'sys\.version_info') { return $script:Versions[$exe] }
    & $Command }
"""


def _run_ps(tmp_path: Path, body: str, *, functions: tuple[str, ...]) -> list[str]:
    setup = (REPO / "setup.ps1").read_text(encoding="utf-8-sig")
    pinned = re.search(r'^\$PythonWingetId = .+$\n(?:#.*\n)*^\$PythonWingetVersion = .+$', setup, re.M)
    assert pinned, "setup.ps1 names the winget package and its pinned version"
    script = "\n".join([pinned.group(0), _STUBS, *(_function(setup, f) for f in functions), body])
    local = tmp_path / "local"
    (local / "Programs" / "Python").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pf").mkdir(exist_ok=True)
    system32 = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    env = {**os.environ, "LOCALAPPDATA": str(local), "ProgramFiles": str(tmp_path / "pf"),
           "PATH": system32 + os.pathsep + str(Path(system32) / "WindowsPowerShell" / "v1.0")}
    env.pop("PYTHON_MANAGER_AUTOMATIC_INSTALL", None)
    out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                          "-Command", script], capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    return [line for line in out.stdout.splitlines() if line.strip()]


def _interpreters(tmp_path: Path, versions: dict[str, str]) -> str:
    """Empty python.exe files in the per-user folders; their versions, as PowerShell."""
    pairs = []
    for folder, version in versions.items():
        exe = tmp_path / "local" / "Programs" / "Python" / folder / "python.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"")
        pairs.append(f"'{exe}' = '{version}'")
    return "$script:Versions = @{ " + "; ".join(pairs) + " }\n"


def test_the_newest_signed_python_is_chosen(tmp_path):
    body = _interpreters(tmp_path, {"Python312": "3.12.10", "Python314": "3.14.10", "Python311": "3.11.9"})
    found = _run_ps(tmp_path, body + "Find-SignedPython", functions=("Find-SignedPython",))
    assert found[-1].lower().endswith(r"python314\python.exe"), found


def test_control_an_older_one_is_taken_only_when_nothing_newer_is_here(tmp_path):
    body = _interpreters(tmp_path, {"Python312": "3.12.10"})
    found = _run_ps(tmp_path, body + "Find-SignedPython", functions=("Find-SignedPython",))
    assert found[-1].lower().endswith(r"python312\python.exe"), found


def test_the_launchers_312_never_wins_over_a_newer_one_and_its_probe_installs_nothing(tmp_path):
    body = _interpreters(tmp_path, {"Python314": "3.14.10", "Elsewhere312": "3.12.10"}) + r"""
$script:Py312 = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Elsewhere312\python.exe')
function py { $script:Calls.Add("py $($args[0]) auto=$env:PYTHON_MANAGER_AUTOMATIC_INSTALL")
    if ($args[0] -eq '-3.12') { $global:LASTEXITCODE = 0; $script:Py312 } else { $global:LASTEXITCODE = 1 } }
$r = Find-SignedPython
$script:Calls
"after=$env:PYTHON_MANAGER_AUTOMATIC_INSTALL"
$r
"""
    out = _run_ps(tmp_path, body, functions=("Find-SignedPython",))
    assert out[-1].lower().endswith(r"python314\python.exe"), out
    probes = [line for line in out if line.startswith("py ")]
    assert probes and all(line.endswith("auto=false") for line in probes), probes
    assert "after=" in out, "the variable is put back as it was (unset)"


def _install(tmp_path: Path, *, yes: bool, interactive: bool, answer: str = "", winget_exit: int = 0) -> list[str]:
    body = rf"""
$Yes = ${str(yes).lower()}
$Interactive = ${str(interactive).lower()}
function Read-Host {{ param($Prompt) $script:Calls.Add("asked: $Prompt"); '{answer}' }}
function winget {{ $script:Calls.Add("winget $($args -join ' ')"); 'Downloading'; 'Installer failed with exit code: 1603'
    $global:LASTEXITCODE = {winget_exit} }}
function Find-SignedPython {{ $null }}
$null = Install-SignedPython
$script:Calls
$script:Said
"""
    return _run_ps(tmp_path, body, functions=("Install-SignedPython",))


def test_with_nobody_to_ask_and_no_yes_nothing_is_installed_and_setup_says_so(tmp_path):
    out = _install(tmp_path, yes=False, interactive=False)
    assert not [line for line in out if line.startswith(("winget", "asked"))], out
    assert any(line.startswith("INFO") and "-Yes" in line for line in out), out


def test_with_yes_the_pinned_version_is_installed_without_asking(tmp_path):
    out = _install(tmp_path, yes=True, interactive=True)
    calls = [line for line in out if line.startswith("winget")]
    assert len(calls) == 1 and "--id Python.Python.3.14" in calls[0] and "--version 3.14." in calls[0], out
    assert not [line for line in out if line.startswith("asked")]


def test_a_person_is_asked_once_and_no_means_no(tmp_path):
    out = _install(tmp_path, yes=False, interactive=True, answer="n")
    assert len([line for line in out if line.startswith("asked")]) == 1
    assert not [line for line in out if line.startswith("winget")], out


def test_control_a_person_who_says_yes_gets_it(tmp_path):
    out = _install(tmp_path, yes=False, interactive=True, answer="y")
    assert len([line for line in out if line.startswith("winget")]) == 1, out


def test_a_failed_install_says_wingets_exit_code_and_last_line(tmp_path):
    out = _install(tmp_path, yes=True, interactive=False, winget_exit=1603)
    warn = [line for line in out if line.startswith("WARN")]
    assert warn and "exit code 1603" in warn[0] and "Installer failed with exit code: 1603" in warn[0], out
