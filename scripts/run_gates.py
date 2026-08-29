#!/usr/bin/env python3
"""Run a pytest gate with the repo's own virtualenv, from a pre-commit hook.

Why this shim exists
--------------------
``.pre-commit-config.yaml`` used to invoke the gates as
``.venv/Scripts/python.exe -m pytest …`` under ``language: system``. That
never ran on Windows: pre-commit executes the entry without a shell, and
CreateProcess does not resolve a *relative* forward-slash path, so both hooks
failed with ``[WinError 2] The system cannot find the file specified`` — a
failure that looks like a broken gate rather than a broken invocation.

The obvious fix, plain ``python -m pytest``, is worse: pre-commit sanitises
PATH for ``system`` hooks, so ``python`` resolves to the *interpreter
pre-commit itself runs under* (e.g. ``C:\\Python314\\python.EXE``), which has
none of Kazma's dependencies — ``No module named pytest``.

So: this shim is launched by whatever interpreter pre-commit has, uses only
the standard library, locates the project virtualenv relative to the repo
root, and re-execs pytest there. Falls back to the current interpreter when
no virtualenv is present (CI images that install into the system Python).

Usage (from .pre-commit-config.yaml)::

    entry: python scripts/run_gates.py tests/test_imports.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Interpreter locations to try, in order, relative to the repo root.
_VENV_CANDIDATES = (
    Path(".venv") / "Scripts" / "python.exe",   # Windows
    Path(".venv") / "bin" / "python",           # POSIX
    Path("venv") / "Scripts" / "python.exe",
    Path("venv") / "bin" / "python",
)


def _find_interpreter() -> str:
    """Return the interpreter that has Kazma's dependencies installed.

    Honours ``KAZMA_GATE_PYTHON`` first so an unusual layout (a venv outside
    the repo, a container path) can point at its own interpreter without
    editing the hook config.
    """
    override = (os.environ.get("KAZMA_GATE_PYTHON") or "").strip()
    if override:
        return override

    for rel in _VENV_CANDIDATES:
        candidate = REPO_ROOT / rel
        if candidate.is_file():
            return str(candidate)

    # No project virtualenv — assume dependencies live in the interpreter
    # running this shim (typical for CI images).
    return sys.executable


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: run_gates.py <pytest args...>", file=sys.stderr)
        return 2

    python = _find_interpreter()
    cmd = [python, "-m", "pytest", *argv, "-q", "--no-header", "-p", "no:cacheprovider"]

    try:
        return subprocess.call(cmd, cwd=str(REPO_ROOT))
    except FileNotFoundError:
        print(
            f"gate could not start: {python!r} not found.\n"
            "Create the project virtualenv (uv sync --all-extras), or point\n"
            "KAZMA_GATE_PYTHON at an interpreter that has pytest installed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
