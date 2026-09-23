"""Every product module is reachable from the product, or says why not.

The 2026-09-22 audit found modules that nothing in the product imported —
some documented as features (``KAZMA_REALTIME_CODEC``), some tested as if
they were live (the TUI footer, a duplicate HITL router whose tests exercised
the copy production never ran), some just forgotten (a PDF exporter, a file
merger, a markup guard). Each looked alive from its own tests. This gate
builds the import graph and fails on a module nothing reaches.

A module is reached when product code imports it, names it in a string (the
``"package.module:Class"`` registries, importlib), it is an entry point, or it
is a native skill's ``tools.py`` next to its manifest. Anything else must be
in ``ALLOWED_UNREACHED`` with the reason.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "kazma-core": "kazma_core",
    "kazma-gateway": "kazma_gateway",
    "kazma-ui": "kazma_ui",
    "kazma-tui": "kazma_tui",
    "kazma-cli": "kazma_cli",
    "kazma-skills": "kazma_skills",
}

#: Deliberately unreached modules, each with the reason.
ALLOWED_UNREACHED: dict[str, str] = {
    "kazma_core.hub.api": (
        "Hub REST API kept as a tested library when its broken Kubernetes "
        "deployment was removed (2026-09-23); nothing runs it."
    ),
}


def _module_name(rel: str) -> str | None:
    parts = Path(rel).parts
    if len(parts) < 2 or PACKAGES.get(parts[0]) != parts[1]:
        return None
    mod = list(parts[1:])
    mod[-1] = mod[-1][:-3]
    if mod[-1] == "__init__":
        mod = mod[:-1]
    return ".".join(mod)


def _is_test(rel: str) -> bool:
    return "_tests/" in rel or "/tests/" in rel or rel.startswith("tests/")


def unreached_modules(
    sources: dict[str, str], extra_text: str = "", launchers: dict[str, str] | None = None
) -> list[str]:
    """Modules in *sources* (``rel path -> text``) nothing reaches.

    *launchers* are repo-level operator scripts (``scripts/``, ``serve.py``):
    they reach modules but are not themselves checked.
    """
    modules = {m: rel for rel in sources if (m := _module_name(rel))}
    imported: set[str] = set()
    strings: set[str] = set()
    entry_points: set[str] = set()
    for rel, text in {**sources, **(launchers or {})}.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        me = _module_name(rel) or ""
        is_pkg = rel.endswith("__init__.py")
        if rel.endswith("__main__.py") or '__name__ == "__main__"' in text:
            entry_points.add(me)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = me.split(".") if is_pkg else me.split(".")[:-1]
                    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                    mod = ".".join(base + ([node.module] if node.module else []))
                else:
                    mod = node.module or ""
                imported.add(mod)
                imported.update(f"{mod}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.add(node.value.split(":", 1)[0])

    def reached(mod: str) -> bool:
        if mod in entry_points or mod in strings or mod in extra_text:
            return True
        return any(i == mod or i.startswith(mod + ".") for i in imported)

    out = []
    for mod, rel in sorted(modules.items()):
        if rel.endswith(("__init__.py", "__main__.py")) or reached(mod):
            continue
        # Native skills: the loader imports <skill>/tools.py from its manifest.
        if rel.endswith("/tools.py") and "/native/" in rel:
            skill_dir = REPO_ROOT / Path(rel).parent
            if any((skill_dir / m).is_file() for m in ("skill_manifest.yaml", "skill.yaml", "manifest.json")):
                continue
        out.append(mod)
    return out


def _product_sources() -> tuple[dict[str, str], str, dict[str, str]]:
    files = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    sources = {
        rel: (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for rel in files
        if rel.endswith(".py") and _module_name(rel) and not _is_test(rel) and (REPO_ROOT / rel).is_file()
    }
    launchers = {
        rel: (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for rel in files
        if rel.endswith(".py")
        and (rel.startswith("scripts/") or rel == "serve.py")
        and (REPO_ROOT / rel).is_file()
    }
    config = "\n".join(
        (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for rel in files
        if rel.endswith((".toml", ".yaml", ".yml", ".json", ".cfg"))
        and not _is_test(rel)
        and (REPO_ROOT / rel).is_file()
        and (REPO_ROOT / rel).stat().st_size < 2_000_000
    )
    return sources, config, launchers


def test_every_product_module_is_reached():
    sources, config, launchers = _product_sources()
    orphans = [
        m for m in unreached_modules(sources, config, launchers) if m not in ALLOWED_UNREACHED
    ]
    assert not orphans, (
        "Nothing in the product imports, names or launches these modules. A "
        "module only its own tests reach looks alive and is not (audit "
        "2026-09-22). Wire it, delete it with its tests, or add it to "
        "ALLOWED_UNREACHED with the reason:\n  " + "\n  ".join(orphans)
    )


def test_allowlist_has_no_stale_entries():
    sources, config, launchers = _product_sources()
    known = {_module_name(rel) for rel in sources}
    unreached = set(unreached_modules(sources, config, launchers))
    for mod in ALLOWED_UNREACHED:
        assert mod in known, f"{mod} no longer exists — drop it from ALLOWED_UNREACHED"
        assert mod in unreached, f"{mod} is reached now — drop it from ALLOWED_UNREACHED"


def test_orphan_gate_catches_an_unreached_module():
    """Negative control (§28)."""
    sources = {
        "kazma-core/kazma_core/__init__.py": "",
        "kazma-core/kazma_core/used.py": "X = 1\n",
        "kazma-core/kazma_core/loaded_by_name.py": "Y = 2\n",
        "kazma-core/kazma_core/forgotten.py": "Z = 3\n",
        "kazma-core/kazma_core/runner.py": (
            "from kazma_core.used import X\n"
            "PROVIDERS = {'p': 'kazma_core.loaded_by_name:Thing'}\n"
            'if __name__ == "__main__":\n    print(X)\n'
        ),
    }
    assert unreached_modules(sources) == ["kazma_core.forgotten"]
