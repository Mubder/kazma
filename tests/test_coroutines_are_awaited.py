"""No product code calls a coroutine function without awaiting it.

Entity resolution's tier 3 (removed 2026-11-22, the memory plan's finding 6)
called ``client.chat(...)`` from a plain function and read the result as a
reply: the call returned a coroutine nobody ran, its text was the
coroutine's repr, and every answer was "skip". It never reached a provider.
The same shape left the terminal app's swarm history table unrefreshed when
shown (``TaskHistoryTable.on_show`` called its async ``_refresh()`` from a
plain method), found by this gate.

Read from the source, as the other AST gates are. A call is a coroutine
call when it resolves to an ``async def`` that is neither decorated (a
decorator -- ``asynccontextmanager``, Textual's ``work`` -- may change what a
call returns) nor an async generator (iterated, not awaited):

* a function of the same module, or a name imported from Kazma's own modules;
* ``module.fn(`` through a Kazma module imported by name or alias;
* ``self.method(`` / ``cls.method(`` of the enclosing class;
* any ``X.chat(`` / ``X.chat_stream(`` -- a model client's methods, whose
  receiver the source cannot type (the tier-3 shape).

Its result must be consumed: awaited, returned or yielded, iterated with
``async for``, entered with ``async with``, handed to a call (a task, a
worker, a timeout: that call owns it), held in a container, or assigned to a
name that is itself consumed so. Discarded, or used as a value (``isinstance``,
``str``, an attribute, a test), it is a finding.
The controls: the removed tier-3 function as it was, and planted shapes.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Calls that read a value without taking the coroutine over: a coroutine
#: handed to one of these is still a coroutine nobody runs.
NOT_CONSUMERS = frozenset({
    "isinstance", "str", "repr", "len", "bool", "int", "float", "getattr", "hasattr", "type", "dict",
    "list", "tuple", "set", "print", "format", "join", "debug", "info", "warning", "error", "exception",
    "dumps", "callable", "id", "hash", "sorted", "iter", "next",
})
#: A model client's methods: the receiver is untyped in the source.
CLIENT_METHODS = frozenset({"chat", "chat_stream"})
_PASS_THROUGH_DECORATORS = frozenset({"staticmethod", "classmethod", "override"})


def _product_files() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard",
                             "kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py"],
                            cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return [f for f in listed if "/tests/" not in f and "_tests/" not in f and (ROOT / f).is_file()]


def _module_of(path: str) -> str:
    parts = Path(path).with_suffix("").parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_generator(node: ast.AST) -> bool:
    todo = list(getattr(node, "body", []))
    while todo:
        n = todo.pop()
        if isinstance(n, (ast.Yield, ast.YieldFrom)):
            return True
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        todo.extend(ast.iter_child_nodes(n))
    return False


def _plain_coroutine(node: ast.AST) -> bool:
    """An ``async def`` whose call returns a coroutine: not decorated, not a generator."""
    return (isinstance(node, ast.AsyncFunctionDef) and not _is_generator(node)
            and all(isinstance(d, ast.Name) and d.id in _PASS_THROUGH_DECORATORS for d in node.decorator_list))


def _top_coroutines(tree: ast.Module) -> set[str]:
    return {n.name for n in tree.body if _plain_coroutine(n)}


def _callee(call: ast.AST | None) -> str:
    if not isinstance(call, ast.Call):
        return ""
    fn = call.func
    return fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""


class _Program:
    """Every product module's top-level coroutine functions, by module name."""

    def __init__(self, sources: dict[str, str]) -> None:
        self.trees = {f: ast.parse(text) for f, text in sources.items()}
        self.modules = {_module_of(f): f for f in self.trees}
        self.coroutines = {f: _top_coroutines(t) for f, t in self.trees.items()}

    def resolver(self, file: str) -> tuple[set[str], dict[str, str]]:
        names = set(self.coroutines[file])
        aliases: dict[str, str] = {}
        for node in ast.walk(self.trees[file]):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0].startswith("kazma"):
                for alias in node.names:
                    as_module = self.modules.get(f"{node.module}.{alias.name}")
                    if as_module:
                        aliases[alias.asname or alias.name] = as_module
                    source = self.modules.get(node.module)
                    if source and alias.name in self.coroutines[source]:
                        names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname and alias.name in self.modules:
                        aliases[alias.asname] = self.modules[alias.name]
        return names, aliases


