"""Once Spaces is enforced, ``default`` is a space like any other (S1-09).

Memory's "install's own view" was the tenant ``default``: a caller bound to
no tenant read, counted and changed every tenant's rows. On an install with
one account that is the whole install, and it stays so. Once Spaces is
enforced, ``default`` is the owner's personal space: the owner reads and
changes its own rows only, and the admin cannot open a member's memory.

The memory route gate (``tests/test_memory_routes_tenant_scope.py``) calls
every memory route as tenant ``alpha`` with tenant ``beta``'s ids. Here its
requests, seed and checks run twice more with Spaces enforced:

* **the owner asks**: the caller's rows are renamed to ``default``, and the
  owner (an admin too) must be shown and change nothing of beta's;
* **the owner is asked about**: beta's rows are renamed to ``default``, and
  the member ``alpha`` must be shown and change nothing of the owner's.

The control runs the first pass without Spaces: the old rule shows beta's
rows, as it must on an install that does not enforce Spaces. The class gate
at the end fails on memory code that compares a tenant with the literal.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest
from kazma_core.memory import tenant_view
from kazma_core.spaces import scope

from tests import test_memory_routes_tenant_scope as gate
from tests.test_memory_routes_tenant_scope import memory_client  # noqa: F401 - the gate's fixture

ROOT = Path(__file__).resolve().parents[1]
OWNER = scope.OWNER_SPACE_ID


def _rename(root: Path, old: str, new: str) -> None:
    """Every row of tenant *old*, in both memory files, becomes *new*'s."""
    for name in ("memory_state.db", "memory_ops.db"):
        conn = sqlite3.connect(root / name)
        try:
            tables = [str(r[0]) for r in conn.execute(
                "SELECT m.name FROM sqlite_master m JOIN pragma_table_info(m.name) c "
                "WHERE m.type = 'table' AND c.name = 'tenant_id' AND m.sql NOT LIKE 'CREATE VIRTUAL%'")]
            for table in tables:
                conn.execute(f"UPDATE {table} SET tenant_id = ? WHERE tenant_id = ?", (new, old))
            conn.commit()
        finally:
            conn.close()


def _victim_rows(tenant: str):
    def rows(root: Path) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        conn = sqlite3.connect(root / "memory_state.db")
        conn.row_factory = sqlite3.Row
        try:
            for table in gate._TENANT_TABLES:
                found = conn.execute(f"SELECT * FROM {table} WHERE tenant_id = ? ORDER BY rowid", (tenant,)).fetchall()
                out[table] = [{k: r[k] for k in r.keys() if k not in gate._CACHE_COLUMNS} for r in found]
        finally:
            conn.close()
        assert any(out.values()), "the victim holds no rows: the check would pass for nothing"
        return out

    return rows


def _world(ctx, monkeypatch, *, caller: str, renamed: tuple[str, str], enforced: bool, admin: bool) -> None:
    seed = gate._seed

    def seed_then_rename(root: Path) -> None:
        seed(root)
        _rename(root, *renamed)

    monkeypatch.setattr(gate, "_seed", seed_then_rename)
    victim = OWNER if renamed == ("beta", OWNER) else "beta"
    monkeypatch.setattr(gate, "_beta_rows", _victim_rows(victim))
    monkeypatch.setattr(scope, "_enforced", enforced)
    monkeypatch.setattr("kazma_ui.auth.admin_decision", lambda request: "ok" if admin else "forbidden")
    ctx.who["tenant"] = caller


@pytest.mark.parametrize("route", sorted(gate.REQUESTS), ids=lambda r: f"{r[0]} {r[1]}")
def test_the_owner_sees_and_changes_only_the_owners_space(memory_client, monkeypatch, route) -> None:  # noqa: F811
    _world(memory_client, monkeypatch, caller=OWNER, renamed=("alpha", OWNER), enforced=True, admin=True)
    assert gate._problems(memory_client, route) == []


@pytest.mark.parametrize("route", sorted(gate.REQUESTS), ids=lambda r: f"{r[0]} {r[1]}")
def test_a_member_sees_and_changes_nothing_of_the_owners_space(memory_client, monkeypatch, route) -> None:  # noqa: F811
    _world(memory_client, monkeypatch, caller="alpha", renamed=("beta", OWNER), enforced=True, admin=False)
    assert gate._problems(memory_client, route) == []


