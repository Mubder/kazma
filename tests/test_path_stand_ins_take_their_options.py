"""A stand-in for a store-path function takes the options the real one takes.

28 Nov 2026: the receipt gave ``memory_ops_db`` and its siblings a keyword-only
``create`` (a reader must not make the folder it then reads), and
``quality_watch.ops_conn`` passed it. The LongMemEval harness
(``scripts/longmemeval_facts_pilot._route_memory_paths``) and about forty test
stand-ins replaced those functions with zero-argument lambdas, so every memory
worker call raised ``TypeError`` inside a retried handler and three LongMemEval
test files hung until their timeout, on main and in CI.

The rule, read from the source: every function in Kazma's packages that
declares a keyword-only ``create`` parameter (the store-path functions) may be
replaced, in a test or a script, only by something that accepts keyword
arguments -- never a bare ``lambda:``.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PACKAGES = ("kazma-core", "kazma-gateway", "kazma-ui", "kazma-tui", "kazma-cli", "kazma-skills")
_STAND_IN_PLACES = ("tests", "scripts", "kazma-core/tests", "kazma-ui/tests", "kazma-cli/tests",
                    "kazma-gateway/tests", "kazma-skills/tests")


def _listed(*places: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", *places], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout.split()
    return [p for p in out if p.endswith(".py")]


def functions_taking_create() -> set[str]:
    """Names of package functions with a keyword-only ``create`` parameter."""
    names: set[str] = set()
    for rel in _listed(*_PACKAGES):
        if "/tests/" in rel:
            continue
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                if any(arg.arg == "create" for arg in node.args.kwonlyargs):
                    names.add(node.name)
    return names


def zero_argument_stand_ins(text: str, names: set[str]) -> list[str]:
    """Each place *text* replaces one of *names* with a bare ``lambda:``."""
    alternatives = "|".join(sorted(re.escape(n) for n in names))
    patterns = (
        # setattr(module, "name", lambda: ...), patch("pkg.name", lambda: ...)
        re.compile(r"""(?<![\w])["']?(?:[\w.]*\.)?(%s)["']\s*,\s*lambda\s*:""" % alternatives),
        # module.name = lambda: ...
        re.compile(r"""\.(%s)\s*=\s*lambda\s*:""" % alternatives),
    )
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for pattern in patterns:
            for match in pattern.finditer(line):
                found.append(f"{number}:{match.group(1)}")
    return found


def test_the_store_path_functions_are_found_from_the_source() -> None:
    names = functions_taking_create()
    assert {"data_dir", "memory_ops_db", "tool_effects_db", "llm_calls_db", "hitl_gates_db",
            "thread_scopes_db"} <= names, names


def test_no_stand_in_drops_the_options() -> None:
    names = functions_taking_create()
    offenders = []
    for rel in _listed(*_STAND_IN_PLACES):
        if rel.endswith("test_path_stand_ins_take_their_options.py"):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        offenders += [f"{rel}:{hit}" for hit in zero_argument_stand_ins(text, names)]
    assert not offenders, (
        "a stand-in for a store-path function takes no arguments, but the real one takes create=: "
        "write `lambda **_: ...` (or pass **kw through):\n" + "\n".join(offenders))


def test_control_each_shape_of_a_zero_argument_stand_in_is_caught() -> None:
    names = {"memory_ops_db", "data_dir"}
    planted = (
        'monkeypatch.setattr(paths, "memory_ops_db", lambda: str(ops))\n'
        'monkeypatch.setattr("kazma_core.paths.data_dir", lambda: tmp_path)\n'
        "paths.memory_ops_db = lambda: (_STORE.get() or ('', ops()))[1]\n"
    )
    assert zero_argument_stand_ins(planted, names) == ["1:memory_ops_db", "2:data_dir", "3:memory_ops_db"]


def test_control_a_stand_in_that_takes_the_options_or_another_name_passes() -> None:
    names = {"memory_ops_db", "data_dir"}
    fine = (
        'monkeypatch.setattr(paths, "memory_ops_db", lambda **_: str(ops))\n'
        "paths.memory_ops_db = lambda **kw: ops(**kw)\n"
        'monkeypatch.setattr(universal, "_data_dir", lambda: data)\n'
    )
    assert zero_argument_stand_ins(fine, names) == []


def test_the_harness_stand_in_passes_create_through(monkeypatch) -> None:
    """The LongMemEval harness's routed path takes ``create=False`` and passes it on."""
    import kazma_core.paths as paths

    seen: list[dict] = []
    monkeypatch.setattr(paths, "primary_memory_db", lambda: "primary.db")
    monkeypatch.setattr(paths, "memory_ops_db", lambda **kw: seen.append(kw) or "ops.db")
    import importlib.util

    spec = importlib.util.spec_from_file_location("_pilot_for_test", ROOT / "scripts" / "longmemeval_facts_pilot.py")
    assert spec and spec.loader
    pilot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pilot)
    pilot._route_memory_paths()
    assert paths.memory_ops_db(create=False) == "ops.db"
    assert paths.memory_ops_db() == "ops.db"
    assert seen == [{"create": False}, {}]
