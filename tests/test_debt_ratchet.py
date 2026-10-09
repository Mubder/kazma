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

* ``async_route_never_awaits`` (counted here until 2026-10-01; 115 then) --
  an ``async def`` route handler with no ``await`` runs its body on the event
  loop that serves every SSE/WS stream. Swept that day: each one left names
  its reason to run on the loop in ``tests/test_async_routes_on_the_loop.py``
  (``ON_THE_LOOP``), which uses this module's detector, and a new one fails
  there.
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
* ``bare_module_attr_assignments`` (2026-09-26) -- a test that sets an
  attribute of something imported from Kazma by plain assignment. Nothing
  restores it, so every later test in the process sees the fake:
  ``rs._db_path = ...`` hid another file's check once a new test shifted the
  chunk boundaries. Some restore it in ``finally``; ``monkeypatch.setattr``
  is the fix either way. All 138 were converted on 2026-09-26, so the
  baseline is zero: a new one fails here, whoever wrote it
  (tests/test_order_independence.py has the other two routes).
* ``shared_temp_names`` (2026-09-26, zero from the start) -- a test that names
  a fixed path under the machine's temp directory, or lists it. Every process
  on the machine shares that directory: the other fast_test chunks, a second
  suite run, the live server. A listing counts their files as the test's own
  (``test_python_exec_cleanup`` failed whenever another chunk ran
  ``python_exec``), and a fixed name can be someone else's file
  (``test_path_anchoring`` unlinked ``%TEMP%/test.txt``, whoever made it).
  Use ``tmp_path``, ``mkdtemp``/``mkstemp``, or a uuid in the name.
* ``async_inline_db_calls`` (2026-09-27) -- an ``async def`` that makes a
  DB-API call (``execute``, ``executemany``, ``executescript``, ``commit``)
  itself, not awaited: the statement runs on the loop that serves every SSE
  and WebSocket stream, and a lock wait stalls them all. §26E's gate catches
  a ``sqlite3.connect`` in async code; opening the connection in a thread
  and then executing on the loop passed it -- the memory admin API did that
  in 17 routes. Fix: the body in a ``_..._sync`` function run with
  ``asyncio.to_thread`` (``memory_api.py`` shows the shape).
* ``async_tools_never_await`` (2026-09-27) -- a registered agent tool that is
  ``async def`` but never awaits. The registry runs an async tool ON the
  loop and threads only sync ones, so all of its work blocks every stream:
  ``memory_search`` embedded the query and searched SQLite there. Fix:
  ``await asyncio.to_thread(...)`` around the work (``memory_store`` shows
  it); a plain ``def`` only when nothing in it needs the loop
  (``spawn_background``, ``create_task``, loop-bound state).
* ``functions_over_complexity_50`` (2026-09-30) -- product functions with
  more than 50 decision points of their own (``if``/loop/``except``/
  conditional expression/``case``/comprehension, each extra ``and``/``or``
  operand; a nested function counts on its own). The full audit found 33 over
  50 (AUD-026): the two chat transports' routers held the same protocol twice
  at 277 and 272. Removing the WebSocket's copy took its router to 45; the
  count only goes down, so a split is locked in and a new god function fails.
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
    "blind_except": 3545,
    # ...whose body is only `pass` (or a docstring): the error vanishes
    "silent_except": 444,
}

#: Structural debt, 2026-09-25 (see the module docstring). Same rules.
STRUCTURAL_BASELINE = {
    # Turn workspace entry consumes the binding resolver through its module.
    "module_local_public_symbols": 554,
    "patched_value_imports": 72,
    "sleep_then_assert": 52,
    "bare_module_attr_assignments": 0,
    "shared_temp_names": 0,
    "async_inline_db_calls": 8,
    "async_tools_never_await": 7,
    "functions_over_complexity_50": 36,
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
        # --others: a new file counts before it is committed. Tracked-only
        # listing let a local full run pass that CI then failed (2026-09-26:
        # observability/supervisor_watch.py was untracked when it ran).
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "kazma-*/*.py", "kazma-*/**/*.py"],
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


def _kazma_aliases(nodes) -> set[str]:
    """Names bound by importing from a Kazma package."""
    out: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("kazma_"):
            out.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(
                a.asname or a.name.split(".")[0]
                for a in node.names
                if a.name.startswith("kazma_")
            )
    return out


def bare_module_attr_assignments(tests: dict[str, str]) -> list[str]:
    """``alias.attr = value`` on something imported from Kazma, inside a test.

    Nothing restores it: the fake outlives the test, and every later test in
    the process sees it. ``rs._db_path = lambda: ...`` in the industry smoke
    matrix hid test_no_cwd_data_dir_fallback's check whenever one chunk ran
    both -- found 2026-09-26, when a new file shifted the chunk boundaries.
    The fix is ``monkeypatch.setattr``.
    """
    found: set[str] = set()
    for rel, text in tests.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        module_level = _kazma_aliases(tree.body)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            aliases = module_level | _kazma_aliases(ast.walk(fn))
            for node in ast.walk(fn):
                if not isinstance(node, ast.Assign):
                    continue
                for tgt in node.targets:
                    if (
                        isinstance(tgt, ast.Attribute)
                        and isinstance(tgt.value, ast.Name)
                        and tgt.value.id in aliases
                    ):
                        found.add(f"{rel}:{node.lineno} {tgt.value.id}.{tgt.attr}")
    return sorted(found)