def test_control_without_spaces_the_owner_is_shown_every_tenant(memory_client, monkeypatch) -> None:  # noqa: F811
    """The same first pass on an install that does not enforce Spaces: the
    install's own view, unchanged. The pass above fails without the rule."""
    _world(memory_client, monkeypatch, caller=OWNER, renamed=("alpha", OWNER), enforced=False, admin=True)
    shown = [route for route in sorted(gate.REQUESTS)
             if any("showed tenant beta's data" in p for p in gate._problems(memory_client, route))]
    assert len(shown) >= 5, shown


#: Every module that reads the one rule by name (``from ... import``), and
#: the rule's own module for the code that imports it at call time.
_RULE_READERS = (
    "kazma_core.memory.tenant_view", "kazma_core.memory.entity_key", "kazma_core.memory.forget",
    "kazma_core.memory.graph_hygiene", "kazma_core.memory.hygiene", "kazma_core.memory.topic_summaries",
    "kazma_core.memory.v2_health", "kazma_ui.memory_api", "kazma_ui.routes_direct.memory",
    "kazma_ui.routes_direct._shared",
)


def _the_rule_before_s1_09(monkeypatch) -> None:
    """Put back the rule memory code held before S1-09: the literal tenant
    ``"default"`` saw every tenant, Spaces or not."""
    import importlib

    def every(tenant_id: str | None) -> bool:
        return (tenant_id or "default") == "default"

    def narrowed(tenant_id: str | None) -> str | None:
        return None if every(tenant_id) else tenant_id

    def reach(tenant_id: str | None) -> tuple[str, str]:
        return (tenant_id or "default", "default")

    for name in _RULE_READERS:
        module = importlib.import_module(name)
        for attr, rule in (("sees_every_tenant", every), ("narrowed_to", narrowed), ("ledger_reach", reach)):
            if hasattr(module, attr):
                monkeypatch.setattr(module, attr, rule)


def test_every_reader_of_the_rule_is_listed_for_the_control() -> None:
    """The control below swaps the rule in every module that holds it by
    name: a module that imports it and is not listed would keep S1-09's."""
    import re

    root = Path(__file__).resolve().parents[1]
    readers = set()
    for folder in ("kazma-core/kazma_core", "kazma-ui/kazma_ui"):
        for path in (root / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"^from kazma_core\.memory\.tenant_view import", text, re.M):
                rel = path.relative_to(root / folder.split("/")[0]).with_suffix("")
                readers.add(".".join(rel.parts))
    assert len(readers) >= 8, readers  # the walk found the readers
    assert readers <= set(_RULE_READERS), sorted(readers - set(_RULE_READERS))


def test_control_the_rule_before_s1_09_shows_the_enforced_owner_every_tenant(
    memory_client, monkeypatch,  # noqa: F811
) -> None:
    """The enforced pass above, run with the rule memory code held before
    S1-09: the owner's literal tenant saw every tenant, so the owner was
    shown beta's rows on the routes the pass checks."""
    _world(memory_client, monkeypatch, caller=OWNER, renamed=("alpha", OWNER), enforced=True, admin=True)
    _the_rule_before_s1_09(monkeypatch)
    shown = [route for route in sorted(gate.REQUESTS)
             if any("showed tenant beta's data" in p for p in gate._problems(memory_client, route))]
    assert len(shown) >= 5, shown


def test_the_owners_health_counts_its_own_rows(memory_client, monkeypatch) -> None:  # noqa: F811
    _world(memory_client, monkeypatch, caller=OWNER, renamed=("alpha", OWNER), enforced=True, admin=True)
    gate._fresh(memory_client, "counts")
    health = memory_client.client.get("/api/memory/v2/health").json()
    assert health["beliefs"]["active"] == 2  # the owner's; beta holds four more
    assert memory_client.client.get("/api/memory/v2/episodes").json()["total"] < 4


# ── the one rule ─────────────────────────────────────────────────────────


