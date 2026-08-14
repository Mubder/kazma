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
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every kazma-*/kazma_* product package in the monorepo.
PACKAGES: dict[str, Path] = {}
for pkg_dir in sorted(REPO_ROOT.glob("kazma-*")):
    for child in sorted(pkg_dir.glob("kazma_*")):
        if (child / "__init__.py").is_file():
            PACKAGES[child.name] = child

assert PACKAGES, "no product packages discovered"


def _iter_module_names(package_name: str, package_dir: Path):
    """Yield dotted module names for every .py under *package_dir*."""
    for py in sorted(package_dir.rglob("*.py")):
        rel = py.relative_to(package_dir)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts or parts[-1] == "__main__":
            continue
        yield package_name + "." + ".".join(parts), py


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
    for pkg_name, pkg_dir in PACKAGES.items():
        if pkg_name not in sys.modules:
            importlib.import_module(pkg_name)
        for mod_name, _py in _iter_module_names(pkg_name, pkg_dir):
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


def _guarded_import_ids(tree: ast.AST) -> set[int]:
    """IDs of Import/ImportFrom nodes lexically inside a Try body.

    Imports wrapped in try/except are deliberate degradation paths
    (optional dependencies, legacy fallbacks) — the module still imports
    and the failure is handled. The gate targets UNGUARDED references,
    which crash the first time the code path runs (the crawl.py class).
    """
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    guarded.add(id(sub))
    return guarded


def test_no_dangling_kazma_import_references():
    """Every UNGUARDED kazma_* import reference in product code must resolve.

    Static (AST) so it also covers function-level imports on paths no test
    executes — the exact shape of the crawl.py incident. Imports inside
    try/except blocks are skipped (deliberate optional/degradation paths).
    """
    dangling: list[str] = []
    scanned = 0
    for pkg_name, pkg_dir in PACKAGES.items():
        for _mod, py in _iter_module_names(pkg_name, pkg_dir):
            scanned += 1
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                # Syntax is py_compile's job; ignore here.
                continue
            guarded = _guarded_import_ids(tree)
            here_parts = py.relative_to(PACKAGES[pkg_name].parent).with_suffix("").parts
            for node in ast.walk(tree):
                if id(node) in guarded:
                    continue
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
