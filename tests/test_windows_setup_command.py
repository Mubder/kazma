"""A fresh Windows can run Kazma's setup.

On a clean Windows 11 (the clean drill VM, 2026-11-07) the README's first
Windows command, ``.\\setup.ps1``, was refused before its first line ran:
"running scripts is disabled on this system". A Windows client's PowerShell
execution policy is Restricted until someone changes it, and it applies to
every ``.ps1`` file. The fix is ``setup.cmd`` beside the script: a batch file is
not a PowerShell script, so the policy does not apply to it, and it starts
Windows PowerShell with ``-ExecutionPolicy Bypass`` for that one process (the
machine's policy is not changed; a Group Policy still wins).

These tests run it the way that machine did, with the policy Restricted
(``PSExecutionPolicyPreference``, the process scope, which outranks the
machine's own setting): ``setup.cmd`` runs setup.ps1, passes its arguments and
its exit code through, and works from another folder; ``powershell -File
setup.ps1`` without the bypass is refused (the negative control). And every
published instruction to run setup on Windows names ``setup.cmd``, never the
refused ``.\\setup.ps1``.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_powershell_scripts_start import _sync_folder

ROOT = Path(__file__).resolve().parent.parent

_DISABLED = "running scripts is disabled"
_POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"

on_windows = pytest.mark.skipif(
    os.name != "nt" or not _POWERSHELL.exists(), reason="Windows PowerShell runs only on Windows")


def _restricted_env(base: dict | None = None) -> dict:
    env = dict(base if base is not None else os.environ)
    env["PSExecutionPolicyPreference"] = "Restricted"
    return env


def _run(argv: list[str], *, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    # stdin from nowhere: setup.ps1 then knows no one can answer, and opens
    # no administrator window.
    return subprocess.run(
        argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=300,
    )


def _copy_pair(folder: Path, cmd_text: str | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "setup.ps1").write_bytes((ROOT / "setup.ps1").read_bytes())
    cmd = folder / "setup.cmd"
    if cmd_text is None:
        cmd.write_bytes((ROOT / "setup.cmd").read_bytes())
    else:
        cmd.write_text(cmd_text, encoding="ascii", newline="")
    return cmd


@on_windows
def test_setup_cmd_runs_where_scripts_are_disabled(tmp_path):
    """Under a Restricted policy setup.cmd runs setup.ps1: its own answer to
    a bad -Mode (exit 2) proves the script ran, got the arguments and handed
    its exit code back. The same script started the README's old way is
    refused before it runs."""
    cmd = _copy_pair(tmp_path / "kazma")
    (cmd.parent / "pyproject.toml").write_text('[project]\nname = "kazma"\nversion = "9.8.7"\n', encoding="utf-8")
    env = _restricted_env()

    done = _run(["cmd.exe", "/d", "/c", str(cmd), "-Mode", "bogus"], cwd=cmd.parent, env=env)
    output = done.stdout + done.stderr
    assert _DISABLED not in output, output
    assert "-Mode is express, full or custom (not 'bogus')" in output, output
    assert done.returncode == 2, output
    # Its first line says which Kazma it installs (no git here: no commit).
    # Windows PowerShell writes a pipe in the console's code page, so only
    # the ASCII of that line is compared.
    assert re.search(r"Kazma \S+ 9\.8\.7 \S+ setup", output), output

    # The negative control: .\setup.ps1 as the README said, on that machine.
    refused = _run([str(_POWERSHELL), "-NoProfile", "-File", str(cmd.parent / "setup.ps1"), "-Mode", "bogus"],
                   cwd=cmd.parent, env=env)
    assert _DISABLED in refused.stdout + refused.stderr
    assert "-Mode is express" not in refused.stdout


@on_windows
def test_setup_cmd_works_from_another_folder(tmp_path):
    """Started by its full path from the folder above (``kazma\\setup.cmd``),
    setup runs in Kazma's folder: past its project checks and uv sync (a
    stand-in uv), it stops at the imports of a venv that holds nothing. A
    setup.cmd that does not change to its own folder stops at "kazma.yaml not
    found" instead (the negative control)."""
    script, env = _sync_folder(tmp_path / "kazma", (ROOT / "setup.ps1").read_text(encoding="utf-8-sig"))
    cmd = script.parent / "setup.cmd"
    cmd.write_bytes((ROOT / "setup.cmd").read_bytes())
    env = _restricted_env(env)

    done = _run(["cmd.exe", "/d", "/c", str(cmd)], cwd=tmp_path, env=env)
    output = done.stdout + done.stderr
    assert _DISABLED not in output, output
    assert "kazma.yaml found" in output, output
    assert "Environment synced" in output, output
    assert done.returncode == 1, output  # its own check: the venv holds nothing

    stays = (ROOT / "setup.cmd").read_text(encoding="ascii")
    without_cd = "\r\n".join(line for line in stays.splitlines() if not line.startswith("cd /d"))
    assert "cd /d" in stays and "cd /d" not in without_cd
    old_cmd = script.parent / "setup-old.cmd"  # beside the same setup.ps1
    old_cmd.write_text(without_cd + "\r\n", encoding="ascii", newline="")
    lost = _run(["cmd.exe", "/d", "/c", str(old_cmd)], cwd=tmp_path, env=env)
    assert "kazma.yaml not found" in lost.stdout + lost.stderr


