"""Import-integrity gates — the crawl.py incident (2026-08-14) class.

A module deletion left ``from kazma_core.web_acquire.crawl import ...`` in
the package ``__init__``: py_compile passed (syntax-only), and no test in
the suite imported ``kazma_core.web_acquire``, so research broke only in
production (``ModuleNotFoundError`` at first use). These gates close that
class permanently:

1. ``test_every_product_module_imports`` — import every module of every
   product package. Any dangling import ANYWHERE breaks the build, even
   when no behavioral test exercises that path.
2. ``test_no_dangling_kazma_import_references`` — AST-scan every product
   file for kazma_* import references and verify each resolves to a real
   module file. Catches references in rarely-executed code paths (function
   -level imports) that even the import smoke can miss when they sit in
   modules excluded from it.
3. ``test_every_imported_kazma_name_exists`` — the NAME half: ``from
   kazma_x.mod import name`` needs ``name`` defined at the top of ``mod``.

An import inside ``try/except`` used to be exempt, as a "degradation path".
For a Kazma module there is none: the module is in this repository, so the
import either resolves or is dead code, and the ``except`` turns the dead
code into a feature that is silently off. Found 2026-09-26, six of them:
the Knowledge Library's meaning search (``memory.chroma_client``, deleted
2026-07-31 -- logged as "chromadb not installed"); the gateway's handling of
a recursion Partial, its continue context and long-task pause (one missing
name, ``record_budget_exhausted``, removed 2026-09-04, took the three names
imported beside it down too) and the same name in the no-Partial branch's
budget counter; kazma.yaml's ``agent.nonstop`` (``config_loader.load_config``
never existed); the ``/memory`` fact count (``agent_runner.get_agent``); and
a health dependency map nothing called (``model_registry.get_registry``).
Only a third-party package may be optional.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]



def _enumeration():
    """scripts/check_fresh_imports.py: the one list of product modules.

    That script imports each module alone in a fresh interpreter; this file
    imports them all in one process. Same list, so neither can drift.
    """
    name = "_kazma_check_fresh_imports"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, REPO_ROOT / "scripts" / "check_fresh_imports.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


# Every kazma-*/kazma_* product package in the monorepo, and every module in
# them (packages included) as (dotted name, file).
PACKAGES: dict[str, Path] = _enumeration().product_packages(REPO_ROOT)
MODULES: list[tuple[str, Path]] = list(_enumeration().iter_modules(REPO_ROOT))

assert PACKAGES, "no product packages discovered"


def test_packages_discovered():
    """The gate must see the full product surface — a glob regression here
    would silently shrink the import smoke below."""
    names = set(PACKAGES)
    assert "kazma_core" in names
    assert "kazma_ui" in names
    assert "kazma_gateway" in names
    assert "kazma_tui" in names


def test_every_product_module_imports():
    """Import every product module — dangling imports fail the build here,
    not at first use in production."""
    failures: list[str] = []
    count = 0
    for mod_name, _py in MODULES:
        count += 1
        try:
            importlib.import_module(mod_name)
        except Exception as exc:
            failures.append(f"{mod_name}: {type(exc).__name__}: {exc}")
    assert not failures, (
        f"{len(failures)}/{count} product modules failed to import "
        "(dangling import after a deletion?):\n" + "\n".join(failures[:40])
    )


def _module_file_exists(dotted: str) -> bool:
    """Does *dotted* resolve to a real module file in a product package?"""
    parts = dotted.split(".")
    root = parts[0]
    if root not in PACKAGES:
        return True  # not a product package (stdlib / third-party) — not ours
    base = PACKAGES[root].joinpath(*parts[1:])
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _module_path(dotted: str) -> Path | None:
    """The file of product module *dotted* (a package's ``__init__.py``)."""
    parts = dotted.split(".")
    if parts[0] not in PACKAGES:
        return None
    base = PACKAGES[parts[0]].joinpath(*parts[1:])
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    return None


_TOP_NAMES: dict[Path, tuple[frozenset[str], bool]] = {}


def _top_level_names(path: Path) -> tuple[frozenset[str], bool]:
    """Names a module binds at top level, and whether it is open-ended
    (a module ``__getattr__`` or a star import can supply any name)."""
    if path in _TOP_NAMES:
        return _TOP_NAMES[path]
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    names: set[str] = set()
    open_ended = False

    def bind(target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                bind(elt)

    def walk(stmts: list[ast.stmt]) -> None:
        nonlocal open_ended
        for node in stmts:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                open_ended = open_ended or node.name == "__getattr__"
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    bind(target)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                bind(node.target)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    open_ended = open_ended or alias.name == "*"
                    names.add(alias.asname or alias.name)
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                for field in ("body", "orelse", "finalbody"):
                    walk(getattr(node, field, None) or [])
                for handler in getattr(node, "handlers", None) or []:
                    walk(handler.body)

    walk(tree.body)
    _TOP_NAMES[path] = (frozenset(names), open_ended)
    return _TOP_NAMES[path]


def _missing_names(tree: ast.AST, here_parts: tuple[str, ...]) -> list[tuple[int, str]]:
    """``from kazma_x.mod import name`` where ``mod`` has no ``name``."""
    missing: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        base = list(here_parts[: len(here_parts) - node.level]) if node.level else []
        if node.module:
            base += node.module.split(".")
        if not base or base[0] not in PACKAGES:
            continue
        module = ".".join(base)
        path = _module_path(module)
        if path is None:
            continue  # a missing MODULE is the other test's finding
        names, open_ended = _top_level_names(path)
        if open_ended:
            continue
        for alias in node.names:
            if alias.name == "*" or alias.name in names or _module_path(f"{module}.{alias.name}"):
                continue
            missing.append((node.lineno, f"{module}.{alias.name}"))
    return missing


def test_every_imported_kazma_name_exists():
    """``from kazma_x.mod import name``: ``mod`` defines ``name``, guarded or not."""
    missing: list[str] = []
    for mod_name, py in MODULES:
        pkg_name = mod_name.split(".")[0]
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        here = py.relative_to(PACKAGES[pkg_name].parent).with_suffix("").parts
        missing += [f"{py.relative_to(REPO_ROOT)}:{line}: {name}"
                    for line, name in _missing_names(tree, here)]
    assert not missing, (
        "Imports of names their Kazma module does not define. Inside a "
        "try/except that is a feature silently switched off:\n  " + "\n  ".join(missing)
    )


def test_the_name_gate_sees_a_missing_name_inside_a_try():
    """Negative control: the gateway's Partial handler before its fix."""
    planted = ast.parse(
        "def partial(thread):\n"
        "    try:\n"
        "        from kazma_core.agent.long_task import (\n"
        "            pause_long_task,\n"
        "            record_budget_exhausted,\n"
        "        )\n"
        "    except Exception:\n"
        "        pass\n"
        "from kazma_core.config_loader import deep_merge\n"
    )
    here = ("kazma_gateway", "agent_handler", "graph")
    assert _missing_names(planted, here) == [
        (3, "kazma_core.agent.long_task.record_budget_exhausted")]


def test_no_dangling_kazma_import_references():
    """Every kazma_* import reference in product code must resolve.

    Static (AST) so it also covers function-level imports on paths no test
    executes — the exact shape of the crawl.py incident. An import inside
    try/except is checked too (see the module docstring).
    """
    dangling: list[str] = []
    scanned = 0
    for mod_name, py in MODULES:
        pkg_name = mod_name.split(".")[0]
        scanned += 1
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            # Syntax is py_compile's job; ignore here.
            continue
        here_parts = py.relative_to(PACKAGES[pkg_name].parent).with_suffix("").parts
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base_parts: list[str] = []
                if node.level:  # relative: strip (level-1) packages
                    base_parts = list(here_parts[: len(here_parts) - node.level])
                if node.module:
                    base_parts = base_parts + node.module.split(".")
                if not base_parts:
                    continue
                target = ".".join(base_parts)
                # `from pkg.mod import name` — name may be a submodule…
                if base_parts[0] in PACKAGES:
                    for a in node.names:
                        sub = f"{target}.{a.name}"
                        if _module_file_exists(sub):
                            targets.append(sub)
                            continue
                        # …or an attribute of pkg.mod — the module must exist
                        targets.append(target)
                        break
                else:
                    targets.append(target)
            for t in targets:
                if t.split(".")[0] in PACKAGES and not _module_file_exists(t):
                    dangling.append(f"{py.relative_to(REPO_ROOT)}: import {t}")
    assert not dangling, (
        f"{len(dangling)} dangling kazma_* import references in {scanned} files "
        "(module deleted but still imported?):\n" + "\n".join(dangling[:40])
    )


@pytest.mark.parametrize("pkg", sorted(PACKAGES))
def test_package_importable(pkg):
    """Each product package's __init__ imports cleanly (fast subset of the
    full smoke, useful for triage when the full test fails)."""
    importlib.import_module(pkg)
