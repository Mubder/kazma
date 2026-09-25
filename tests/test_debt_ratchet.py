"""Debt that cannot grow: counts that may only go down.

The 2026-09-22 audit found 3,846 ``except Exception``/bare ``except`` handlers
in product code, 577 of them with a body of just ``pass``. The one place that
most needed a guard (the supervisor's snapshot capture) had none, while
thousands of calls that did not need one swallowed everything. Nobody is going
to rewrite 3,846 handlers in one change, and a gate that is red on day one
gets ignored (docs/KNOWN_GAPS.md). So this is a ratchet:

* a count ABOVE the baseline fails — new debt, fix it or justify it by
  raising the number in review, where someone has to look at it;
* a count BELOW the baseline also fails, with the new number — lock the gain
  in by lowering the baseline in the same change.

2026-09-25 added four more, for debt that was measured, recorded in
docs/KNOWN_GAPS.md and deliberately NOT swept, because a big-bang rewrite is
riskier than the debt — but which nothing stopped from growing:

* ``async_route_never_awaits`` — an ``async def`` route handler with no
  ``await`` runs its body on the event loop that serves every SSE/WS stream;
  AGENTS §35 says such a handler is a plain ``def``. Not swept: moving a
  handler into the threadpool breaks ``spawn_background``/``create_task``
  calls and exposes in-memory state the loop used to serialise.
* ``module_local_public_symbols`` — public (no ``_``), undecorated top-level
  functions and classes named in no other product file, script or config.
  Either dead or private-by-use; the fix is a ``_`` prefix or deletion. Not
  swept: skills and plugins reach code a scanner cannot see (a "remove unused
  imports" pass once broke every native skill). Broader than the 2026-09-16
  audit's 175 "unreferenced" symbols: it also counts helpers used only
  inside their own module.
* ``patched_value_imports`` — module-level ``from a.b import name`` where a
  test patches ``a.b.name``: the importer keeps the original object, so the
  patch misses it — or captures the fake forever (the ``_streaming``
  ``persist_reply`` failure, 2026-09-21).
* ``sleep_then_assert`` — a test that sleeps under two seconds and asserts
  within the next few statements: a bet on timing (both flakes fixed that
  day were this shape). Some are correct; each one needs reading.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Lower these whenever the counts drop. Never raise them casually.
BASELINE = {
    # except Exception / except BaseException / bare except, any body
    "blind_except": 3827,
    # ...whose body is only `pass` (or a docstring): the error vanishes
    "silent_except": 571,
}

#: Structural debt, 2026-09-25 (see the module docstring). Same rules.
STRUCTURAL_BASELINE = {
    "async_route_never_awaits": 267,
    "module_local_public_symbols": 604,
    "patched_value_imports": 83,
    "sleep_then_assert": 53,
}


def _is_blind(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    return t is None or (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException"))


def _is_silent(handler: ast.ExceptHandler) -> bool:
    return all(
        isinstance(s, ast.Pass)
        or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
        for s in handler.body
    )


def debt_counts(sources: list[str]) -> dict[str, int]:
    counts = {"blind_except": 0, "silent_except": 0}
    for text in sources:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_blind(node):
                counts["blind_except"] += 1
                if _is_silent(node):
                    counts["silent_except"] += 1
    return counts


def _product_sources() -> list[str]:
    files = subprocess.run(
        ["git", "ls-files", "kazma-*/*.py", "kazma-*/**/*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [
        (REPO_ROOT / f).read_text(encoding="utf-8", errors="replace")
        for f in files
        if "_tests" not in f and "/tests/" not in f and (REPO_ROOT / f).is_file()
    ]


def test_exception_debt_only_goes_down():
    counts = debt_counts(_product_sources())
    grew = {k: (v, BASELINE[k]) for k, v in counts.items() if v > BASELINE[k]}
    shrank = {k: (v, BASELINE[k]) for k, v in counts.items() if v < BASELINE[k]}
    assert not grew, (
        "New blind/silent exception handlers. Catch the exception you expect, "
        "or log it (logger.exception / exc_info=True) instead of passing:\n  "
        + "\n  ".join(f"{k}: {now} > baseline {base}" for k, (now, base) in grew.items())
    )
    assert not shrank, (
        "Debt went down — lock it in by lowering BASELINE in this file:\n  "
        + "\n  ".join(f"{k}: set to {now} (was {base})" for k, (now, base) in shrank.items())
    )


def test_ratchet_counts_what_it_says():
    """Negative control (§28)."""
    source = '''
try:
    a()
except Exception:
    pass
try:
    b()
except:
    log()
try:
    c()
except ValueError:
    pass
try:
    d()
except BaseException:
    "documented, still silent"
'''
    assert debt_counts([source]) == {"blind_except": 3, "silent_except": 2}


# ── structural debt (2026-09-25) ────────────────────────────────────────────

_ROUTE_METHODS = frozenset({"get", "post", "put", "delete", "patch", "api_route"})
_SHORT_SLEEP_S = 2.0
_ASSERT_WINDOW = 3


def _own_awaits(fn: ast.AST) -> int:
    """Awaits in *fn*'s own body — nested defs and lambdas run elsewhere."""
    n = 0
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
            n += 1
        stack.extend(ast.iter_child_nodes(node))
    return n