def test_who_sees_every_tenant(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    assert tenant_view.sees_every_tenant(None) and tenant_view.sees_every_tenant(OWNER)
    assert tenant_view.sees_every_tenant("") and not tenant_view.sees_every_tenant("alpha")
    assert tenant_view.narrowed_to(OWNER) is None and tenant_view.narrowed_to("alpha") == "alpha"
    assert tenant_view.ledger_reach("alpha") == ("alpha", OWNER)
    monkeypatch.setattr(scope, "_enforced", True)
    assert tenant_view.sees_every_tenant(None)  # Kazma's own work
    assert not tenant_view.sees_every_tenant(OWNER) and not tenant_view.sees_every_tenant("")
    assert tenant_view.narrowed_to(OWNER) == OWNER and tenant_view.narrowed_to("") == OWNER
    assert tenant_view.ledger_reach("alpha") == ("alpha", "alpha")
    assert tenant_view.ledger_reach(OWNER) == (OWNER, OWNER)


# ── class gate: no memory code compares a tenant with the literal ────────

_MEMORY_CODE = (
    "kazma-core/kazma_core/memory",
    "kazma-ui/kazma_ui/memory_api.py",
    "kazma-ui/kazma_ui/memory_actions.py",
    "kazma-ui/kazma_ui/history_import_api.py",
    "kazma-ui/kazma_ui/routes_direct/memory.py",
    "kazma-ui/kazma_ui/routes_direct/_shared.py",
    "kazma-core/kazma_core/agent/tool_builtins/memory.py",
)

#: A comparison that is not "may this caller see every tenant?", with why.
DECLARED: dict[tuple[str, str], str] = {
    ("kazma-core/kazma_core/memory/config.py", "resolve_tenant_id"):
        "the retired per-platform and per-user modes: a platform or user with no name is the owner's",
    ("kazma-core/kazma_core/memory/export.py", "_name"):
        "the nightly export's file name: the owner's keeps the name it always had",
    ("kazma-core/kazma_core/memory/profile.py", "about_for_turn"):
        "the owner's own About me needs no second read",
}


def _literal_comparisons(source: str) -> list[tuple[str, int]]:
    """``(function, line)`` of every comparison with the literal "default"."""
    tree = ast.parse(source)
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, owner: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            ops_ok = all(isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)) for op in node.ops)
            literal = any(
                (isinstance(s, ast.Constant) and s.value == "default")
                or (isinstance(s, (ast.Tuple, ast.Set, ast.List))
                    and any(isinstance(e, ast.Constant) and e.value == "default" for e in s.elts))
                for s in sides)
            if ops_ok and literal:
                found.append((owner, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree, "<module>")
    return found


def _memory_files() -> list[Path]:
    files: list[Path] = []
    for entry in _MEMORY_CODE:
        path = ROOT / entry
        files += sorted(path.rglob("*.py")) if path.is_dir() else [path]
    return files


def test_no_memory_code_compares_a_tenant_with_the_literal() -> None:
    """``x == "default"`` decided who saw every tenant in about forty places.
    They ask ``tenant_view`` (or name ``OWNER_SPACE_ID`` for a reason that is
    declared); a new literal comparison fails here."""
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in _memory_files():
        rel = path.relative_to(ROOT).as_posix()
        for function, line in _literal_comparisons(path.read_text(encoding="utf-8")):
            if (rel, function) in DECLARED:
                seen.add((rel, function))
            else:
                problems.append(f"{rel}:{line} ({function}) compares with the literal 'default'")
    assert not problems, "\n".join(problems)
    assert seen == set(DECLARED), f"declared and not found: {sorted(set(DECLARED) - seen)}"


def test_the_class_gate_sees_a_planted_comparison() -> None:
    planted = (
        "def _clause(tid):\n"
        "    if tid != 'default':\n"
        "        return 'AND tenant_id = ?'\n"
        "    return ''\n"
        "def _ok(tid):\n"
        "    return tid in ('default', '')\n"
    )
    assert [name for name, _ in _literal_comparisons(planted)] == ["_clause", "_ok"]
    assert _literal_comparisons("def f(mode):\n    return mode == 'shared'\n") == []
