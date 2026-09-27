"""A package's name for one of its submodules is that submodule.

``kazma_core/tools/__init__.py`` re-exported nine tools under their modules'
names (``read_url``, ``file_read``, ``file_write`` ...), and
``kazma_core/web_acquire`` its ``search`` function over ``search.py``. Python
resolves ``import a.b as c`` and ``from a import b`` by attribute, so each
gave the function where a reader expected the module. It cost more than an
hour: a fixture in ``tests/test_file_read_cache_invalidation.py`` did
``from kazma_core.tools import file_write as fw`` and
``monkeypatch.setattr(fw, "check_path_access", ...)`` -- an attribute set on
the function, which patched nothing, and the tests passed anyway. Found and
fixed 2026-09-27: every such name is the module now (``web_acquire/search.py``
became ``serp.py``).

Enumerated from the one list of product modules
(``scripts/check_fresh_imports.py``), with a negative control.
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _enumeration():
    name = "_kazma_check_fresh_imports"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / "check_fresh_imports.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def shadowed_submodules(package: types.ModuleType) -> list[str]:
    """``package.name -> type`` for each submodule the package's namespace
    binds to something other than that module."""
    out: list[str] = []
    for info in pkgutil.iter_modules(getattr(package, "__path__", [])):
        bound = package.__dict__.get(info.name)
        if bound is not None and not isinstance(bound, types.ModuleType):
            out.append(f"{package.__name__}.{info.name} -> {type(bound).__name__}")
    return out


def test_no_package_shadows_its_own_submodule():
    packages = [
        name for name, path in _enumeration().iter_modules(REPO_ROOT)
        if path.name == "__init__.py"
    ]
    assert len(packages) > 50, packages  # the enumeration itself is not blind
    found = []
    for name in packages:
        found += shadowed_submodules(importlib.import_module(name))
    assert found == [], (
        "a package binds a submodule's name to something else; import the "
        "module under its own name and the object from the module: " + ", ".join(found)
    )


def test_negative_control_a_shadowing_package_is_found(tmp_path, monkeypatch):
    pkg = tmp_path / "shadowpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from shadowpkg.thing import thing\nfrom shadowpkg import other\n", encoding="utf-8")
    (pkg / "thing.py").write_text("def thing():\n    return 1\n", encoding="utf-8")
    (pkg / "other.py").write_text("def other():\n    return 2\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        module = importlib.import_module("shadowpkg")
        assert shadowed_submodules(module) == ["shadowpkg.thing -> function"]
    finally:
        for name in [n for n in sys.modules if n == "shadowpkg" or n.startswith("shadowpkg.")]:
            monkeypatch.delitem(sys.modules, name, raising=False)
