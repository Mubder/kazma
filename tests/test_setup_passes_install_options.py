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

REPO = Path(__file__).resolve().parents[1]


def _bash() -> str | None:
    found = shutil.which("bash")
    return found if found and "System32" not in found else None


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