def _is_route(fn: ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr in _ROUTE_METHODS
        for d in fn.decorator_list
    )


def async_routes_never_awaiting(sources: dict[str, str]) -> list[str]:
    found: list[str] = []
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and _is_route(node) and not _own_awaits(node):
                found.append(f"{rel}:{node.lineno} {node.name}")
    return found


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def module_local_public_symbols(product: dict[str, str], elsewhere: dict[str, str]) -> list[str]:
    """Public, undecorated top-level defs named in no OTHER product/script/config file.

    Decorated definitions are excluded: routes, tool registrations and CLI
    commands are reached through their decorator, not by name.
    """
    defs: dict[str, list[str]] = {}
    for rel, text in product.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and not node.name.startswith("_")
                and not node.decorator_list
            ):
                defs.setdefault(node.name, []).append(rel)
    seen_in: dict[str, set[str]] = {}
    for rel, text in {**product, **elsewhere}.items():
        for tok in set(_IDENT.findall(text)):
            if tok in defs:
                seen_in.setdefault(tok, set()).add(rel)
    unused: list[str] = []
    for name, homes in defs.items():
        for home in homes:
            if not (seen_in.get(name, set()) - {home}):
                unused.append(f"{home} {name}")
    return sorted(unused)


_PATCH_TARGET = re.compile(
    r"""(?:setattr|patch)\(\s*['"]((?:kazma_[a-z_]+)(?:\.[A-Za-z_][A-Za-z0-9_]*)+)['"]"""
)


def patched_value_imports(product: dict[str, str], tests: dict[str, str]) -> list[str]:
    """Module-level ``from a.b import name`` where a test patches ``a.b.name``."""
    targets: set[str] = set()
    for text in tests.values():
        targets.update(_PATCH_TARGET.findall(text))
    found: list[str] = []
    for rel, text in product.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    if f"{node.module}.{alias.name}" in targets:
                        found.append(f"{rel}:{node.lineno} {node.module}.{alias.name}")
    return sorted(found)


def _short_sleep(node: ast.AST) -> bool:
    call = node.value if isinstance(node, ast.Expr) else None
    if isinstance(call, ast.Await):
        call = call.value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
        return False
    if call.func.attr != "sleep" or not call.args:
        return False
    arg = call.args[0]
    return (
        isinstance(arg, ast.Constant)
        and isinstance(arg.value, (int, float))
        and 0 < float(arg.value) < _SHORT_SLEEP_S
    )


def sleep_then_assert(tests: dict[str, str]) -> list[str]:
    found: list[str] = []
    for rel, text in tests.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list):
                continue
            for i, stmt in enumerate(body):
                if _short_sleep(stmt) and any(
                    isinstance(nxt, ast.Assert) for nxt in body[i + 1 : i + 1 + _ASSERT_WINDOW]
                ):
                    found.append(f"{rel}:{stmt.lineno}")
    return found


