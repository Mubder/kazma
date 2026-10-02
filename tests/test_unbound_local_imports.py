"""No function reads a name it imports on a path where the import did not run.

Python makes a name local to the whole function when any statement in it
imports the name, so a read on a path that skipped the import raises
UnboundLocalError. Ruff's undefined-name check does not see it (the name IS
bound, in another branch). Found 2026-10-02:

- ``send_file`` imported ``asyncio`` inside its web-chat branch and called
  ``asyncio.to_thread`` on every other path. From 2026-09-30 every send with
  no bound chat (a reminder, the CLI) raised there; a DEBUG line swallowed
  it, and the tool answered "No active Telegram/chat channel configured".
- ``discover_models`` imported ``SSRFError`` inside a ``try`` after
  ``urlparse``; a URL urlparse refuses ("http://[::1") reached
  ``except SSRFError`` with the name unbound, and the guard raised instead
  of answering.

The check is definite-assignment analysis over each function's statements,
for the names the function binds by import. An import of Kazma's own
modules or the standard library is taken not to raise (a broken install is
not a path the code designs for); a third-party import can.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOP = None  # the state after a path that never continues: every name bound


def _meet(a: set[str] | None, b: set[str] | None) -> set[str] | None:
    if a is TOP:
        return b
    if b is TOP:
        return a
    return a & b


def _bound_by(stmt: ast.stmt) -> set[str]:
    out: set[str] = set()
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        for alias in stmt.names:
            if alias.name != "*":
                out.add((alias.asname or alias.name).split(".")[0])
    return out


def _own_or_stdlib(module: str) -> bool:
    top = module.split(".")[0]
    return top.startswith("kazma") or top in sys.stdlib_module_names


def _can_raise(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.ImportFrom):
        return not (stmt.level or _own_or_stdlib(stmt.module or ""))
    if isinstance(stmt, ast.Import):
        return not all(_own_or_stdlib(a.name) for a in stmt.names)
    return True


def _exits(call: ast.AST) -> bool:
    """``sys.exit(...)``, ``os._exit(...)``, ``exit(...)``, ``quit(...)``."""
    if not isinstance(call, ast.Call):
        return False
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return (f.value.id, f.attr) in {("sys", "exit"), ("os", "_exit")}
    return isinstance(f, ast.Name) and f.id in {"exit", "quit"}


def _loads(node: ast.AST) -> list[ast.Name]:
    """Names a statement's own expressions read (not nested bodies)."""
    out: list[ast.Name] = []
    stack: list[ast.AST] = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            out.append(n)
        for child in ast.iter_child_nodes(n):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                # Decorators and defaults run here; the body runs later.
                if not isinstance(child, ast.Lambda):
                    stack.extend(child.decorator_list)
                if not isinstance(child, ast.ClassDef):
                    stack.extend(child.args.defaults)
                    stack.extend(d for d in child.args.kw_defaults if d is not None)
                continue
            if isinstance(child, ast.stmt):
                continue  # the flow walks statements itself
            stack.append(child)
    return out


class _Flow:
    def __init__(self, tracked: set[str]) -> None:
        self.tracked = tracked
        self.problems: list[tuple[int, str]] = []

    def _check(self, node: ast.AST, defs: set[str] | None) -> None:
        if defs is TOP:
            return
        for name in _loads(node):
            if name.id in self.tracked and name.id not in defs:
                self.problems.append((name.lineno, name.id))

    def block(self, stmts: list[ast.stmt], defs: set[str] | None) -> set[str] | None:
        for stmt in stmts:
            if defs is TOP:
                return TOP
            defs = self.stmt(stmt, defs)
        return defs

    def stmt(self, s: ast.stmt, defs: set[str]) -> set[str] | None:
        if isinstance(s, (ast.Import, ast.ImportFrom)):
            return defs | _bound_by(s)
        if isinstance(s, (ast.Return, ast.Raise)) or (isinstance(s, ast.Expr) and _exits(s.value)):
            self._check(s, defs)
            return TOP
        if isinstance(s, (ast.Continue, ast.Break)):
            return TOP
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for d in s.decorator_list:
                self._check(d, defs)
            return defs | {s.name}
        if isinstance(s, ast.If):
            self._check(s.test, defs)
            return _meet(self.block(s.body, defs), self.block(s.orelse, defs))
        if isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
            self._check(s.iter if isinstance(s, (ast.For, ast.AsyncFor)) else s.test, defs)
            self.block(s.body, defs)  # may run zero times
            after = self.block(s.orelse, defs)
            return defs if after is TOP else after
        if isinstance(s, (ast.With, ast.AsyncWith)):
            for item in s.items:
                self._check(item.context_expr, defs)
            return self.block(s.body, defs)
        if isinstance(s, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            # A handler starts from the state before the body statement that
            # raised; an import that cannot raise starts no handler.
            state: set[str] | None = defs
            entry: set[str] | None = TOP
            for inner in s.body:
                if state is TOP:
                    break
                if _can_raise(inner):
                    entry = _meet(entry, state)
                state = self.stmt(inner, state)
            after = self.block(s.orelse, state) if state is not TOP else TOP
            for h in s.handlers:
                if entry is TOP:
                    continue
                if h.type is not None:
                    self._check(h.type, entry)
                after = _meet(after, self.block(h.body, entry | ({h.name} if h.name else set())))
            if s.finalbody:
                fin = self.block(s.finalbody, defs if entry is TOP else _meet(entry, defs))
                if fin is TOP:
                    return TOP
                if after is not TOP:
                    after = after | (fin - defs)
            return after
        if isinstance(s, ast.Match):
            self._check(s.subject, defs)
            after: set[str] | None = TOP
            for case in s.cases:
                after = _meet(after, self.block(case.body, defs))
            wildcard = any(
                isinstance(c.pattern, ast.MatchAs) and c.pattern.pattern is None and c.guard is None
                for c in s.cases
            )
            return after if wildcard else _meet(after, defs)
        self._check(s, defs)
        return defs | {
            n.id for n in ast.walk(s) if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))
        }


