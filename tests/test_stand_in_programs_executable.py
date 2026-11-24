"""A test's stand-in program is made executable (2026-11-24).

On Linux a program that is not executable is passed over on PATH and the
machine's own answers: the firewall tests' stand-in ``iptables`` was never
asked on CI, and run by root they would have written that computer's rules.
Windows runs it either way, so only Linux showed it."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _writes_a_program(tree: ast.AST) -> bool:
    """Whether the file writes a shell script (a ``#!`` literal in a write call)."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("write_text", "write_bytes")):
            continue
        for value in (n for arg in node.args for n in ast.walk(arg)):
            if isinstance(value, ast.Constant) and isinstance(value.value, (str, bytes)):
                text = value.value.decode("utf-8", "replace") if isinstance(value.value, bytes) else value.value
                if text.startswith("#!/"):
                    return True
    return False


def _left_unexecutable(name: str, source: str) -> bool:
    return _writes_a_program(ast.parse(source, name)) and "chmod" not in source


def test_every_test_that_writes_a_program_makes_it_executable():
    listed = subprocess.run(["git", "ls-files", "tests/*.py", "tests/**/*.py"], cwd=REPO, capture_output=True,
                            text=True, encoding="utf-8", check=True).stdout.split()
    assert len(listed) > 500, len(listed)
    left = [name for name in listed if _left_unexecutable(name, (REPO / name).read_text(encoding="utf-8"))]
    assert not left, f"written but never made executable (chmod 0o755): {left}"


def test_the_gate_sees_a_planted_one():
    planted = "(here / 'git').write_text('#!/bin/sh exit 1')"
    assert _left_unexecutable("planted.py", planted)
    assert not _left_unexecutable("planted.py", planted + "; (here / 'git').chmod(0o755)")
