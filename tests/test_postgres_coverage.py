"""Every product module that opens a Postgres connection is tested on one.

The CI Postgres job runs the tests marked ``@pytest.mark.postgres``
(``scripts/postgres_suite.py``); everything else runs on SQLite. That makes
coverage a property of each module, and it was never checked: on 2026-09-27
three modules opened Postgres without a marked test -- the agent's
checkpointer (which, once tested, turned out never to close its pool), the
gateway's, and the research panel's Delete (raw SQL against the task table).
Each is now covered or routed through a covered module.

A module opens Postgres when it imports a driver (``psycopg``,
``psycopg_pool``, ``psycopg2``), LangGraph's Postgres saver, or a pool from
``kazma_core.db`` (``get_postgres_pool``, ``get_pool``, ``PostgresPool``).
It is covered when a marked test file imports it or names it as a string
(``monkeypatch`` targets included).
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DRIVERS = ("psycopg", "psycopg_pool", "psycopg2")
POOL_MODULES = ("kazma_core.db.postgres_pool", "kazma_core.db.pg_helpers")
POOL_NAMES = {"get_postgres_pool", "get_pool", "PostgresPool"}


def _script(name: str):
    key = f"_kazma_{name}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, REPO / "scripts" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def opens_postgres(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in DRIVERS or alias.name.startswith("langgraph.checkpoint.postgres"):
                    return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in DRIVERS or node.module.startswith("langgraph.checkpoint.postgres"):
                return True
            if node.module in POOL_MODULES and POOL_NAMES & {a.name for a in node.names}:
                return True
    return False


def referenced_modules(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    names.update(re.findall(r"[\"'](kazma_[a-z_]+(?:\.[a-z_0-9]+)+)", source))
    return names


def uncovered(modules: dict[str, str], marked_sources: list[str]) -> list[str]:
    refs: set[str] = set()
    for src in marked_sources:
        refs |= referenced_modules(src)
    return sorted(
        name for name, src in modules.items()
        if opens_postgres(src) and not any(r == name or r.startswith(name + ".") for r in refs)
    )


def test_every_module_that_opens_postgres_is_tested_on_postgres():
    modules = {
        name: path.read_text(encoding="utf-8", errors="replace")
        for name, path in _script("check_fresh_imports").iter_modules(REPO)
    }
    openers = [n for n, s in modules.items() if opens_postgres(s)]
    assert len(openers) >= 10, openers  # the detection itself is not blind
    marked = [p.read_text(encoding="utf-8", errors="replace") for p in _script("postgres_suite").marked_files()]
    missing = uncovered(modules, marked)
    assert missing == [], (
        "these modules open Postgres and no @pytest.mark.postgres test imports them; "
        "write one and run it on a throwaway Postgres twice (scripts/postgres_suite.py): "
        + ", ".join(missing)
    )


def test_negative_control_an_untested_opener_is_named():
    modules = {
        "kazma_core.planted": "from psycopg_pool import ConnectionPool\n",
        "kazma_core.via_pool": "from kazma_core.db.pg_helpers import get_pool\n",
        "kazma_core.helper_only": "from kazma_core.db.pg_helpers import store_errors\n",
    }
    marked = ["import pytest\nfrom kazma_core.via_pool import thing\n"]
    assert uncovered(modules, marked) == ["kazma_core.planted"]