_LISTING_CALLS = frozenset({"listdir", "scandir", "walk", "glob", "iglob"})
_LISTING_METHODS = frozenset({"iterdir", "glob", "rglob"})


def _is_gettempdir(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Attribute) and node.func.attr == "gettempdir")
        or (isinstance(node.func, ast.Name) and node.func.id == "gettempdir")
    )


def _temp_root(node: ast.AST, names: set[str]) -> bool:
    """``gettempdir()``, a Path or str around it, its resolve(), or a name bound to one."""
    if _is_gettempdir(node):
        return True
    if isinstance(node, ast.Name):
        return node.id in names
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in ("resolve", "absolute"):
        return _temp_root(func.value, names)
    callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    return callee in ("Path", "PurePath", "str") and bool(node.args) and _temp_root(node.args[0], names)


def shared_temp_names(tests: dict[str, str]) -> list[str]:
    """A fixed name under, or a listing of, the machine's temp directory."""
    found: set[str] = set()
    for rel, text in tests.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        scopes = [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]
        for scope in scopes:
            names: set[str] = set()
            grew = True
            while grew:  # a = gettempdir(); b = Path(a) -- both are the temp root
                grew = False
                for node in ast.walk(scope):
                    if (isinstance(node, ast.Assign) and len(node.targets) == 1
                            and isinstance(node.targets[0], ast.Name)
                            and node.targets[0].id not in names and _temp_root(node.value, names)):
                        names.add(node.targets[0].id)
                        grew = True
            for node in ast.walk(scope):
                fixed_name = (
                    isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                    and _temp_root(node.left, names)
                    and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)
                )
                if isinstance(node, ast.Call) and node.args and _temp_root(node.args[0], names):
                    callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                    if callee == "join" and any(
                        isinstance(a, ast.Constant) and isinstance(a.value, str) for a in node.args[1:]
                    ):
                        fixed_name = True
                    if callee in _LISTING_CALLS:
                        fixed_name = True
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _LISTING_METHODS and _temp_root(node.func.value, names)):
                    fixed_name = True
                if fixed_name:
                    found.add(f"{rel}:{node.lineno}")
    return sorted(found, key=lambda e: (e.rsplit(":", 1)[0], int(e.rsplit(":", 1)[1])))


#: Calls that run a statement on a sync DB-API connection or cursor.
_DB_API_CALLS = frozenset({"execute", "executemany", "executescript", "commit"})
#: Functions whose arguments are coroutines: ``wait_for(executor.execute(...))``
#: is an async tool executor, not a database.
_COROUTINE_CONSUMERS = frozenset({
    "wait_for", "gather", "shield", "create_task", "ensure_future",
    "spawn_background", "run_coroutine_threadsafe",
})


def _own_nodes(fn: ast.AST):
    """Nodes of *fn*'s own body: not a nested def, lambda or class."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _runs_db_call_inline(fn: ast.AsyncFunctionDef) -> bool:
    awaited: set[int] = set()
    for node in _own_nodes(fn):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            awaited.add(id(node.value))  # an async driver's call
        if isinstance(node, ast.AsyncWith):
            awaited.update(id(i.context_expr) for i in node.items)  # `async with db.execute(...)`
        if isinstance(node, ast.Call):
            callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if callee in _COROUTINE_CONSUMERS:
                awaited.update(id(a) for a in node.args if isinstance(a, ast.Call))
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _DB_API_CALLS
        and id(node) not in awaited
        for node in _own_nodes(fn)
    )


def async_inline_db_calls(product: dict[str, str]) -> list[str]:
    found: list[str] = []
    for rel, text in product.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        found += [
            f"{rel} {fn.name}"
            for fn in ast.walk(tree)
            if isinstance(fn, ast.AsyncFunctionDef) and _runs_db_call_inline(fn)
        ]
    return sorted(found)


def _is_tool_registration(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
    return name in ("register", "register_tool")


def async_tools_never_await(product: dict[str, str]) -> list[str]:
    found: list[str] = []
    for rel, text in product.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if (
                isinstance(fn, ast.AsyncFunctionDef)
                and any(_is_tool_registration(d) for d in fn.decorator_list)
                and not any(isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for n in _own_nodes(fn))
            ):
                found.append(f"{rel} {fn.name}")
    return sorted(found)


#: Decision points the complexity count adds one for each.
_BRANCH_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp,
    ast.match_case,
)


def _complexity(fn: ast.AST) -> int:
    """Cyclomatic complexity of *fn*'s own body (nested defs count alone)."""
    n = 1
    for node in _own_nodes(fn):
        if isinstance(node, _BRANCH_NODES):
            n += 1
        elif isinstance(node, ast.BoolOp):
            n += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            n += 1 + len(node.ifs)
    return n