def function_problems(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[int, str]]:
    """(line, name): a read of an import-bound local on a path without its import."""
    tracked: set[str] = set()
    declared: set[str] = set()
    stack: list[ast.AST] = list(fn.body)
    while stack:
        s = stack.pop()
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(s, (ast.Global, ast.Nonlocal)):
            declared.update(s.names)
        if isinstance(s, ast.stmt):
            tracked |= _bound_by(s)
        for child in ast.iter_child_nodes(s):
            if isinstance(child, (ast.stmt, ast.ExceptHandler, ast.match_case)):
                stack.append(child)
    tracked -= declared
    if not tracked:
        return []
    flow = _Flow(tracked)
    args = fn.args
    params = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
    params |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
    flow.block(fn.body, params)
    return sorted(set(flow.problems))


def scan(source: str, path: str) -> list[str]:
    tree = ast.parse(source)
    return [
        f"{path}:{line} {fn.name}(): {name}"
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        for line, name in function_problems(fn)
    ]


def _python_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(p for p in out if p.startswith(("kazma-", "scripts/")))


def test_no_function_reads_an_import_on_a_path_that_skipped_it() -> None:
    findings: list[str] = []
    for rel in _python_files():
        try:
            source = (ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings += scan(source, rel)
    assert findings == [], "\n".join(findings)


# ── what the check catches (each live shape) and what it leaves alone ────

_CAUGHT = {
    "send_file (2026-09-30)": (
        "async def send_file(p, platform):\n"
        "    if platform == 'web':\n"
        "        import asyncio\n"
        "        return await asyncio.to_thread(share, p)\n"
        "    return await asyncio.to_thread(lookup)\n"
    ),
    "discover_models (urlparse before the import)": (
        "async def discover(url):\n"
        "    try:\n"
        "        from urllib.parse import urlparse\n"
        "        host = urlparse(url).hostname\n"
        "        from kazma_core.security.ssrf import SSRFError, validate_url\n"
        "        validate_url(url)\n"
        "    except SSRFError:\n"
        "        return []\n"
    ),
    "a third-party import that can fail": (
        "def load():\n"
        "    try:\n"
        "        import numpy\n"
        "    except ImportError:\n"
        "        pass\n"
        "    return numpy.zeros(3)\n"
    ),
    "an import inside a loop that may not run": (
        "def f(items):\n"
        "    for _ in items:\n"
        "        import json\n"
        "    return json.dumps(items)\n"
    ),
}

_LEFT_ALONE = {
    "the import is the try's first statement": (
        "def f(url):\n"
        "    try:\n"
        "        from kazma_core.security.ssrf import SSRFError, validate_url\n"
        "        validate_url(url)\n"
        "    except SSRFError:\n"
        "        return False\n"
        "    return True\n"
    ),
    "a failed optional import returns": (
        "def f():\n"
        "    try:\n"
        "        import numpy\n"
        "    except ImportError:\n"
        "        return None\n"
        "    return numpy.zeros(3)\n"
    ),
    "both branches import": (
        "def f(flag):\n"
        "    if flag:\n"
        "        import json\n"
        "    else:\n"
        "        import json\n"
        "    return json.dumps(flag)\n"
    ),
    "the failing branch exits": (
        "def f(flag):\n"
        "    try:\n"
        "        import uvicorn\n"
        "    except ImportError:\n"
        "        sys.exit(1)\n"
        "    return uvicorn.run\n"
    ),
    "read only inside the branch": (
        "async def f(platform):\n"
        "    if platform == 'web':\n"
        "        import asyncio\n"
        "        await asyncio.sleep(0)\n"
        "    return platform\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(_CAUGHT))
def test_the_check_catches(shape: str) -> None:
    assert scan(_CAUGHT[shape], "x.py"), shape


@pytest.mark.parametrize("shape", sorted(_LEFT_ALONE))
def test_the_check_leaves_alone(shape: str) -> None:
    assert scan(_LEFT_ALONE[shape], "x.py") == [], shape


# ── the discover_models answer ───────────────────────────────────────────


async def test_a_url_urlparse_refuses_is_refused_not_raised(tmp_path: Path) -> None:
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import initialize_model_registry, reset_model_registry

    reset_model_registry()
    try:
        store = ConfigStore(db_path=str(tmp_path / "r.db"), yaml_path=str(tmp_path / "none.yaml"))
        registry = initialize_model_registry(store)
        registry.upsert_provider({"name": "broken", "base_url": "http://[::1", "api_key": "k", "enabled": True})
        assert await registry.discover_models("broken") == []
    finally:
        reset_model_registry()
