"""Every writer of a fact someone else named passes the user's facts through
Kazma's names (the main session's check of the third review of F1, fix 1).

The name map (``fact_names.users_names``: "home_city", "lives-in",
"currentLocation" -> "lives_in"; "User" -> "user") ran in the deep pass
only; a memory tool's ``home_city`` stood beside the user's own home at the
user's trust, nothing asked. It is now one helper every writer calls before
``mutate_belief`` -- the deep pass, the memory tools, the Memory page's
link, the V1 backfill, a confirmed change -- and this gate reads the product
code: a function that calls ``mutate_belief`` or holds an ``INSERT ...
INTO beliefs`` calls ``users_names`` (or ``users_predicate``), or is
declared below with why its names are not someone's naming of the user's
facts. A declaration that matches nothing fails.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: An insert into the facts table (never its archive or search index).
_INSERT_RE = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+beliefs\b(?!_)", re.IGNORECASE)

_MAP = {"users_names", "users_predicate"}

#: Writers whose names are not someone's naming of the user's facts.
DECLARED = {
    ("kazma-core/kazma_core/memory/belief_mutation.py", "_insert_belief"):
        "the insert behind mutate_belief, reached only from its own module",
    ("kazma-core/kazma_core/memory/ego_anchor.py", "anchor_leaf_subject"):
        "writes the hub's own link (ANCHOR_PREDICATE), never one of the user's kinds",
    ("kazma-core/kazma_core/memory/benchmark.py", "_seed"):
        "the retrieval benchmark's fixture: it seeds the dataset's facts as the dataset names "
        "them, and its ratchet is measured on those names",
    ("kazma-core/kazma_core/memory/eval_golden.py", "_seed_belief"):
        "the golden eval's fixture: a case's fact as the case names it, on a private database",
}


def _product_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines()
            if re.match(r"kazma-[a-z]+/kazma_[a-z]+/", p) and "/tests/" not in p and "_tests/" not in p]


def _own_nodes(func: ast.AST) -> list[ast.AST]:
    """The nodes of *func* outside the functions nested in it."""
    nested = {id(n) for child in ast.walk(func) if child is not func
              and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) for n in ast.walk(child)}
    return [n for n in ast.walk(func) if id(n) not in nested]


def _called(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _writers(source: str) -> list[tuple[str, set[str]]]:
    """Each function that writes a fact (calls ``mutate_belief`` or holds an
    insert into ``beliefs``), with the names it calls."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        own = _own_nodes(node)
        calls = {_called(n) for n in own if isinstance(n, ast.Call)}
        inserts = any(isinstance(n, ast.Constant) and isinstance(n.value, str) and _INSERT_RE.search(n.value)
                      for n in own)
        if "mutate_belief" in calls or inserts:
            found.append((node.name, calls))
    return found


def _skipping(files: dict[str, str]) -> list[str]:
    return [f"{path}:{name}" for path, source in files.items() for name, calls in _writers(source)
            if (path, name) not in DECLARED and not calls & _MAP]


def _files() -> dict[str, str]:
    return {p: (ROOT / p).read_text(encoding="utf-8") for p in _product_files()}


def test_every_writer_of_a_fact_passes_the_users_names():
    files = _files()
    writers = {f"{p}:{n}" for p, s in files.items() for n, _ in _writers(s)}
    # The gate reads the writers it is about.
    assert {"kazma-core/kazma_core/agent/tool_builtins/memory.py:_mem_link_entities",
            "kazma-core/kazma_core/memory/belief_extractor.py:_apply_beliefs_to_v2",
            "kazma-core/kazma_core/memory/backfill_v2.py:_backfill_graph_to_beliefs",
            "kazma-ui/kazma_ui/memory_api.py:_link_entities_sync"} <= writers, sorted(writers)
    assert _skipping(files) == []


def test_every_declaration_names_a_writer():
    files = _files()
    writers = {(p, n) for p, s in files.items() for n, _ in _writers(s)}
    assert sorted(set(DECLARED) - writers) == []


def test_the_insert_behind_mutate_belief_is_reached_only_from_its_module():
    callers = [p for p, s in _files().items() if "_insert_belief(" in s]
    assert callers == ["kazma-core/kazma_core/memory/belief_mutation.py"]


def test_control_a_planted_writer_that_skips_the_map_is_found():
    planted = (
        "def remember(conn, subject, predicate, value):\n"
        "    mutate_belief(conn, subject, predicate, value)\n"
        "def store(conn, subject, predicate, value):\n"
        "    conn.execute(\"INSERT OR IGNORE INTO beliefs (subject, predicate, object) VALUES (?, ?, ?)\",\n"
        "                 (subject, predicate, value))\n"
        "def archive(conn):\n"
        "    conn.execute(\"INSERT INTO beliefs_archive SELECT * FROM beliefs\")\n"
    )
    path = "kazma-core/kazma_core/memory/planted.py"
    assert _skipping({path: planted}) == [f"{path}:remember", f"{path}:store"]
    mapped = planted.replace("    mutate_belief(", "    subject, predicate = users_names(subject, predicate)\n"
                             "    mutate_belief(", 1)
    assert _skipping({path: mapped}) == [f"{path}:store"]
