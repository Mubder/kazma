"""Every ``module.name`` in Kazma's code names something that module defines.

``tests/test_imports.py`` holds ``from kazma_x.mod import name`` to names
``mod`` defines. Nothing held ``mod.name`` where ``mod`` is a module bound by
an import -- and that is how a fix went dead for five weeks: when
``graph_builder`` was split (2026-08-25) the failover caches moved to
``graph_supervisor``, while ``KazmaAgent.sync_active_model`` kept clearing
``graph_builder._failover_clients`` inside ``try/except: pass``. Every model
switch raised AttributeError and swallowed it (fixed 2026-09-30,
``tests/test_failover_cache_reset.py``).

This walks every product module, finds each name bound to a Kazma MODULE by
an import (``import kazma_x.m as m``, ``from kazma_x import m``, relative
forms), scope by scope, and requires every attribute read through that name
to be defined at the top level of the module: a def, class, assignment,
import, ``global`` target -- or, for a package, a submodule. Modules with a
module-level ``__getattr__`` or a star import are skipped (their names are
not static).
"""

from __future__ import annotations

import ast
import subprocess
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _module_file(dotted: str) -> Path | None:
    parts = dotted.split(".")
    for dist in REPO_ROOT.glob("kazma-*"):
        base = dist.joinpath(*parts)
        if (base / "__init__.py").is_file():
            return base / "__init__.py"
        f = base.with_suffix(".py")
        if f.is_file():
            return f
    return None


def _bound_names(stmts: list[ast.stmt]) -> set[str]:
    """Names a block binds at its own level (not inside defs/classes)."""
    names: set[str] = set()

    def targets(t: ast.AST) -> None:
        if isinstance(t, ast.Name):
            names.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                targets(e)
        elif isinstance(t, ast.Starred):
            targets(t.value)

    def walk(block: list[ast.stmt]) -> None:
        for st in block:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(st.name)
            elif isinstance(st, ast.Assign):
                for t in st.targets:
                    targets(t)
            elif isinstance(st, (ast.AnnAssign, ast.AugAssign)):
                targets(st.target)
            elif isinstance(st, (ast.Import, ast.ImportFrom)):
                for a in st.names:
                    names.add((a.asname or a.name).split(".")[0])
            elif isinstance(st, (ast.For, ast.AsyncFor)):
                targets(st.target)
                walk(st.body)
                walk(st.orelse)
            elif isinstance(st, (ast.With, ast.AsyncWith)):
                for item in st.items:
                    if item.optional_vars is not None:
                        targets(item.optional_vars)
                walk(st.body)
            elif isinstance(st, ast.If):
                walk(st.body)
                walk(st.orelse)
            elif isinstance(st, ast.Try) or type(st).__name__ == "TryStar":
                walk(st.body)
                for h in st.handlers:
                    if h.name:
                        names.add(h.name)
                    walk(h.body)
                walk(st.orelse)
                walk(st.finalbody)
            elif isinstance(st, ast.While):
                walk(st.body)
                walk(st.orelse)
            elif isinstance(st, ast.Match):
                for case in st.cases:
                    walk(case.body)

    walk(stmts)
    return names


@cache
def module_names(dotted: str) -> frozenset[str] | None:
    """Top-level names of a Kazma module; None when they cannot be known."""
    path = _module_file(dotted)
    if path is None:
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return None
    names = _bound_names(tree.body)
    if "__getattr__" in names:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            names.update(node.names)
    if path.name == "__init__.py":
        for child in path.parent.iterdir():
            if child.suffix == ".py":
                names.add(child.stem)
            elif (child / "__init__.py").is_file():
                names.add(child.name)
    names.update({"__name__", "__file__", "__doc__", "__path__", "__spec__", "__dict__", "__all__"})
    return frozenset(names)


