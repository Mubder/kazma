"""Every hard delete of a memory row is declared, with why nothing is lost.

Memory's rule since 2026-09-26 is that nothing is lost: archiving is cold
storage, and recovery re-creates erased text (``test_memory_nothing_lost``).
Two deletes broke it in place and nothing looked at them. ``/new`` deleted the
conversation's last turn (Stage 2, W3), and a leftover
``POST /api/settings/memory/clean`` -- no page called it, no test covered it --
deleted by pattern: every fact a tool produced, every "concept" entity, every
entity whose id began with four hex letters ("face_id", "dead_sea") and every
turn that said "retroactive".

This gate finds each ``DELETE FROM episodes|beliefs|entities`` in product code
and holds it to a declared reason. A new one fails here until someone writes
down why it loses nothing.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_DELETE = re.compile(r"DELETE\s+FROM\s+(episodes|beliefs|entities)\b", re.IGNORECASE)

#: (file, function) -> why the row is not lost.
DECLARED = {
    ("kazma-core/kazma_core/memory/macro_sleep.py", "run_macro_sleep"):
        "superseded beliefs past the archive age: copied into beliefs_archive first",
    ("kazma-ui/kazma_ui/memory_api.py", "hygiene_run"):
        "operator's archive_invalidated action: copied into beliefs_archive first",
    ("kazma-ui/kazma_ui/memory_api.py", "delete_entity"):
        "operator deletes one graph node by id; its facts stay, the merge ledger is "
        "preserved, and the route offers a restore",
    ("kazma-core/kazma_core/agent/tool_builtins/memory.py", "_mem_delete_entity"):
        "one graph node by id, a danger-tier tool (approval card); protected ids "
        "refused, facts stay, merge ledger preserved",
    ("kazma-core/kazma_core/agent/tool_builtins/memory.py", "_mem_purge_empty_entities"):
        "entity shells with no facts, danger-tier and confirm=true; merge ledger preserved",
    ("kazma-core/kazma_core/memory/eval_golden.py", "run_golden_eval"):
        "the golden eval's own private database; it refuses the live one",
}


def memory_deletes(sources: dict[str, str]) -> set[tuple[str, str]]:
    """(file, enclosing function) of every SQL string that deletes memory rows."""
    found: set[tuple[str, str]] = set()
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        def visit(node: ast.AST, function: str) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = node.name
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _DELETE.search(node.value):
                found.add((rel, function))
            for child in ast.iter_child_nodes(node):
                visit(child, function)

        visit(tree, "<module>")
    return found


def _product_sources() -> dict[str, str]:
    out = {}
    for pkg in REPO.glob("kazma-*/kazma_*"):
        for path in pkg.rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" not in rel:
                out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def test_every_memory_delete_is_declared():
    found = memory_deletes(_product_sources())
    undeclared = sorted(found - set(DECLARED))
    assert not undeclared, (
        "Memory rows deleted where nothing says why they are not lost -- archive, "
        "move or invalidate instead, or declare the site in DECLARED with the reason:\n  "
        + "\n  ".join(f"{f} in {fn}()" for f, fn in undeclared)
    )
    stale = sorted(set(DECLARED) - found)
    assert not stale, f"declared but gone -- drop the entry: {stale}"


def test_the_gate_finds_a_delete_by_pattern():
    """Negative control: the removed cleanup's statements, planted."""
    planted = {
        "x.py": textwrap.dedent(
            '''
            def cleanup(conn):
                conn.execute("DELETE FROM episodes WHERE user_text LIKE '%retroactive%'")
                conn.execute(
                    "delete from entities where id GLOB '[a-f0-9][a-f0-9][a-f0-9][a-f0-9]*'"
                )
            def fine(conn):
                conn.execute("DELETE FROM entity_merges WHERE source_entity_id=?", ("x",))
            '''
        )
    }
    assert memory_deletes(planted) == {("x.py", "cleanup")}


def test_the_pattern_cleanup_route_is_gone():
    from kazma_core.memory import backfill_v2

    assert not hasattr(backfill_v2, "cleanup_polluted_backfill")
    settings = (REPO / "kazma-ui" / "kazma_ui" / "settings.py").read_text(encoding="utf-8")
    assert "/api/settings/memory/clean" not in settings
