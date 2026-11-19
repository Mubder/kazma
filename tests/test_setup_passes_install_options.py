"""The setup scripts hand install options to ``kazma setup install`` (2026-11-14,
REMAINING 2.2: the fresh-install CI job leaves the two model downloads out).

``./setup.sh ... -- <options>`` and ``.\\setup.cmd ... -InstallOptions "<options>"``.
Each script's own parsing is run (the argument loop of setup.sh in bash, the
split line of setup.ps1 in Windows PowerShell); the CI job must use them.
PowerShell's variable names ignore case, so the option may not be called
``InstallArgs``: setup.ps1 builds ``$installArgs`` itself (the control).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._bash import find_bash

REPO = Path(__file__).resolve().parents[1]


def _bash() -> str | None:
    return find_bash()


def _setup_sh_parser() -> str:
    """setup.sh's option handling, from its first assignment to the end of the loop."""
    text = (REPO / "setup.sh").read_text(encoding="utf-8")
    start = text.index('MODE=""')
    end = text.index("done\n", start) + len("done\n")
    return text[start:end]


@pytest.mark.skipif(_bash() is None, reason="no bash")
def test_setup_sh_hands_what_follows_dashes_to_the_install() -> None:
    script = ("set -euo pipefail\n" + _setup_sh_parser()
              + 'INSTALL_ARGS=(-m kazma_cli setup install)\n'
              + 'INSTALL_ARGS+=(${INSTALL_EXTRA[@]+"${INSTALL_EXTRA[@]}"})\n'
              + 'printf "%s|" "$MODE" "$YES" "${INSTALL_ARGS[@]}"\n')
    out = subprocess.run([_bash(), "-c", script, "setup.sh", "--mode", "express", "-y", "--",
                          "--without", "meaning_search", "--no-browser"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout == "express|1|-m|kazma_cli|setup|install|--without|meaning_search|--no-browser|"
    # With nothing after --, nothing is added (and set -u does not trip on the empty list).
    none = subprocess.run([_bash(), "-c", script, "setup.sh", "-y"], capture_output=True, text=True, timeout=30)
    assert none.returncode == 0 and none.stdout == "|1|-m|kazma_cli|setup|install|", none.stderr


def test_setup_sh_uses_the_expansion_bash_3_accepts() -> None:
    text = (REPO / "setup.sh").read_text(encoding="utf-8")
    assert 'INSTALL_ARGS+=(${INSTALL_EXTRA[@]+"${INSTALL_EXTRA[@]}"})' in text
    assert 'INSTALL_ARGS+=("${INSTALL_EXTRA[@]}")' not in text


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(_powershell() is None, reason="no PowerShell")
def test_setup_ps1_splits_the_options_onto_the_install(tmp_path) -> None:
    text = (REPO / "setup.ps1").read_text(encoding="utf-8-sig")
    assert re.search(r'\[string\]\$InstallOptions = ""', text)
    line = next(ln for ln in text.splitlines() if ln.startswith("if ($InstallOptions)"))
    script = ('param([string]$InstallOptions = "")\n'
              '$installArgs = @("-m", "kazma_cli", "setup", "install")\n'
              + line + '\n($installArgs -join "|")\n')
    path = tmp_path / "options.ps1"
    path.write_text(script, encoding="utf-8")
    out = subprocess.run([_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path),
                          "-InstallOptions", "--without meaning_search  --no-browser"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "-m|kazma_cli|setup|install|--without|meaning_search|--no-browser"


def _elevation_block(text: str) -> str:
    """setup.ps1's reopening in an administrator window, from its list of
    forwarded parameters to the command it runs there."""
    start = text.index("$forward = @()")
    return text[start:text.index("$shell =", start)]


def _forwarded(text: str) -> tuple[set[str], set[str]]:
    params = text[text.index("param("):text.index(")\n", text.index("param("))]
    names = set(re.findall(r"\$(\w+)", params)) - {"NoElevate"}
    block = _elevation_block(text)
    return {name for name in names if f'"-{name}' in block}, names


def test_every_parameter_reaches_the_administrator_window() -> None:
    """2026-11-19: the owner's ``.\\setup.cmd -Mode full -InstallOptions
    "--without docling ..."`` reopened itself as administrator with -Mode
    alone, and Full installed the parts it was told to leave out."""
    text = (REPO / "setup.ps1").read_text(encoding="utf-8-sig")
    forwarded, names = _forwarded(text)
    assert forwarded == names, sorted(names - forwarded)
    # Negative control: the block before the fix forwarded four of five.
    old = text.replace(_elevation_block(text), _elevation_block(text).split("    # Every parameter")[0]
                       + "    $here = ")
    assert _forwarded(old)[0] == names - {"InstallOptions"}


@pytest.mark.skipif(_powershell() is None, reason="no PowerShell")
def test_the_administrator_window_gets_the_options_as_typed(tmp_path) -> None:
    """The command built for the administrator window, run as Start-Process
    runs it (one double-quoted -Command argument), hands the options over."""
    text = (REPO / "setup.ps1").read_text(encoding="utf-8-sig")
    stub = tmp_path / "stub.ps1"
    out_file = tmp_path / "got.txt"
    stub.write_text('param([string]$Mode = "", [switch]$Yes, [int]$Port = 0, [switch]$Debug, '
                    '[string]$InstallOptions = "")\n'
                    f"Set-Content -LiteralPath '{out_file}' -Value \"$Mode|$Yes|$InstallOptions\"\n",
                    encoding="utf-8")
    block = _elevation_block(text).replace("$PSCommandPath", f"'{stub}'")
    script = ('param([switch]$Debug, [string]$Mode = "", [switch]$Yes, [int]$Port = 0, '
              '[string]$InstallOptions = "")\n'
              + block
              + "$shell = (Get-Process -Id $PID).Path\n"
              + 'Start-Process -Wait -NoNewWindow -FilePath $shell -ArgumentList '
                '"-NoProfile -ExecutionPolicy Bypass -Command `"$command`""\n')
    runner = tmp_path / "runner.ps1"
    runner.write_text(script, encoding="utf-8")
    out = subprocess.run([_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner),
                          "-Mode", "full", "-Yes", "-InstallOptions",
                          "--without docling --without cloud_sandbox --label it's"],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    got = out_file.read_text(encoding="utf-8-sig").strip()
    assert got == "full|True|--without docling --without cloud_sandbox --label it's", got


def test_the_option_name_does_not_collide_with_the_scripts_own_list() -> None:
    """Negative control: a parameter named like the list setup.ps1 builds is
    the same variable to PowerShell (names ignore case), and would be lost."""
    text = (REPO / "setup.ps1").read_text(encoding="utf-8-sig")
    params = text[text.index("param("):text.index(")\n", text.index("param("))]
    builds = set(re.findall(r"^\s*\$(\w+)\s*=", text, re.M))
    for name in re.findall(r"\$(\w+)", params):
        clashing = [b for b in builds if b.lower() == name.lower() and b != name]
        assert not clashing, (name, clashing)
    assert "installArgs".lower() not in {p.lower() for p in re.findall(r"\$(\w+)", params)}


def test_the_fresh_install_job_installs_as_a_person_does_on_both_systems() -> None:
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci[ci.index("  fresh-install:"):ci.index("\n  upgrade-drill:")]
    assert "os: [ubuntu-latest, windows-latest]" in job
    assert "./setup.sh --mode express -y -- --without meaning_search --without neural_ocr" in job
    assert ".\\setup.cmd -Mode express -Yes -NoElevate -InstallOptions" in job
    assert "setup install --check" in job and "/health/live" in job