def unawaited(file: str, program: _Program) -> list[str]:
    """The coroutine calls in *file* whose result nobody runs."""
    tree = program.trees[file]
    names, aliases = program.resolver(file)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def enclosing(node: ast.AST, kinds: tuple[type, ...]) -> ast.AST | None:
        while node in parents:
            node = parents[node]
            if isinstance(node, kinds):
                return node
        return None

    def coroutine_call(call: ast.Call) -> str | None:
        fn = call.func
        if isinstance(fn, ast.Name) and fn.id in names:
            return fn.id
        if not isinstance(fn, ast.Attribute):
            return None
        if fn.attr in CLIENT_METHODS:
            return "." + fn.attr
        if isinstance(fn.value, ast.Name):
            if fn.value.id in aliases and fn.attr in program.coroutines[aliases[fn.value.id]]:
                return f"{fn.value.id}.{fn.attr}"
            if fn.value.id in ("self", "cls"):
                cls = enclosing(call, (ast.ClassDef,))
                if cls is not None and any(_plain_coroutine(n) and n.name == fn.attr for n in cls.body):
                    return f"{fn.value.id}.{fn.attr}"
        return None

    def takes_it(node: ast.AST) -> bool:
        """Whether *node*'s value is run or handed on by where it stands."""
        parent = parents.get(node)
        if isinstance(parent, (ast.Await, ast.Return, ast.Yield, ast.Lambda, ast.AsyncFor, ast.Starred,
                               ast.List, ast.Tuple, ast.Set, ast.Dict, ast.ListComp, ast.SetComp,
                               ast.GeneratorExp, ast.DictComp, ast.comprehension)):
            return True
        if isinstance(parent, ast.withitem):
            return isinstance(parents.get(parent), ast.AsyncWith)
        if isinstance(parent, ast.Call):
            return node is not parent.func and _callee(parent) not in NOT_CONSUMERS
        if isinstance(parent, ast.keyword):
            return _callee(parents.get(parent)) not in NOT_CONSUMERS
        return False

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        what = coroutine_call(node)
        if what is None or takes_it(node):
            continue
        parent = parents.get(node)
        if isinstance(parent, (ast.Assign, ast.AnnAssign)):
            target = parent.targets[0] if isinstance(parent, ast.Assign) else parent.target
            if isinstance(target, ast.Name):
                scope = enclosing(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or tree
                if any(isinstance(n, ast.Name) and n.id == target.id and isinstance(n.ctx, ast.Load)
                       and takes_it(n) for n in ast.walk(scope)):
                    continue
        found.append(f"{file}:{node.lineno} {what}: {ast.unparse(node)[:80]}")
    return found


def test_no_product_code_leaves_a_coroutine_unawaited() -> None:
    files = _product_files()
    program = _Program({f: (ROOT / f).read_text(encoding="utf-8") for f in files})
    problems = [p for f in files for p in unawaited(f, program)]
    assert not problems, (
        "A coroutine function is called and its result never run: await it, or hand it to "
        "what runs it (a task, a worker):\n  " + "\n  ".join(problems))


# ── controls ─────────────────────────────────────────────────────────────

_OLD_TIER3 = '''
def _tier3_llm_disambiguate(name, candidate_id, *, entity_type, distance, cfg):
    from kazma_core.model_registry import get_model_registry

    client = get_model_registry().get_client()
    if hasattr(client, "chat"):
        resp = client.chat(
            messages=[{"role": "user", "content": "MERGE or DISTINCT?"}],
            temperature=0.0,
            max_tokens=8,
        )
        text = ""
        if isinstance(resp, dict):
            text = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        else:
            text = str(resp or "")
    return "skip"
'''


def _scan(source: str, file: str = "kazma-core/kazma_core/memory/planted.py") -> list[str]:
    return unawaited(file, _Program({file: source}))


def test_control_the_removed_tier3_line_is_found() -> None:
    found = _scan(_OLD_TIER3)
    assert len(found) == 1 and ".chat" in found[0], found


def test_control_the_swarm_table_as_it_was_is_found() -> None:
    planted = '''
class TaskHistoryTable:
    def on_show(self) -> None:
        self._refresh()

    async def _refresh(self) -> None:
        return None
'''
    assert _scan(planted) == ["kazma-core/kazma_core/memory/planted.py:4 self._refresh: self._refresh()"]


def test_what_runs_or_hands_on_a_coroutine_passes() -> None:
    planted = '''
import asyncio
from contextlib import asynccontextmanager

async def work():
    return 1

async def stream():
    yield 1

@asynccontextmanager
async def scope():
    yield

async def caller(client, spawn):
    await work()
    task = asyncio.create_task(work())
    spawn(work())
    pending = work()
    await pending
    async for _ in stream():
        pass
    async with scope():
        pass
    async for _ in client.chat_stream([]):
        pass
    reply = await client.chat([])
    return await asyncio.gather(*[work() for _ in range(2)]), task, reply

def handed(loop):
    return loop.run_until_complete(work())
'''
    assert _scan(planted) == []


def test_control_a_discarded_or_inspected_coroutine_is_found() -> None:
    planted = '''
async def work():
    return 1

def careless():
    work()
    value = work()
    return isinstance(value, int)
'''
    found = _scan(planted)
    assert len(found) == 2 and all("work" in f for f in found), found
