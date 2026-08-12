"""Architectural enforcement: ``Command(resume=…)`` is built in ONE place.

The WS permission-loop incident (2026-08-12) existed because each transport
built its own resume value and one drifted. PR2 collapses all 9 sites
(HTTP / WS / gateway / watchdog / supersede / 3× steer) into
``build_resume_command`` in ``resume.py``. This test makes that structure
self-enforcing: it fails if any production module outside ``resume.py``
constructs a ``Command(resume=…)``. That catches a future 4th transport the
moment it's added — the real "never again" guarantee for the drift class.

Parsed with ``ast`` so comments/docstrings that merely *mention*
``Command(resume=…)`` don't trip it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROD_ROOTS = ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway")
CHOKEPOINT_SUFFIX = "safety/commitment/resume.py"


def _resume_command_lines(tree: ast.AST) -> list[int]:
    """Line numbers of ``Command(..., resume=...)`` calls in a module."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            continue
        if name != "Command":
            continue
        if any(kw.arg == "resume" for kw in node.keywords):
            hits.append(node.lineno)
    return hits


@pytest.fixture(scope="module")
def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_command_resume_only_in_resume_helper(_repo_root: Path) -> None:
    """No production module outside resume.py may build a resume Command."""
    violations: list[str] = []
    for root in PROD_ROOTS:
        base = _repo_root / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if any(part in {"tests", "__pycache__", "_tests"} for part in path.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            hits = _resume_command_lines(tree)
            if not hits:
                continue
            norm = path.relative_to(_repo_root).as_posix()
            if norm.endswith(CHOKEPOINT_SUFFIX):
                continue  # the one allowed chokepoint
            for ln in hits:
                violations.append(f"{norm}:{ln}")

    assert not violations, (
        "Command(resume=…) must only be constructed in "
        f"{CHOKEPOINT_SUFFIX} (build_resume_command). "
        "A new transport must route through it. Found: " + ", ".join(violations)
    )


def test_chokepoint_helper_exists(_repo_root: Path) -> None:
    """The allowed chokepoint actually defines the helper (not just allowed)."""
    from kazma_core.safety.commitment.resume import (  # noqa: WPS433
        build_resume_command,
        read_pending_interrupt,
    )

    assert callable(build_resume_command)
    assert callable(read_pending_interrupt)
