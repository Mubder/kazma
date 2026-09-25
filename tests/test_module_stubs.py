"""``patch.dict(sys.modules, ...)`` evicts modules. The suite must not use it.

On exit ``patch.dict`` clears ``sys.modules`` and restores its entry
snapshot, so every module first imported while the patch was open is thrown
away. The next import makes a new module object; a later test patches one copy
while the code under test holds the other. That removed
``kazma_core.security.ssrf`` mid-suite and made a read_url test pass or fail
by chunk order (docs/KNOWN_GAPS.md, 2026-09-21). The file that hit it was
fixed by pre-importing the module; the pattern stayed in three other files.

``tests._module_stubs.stub_modules`` restores only the names it stubbed. This
file proves the difference and bans the pattern, with a negative control.
"""

from __future__ import annotations

import ast
import sys
import textwrap
import types
from pathlib import Path
from unittest.mock import patch

from tests._module_stubs import stub_modules

REPO = Path(__file__).resolve().parents[1]
_PROBE = "_kazma_stub_probe_mod"


def _import_probe() -> types.ModuleType:
    """Stand-in for 'something imported lazily while the stub is open'."""
    mod = types.ModuleType(_PROBE)
    sys.modules[_PROBE] = mod
    return mod


def test_stub_modules_keeps_what_was_imported_inside_it():
    sys.modules.pop(_PROBE, None)
    fake = types.ModuleType("fake_dep")
    with stub_modules({"fake_dep_for_test": fake}):
        assert sys.modules["fake_dep_for_test"] is fake
        probe = _import_probe()
    assert "fake_dep_for_test" not in sys.modules, "the stub itself is removed"
    assert sys.modules.get(_PROBE) is probe, "a module imported inside survives"
    sys.modules.pop(_PROBE, None)


def test_stub_modules_restores_a_real_module_it_replaced():
    real = sys.modules["json"]
    with stub_modules({"json": None}):
        try:
            import json  # noqa: F401
        except ImportError:
            blocked = True
        else:
            blocked = False
    assert blocked, "None makes the import fail, as with patch.dict"
    assert sys.modules["json"] is real


def test_negative_control_patch_dict_evicts_the_inner_import():
    """The hazard itself, so the helper above is shown to be doing something."""
    sys.modules.pop(_PROBE, None)
    with patch.dict(sys.modules, {"fake_dep_for_test": types.ModuleType("x")}):  # noqa: KAZMA-STUB
        _import_probe()
    assert _PROBE not in sys.modules, "patch.dict threw away a module imported inside it"


def _patch_dict_over_sys_modules(sources: dict[str, str]) -> list[str]:
    hits: list[str] = []
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and f.attr == "dict"):
                continue
            target = node.args[0]
            names_sys_modules = (
                isinstance(target, ast.Attribute)
                and target.attr == "modules"
                and isinstance(target.value, ast.Name)
                and target.value.id == "sys"
            ) or (isinstance(target, ast.Constant) and target.value == "sys.modules")
            if names_sys_modules and "KAZMA-STUB" not in lines[node.lineno - 1]:
                hits.append(f"{rel}:{node.lineno}")
    return sorted(hits)


def _test_sources() -> dict[str, str]:
    roots = [REPO / "tests", *sorted(REPO.glob("kazma-*/tests"))]
    out: dict[str, str] = {}
    for root in roots:
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            out[p.relative_to(REPO).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    return out


def test_no_test_patches_sys_modules_with_patch_dict():
    hits = _patch_dict_over_sys_modules(_test_sources())
    assert not hits, (
        "patch.dict(sys.modules, ...) evicts every module imported while it is open,\n"
        "and the damage lands on a later, unrelated test. Use\n"
        "tests._module_stubs.stub_modules({...}) or monkeypatch.setitem(sys.modules, ...):\n  "
        + "\n  ".join(hits)
    )


def test_the_gate_sees_every_spelling():
    planted = {
        "t.py": textwrap.dedent(
            '''
            import sys
            from unittest import mock
            from unittest.mock import patch
            def a():
                with patch.dict(sys.modules, {"x": None}):
                    pass
            def b():
                with mock.patch.dict("sys.modules", {"x": None}, clear=True):
                    pass
            def c():
                with patch.dict(os.environ, {"X": "1"}):
                    pass
            '''
        )
    }
    assert _patch_dict_over_sys_modules(planted) == ["t.py:6", "t.py:9"]