def _tracked(patterns: list[str]) -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", *patterns],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return {
        f: (REPO_ROOT / f).read_text(encoding="utf-8", errors="replace")
        for f in files
        if (REPO_ROOT / f).is_file()
    }


def _is_test_path(f: str) -> bool:
    return "_tests" in f or "/tests/" in f or f.startswith("tests/")


def structural_debt() -> dict[str, list[str]]:
    everything = _tracked(["*.py", "*.yaml", "*.yml", "*.toml", "*.json"])
    product = {
        f: t for f, t in everything.items()
        if f.startswith("kazma-") and f.endswith(".py") and not _is_test_path(f)
    }
    tests = {f: t for f, t in everything.items() if f.endswith(".py") and _is_test_path(f)}
    elsewhere = {
        f: t for f, t in everything.items()
        if f not in product and not _is_test_path(f)
    }
    return {
        "async_route_never_awaits": async_routes_never_awaiting(product),
        "module_local_public_symbols": module_local_public_symbols(product, elsewhere),
        "patched_value_imports": patched_value_imports(product, tests),
        "sleep_then_assert": sleep_then_assert(tests),
    }


def test_structural_debt_only_goes_down():
    found = structural_debt()
    counts = {k: len(v) for k, v in found.items()}
    grew = {k: (counts[k], STRUCTURAL_BASELINE[k]) for k in counts if counts[k] > STRUCTURAL_BASELINE[k]}
    shrank = {k: (counts[k], STRUCTURAL_BASELINE[k]) for k in counts if counts[k] < STRUCTURAL_BASELINE[k]}
    assert not grew, (
        "New structural debt (see this module's docstring for each kind and its fix):\n  "
        + "\n  ".join(
            f"{k}: {now} > baseline {base}; newest entries: {found[k][-3:]}"
            for k, (now, base) in grew.items()
        )
    )
    assert not shrank, (
        "Structural debt went down — lock it in by lowering STRUCTURAL_BASELINE:\n  "
        + "\n  ".join(f"{k}: set to {now} (was {base})" for k, (now, base) in shrank.items())
    )


def test_structural_scanners_count_what_they_say():
    """Negative controls (§28): one planted instance of each kind, and its fix."""
    routes = {
        "r.py": (
            "@router.get('/a')\nasync def a():\n    return store.read()\n"
            "@router.post('/b')\nasync def b():\n    await x()\n"
            "@router.get('/c')\ndef c():\n    return store.read()\n"
            "@router.get('/d')\nasync def d():\n    async def inner():\n        await y()\n    return 1\n"
        )
    }
    assert [e.split()[-1] for e in async_routes_never_awaiting(routes)] == ["a", "d"]

    product = {
        "kazma-core/kazma_core/m.py": "def used():\n    pass\ndef lonely():\n    pass\n"
        "@router.get('/x')\ndef routed():\n    pass\ndef _private():\n    pass\n",
        "kazma-core/kazma_core/n.py": "from kazma_core.m import used\n",
    }
    assert module_local_public_symbols(product, {}) == ["kazma-core/kazma_core/m.py lonely"]

    tests = {"tests/test_t.py": "monkeypatch.setattr('kazma_core.a.helper', fake)\n"}
    product2 = {
        "kazma-core/kazma_core/b.py": "from kazma_core.a import helper\nfrom kazma_core.a import other\n",
        "kazma-core/kazma_core/c.py": "def f():\n    from kazma_core.a import helper\n",
    }
    assert patched_value_imports(product2, tests) == ["kazma-core/kazma_core/b.py:1 kazma_core.a.helper"]

    sleepy = {
        "tests/test_s.py": (
            "def test_a():\n    start()\n    time.sleep(0.2)\n    assert done()\n"
            "async def test_b():\n    await asyncio.sleep(0.05)\n    x = 1\n    assert x\n"
            "def test_c():\n    time.sleep(5)\n    assert done()\n"
            "def test_d():\n    time.sleep(0.2)\n    a()\n    b()\n    c()\n    assert done()\n"
        )
    }
    assert sleep_then_assert(sleepy) == ["tests/test_s.py:3", "tests/test_s.py:6"]