def complex_functions(product: dict[str, str], limit: int = 50) -> list[str]:
    """Product functions (any depth) whose own complexity exceeds *limit*."""
    found: list[str] = []
    for rel, text in product.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                score = _complexity(node)
                if score > limit:
                    found.append(f"{rel}:{node.lineno} {node.name} ({score})")
    return sorted(found)


def _tracked(patterns: list[str]) -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", *patterns],
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
        "module_local_public_symbols": module_local_public_symbols(product, elsewhere),
        "patched_value_imports": patched_value_imports(product, tests),
        "sleep_then_assert": sleep_then_assert(tests),
        "bare_module_attr_assignments": bare_module_attr_assignments(tests),
        "shared_temp_names": shared_temp_names(tests),
        "async_inline_db_calls": async_inline_db_calls(product),
        "async_tools_never_await": async_tools_never_await(product),
        "functions_over_complexity_50": complex_functions(product),
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

    db = {
        "r.py": (
            "async def a():\n    conn = await asyncio.to_thread(_conn)\n    conn.execute('x')\n"
            "async def b():\n    await db.execute('x')\n    await db.commit()\n"
            "async def c():\n    return await asyncio.to_thread(_c_sync)\n"
            "async def d():\n    async with db.execute('x') as cur:\n        pass\n"
            "async def e():\n    def inner():\n        conn.commit()\n    return await asyncio.to_thread(inner)\n"
            "async def f():\n    rows = (await db.execute('x')).fetchall()\n    conn.commit()\n"
            "async def g():\n    return await asyncio.wait_for(executor.execute(name, args), 5)\n"
            "async def h():\n    await asyncio.to_thread(process, conn.execute('x'))\n"
        )
    }
    # g hands a coroutine to wait_for (the tool executor); h runs the
    # statement on the loop to build to_thread's argument.
    assert [e.split()[-1] for e in async_inline_db_calls(db)] == ["a", "f", "h"]

    tools = {
        "t.py": (
            "@registry.register(description='x')\nasync def a(q):\n    return search(q)\n"
            "@registry.register(description='x')\nasync def b(q):\n    return await asyncio.to_thread(search, q)\n"
            "@registry.register(description='x')\ndef c(q):\n    return search(q)\n"
            "async def d(q):\n    return search(q)\n"
        )
    }
    assert [e.split()[-1] for e in async_tools_never_await(tools)] == ["a"]

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

    leaky = {
        "tests/test_l.py": (
            "from kazma_core.tools import research_session as rs\n"
            "def test_a():\n    rs._db_path = lambda: 1\n"
            "def test_b(monkeypatch):\n    monkeypatch.setattr(rs, '_db_path', lambda: 1)\n"
            "def test_c():\n    import kazma_core.paths as paths\n    paths.data_dir = None\n"
            "def test_d():\n    local = object()\n    local.x = 1\n"
        )
    }
    assert bare_module_attr_assignments(leaky) == [
        "tests/test_l.py:3 rs._db_path", "tests/test_l.py:8 paths.data_dir",
    ]

    shared = {
        "tests/test_tmp.py": (
            "import os, tempfile, uuid\nfrom pathlib import Path\n"
            "def test_a():\n    Path(tempfile.gettempdir()) / 'fixed.txt'\n"
            "def test_b():\n    base = tempfile.gettempdir()\n    os.listdir(base)\n"
            "def test_c():\n    os.path.join(tempfile.gettempdir(), 'x')\n"
            "def test_d():\n    Path(tempfile.gettempdir()).resolve().iterdir()\n"
            "def test_ok(tmp_path):\n    Path(tempfile.gettempdir()) / f'x-{uuid.uuid4().hex}'\n"
            "    root = Path(tempfile.gettempdir()).resolve()\n    tempfile.mkstemp()\n"
            "    os.listdir(tmp_path)\n"
        )
    }
    assert shared_temp_names(shared) == [
        "tests/test_tmp.py:4", "tests/test_tmp.py:7", "tests/test_tmp.py:9", "tests/test_tmp.py:11",
    ]


def test_the_complexity_count_counts_what_it_says():
    """Negative control: 51 branches of its own count, a nested function
    counts alone, and 10 branches do not."""
    big = "def big(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(51))
    small = "def small(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(10))
    nested = (
        "def outer(x):\n"
        "    def inner(y):\n"
        + "".join(f"        if y == {i}:\n            return {i}\n" for i in range(51))
        + "    return inner\n"
    )
    found = complex_functions({"a.py": big, "b.py": small, "c.py": nested})
    assert [f.split(" ")[1] for f in found] == ["big", "inner"], found
    assert all("(52)" in f for f in found), found
