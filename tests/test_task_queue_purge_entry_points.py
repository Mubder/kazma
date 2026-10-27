"""The memory task queue's purge has one entry point, and it is called.

Audit 2026-10-26 AUD-078: ``async_purge_completed_tasks`` (an
``asyncio.to_thread`` wrapper of ``purge_completed_tasks``) had no caller.
The purge runs on the maintenance cadence (``worker_bootstrap.
_purge_task_queue``). Every module-level function of ``task_queue`` whose
name says it purges must be called from product code outside the module.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "kazma-core" / "kazma_core" / "memory" / "task_queue.py"
PACKAGES = sorted(p for p in ROOT.glob("kazma-*/kazma_*") if p.is_dir())


def _purge_functions(source: str) -> list[str]:
    return [
        node.name for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "purge" in node.name
    ]


def _uncalled(names: list[str]) -> list[str]:
    product = [
        p.read_text(encoding="utf-8", errors="replace")
        for pkg in PACKAGES for p in pkg.rglob("*.py")
        if p.resolve() != MODULE.resolve() and "tests" not in p.parts
    ]
    return [n for n in names if not any(re.search(rf"\b{re.escape(n)}\b", text) for text in product)]


def test_every_purge_entry_point_has_a_caller():
    names = _purge_functions(MODULE.read_text(encoding="utf-8"))
    assert names == ["purge_completed_tasks"]
    assert _uncalled(names) == []


def test_the_async_wrapper_is_gone():
    import kazma_core.memory.task_queue as tq

    assert not hasattr(tq, "async_purge_completed_tasks")
    assert "async_purge_completed_tasks" not in tq.__all__


def test_negative_control_the_old_module_fails_the_gate():
    """The old module's wrapper, planted: the gate names it."""
    planted = MODULE.read_text(encoding="utf-8") + (
        "\n\nasync def async_purge_completed_tasks(retention_days: int = 7) -> int:\n"
        "    return await asyncio.to_thread(purge_completed_tasks, retention_days=retention_days)\n"
    )
    names = _purge_functions(planted)
    assert "async_purge_completed_tasks" in names
    assert _uncalled(names) == ["async_purge_completed_tasks"]
