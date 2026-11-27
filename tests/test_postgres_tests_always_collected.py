"""A test module that needs psycopg is collected everywhere and skipped where it is missing.

2026-11-27: README's test count is checked against what CI collects. CI's
light Tests job has no psycopg, and Spaces' Postgres test modules began with
``psycopg = pytest.importorskip("psycopg")``, which skips the whole module at
collection: its tests vanished from CI's count, and four such modules took
the gap past the README gate's slack. Collected and skipped by a mark, they
count the same on every machine.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _skips_at_collection(source: str, package: str = "psycopg") -> bool:
    """True when the module calls ``pytest.importorskip(package)`` at its top level."""
    for node in ast.parse(source).body:
        call = node.value if isinstance(node, (ast.Assign, ast.Expr)) else None
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "importorskip" and call.args
                and isinstance(call.args[0], ast.Constant) and call.args[0].value == package):
            return True
    return False


def _test_modules() -> list[Path]:
    listed = subprocess.run(["git", "ls-files", "tests", "kazma-core/tests"], cwd=REPO,
                            capture_output=True, text=True, check=True).stdout.split()
    return [REPO / p for p in listed if p.endswith(".py") and Path(p).name.startswith("test_")]


def test_no_test_module_skips_itself_without_psycopg():
    found = [p.relative_to(REPO).as_posix() for p in _test_modules()
             if _skips_at_collection(p.read_text(encoding="utf-8"))]
    assert found == [], found


def test_control_a_module_level_importorskip_is_found():
    assert _skips_at_collection('import pytest\npsycopg = pytest.importorskip("psycopg")\n')
    assert _skips_at_collection('import pytest\npytest.importorskip("psycopg")\n')
    assert not _skips_at_collection('def f():\n    import pytest\n    pytest.importorskip("psycopg")\n')
