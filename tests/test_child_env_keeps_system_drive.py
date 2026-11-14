"""A program Kazma starts with a minimal environment keeps Windows' SystemDrive.

Live 2026-11-14: a folder named ``%SystemDrive%`` appeared in the live install
(``%SystemDrive%\\ProgramData\\Microsoft\\Windows\\Caches``), made at the minute
Kazma launched its browser four times. Every minimal environment kept
``SystemRoot`` and dropped ``SystemDrive`` -- the browser's, the page parser's,
the search and DNS workers', the document workers' and broker's, the shell
tool's -- and Windows expands ``%SystemDrive%`` in some of its own paths (the
font cache's): unexpanded, the path is relative and lands in the program's
working folder, which was the install.

The gate reads every literal list of variable names in the product: one that
keeps SystemRoot keeps SystemDrive. The behaviour test starts a real program
with the document sandbox's own list (the old list as the negative control).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PRODUCT = ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-tui", "kazma-skills", "scripts")


def _names(node: ast.AST) -> set[str] | None:
    """The string constants of a literal collection (or a dict's keys)."""
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        items = node.elts
    elif isinstance(node, ast.Dict):
        items = [key for key in node.keys if key is not None]
    else:
        return None
    if not items or not all(isinstance(i, ast.Constant) and isinstance(i.value, str) for i in items):
        return None
    return {i.value.lower() for i in items}


def _without_system_drive(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        names = _names(node)
        if names and "systemroot" in names and "systemdrive" not in names:
            lines.append(node.lineno)
    return lines


def test_every_list_that_keeps_system_root_keeps_system_drive():
    missing = []
    for top in PRODUCT:
        for path in (REPO / top).rglob("*.py"):
            if any(part in {".venv", "node_modules", "__pycache__"} for part in path.parts):
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line in _without_system_drive(source):
                missing.append(f"{path.relative_to(REPO).as_posix()}:{line}")
    assert not missing, "keeps SystemRoot without SystemDrive:\n" + "\n".join(missing)


def test_control_the_old_lists_are_found():
    """Negative control: the browser's list as it was, and the renderer's."""
    assert _without_system_drive("allowed = {'systemroot', 'windir', 'temp', 'tmp', 'path'}") == [1]
    assert _without_system_drive('keys = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")') == [1]
    assert _without_system_drive("allowed = {'systemroot', 'systemdrive', 'windir'}") == []


@pytest.mark.skipif(os.name != "nt", reason="Windows expands %SystemDrive% in its own paths")
def test_a_program_started_with_the_sandbox_environment_sees_its_drive():
    from kazma_core.documents.sandbox import _INHERITED_ENV

    def drive_seen(keep) -> str:
        env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        out = subprocess.run(["cmd", "/c", "echo %SystemDrive%"], env=env, capture_output=True,
                             text=True, check=True)
        return out.stdout.strip()

    assert drive_seen(_INHERITED_ENV) == os.environ["SYSTEMDRIVE"]
    # Negative control: the list as it was leaves the text unexpanded.
    old = {"COMSPEC", "LANG", "LC_ALL", "PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "TZ", "WINDIR"}
    assert drive_seen(old) == "%SystemDrive%"


def test_a_child_python_keeps_its_drive(tmp_path):
    """The workers are Python children: one started with a list that keeps
    SystemDrive reads it."""
    env = {k: v for k, v in os.environ.items()
           if k.lower() in {"systemroot", "systemdrive", "windir", "temp", "tmp", "path"}}
    out = subprocess.run([sys.executable, "-I", "-c", "import os; print(os.environ.get('SYSTEMDRIVE', ''))"],
                         env=env, capture_output=True, text=True, check=True, cwd=str(tmp_path))
    assert out.stdout.strip() == os.environ.get("SYSTEMDRIVE", "")