def test_setup_cmd_is_checked_out_with_crlf_endings():
    """Command Prompt misreads a batch file with LF endings in places, and a
    clone's own core.autocrlf decides what it gets unless the repository says
    so. The file is ASCII (no code page question) and ends every line CRLF."""
    attrs = subprocess.run(["git", "check-attr", "eol", "--", "setup.cmd"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout
    assert attrs.strip().endswith("eol: crlf"), attrs
    tracked = subprocess.run(["git", "ls-files", "--", "setup.cmd"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    assert tracked.strip() == "setup.cmd", "setup.cmd is not tracked"
    data = (ROOT / "setup.cmd").read_bytes()
    assert all(byte < 128 for byte in data)
    if os.name == "nt":
        assert data.count(b"\n") == data.count(b"\r\n")


# ── every instruction names setup.cmd ─────────────────────────────────────

# `.\setup.ps1` (or ./setup.ps1) used as a command. Naming the file is fine;
# running it the way a fresh Windows refuses is not, unless the line bypasses
# the policy itself.
_REFUSED_COMMAND = re.compile(r"(?<![\w/\\-])\.[\\/]setup\.ps1\b")


def _published_texts() -> dict[str, str]:
    files = [ROOT / "README.md", ROOT / "README.ar.md", ROOT / "CONTRIBUTING.md",
             ROOT / "setup.ps1", ROOT / "kazma-core" / "kazma_core" / "product_knowledge.py"]
    files += sorted((ROOT / "docs" / "docs").rglob("*.md"))
    return {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8-sig") for p in files if p.is_file()}


def _refused_commands(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()
            if _REFUSED_COMMAND.search(line) and "ExecutionPolicy Bypass" not in line]


def test_every_instruction_to_run_setup_names_setup_cmd():
    found = {name: rows for name, text in _published_texts().items()
             if (rows := _refused_commands(text))}
    assert found == {}, found


def test_the_instruction_check_catches_the_old_readme():
    old_readme = (
        "**Windows (PowerShell)**\n\n```powershell\n"
        "git clone https://github.com/Mubder/kazma.git\ncd kazma\n.\\setup.ps1\n```\n"
    )
    assert _refused_commands(old_readme) == [".\\setup.ps1"]
    assert _refused_commands('Log-Info "Everything:     .\\setup.ps1 -Mode full"')
    assert _refused_commands("powershell -ExecutionPolicy Bypass -File .\\setup.ps1") == []
    assert _refused_commands("setup.ps1 is the Windows bootstrap; .\\setup.cmd runs it") == []
