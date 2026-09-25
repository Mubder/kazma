#!/usr/bin/env python3
"""Regenerate docs/docs/reference/environment-variables-index.md from the code.

Usage (repo root):
    python scripts/generate_env_reference.py            # write the index
    python scripts/generate_env_reference.py --check    # exit 1 if it is stale

The curated page, ``environment-variables.md``, explains what a variable is
FOR. It is written by hand and it drifts: the 2026-09-16 audit counted 272
``KAZMA_*`` variables read by the code against 43 documented, and none of the
sixteen that turn a safety default off. This page cannot drift, because it is
not written by hand. It lists every ``KAZMA_*`` name the product code reads,
where, the default the code falls back to, and whether the curated page
covers it. ``tests/test_env_reference.py`` fails when it is stale, and holds
the number of undocumented variables on a ratchet that may only go down.

A name counts when it appears as an exact string constant in product code
(``os.environ.get("KAZMA_X", ...)``, ``os.getenv``, ``environ[...]``, the
``_env_*`` helpers, or a tuple of names that is iterated), except entries of
``__all__`` and names that are Python identifiers assigned somewhere in the
product (``KAZMA_PG_TABLES`` is a list, not a variable).
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "docs" / "reference" / "environment-variables-index.md"
CURATED = ROOT / "docs" / "docs" / "reference" / "environment-variables.md"

_NAME = re.compile(r"^KAZMA_[A-Z0-9_]+$")
_PACKAGES = ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-skills", "kazma-tui")


@dataclass
class EnvVar:
    name: str
    modules: set[str] = field(default_factory=set)
    defaults: set[str] = field(default_factory=set)


def _product_sources(root: Path) -> dict[str, str]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", *(f"{p}/*.py" for p in _PACKAGES), *(f"{p}/**/*.py" for p in _PACKAGES)],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        listed = [p.relative_to(root).as_posix() for pkg in _PACKAGES for p in (root / pkg).rglob("*.py")]
    out: dict[str, str] = {}
    for rel in sorted(set(listed)):
        if "/tests/" in rel or "__pycache__" in rel:
            continue
        path = root / rel
        if path.is_file():
            out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def _module_name(rel: str) -> str:
    parts = rel.split("/", 1)[1] if "/" in rel else rel
    return parts[:-3].replace("/", ".") if parts.endswith(".py") else parts


def _literal_default(call: ast.Call) -> str | None:
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        value = call.args[1].value
        if value is None:
            return None
        return repr(value) if not isinstance(value, str) else (f'"{value}"' if value != "" else '""')
    return None


def collect(sources: dict[str, str]) -> dict[str, EnvVar]:
    """Every KAZMA_* variable the given product sources read."""
    trees: dict[str, ast.AST] = {}
    assigned: set[str] = set()
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        trees[rel] = tree
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and _NAME.match(t.id):
                        assigned.add(t.id)

    found: dict[str, EnvVar] = {}
    for rel, tree in trees.items():
        exported: set[int] = set()
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            ):
                exported.update(id(n) for n in ast.walk(node.value))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            name = node.value
            if not _NAME.match(name) or id(node) in exported or name in assigned:
                continue
            var = found.setdefault(name, EnvVar(name))
            var.modules.add(_module_name(rel))
            parent = parents.get(id(node))
            if isinstance(parent, ast.Call) and parent.args and parent.args[0] is node:
                default = _literal_default(parent)
                if default is not None:
                    var.defaults.add(default)
    return found


def _anchor_documented(curated: str, name: str) -> bool:
    return re.search(rf"(?<![A-Z0-9_]){re.escape(name)}(?![A-Z0-9_])", curated) is not None


def undocumented(found: dict[str, EnvVar], curated: str) -> list[str]:
    return sorted(n for n in found if not _anchor_documented(curated, n))


def render(found: dict[str, EnvVar], curated: str) -> str:
    missing = set(undocumented(found, curated))
    lines = [
        "---",
        "id: environment-variables-index",
        "title: Environment Variable Index (generated)",
        "sidebar_label: Env Variable Index",
        "description: Every KAZMA_* variable the code reads, where, and its default. Generated; do not edit.",
        "---",
        "",
        "> **Generated by `scripts/generate_env_reference.py` — do not edit by hand.**",
        "> What a variable is *for* lives in [Environment Variables](./environment-variables);",
        "> this page is the inventory that page is checked against. Run",
        "> `python scripts/generate_env_reference.py` after adding or removing a variable;",
        "> `tests/test_env_reference.py` fails while this page is stale.",
        "",
        f"**{len(found)}** variables are read by the product code; "
        f"**{len(found) - len(missing)}** are described on the curated page and "
        f"**{len(missing)}** are not yet (marked —). New variables must be described there:",
        "the undocumented count is on a ratchet that may only go down.",
        "",
        "| Variable | Default in code | Read in | Described |",
        "|----------|-----------------|---------|-----------|",
    ]
    for name in sorted(found):
        var = found[name]
        defaults = ", ".join(f"`{d}`" for d in sorted(var.defaults)) or "(none)"
        mods = sorted(var.modules)
        shown = ", ".join(f"`{m}`" for m in mods[:3]) + (f" +{len(mods) - 3}" if len(mods) > 3 else "")
        described = "—" if name in missing else "yes"
        lines.append(f"| `{name}` | {defaults} | {shown} | {described} |")
    lines.append("")
    return "\n".join(lines)


def build(root: Path = ROOT) -> tuple[str, dict[str, EnvVar], list[str]]:
    curated = CURATED.read_text(encoding="utf-8") if CURATED.exists() else ""
    found = collect(_product_sources(root))
    return render(found, curated), found, undocumented(found, curated)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the index is stale")
    args = parser.parse_args(argv)
    text, found, missing = build()
    current = OUT.read_text(encoding="utf-8").replace("\r\n", "\n") if OUT.exists() else ""
    if args.check:
        if current != text:
            print(f"{OUT.relative_to(ROOT)} is stale: run python scripts/generate_env_reference.py")
            return 1
        print(f"env index current: {len(found)} variables, {len(missing)} not yet described")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(found)} variables, {len(missing)} not yet described")
    return 0


if __name__ == "__main__":
    sys.exit(main())