def _resolve(node: ast.ImportFrom, module: str, is_package: bool) -> str:
    if not node.level:
        return node.module or ""
    base = module.split(".") if is_package else module.split(".")[:-1]
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _module_aliases(stmts: list[ast.stmt], module: str, is_package: bool) -> dict[str, str]:
    """alias -> Kazma module, for imports at this block's own level."""
    out: dict[str, str] = {}

    def walk(block: list[ast.stmt]) -> None:
        for st in block:
            if isinstance(st, ast.Import):
                for a in st.names:
                    if a.asname and a.name.startswith("kazma_") and _module_file(a.name):
                        out[a.asname] = a.name
            elif isinstance(st, ast.ImportFrom):
                base = _resolve(st, module, is_package)
                if not base.startswith("kazma_"):
                    continue
                for a in st.names:
                    full = f"{base}.{a.name}"
                    if _module_file(full) is not None:
                        out[a.asname or a.name] = full
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            else:
                for field in ("body", "orelse", "finalbody"):
                    walk(getattr(st, field, []) or [])
                for h in getattr(st, "handlers", []) or []:
                    walk(h.body)
                for case in getattr(st, "cases", []) or []:
                    walk(case.body)

    walk(stmts)
    return out


def _own_nodes(scope: ast.AST):
    """Nodes of a scope, not descending into nested defs/classes/lambdas."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def dangling_module_attributes(sources: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        parts = Path(rel).parts[1:]
        is_package = parts[-1] == "__init__.py"
        module = ".".join(parts[:-1] if is_package else [*parts[:-1], parts[-1][:-3]])

        def check(scope: ast.AST, body: list[ast.stmt], outer: dict[str, str], params: set[str]) -> None:
            own = _module_aliases(body, module, is_package)
            rebound = _bound_names(body) - set(own)
            visible = {k: v for k, v in outer.items() if k not in rebound and k not in params}
            visible.update(own)
            for node in _own_nodes(scope):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.ctx, ast.Load)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in visible
                ):
                    names = module_names(visible[node.value.id])
                    if names is not None and node.attr not in names:
                        problems.append(f"{rel}:{node.lineno} {node.value.id}.{node.attr} ({visible[node.value.id]})")
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a = node.args
                    p = {x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]}
                    if a.vararg:
                        p.add(a.vararg.arg)
                    if a.kwarg:
                        p.add(a.kwarg.arg)
                    check(node, node.body, visible, p)
                elif isinstance(node, ast.ClassDef):
                    check(node, node.body, visible, set())

        check(tree, tree.body, {}, set())
    return sorted(problems)


def _product_sources() -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "kazma-*/*.py", "kazma-*/**/*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    out = {}
    for rel in files:
        if "_tests" in rel or "/tests/" in rel:
            continue
        path = REPO_ROOT / rel
        if path.is_file():
            out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def test_every_module_attribute_is_defined_by_the_module() -> None:
    sources = _product_sources()
    assert len(sources) > 300, "the source listing is broken"
    problems = dangling_module_attributes(sources)
    assert not problems, (
        "Read through a module alias, but the module does not define it (a move or\n"
        "rename left the reader behind; inside try/except it fails silently):\n  "
        + "\n  ".join(problems)
    )


def test_negative_control_the_failover_reach_is_caught() -> None:
    """The line that went dead in agent_runner, planted in a product path."""
    planted = {
        "kazma-core/kazma_core/zz_planted.py": (
            "def sync():\n"
            "    try:\n"
            "        from kazma_core.agent import graph_builder as _gb\n"
            "        _gb._failover_clients.clear()\n"
            "    except Exception:\n"
            "        pass\n"
        ),
        # A name the module does define passes; a local that shadows the
        # alias is not the module.
        "kazma-core/kazma_core/zz_fine.py": (
            "from kazma_core.agent import graph_supervisor\n"
            "def ok():\n"
            "    graph_supervisor.reset_failover_cache()\n"
            "def shadow(graph_supervisor):\n"
            "    return graph_supervisor.anything\n"
        ),
    }
    assert dangling_module_attributes(planted) == [
        "kazma-core/kazma_core/zz_planted.py:4 _gb._failover_clients (kazma_core.agent.graph_builder)"
    ]
