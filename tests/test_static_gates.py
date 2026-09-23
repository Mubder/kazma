"""Static gates that would each have caught a finding in the 2026-08-29 audit.

These sit alongside ``tests/test_imports.py`` in the pre-commit hook: fast,
AST-only, no app boot. Each gate closes a *class* of defect rather than one
instance, because every one of them was found by grepping the tree rather than
by a failing test.

1. ``test_no_blocking_db_driver_in_async``  — audit F-06.
   A synchronous ``sqlite3.connect`` inside ``async def`` pins the event loop
   that also serves every SSE and WebSocket stream.

2. ``test_no_bare_create_task``             — audit F-07.
   ``asyncio`` holds only a weak reference to a task, so a discarded
   ``create_task(...)`` result can be garbage-collected mid-run.
   ``kazma_core.background.spawn_background`` retains it.

3. ``test_every_registered_tool_has_a_tier`` — audit F-04.
   HITL default-denies anything it cannot classify, so an untiered tool would
   start prompting for approval on a read. This keeps ``TOOL_TIERS``
   exhaustive.

4. ``test_no_unfenced_web_tool_output``      — audit F-09.
   Tools returning remote-authored text must fence it as untrusted.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

PRODUCT_DIRS = [
    REPO_ROOT / "kazma-core" / "kazma_core",
    REPO_ROOT / "kazma-ui" / "kazma_ui",
    REPO_ROOT / "kazma-gateway" / "kazma_gateway",
    REPO_ROOT / "kazma-cli" / "kazma_cli",
    REPO_ROOT / "kazma-tui" / "kazma_tui",
    REPO_ROOT / "kazma-skills" / "kazma_skills",
]


def _product_files() -> list[Path]:
    out: list[Path] = []
    for base in PRODUCT_DIRS:
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts or "_tests" in str(p) or "/tests/" in p.as_posix():
                continue
            out.append(p)
    return out


def _rel(p: Path) -> str:
    return p.relative_to(REPO_ROOT).as_posix()


def _dotted(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    if isinstance(func, ast.Name):
        return func.id
    return None


# ── 1. Blocking DB driver inside async def (F-06) ────────────────────────

#: Synchronous calls that pin the event loop for the duration of the work.
#:
#: This started as SQLite-only and that was the hole: the gate's own docstring
#: says a blocking call "stalls the event loop shared by every SSE/WebSocket
#: stream", but the rule only ever looked for ``sqlite3.connect``. Meanwhile
#: twenty ``subprocess.run`` calls sat inside ``async def`` agent tools with
#: timeouts up to ninety seconds — a far bigger stall than any SQLite query,
#: scanned by this very gate, and waved straight through (audit 2026-09-16
#: F-4). The rule is about *blocking the loop*, so it names every way we do it.
BLOCKING_CALLS = {
    "sqlite3.connect",
    "_sqlite3.connect",
    # Waits for a child process. Use kazma_skills.native._subprocess.run_off_loop
    # (skills) or `await asyncio.to_thread(subprocess.run, ...)` (core).
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    # Sync HTTP clients — use httpx.AsyncClient.
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.delete",
    "requests.request",
    # Sleeps the whole loop. Use `await asyncio.sleep`.
    "time.sleep",
    # Sync DB drivers beyond SQLite.
    "psycopg.connect",
    "psycopg2.connect",
    "pymysql.connect",
}
#: Sync helpers whose body opens SQLite; calling them from async def is the
#: same pin as an inline connect (audit M-14 memory_api ``_conn()``).
BLOCKING_HELPERS = {"_conn", "_connect_sqlite"}

#: ``(file, function)`` pairs that are deliberately exempt, each with a reason.
BLOCKING_ALLOWLIST: dict[tuple[str, str], str] = {
    (
        "kazma-core/kazma_core/memory/worker_bootstrap.py",
        "_handle_micro_consolidation",
    ): (
        "Interleaves an awaited LLM call with its SQLite work, so it cannot be "
        "offloaded wholesale. Its synchronous queries are a single indexed "
        "lookup by episode id."
    ),
}


def test_no_blocking_db_driver_in_async():
    """Synchronous DB drivers must not run on the event loop (audit F-06)."""
    offenders: list[str] = []
    for path in _product_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        stack: list[str | None] = []

        class Visitor(ast.NodeVisitor):
            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                stack.append(None)  # a sync def re-enters the threadpool
                self.generic_visit(node)
                stack.pop()

            def visit_Lambda(self, node: ast.Lambda) -> None:
                stack.append(None)
                self.generic_visit(node)
                stack.pop()

            def visit_Call(self, node: ast.Call) -> None:
                if stack and stack[-1] is not None:
                    name = _dotted(node.func)
                    if name in BLOCKING_CALLS or (
                        isinstance(node.func, ast.Name)
                        and node.func.id in BLOCKING_HELPERS
                    ):
                        key = (_rel(path), stack[-1])
                        if key not in BLOCKING_ALLOWLIST:
                            offenders.append(
                                f"{_rel(path)}:{node.lineno} async def "
                                f"{stack[-1]} calls {name or getattr(node.func, 'id', '?')}"
                            )
                self.generic_visit(node)

        Visitor().visit(tree)

    assert not offenders, (
        "Blocking DB call inside async def — this stalls the event loop shared "
        "by every SSE/WebSocket stream (audit F-06).\n"
        "Fix: drop `async` (FastAPI threadpools sync handlers), or wrap the "
        "blocking section in `await asyncio.to_thread(...)`.\n  "
        + "\n  ".join(offenders)
    )


# ── 2. Fire-and-forget asyncio tasks (F-07) ──────────────────────────────

def test_no_bare_create_task():
    """Background tasks must be retained via ``spawn_background`` (audit F-07)."""
    offenders: list[str] = []
    allowed_files = {
        "kazma-core/kazma_core/background.py",  # defines the helper
    }
    for path in _product_files():
        rel = _rel(path)
        if rel in allowed_files:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # A bare expression statement discards the task object, leaving the
            # loop's weak reference as the only one.
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            dotted = _dotted(node.value.func)
            # Discarded `loop.create_task(...)` is the same GC-able fire-and-
            # forget (audit M-4 / M-14 embedder rebuild). Assigned tasks
            # (`task = loop.create_task`) stay allowed.
            is_loop_create = (
                isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "create_task"
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == "loop"
            )
            if dotted in ("asyncio.create_task", "asyncio.ensure_future") or is_loop_create:
                offenders.append(f"{rel}:{node.lineno}")

    # create_task inside a Lambda is the same discard — the Call is not an
    # Expr statement, so the walk above misses
    # `call_soon_threadsafe(lambda: loop.create_task(...))` (audit 2026-09-17).
    for path in _product_files():
        rel = _rel(path)
        if rel in allowed_files:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Lambda):
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                dotted = _dotted(sub.func)
                is_loop_create = (
                    isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "create_task"
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "loop"
                )
                if dotted in ("asyncio.create_task", "asyncio.ensure_future") or is_loop_create:
                    offenders.append(f"{rel}:lambda:{sub.lineno}")

    assert not offenders, (
        "Fire-and-forget asyncio task: the event loop keeps only a weak "
        "reference, so this can be garbage-collected mid-run (audit F-07).\n"
        "Fix: `from kazma_core.background import spawn_background` and call "
        "`spawn_background(coro, name=...)`, or assign the task to a variable "
        "that outlives it.\n  " + "\n  ".join(offenders)
    )


def test_no_unretained_thread_start():
    """``threading.Thread(...).start()`` must keep a handle (audit 2026-09-16).

    The raw-thread twin of F-07 above, and it is worse than a GC'd task: a
    daemon thread nobody holds cannot be waited for, so it outlives whatever
    spawned it and keeps writing. ops_alerts._dispatch did exactly this --
    started an unretained daemon thread that ran asyncio.run(_deliver(...)),
    did network I/O, and logged warnings from inside itself. On Linux it
    SEGFAULTED CPython by writing to stderr while pytest tore down its fd
    capture: tests/test_reply_sink.py exited 139, reproducibly, from the one
    test that reaches an alert.

    Chaining `.start()` straight onto the constructor is the tell -- the
    Thread object is discarded on the same line. Assign it, keep it
    somewhere, and give callers a way to join.
    """
    offenders: list[str] = []
    for path in _product_files():
        rel = _rel(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            # Match `<something>(...).start()` where the receiver is a call to
            # Thread / threading.Thread / Timer / threading.Timer.
            if not (isinstance(func, ast.Attribute) and func.attr == "start"):
                continue
            recv = func.value
            if not isinstance(recv, ast.Call):
                continue
            ctor = _dotted(recv.func)
            if ctor in ("threading.Thread", "Thread", "threading.Timer", "Timer"):
                offenders.append(f"{rel}:{node.lineno} {ctor}(...).start()")

    assert not offenders, (
        "Unretained background thread: the Thread object is discarded on the "
        "same line it is started, so nothing can join or stop it. A daemon "
        "thread that outlives its owner and still writes to a descriptor "
        "segfaults CPython (audit 2026-09-16, ops_alerts).\n"
        "Fix: assign the thread, keep it in a module-level registry, and "
        "expose a drain/join -- see ops_alerts.drain_alerts().\n  "
        + "\n  ".join(offenders)
    )


# ── 2b. Deferred closures reading loop-rebound names (audit 2026-09-22) ──
#
# The Discord gateway defined its per-channel chain closure inside the
# ``async for`` receive loop and handed it to ``create_task``. The closure read
# the previous task and the parsed message only when it first ran, and
# ``websockets`` returns buffered frames without suspending — so a burst let the
# loop rebind both names first. A burst of four was delivered as the LAST
# message four times: three lost, one run four times. Slack and Telegram had
# the correct shape; nothing compared the siblings.
#
# The rule: a closure created in a loop and scheduled to run later (task,
# callback, thread, executor) must not read a name the loop rebinds. Bind it
# as a default argument, or pass it to a method. A closure that is awaited in
# the same iteration is fine — the loop cannot advance while it waits.

_SCHEDULERS = {
    "create_task", "ensure_future", "spawn_background", "add_done_callback",
    "call_soon", "call_soon_threadsafe", "call_later", "call_at",
    "run_in_executor", "submit", "run_coroutine_threadsafe", "start_soon",
    "Thread", "Timer",
}
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_LOOPS = (ast.For, ast.AsyncFor, ast.While)


def _own_nodes(stmts: list[ast.stmt]):
    """Walk statements without entering nested function or class bodies."""
    stack: list[ast.AST] = list(stmts)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _SCOPES):
                yield child  # the definition itself, not its body
            else:
                stack.append(child)


def _loop_rebound(loop: ast.For | ast.AsyncFor | ast.While) -> set[str]:
    """Names each iteration of *loop* binds afresh."""
    names: set[str] = set()
    if isinstance(loop, (ast.For, ast.AsyncFor)):
        names |= {n.id for n in ast.walk(loop.target) if isinstance(n, ast.Name)}
    for n in _own_nodes(loop.body):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        # An import inside a loop rebinds the same module object: harmless.
    return names


def _free_reads(fn: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> set[str]:
    """Names *fn*'s body reads from an enclosing scope (defaults excluded)."""
    body = [fn.body] if isinstance(fn, ast.Lambda) else fn.body
    params: set[str] = set()
    local: set[str] = set()
    reads: set[str] = set()
    for sub in [fn, *(n for stmt in body for n in ast.walk(stmt))]:
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = sub.args
            params |= {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
            params |= {x.arg for x in (a.vararg, a.kwarg) if x is not None}
            if sub is not fn and not isinstance(sub, ast.Lambda):
                local.add(sub.name)
        elif isinstance(sub, ast.Name):
            (reads if isinstance(sub.ctx, ast.Load) else local).add(sub.id)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            local.add(sub.name)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            local |= {(a.asname or a.name).split(".")[0] for a in sub.names}
        elif isinstance(sub, ast.ClassDef):
            local.add(sub.name)
    return reads - params - local


def _deferred_loop_closures(tree: ast.AST) -> list[tuple[int, str, list[str]]]:
    """``(line, closure name, [late-read names])`` for every offending closure."""
    found: dict[tuple[int, str], set[str]] = {}
    for loop in ast.walk(tree):
        if not isinstance(loop, _LOOPS):
            continue
        rebound = _loop_rebound(loop)
        defs = {
            n.name: n
            for n in _own_nodes(loop.body)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        awaited = {id(n.value) for n in _own_nodes(loop.body) if isinstance(n, ast.Await)}
        for call in _own_nodes(loop.body):
            if not isinstance(call, ast.Call) or id(call) in awaited:
                continue
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in _SCHEDULERS:
                continue
            for arg in (*call.args, *(k.value for k in call.keywords)):
                closure = None
                if isinstance(arg, ast.Lambda):
                    closure = arg
                elif isinstance(arg, ast.Name) and arg.id in defs:
                    closure = defs[arg.id]
                elif (
                    isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Name)
                    and arg.func.id in defs
                ):
                    closure = defs[arg.func.id]
                if closure is None:
                    continue
                late = _free_reads(closure) & rebound
                if late:
                    key = (closure.lineno, getattr(closure, "name", "<lambda>"))
                    found.setdefault(key, set()).update(late)
    return sorted((line, label, sorted(names)) for (line, label), names in found.items())


def test_no_deferred_closure_reads_loop_rebound_names():
    """A closure scheduled from a loop must bind the loop's names (2026-09-22)."""
    offenders: list[str] = []
    for path in _product_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for line, label, names in _deferred_loop_closures(tree):
            offenders.append(f"{_rel(path)}:{line} {label} reads {', '.join(names)}")

    assert not offenders, (
        "A closure defined in a loop is scheduled to run later but reads names "
        "the loop rebinds. By the time it runs, the next iteration may have "
        "replaced them — the Discord gateway delivered a burst of four "
        "messages as the last one four times (audit 2026-09-22).\n"
        "Fix: bind each name as a default (`def f(x=x)`, `lambda t, x=x: ...`) "
        "or pass it to a method, as Slack's `_chain_channel_work` does.\n  "
        + "\n  ".join(offenders)
    )


def test_loop_closure_gate_catches_the_discord_shape():
    """Negative control (§28): the gate flags the pre-fix Discord code."""
    source = '''
async def listen(ws, chains, queue):
    async for raw in ws:
        parsed = parse(raw)
        _prev = chains.get("c")

        async def _chained():
            if _prev is not None:
                await _prev
            await enqueue(parsed)

        chains["c"] = loop.create_task(_chained())
        task.add_done_callback(lambda t: unregister(parsed, t))
'''
    hits = _deferred_loop_closures(ast.parse(source))
    assert [(label, names) for _, label, names in hits] == [
        ("_chained", ["_prev", "parsed"]),
        ("<lambda>", ["parsed"]),
    ]


def test_loop_closure_gate_passes_correct_shapes():
    """The gate must not block the shapes it tells people to use."""
    source = '''
async def listen(ws, chains, loop):
    async for raw in ws:
        parsed = parse(raw)
        _prev = chains.get("c")

        async def _bound(_prev=_prev, parsed=parsed):
            await enqueue(parsed)

        chains["c"] = loop.create_task(_bound())
        chains["c"].add_done_callback(lambda t, p=parsed: unregister(p, t))
        self._chain_channel_work("c", self._process(parsed))
        # Awaited in the same iteration: the loop cannot advance meanwhile.
        await loop.run_in_executor(None, lambda: handle(parsed))
'''
    assert _deferred_loop_closures(ast.parse(source)) == []


# ── 2c. The environment is set by entry points, not imports (2026-09-22) ──
#
# ``cost_breaker`` ran a bare ``load_dotenv()`` at import time. Because
# ``kazma_core/__init__`` imports it, every process that imported kazma_core
# loaded a ``.env`` found by walking up from the package or the working
# directory — including the document sandbox, which had just scrubbed its
# environment before parsing an untrusted file (KAZMA_VAULT_KEY came back).
# The root conftest no-ops ``dotenv.load_dotenv``, so no test could see it.
# ``tests/test_env_loading.py`` holds the behavioural half of this gate.

_ENV_LOADER = "kazma-core/kazma_core/env_files.py"

#: Import-time ``os.environ`` writes that are deliberate: ``(file, key)``.
_IMPORT_TIME_ENV_WRITES: dict[tuple[str, str], str] = {
    ("kazma-core/kazma_core/__init__.py", "GIT_TERMINAL_PROMPT"): (
        "git must fail instead of prompting for credentials in any process"
    ),
    ("kazma-core/kazma_core/__init__.py", "GIT_ASKPASS"): "same as GIT_TERMINAL_PROMPT",
    (
        "kazma-skills/kazma_skills/native/git_github_manager/tools.py",
        "GIT_ASKPASS",
    ): "same as GIT_TERMINAL_PROMPT, for the skill loaded without kazma_core",
    (
        "kazma-skills/kazma_skills/native/git_github_manager/tools.py",
        "GIT_TERMINAL_PROMPT",
    ): "same as GIT_TERMINAL_PROMPT, for the skill loaded without kazma_core",
    # Entry-point modules turning Hugging Face telemetry and a warning OFF
    # before any model library is imported. They remove behaviour; they add
    # nothing a sandbox would need to scrub.
    ("kazma-ui/kazma_ui/app.py", "HF_HUB_DISABLE_TELEMETRY"): "privacy: no hub telemetry",
    ("kazma-ui/kazma_ui/app.py", "HF_HUB_DISABLE_SYMLINKS_WARNING"): "Windows log noise",
    ("kazma-cli/kazma_cli/main.py", "HF_HUB_DISABLE_TELEMETRY"): "privacy: no hub telemetry",
    ("kazma-cli/kazma_cli/main.py", "HF_HUB_DISABLE_SYMLINKS_WARNING"): "Windows log noise",
}


def _load_dotenv_calls(tree: ast.AST) -> list[int]:
    return [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "load_dotenv")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "load_dotenv")
        )
    ]


def _is_os_environ(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _import_time_env_writes(tree: ast.Module) -> list[tuple[int, str]]:
    """``(line, key or '?')`` for environment writes that run on import.

    Import time is the module body and class bodies, but not function bodies
    and not an ``if __name__ == "__main__":`` block, which only runs as a
    script.
    """
    out: list[tuple[int, str]] = []

    def is_main_guard(node: ast.stmt) -> bool:
        return (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        )

    def key_of(sub: ast.expr) -> str:
        return sub.value if isinstance(sub, ast.Constant) and isinstance(sub.value, str) else "?"

    stack: list[ast.AST] = [s for s in tree.body if not is_main_guard(s)]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for t in targets:
                if isinstance(t, ast.Subscript) and _is_os_environ(t.value):
                    out.append((node.lineno, key_of(t.slice)))
        elif isinstance(node, ast.Call):
            f = node.func
            if (
                isinstance(f, ast.Attribute)
                and _is_os_environ(f.value)
                and f.attr in {"update", "setdefault", "pop", "clear", "popitem", "__setitem__"}
            ):
                out.append((node.lineno, key_of(node.args[0]) if node.args else "?"))
            elif _dotted(f) in {"os.putenv", "os.unsetenv"}:
                out.append((node.lineno, key_of(node.args[0]) if node.args else "?"))
            elif (isinstance(f, ast.Name) and f.id == "load_dotenv") or (
                isinstance(f, ast.Attribute) and f.attr == "load_dotenv"
            ):
                out.append((node.lineno, "<load_dotenv>"))
        stack.extend(ast.iter_child_nodes(node))
    return out


def test_load_dotenv_lives_only_in_the_env_loader():
    """One ``.env`` loader: every other module calls ``load_env_files()``."""
    offenders: list[str] = []
    for path in [*_product_files(), REPO_ROOT / "serve.py"]:
        rel = _rel(path)
        if rel == _ENV_LOADER:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        offenders += [f"{rel}:{line}" for line in _load_dotenv_calls(tree)]
    assert not offenders, (
        "load_dotenv() outside kazma_core/env_files.py. Its default search walks "
        "up from the package or the working directory and loaded the "
        "installation's secrets into the document sandbox (audit 2026-09-22).\n"
        "Fix: call `kazma_core.env_files.load_env_files()` from the entry "
        "point instead.\n  " + "\n  ".join(offenders)
    )


def test_no_import_time_environment_writes():
    """Importing a module must not change ``os.environ`` (audit 2026-09-22)."""
    offenders: list[str] = []
    for path in _product_files():
        rel = _rel(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for line, key in _import_time_env_writes(tree):
            if (rel, key) not in _IMPORT_TIME_ENV_WRITES:
                offenders.append(f"{rel}:{line} {key}")
    assert not offenders, (
        "Importing this module changes os.environ for the whole process — "
        "including sandboxed workers that scrubbed it on purpose.\n"
        "Fix: do it in a function the entry point calls, or add it to "
        "_IMPORT_TIME_ENV_WRITES with the reason it must happen on import.\n  "
        + "\n  ".join(offenders)
    )


def test_env_gates_catch_the_cost_breaker_shape():
    """Negative control (§28): both gates flag the pre-fix cost_breaker code."""
    source = '''
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
os.environ["KAZMA_X"] = "1"
os.environ.setdefault("KAZMA_Y", "2")

class Config:
    os.environ.update({"KAZMA_Z": "3"})

def fine():
    os.environ["INSIDE_A_FUNCTION"] = "ok"

if __name__ == "__main__":
    os.environ["SCRIPT_ONLY"] = "ok"
'''
    tree = ast.parse(source)
    assert _load_dotenv_calls(tree) == [5]
    assert sorted(k for _, k in _import_time_env_writes(tree)) == [
        "<load_dotenv>",
        "?",
        "KAZMA_X",
        "KAZMA_Y",
    ]
    assert _import_time_env_writes(ast.parse("import os\nos.putenv('K', '1')\n")) == [(2, "K")]


# ── 2d. "Is this caller an admin?" has one answer (2026-09-22) ─────────────
#
# The decision was copied into six route modules and the copies drifted: three
# denied when the check raised and three ALLOWED, backup download among them.
# kazma_ui.auth.admin_decision is the one copy and it fails closed; a route
# keeps its own response shape by wrapping it, never by re-deriving it.

_ADMIN_DECISION_HOME = "kazma-ui/kazma_ui/auth.py"
_ADMIN_REDERIVED = re.compile(
    r"""get\(\s*["']role["']\s*\)\s*(==|!=)\s*["']admin["']"""
    r"""|get\(\s*["']source["']\s*\)\s*==\s*["']secret["']"""
)


def _admin_rederivations(text: str) -> list[int]:
    return [i for i, line in enumerate(text.splitlines(), 1) if _ADMIN_REDERIVED.search(line)]


def test_the_admin_decision_lives_in_one_place():
    offenders: list[str] = []
    for path in _product_files():
        rel = _rel(path)
        if rel == _ADMIN_DECISION_HOME:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        offenders += [f"{rel}:{line}" for line in _admin_rederivations(text)]
    assert not offenders, (
        "The admin decision is re-derived here. Six copies of it drifted until "
        "half of them allowed access when the check failed (audit 2026-09-22).\n"
        "Fix: `from kazma_ui.auth import admin_decision` (or require_admin) "
        "and map its result to this route's response shape.\n  "
        + "\n  ".join(offenders)
    )


def test_admin_rederivation_gate_catches_the_drifted_copy():
    """Negative control (§28): the pre-fix backup gate, and the fix passes."""
    drifted = '''
        principal = get_request_principal(request) or {}
        if principal.get("source") == "secret":
            return None
        if principal.get("role") != "admin":
            return _JSONResponse({"error": "Admin role required"}, status_code=403)
'''
    fixed = '''
        decision = admin_decision(request)
        if decision == "ok":
            return None
'''
    assert _admin_rederivations(drifted) == [3, 5]
    assert _admin_rederivations(fixed) == []


# ── 2e. A write the server refused is not reported as saved (2026-09-22) ──
#
# 24 save/delete handlers in the web UI did `await fetch(...)`, threw the
# Response away and toasted success — a 403, 422 or 500 still said "saved",
# Settings → Safety (the HITL policy) among them. Seven more had no error path
# at all. The rule: never discard an awaited fetch. Use
# `window.kazmaSave(url, init)` (auth-guard.js), which rejects with the
# server's reason, or keep the Response and check `resp.ok`.
# tests/js/test_kazma_save.js holds the behavioural half.

_UI_SCRIPT_ROOTS = (
    REPO_ROOT / "kazma-ui" / "kazma_ui" / "static" / "js",
    REPO_ROOT / "kazma-ui" / "kazma_ui" / "templates",
)
_DISCARDED_FETCH = re.compile(r"^\s*await\s+fetch\s*\(")


def _discarded_fetches(text: str) -> list[int]:
    return [i for i, line in enumerate(text.splitlines(), 1) if _DISCARDED_FETCH.match(line)]


def test_no_discarded_fetch_results_in_the_ui():
    offenders: list[str] = []
    for root in _UI_SCRIPT_ROOTS:
        for path in sorted([*root.rglob("*.js"), *root.rglob("*.html")]):
            if path.name.endswith(".min.js") or "vendor" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            offenders += [f"{_rel(path)}:{line}" for line in _discarded_fetches(text)]
    assert not offenders, (
        "An awaited fetch() whose Response is thrown away cannot tell a refused "
        "write from a saved one (audit 2026-09-22).\n"
        "Fix: `await window.kazmaSave(url, init)` inside try/catch, or keep the "
        "Response and check `resp.ok`.\n  " + "\n  ".join(offenders)
    )


def test_discarded_fetch_gate_catches_the_audited_shape():
    """Negative control (§28): flags the pre-fix saveSafety, passes the fix."""
    js = """
        async saveSafety() {
            try {
                await fetch('/api/settings/agent/safety', { method: 'PUT' });
                showToast('Safety settings saved', 'success');
            } catch (e) {}
        },
        async fixed() {
            try {
                await window.kazmaSave('/api/settings/agent/safety', { method: 'PUT' });
                const resp = await fetch('/api/x');
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
            } catch (e) {}
        },
        async ping() {
            await fetch('/api/push/subscribe', {}).catch(function () {});
        },
    """
    assert _discarded_fetches(js) == [4, 16]


# ── 2f. Work sent to a thread keeps the caller's context (2026-09-22) ─────
#
# ``loop.run_in_executor`` does not copy ContextVars; ``asyncio.to_thread``
# does. The tenant, per-task workspace scope, thread id and HITL flags are all
# ContextVars, so a sync tool run through the executor read no tenant and the
# global workspace (tests/test_sync_tool_context.py). Every call is now
# to_thread; a custom executor drops the context just the same, so the whole
# API is off-limits in product code.


def _context_dropping_executor_calls(tree: ast.AST) -> list[int]:
    return [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "run_in_executor"
    ]


def test_no_context_dropping_executor_calls():
    offenders: list[str] = []
    for path in _product_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        offenders += [f"{_rel(path)}:{line}" for line in _context_dropping_executor_calls(tree)]
    assert not offenders, (
        "run_in_executor does not carry ContextVars into the thread — the "
        "tenant, workspace scope and HITL flags are lost (audit 2026-09-22).\n"
        "Fix: `await asyncio.to_thread(fn, *args)`.\n  " + "\n  ".join(offenders)
    )


def test_executor_gate_catches_the_tool_registry_shape():
    """Negative control (§28): the pre-fix sync-tool path, and the fix passes."""
    bad = "async def f(tool, args):\n    loop = asyncio.get_running_loop()\n    return await loop.run_in_executor(None, lambda: tool(**args))\n"
    good = "async def f(tool, args):\n    return await asyncio.to_thread(tool, **args)\n"
    assert _context_dropping_executor_calls(ast.parse(bad)) == [3]
    assert _context_dropping_executor_calls(ast.parse(good)) == []


# ── 2f'. No DNS resolution on the event loop (2026-09-23) ─────────────────
#
# ``validate_url`` resolves the host (``socket.getaddrinfo``) — milliseconds
# normally, the resolver timeout when a name does not answer. 23 async
# functions called it inline (read_url, web research, KB crawl, per-hop
# redirect checks, model discovery, provider tests), each stalling every SSE
# and WebSocket stream while it waited (AGENTS.md §26E). The fix keeps the
# name — ``await asyncio.to_thread(validate_url, url, ...)`` — so the tests
# that patch it still patch it.

_BLOCKING_DNS_CALLS = frozenset({"validate_url", "getaddrinfo", "gethostbyname", "gethostbyname_ex"})


def _blocking_dns_in_async(tree: ast.AST) -> list[int]:
    lines: list[int] = []

    def visit(node: ast.AST, in_async: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef):
                visit(child, True)
            elif isinstance(child, (ast.FunctionDef, ast.Lambda)):
                visit(child, False)  # a sync helper is what to_thread runs
            else:
                if in_async and isinstance(child, ast.Call):
                    fn = child.func
                    name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                    if name in _BLOCKING_DNS_CALLS:
                        lines.append(child.lineno)
                visit(child, in_async)

    visit(tree, False)
    return lines


def test_no_blocking_dns_in_async_functions():
    offenders: list[str] = []
    for path in _product_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        offenders += [f"{_rel(path)}:{line}" for line in _blocking_dns_in_async(tree)]
    assert not offenders, (
        "DNS resolution called directly inside an async function blocks the "
        "event loop for every chat stream until the resolver answers.\n"
        "Fix: `await asyncio.to_thread(validate_url, url, block_unresolved=True)`.\n  "
        + "\n  ".join(offenders)
    )


def test_dns_gate_catches_the_redirect_hop_shape():
    """Negative control (§28): the pre-fix redirect loop, and the fix passes."""
    bad = (
        "async def hops(client, url):\n"
        "    validate_url(url, block_unresolved=True)\n"
        "    ips = socket.getaddrinfo(url, None)\n"
        "    def later():\n"
        "        return validate_url(url)\n"
        "    return later\n"
    )
    good = (
        "async def hops(client, url):\n"
        "    await asyncio.to_thread(validate_url, url, block_unresolved=True)\n"
        "def sync_path(url):\n"
        "    validate_url(url)\n"
    )
    assert _blocking_dns_in_async(ast.parse(bad)) == [2, 3]
    assert _blocking_dns_in_async(ast.parse(good)) == []


# ── 2f'''. Helpers the loop-stall dumps caught stay off the loop (2026-09-23)
#
# The live install wrote 70 loop-stall dumps between 2026-08-30 and 09-23, one
# of which ended in a health-gated restart. None of the frames was a direct
# sqlite3.connect or DNS call in an async body -- the gates above cover those.
# Each was a synchronous helper that does I/O (a Postgres ConfigStore read, a
# SQLite write, a backup) called from async code: the HITL watchdog tick, the
# X scheduler and mentions poller, the per-request session lookup, queue
# handlers. The same helpers were then found at 17 more async call sites.
#
# This list is the helpers a dump has proven slow. It grows when a new dump
# names one; a call in an async body must go through asyncio.to_thread.

_LOOP_STALL_HELPERS = frozenset({
    "get_hitl_config", "record_watcher", "get_reply_config", "get_x_config",
    "list_due", "perform_native_backups", "perform_document_backup",
    "export_nightly_snapshots", "run_gc_cycle", "gc_sweep", "log_x_event",
    "get_session_payload", "run_weekly_sweep", "build_report", "scan_log",
    "mirror_drift_summary", "purge_completed_tasks", "expire_due_gates",
    "check_database", "perform_universal_backup",
})


def _loop_stall_helper_calls(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST, in_async: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef):
                visit(child, True)
            elif isinstance(child, (ast.FunctionDef, ast.Lambda)):
                visit(child, False)  # what to_thread runs
            else:
                if in_async and isinstance(child, ast.Call):
                    fn = child.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                    if name in _LOOP_STALL_HELPERS:
                        found.append((child.lineno, name))
                visit(child, in_async)

    visit(tree, False)
    return found


def test_loop_stall_helpers_are_not_called_on_the_loop():
    offenders: list[str] = []
    for path in _product_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        offenders += [f"{_rel(path)}:{line} {name}()" for line, name in _loop_stall_helper_calls(tree)]
    assert not offenders, (
        "A helper a loop-stall dump caught blocking the event loop is called "
        "directly in async code. Every SSE/WebSocket stream and the guard's "
        "health probe wait on it.\nFix: `await asyncio.to_thread(helper, ...)`.\n  "
        + "\n  ".join(offenders)
    )


def test_loop_stall_gate_catches_the_watchdog_shape():
    """Negative control (§28): the pre-fix HITL watchdog tick."""
    bad = (
        "async def _watchdog_loop():\n"
        "    cfg = get_hitl_config()\n"
        "    record_watcher(kind='ui')\n"
        "    def later():\n"
        "        return get_hitl_config()\n"
    )
    good = (
        "async def _watchdog_loop():\n"
        "    cfg = await asyncio.to_thread(get_hitl_config)\n"
        "    await asyncio.to_thread(record_watcher, kind='ui')\n"
    )
    assert _loop_stall_helper_calls(ast.parse(bad)) == [(2, "get_hitl_config"), (3, "record_watcher")]
    assert _loop_stall_helper_calls(ast.parse(good)) == []


# ── 2f''. One statement drops episode text, and it keeps a stub (2026-09-23)
#
# Archival nulls an episode's raw text. The one statement that did it used
# COALESCE(summary_text, ...) — and ordinary chat turns are written with
# summary_text = '' (not NULL), so every archived chat turn kept nothing at
# all. The fixed statement is memory/macro_sleep.py:_ARCHIVE_EPISODE_SQL; a
# second path that nulls episode text would have to re-solve the stub, and
# the first one got it wrong.

_EPISODE_TEXT_NULLED = re.compile(r"\b(?:user_text|assistant_text)\s*=\s*NULL\b", re.IGNORECASE)
_ARCHIVE_STATEMENT_HOME = "kazma-core/kazma_core/memory/macro_sleep.py"


def _episode_text_drops(text: str, *, allowed_name: str | None) -> list[int]:
    allowed: set[int] = set()
    if allowed_name:
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == allowed_name for t in node.targets
            ):
                allowed.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return [
        i for i, line in enumerate(text.splitlines(), 1)
        if _EPISODE_TEXT_NULLED.search(line) and i not in allowed
    ]


def test_only_the_archive_statement_drops_episode_text():
    offenders: list[str] = []
    for path in _product_files():
        rel = _rel(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _EPISODE_TEXT_NULLED.search(text):
            continue
        home = rel.replace("\\", "/") == _ARCHIVE_STATEMENT_HOME
        offenders += [
            f"{rel}:{line}"
            for line in _episode_text_drops(text, allowed_name="_ARCHIVE_EPISODE_SQL" if home else None)
        ]
    assert not offenders, (
        "Episode text is nulled outside macro_sleep._ARCHIVE_EPISODE_SQL. Archive "
        "through that statement: it keeps a stub, and the first hand-written "
        "version kept nothing (summary_text '' is not NULL).\n  " + "\n  ".join(offenders)
    )


def test_episode_text_gate_catches_a_second_archive_path():
    """Negative control (§28): the pre-fix statement outside the constant."""
    bad = (
        "_ARCHIVE_EPISODE_SQL = (\n"
        "    \"UPDATE episodes SET user_text=NULL, assistant_text=NULL WHERE id=?\"\n"
        ")\n"
        "SQL = \"UPDATE episodes SET tier='archived', user_text = NULL WHERE id=?\"\n"
    )
    assert _episode_text_drops(bad, allowed_name="_ARCHIVE_EPISODE_SQL") == [4]
    assert _episode_text_drops(bad, allowed_name=None) == [2, 4]


# ── 2g. Voice never hands the conversation to a Realtime/Live API ─────────
#
# OpenAI Realtime and Gemini Live run their own tool loop; Kazma cannot keep
# them codec-only without bypassing HITL, turn_failed and the commitment
# layer. voice/realtime_codec.py described that policy behind a
# KAZMA_REALTIME_CODEC flag nothing read (removed 2026-09-23). The policy is
# unconditional, so it is enforced here instead of in a flag.

_REALTIME_API_MARKERS = re.compile(
    r"v1/realtime|gpt-4o[\w.-]*realtime|BidiGenerateContent|live\.connect\(|aio\.live\b",
    re.IGNORECASE,
)


def _realtime_api_uses(text: str) -> list[int]:
    return [i for i, line in enumerate(text.splitlines(), 1) if _REALTIME_API_MARKERS.search(line)]


def test_no_realtime_or_live_conversation_apis():
    offenders: list[str] = []
    for path in _product_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        offenders += [f"{_rel(path)}:{line}" for line in _realtime_api_uses(text)]
    assert not offenders, (
        "A Realtime/Live API owns its own tool loop and would bypass HITL. "
        "Voice uses REST STT/TTS only (voice/mode.py).\n  " + "\n  ".join(offenders)
    )


# ── 2h. Untrusted XML goes through the guarded parser (2026-09-22) ────────
#
# The stdlib parsers accept a DTD, and a DTD is where entity expansion and
# external entities live. kazma_core.security.safe_xml refuses one before
# parsing; everything else must call it rather than ElementTree directly.

_SAFE_XML_HOME = "kazma-core/kazma_core/security/safe_xml.py"
_XML_MODULES = {"ET", "ElementTree", "cElementTree", "etree", "minidom", "expat", "sax", "pulldom"}
_XML_PARSE_CALLS = {"fromstring", "parse", "parseString", "iterparse", "XMLParser", "XML", "make_parser", "ParserCreate"}


def _raw_xml_parses(tree: ast.AST) -> list[int]:
    return [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in _XML_PARSE_CALLS
        and isinstance(n.func.value, (ast.Name, ast.Attribute))
        and (getattr(n.func.value, "id", None) or getattr(n.func.value, "attr", None)) in _XML_MODULES
    ]


def test_untrusted_xml_uses_the_guarded_parser():
    offenders: list[str] = []
    for path in _product_files():
        if _rel(path) == _SAFE_XML_HOME:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        offenders += [f"{_rel(path)}:{line}" for line in _raw_xml_parses(tree)]
    assert not offenders, (
        "Raw stdlib XML parsing accepts a DTD (entity expansion, external "
        "entities). Use kazma_core.security.safe_xml.parse_untrusted_xml.\n  "
        + "\n  ".join(offenders)
    )


def test_xml_gate_catches_the_sitemap_shape():
    """Negative control (§28): the pre-fix sitemap parse, and the fix passes."""
    bad = "import xml.etree.ElementTree as ET\nroot = ET.fromstring(text)\ndoc = minidom.parseString(text)\n"
    good = "root = parse_untrusted_xml(text)\n"
    assert _raw_xml_parses(ast.parse(bad)) == [2, 3]
    assert _raw_xml_parses(ast.parse(good)) == []


# ── 2i. No Python between fork and exec (2026-09-22) ──────────────────────
#
# preexec_fn runs in the child after fork(); in a threaded process a lock held
# by another thread at the fork can deadlock it before exec. The server is
# always threaded. Limits go through kazma_core.security.rlimits instead.


def _preexec_uses(tree: ast.AST) -> list[int]:
    lines = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and any(k.arg == "preexec_fn" for k in n.keywords)
    ]
    lines += [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value == "preexec_fn"
    ]
    return sorted(lines)


def test_no_preexec_fn():
    offenders: list[str] = []
    for path in _product_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        offenders += [f"{_rel(path)}:{line}" for line in _preexec_uses(tree)]
    assert not offenders, (
        "preexec_fn runs Python between fork and exec and can deadlock the "
        "child in a threaded server. Use "
        "kazma_core.security.rlimits.limited_command.\n  " + "\n  ".join(offenders)
    )


def test_preexec_gate_catches_both_spellings():
    """Negative control (§28): the sandbox keyword and the code_exec dict key."""
    bad = (
        "p = subprocess.Popen(cmd, preexec_fn=apply_limits)\n"
        'kwargs["preexec_fn"] = _set_limits\n'
    )
    assert _preexec_uses(ast.parse(bad)) == [1, 2]
    assert _preexec_uses(ast.parse("p = subprocess.Popen(limited_command(cmd))\n")) == []


def test_realtime_gate_catches_a_live_session():
    """Negative control (§28)."""
    assert _realtime_api_uses(
        'url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"\n'
        "session = await client.aio.live.connect(model=m)\n"
        "text = await transcribe(audio)\n"
    ) == [1, 2]


# ── 3. Exhaustive HITL tool tiers (F-04) ─────────────────────────────────

def test_every_registered_tool_has_a_tier():
    """Every registered tool must be classified in ``TOOL_TIERS`` (audit F-04)."""
    pytest.importorskip("kazma_core.agent.tool_builtins")
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.safety.hitl import TOOL_TIERS

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    untiered = sorted(set(registry._tools) - set(TOOL_TIERS))

    assert not untiered, (
        "Tool registered with no entry in kazma_core.safety.hitl.TOOL_TIERS. "
        "HITL default-denies unclassified tools (audit F-04), so this would "
        "prompt for approval on every call.\n"
        "Fix: add each to TOOL_TIERS as 'read' / 'write' / 'danger'; anything "
        "destructive, outbound, or credential-touching is 'danger' and also "
        "belongs in CANONICAL_DANGER_TOOLS.\n  " + ", ".join(untiered)
    )


def test_danger_tools_are_gated():
    """Every 'danger' tier tool must actually require approval (audit F-04)."""
    from kazma_core.safety.hitl import TOOL_TIERS, get_hitl_config, requires_approval

    cfg = get_hitl_config({})
    ungated = sorted(
        name
        for name, tier in TOOL_TIERS.items()
        if tier == "danger" and not requires_approval(name, cfg)
    )
    assert not ungated, (
        "Tools tiered 'danger' that do not require HITL approval:\n  "
        + ", ".join(ungated)
    )


# ── 4. Untrusted tool output must be fenced (F-09) ───────────────────────

FENCED_TOOL_MODULES = [
    "kazma-core/kazma_core/tools/read_url.py",
    "kazma-core/kazma_core/tools/web_search.py",
]


@pytest.mark.parametrize("rel", FENCED_TOOL_MODULES)
def test_no_unfenced_web_tool_output(rel):
    """Tools returning remote-authored text must fence it (audit F-09)."""
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "fence_untrusted" in src or "format_untrusted_block" in src, (
        f"{rel} returns fetched web content but never wraps it in an untrusted "
        "fence. A fetched page is the largest source of attacker-controlled "
        "text in the system (audit F-09).\n"
        "Fix: `from kazma_core.safety.prompt_fence import fence_untrusted` and "
        "return `fence_untrusted(text, source=...)`."
    )


# ── 4b. Per-FUNCTION fencing (audit 2026-09-16 F-2) ──────────────────────
#
# The module-level check above is necessary and not sufficient: it passes as
# soon as the string `fence_untrusted` appears ANYWHERE in the file. read_url.py
# is 1,500 lines with eleven public tools; one of them fenced, and that was
# enough to make the gate green while `digest_research_file`,
# `summarize_research_file` and `list_research_chunks` returned verbatim
# remote-authored text — and the tool descriptions steer the model to the
# digest, so the *recommended* research path was the unfenced one.
#
# This gate is per-function and closed by default: every public coroutine in a
# listed module must be classified, so a new reader cannot be added without a
# deliberate decision about its provenance.

#: module → {function: must_fence}. False means "this function returns only
#: our own text" and needs a reason in the comment beside it.
FENCED_TOOL_FUNCTIONS: dict[str, dict[str, bool]] = {
    "kazma-core/kazma_core/mcp/spec_tools.py": {
        # Body already fenced in AsyncMCPManager.read_resource (fence_resource).
        "mcp_read_resource": False,
        "mcp_get_prompt": True,
        "mcp_list_prompts": True,
        # Server-supplied names/URIs are the same untrusted channel as prompt
        # descriptions — an MCP server can put a payload in a resource name.
        "mcp_list_resources": True,
    },
    "kazma-skills/kazma_skills/native/calendar/tools.py": {
        "list_events": True,
        "create_event": False,
        "update_event": False,
        "delete_event": False,
        "find_free_slots": False,
    },
    "kazma-skills/kazma_skills/native/git_github_manager/tools.py": {
        "git_status": False,
        "git_commit": False,
        "git_push": False,
        "git_pull": False,
        "git_push_pull": False,
        "git_checkout": False,
        "git_merge": False,
        "github_create_pr": False,
        "github_merge_pr": False,
        "github_create_issue": False,
        "github_comment_issue": False,
        "github_list_issues": True,
    },
    "kazma-skills/kazma_skills/native/database_client/tools.py": {
        "inspect_db_schema": True,
        "execute_db_query": True,
        # Thin alias of execute_db_query — that function fences.
        "sqlite_query": False,
        "execute_db_query_any": True,
    },
    "kazma-core/kazma_core/tools/read_url.py": {
        # Return remote-authored bytes in some form — all must fence.
        "read_url": True,
        "read_research_chunk": True,
        "digest_research_file": True,
        "summarize_research_file": True,
        "list_research_chunks": True,
        # Writes to disk and returns OUR path/byte-count receipt, not the body.
        "read_url_to_file": False,
    },
    "kazma-skills/kazma_skills/native/email_manager/tools.py": {
        # A mailbox is the one inbound channel anyone on the internet can
        # write to. Anything echoing a sender's text must fence.
        "email_list": True,
        "email_get": True,
        # Our own send/delete/categorise receipts.
        "email_send": False,
        "email_delete": False,
        "email_categorize": False,
        "email_analyze": False,
    },
}


def _fences_somewhere(node: ast.AST) -> bool:
    """True if this function body calls a fence helper anywhere."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            name = _dotted(sub.func)
            if name in {
                "fence_untrusted",
                "format_untrusted_block",
                "fence_resource",
                "_fence_result",
            }:
                return True
    return False


@pytest.mark.parametrize("rel", sorted(FENCED_TOOL_FUNCTIONS))
def test_untrusted_readers_fence_per_function(rel):
    """Every classified reader fences; every public coroutine is classified."""
    path = REPO_ROOT / rel
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    expected = FENCED_TOOL_FUNCTIONS[rel]

    public_async = {
        n.name: n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and not n.name.startswith("_")
    }

    unclassified = sorted(set(public_async) - set(expected))
    assert not unclassified, (
        f"{rel} defines public tool coroutine(s) with no entry in "
        "FENCED_TOOL_FUNCTIONS. This gate is closed by default: decide whether "
        "each one returns remote-authored text and add it as True/False with a "
        "reason (audit 2026-09-16 F-2).\n  " + ", ".join(unclassified)
    )

    offenders = [
        name
        for name, must in expected.items()
        if must and name in public_async and not _fences_somewhere(public_async[name])
    ]
    assert not offenders, (
        f"{rel}: function(s) declared to return remote-authored text but "
        "calling no fence helper. A module-level grep for `fence_untrusted` "
        "does NOT cover this — one fenced sibling made the whole file pass "
        "while the recommended research path shipped unfenced (F-2).\n"
        "Fix: `from kazma_core.safety.prompt_fence import fence_untrusted` and "
        "wrap the remote-authored portion, keeping your own scaffolding "
        "outside the fence.\n  " + ", ".join(sorted(offenders))
    )


#: Name fragments that mark an env var as a security switch — something that
#: weakens a default and therefore has to be discoverable.
SECURITY_ENV_MARKERS = (
    "AUTH_DISABLED", "BYPASS", "ALLOW_UNGATED", "ALLOW_LOCAL", "ALLOW_MUTATE",
    "GATEWAY_ADMINS", "CANONICAL_FLOOR", "DEMO_MODE", "TRUSTED_IN_PROD",
    "AUTOLOGIN_HOSTS", "STRICT_ALLOWLIST", "CHAOS_ENABLED",
    "DISABLE_COST_BREAKER", "YOLO_TTL", "SAFE_ALLOWLIST",
)


#: Operator-facing documentation for security switches. BOTH are required.
#:
#: The gate originally checked ``.env.example`` alone. That is the file a
#: developer copies, not the page an operator reads: on 2026-09-20 ten of the
#: fifteen security switches — including ``KAZMA_DEV_WS_BYPASS``, which skips
#: authentication on every WebSocket handshake — were in ``.env.example`` and
#: absent from the reference page, and the one row that page *did* carry for a
#: security default (``KAZMA_REMOTE_PARSE``) documented it as ON when the
#: product defaults it OFF. A gate that watches one of two surfaces is how the
#: docs come to teach the opposite of the policy.
SECURITY_ENV_DOC_SURFACES = (
    ".env.example",
    "docs/docs/reference/environment-variables.md",
)


def test_security_env_vars_are_documented():
    """Every security-weakening ``KAZMA_*`` switch is documented for operators.

    The 2026-09-16 audit found the code reading 272 ``KAZMA_*`` variables
    while ``.env.example`` documented 43 — and none of the sixteen that turn a
    safety default off. A switch nobody can discover is a switch nobody can
    audit, including the operator who set it two years ago.

    Both surfaces are checked: ``.env.example`` (what a developer copies) and
    the environment-variables reference (what an operator reads).
    """
    surfaces = {
        rel: (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for rel in SECURITY_ENV_DOC_SURFACES
    }
    found: set[str] = set()
    for path in _product_files():
        src = path.read_text(encoding="utf-8", errors="replace")
        for name in re.findall(r"KAZMA_[A-Z0-9_]+", src):
            if any(marker in name for marker in SECURITY_ENV_MARKERS):
                found.add(name)

    missing: list[str] = []
    for name in sorted(found):
        absent = [rel for rel, text in surfaces.items() if name not in text]
        if absent:
            missing.append(f"{name}  (missing from: {', '.join(absent)})")

    assert not missing, (
        "Security-relevant env var read by the code but not documented for "
        "operators (audit 2026-09-16 F-8; second surface added 2026-09-20). "
        "Each of these weakens a default; document it with what it turns "
        "OFF — not just what it does — and when it is safe to set.\n  "
        + "\n  ".join(missing)
    )


def test_every_websocket_endpoint_authenticates():
    """Every ``@app.websocket`` handler must call ``websocket_is_authenticated``.

    WebSocket handshakes do not pass through the HTTP auth middleware, so a WS
    endpoint's authentication lives *inside its handler* and nothing structural
    enforces that it is there. An external route sweep cannot tell a protected
    WS endpoint from an open one — during the 2026-09-16 audit all three
    (``/ws/chat``, ``/ws/dashboard``, ``/ws/voice``) looked ungated from
    outside and all three were in fact fine. This makes that checkable, so the
    next WS endpoint cannot ship without a decision.
    """
    offenders: list[str] = []
    for path in _product_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorated_ws = any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr == "websocket"
                for d in node.decorator_list
            )
            if not decorated_ws:
                continue
            body = ast.dump(node)
            if "websocket_is_authenticated" not in body:
                offenders.append(f"{_rel(path)}:{node.lineno} {node.name}")

    assert not offenders, (
        "WebSocket endpoint with no authentication check. WS handshakes bypass "
        "the HTTP auth middleware entirely, so the check must be in the handler "
        "(audit 2026-09-16 F-8).\n"
        "Fix: `from kazma_ui.auth import websocket_is_authenticated`, then "
        "`await websocket.accept()` and close 4003 when it returns False.\n  "
        + "\n  ".join(offenders)
    )


def test_shipped_mcp_servers_can_actually_run():
    """No MCP entry in the shipped kazma.yaml may name a shell builtin.

    `test-mcp` ran `echo hello`. echo is a shell builtin, not an
    executable, so the server could never start -- and because it shipped
    ENABLED, every install alerted its operator about a fixture. It was
    disabled once in 683d5198 and silently re-enabled by de6ef2cb, which is
    the reason this is a gate and not another config edit: a value a commit
    can flip back needs a test, not a fix.
    """
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "kazma.yaml").read_text(encoding="utf-8")) or {}
    mcp = cfg.get("mcp")
    servers = mcp.get("servers", []) if isinstance(mcp, dict) else (mcp or [])

    # Builtins of cmd.exe / POSIX shells: never real executables.
    builtins = {"echo", "cd", "set", "dir", "type", "exit", "true", "false"}
    offenders = []
    for srv in servers:
        if not isinstance(srv, dict) or not srv.get("enabled", True):
            continue
        cmd = srv.get("command") or []
        head = str(cmd[0]).lower() if cmd else ""
        if head in builtins:
            offenders.append(f"{srv.get('name')} -> {head}")

    assert not offenders, (
        "shipped MCP servers that cannot start: " + ", ".join(offenders)
    )


# ── 11. Unbounded settings-store scans on the event loop ─────────────────

#: ConfigStore / WorkspaceStore methods that scan or rewrite the WHOLE store.
#:
#: This gate exists because the 2026-09-20 audit asked for the opposite rule:
#: extend BLOCKING_HELPERS so that ``ConfigStore.get`` / ``get_config_store()``
#: inside ``async def`` becomes visible. Measured before implementing, against
#: a 900-key store on the reference box:
#:
#:     get(key) warm .............      1.0 us   (TTL cache hit, dict lookup)
#:     get(key) cold .............      6.9 us   (one indexed SELECT, p99 19us)
#:     get_category(cat) .........    122.0 us
#:     get_all() .................  1_865.0 us   (p99 7.6 ms)
#:     export_yaml() ............. 129_743.0 us  (p99 165 ms)
#:
#: ``get`` is a cached single-key read six orders of magnitude cheaper than the
#: ``subprocess.run`` calls this file's sibling gate was widened to catch. It
#: appears inside ``async def`` at 67 call sites. A rule that reddens 67 sites
#: over one microsecond is a rule that gets 67 allowlist entries and teaches
#: nobody anything — the "green gate next to a sibling that is wrong" failure
#: with the colours swapped. So ``get`` is deliberately NOT gated, and the
#: number is written down here so the next audit re-raises it with evidence or
#: not at all.
#:
#: What IS gated is the unbounded set. ``export_yaml`` at 130 ms blocks every
#: SSE token stream and WebSocket heartbeat open at that moment; ``get_all``
#: at 7.6 ms p99 is a visible hitch on every Settings page load. Three call
#: sites existed when this gate was written and all three were fixed, so the
#: allowlist below is empty on purpose: this gate is closed by default.
UNBOUNDED_STORE_SCANS = {
    "get_all",
    "get_category",
    "export_yaml",
    "import_yaml",
    "reconcile_from_yaml",
    "reset_all",
}

#: ``(file, function)`` pairs deliberately exempt, each with a reason.
#: Empty by design — add an entry only with a measurement, not a hunch.
UNBOUNDED_SCAN_ALLOWLIST: dict[tuple[str, str], str] = {}


def test_no_unbounded_store_scan_on_the_event_loop():
    """Whole-store scans must not run inline in ``async def``.

    A single ``get`` is a cached indexed read and is fine on the loop. A scan
    of every row, or a rewrite of the whole store, is not: it is unbounded in
    the number of settings the operator has, and it stalls the one loop that
    also serves every stream. Use ``await asyncio.to_thread(...)``.
    """
    offenders: list[str] = []
    for path in _product_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        stack: list[str | None] = []

        class Visitor(ast.NodeVisitor):
            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                # A sync def re-enters the threadpool, and `asyncio.to_thread`
                # targets are sync defs — both are off the loop.
                stack.append(None)
                self.generic_visit(node)
                stack.pop()

            def visit_Lambda(self, node: ast.Lambda) -> None:
                stack.append(None)
                self.generic_visit(node)
                stack.pop()

            def visit_Call(self, node: ast.Call) -> None:
                func = node.func
                if (
                    stack
                    and stack[-1] is not None
                    and isinstance(func, ast.Attribute)
                    and func.attr in UNBOUNDED_STORE_SCANS
                ):
                    key = (_rel(path), stack[-1])
                    if key not in UNBOUNDED_SCAN_ALLOWLIST:
                        offenders.append(
                            f"{_rel(path)}:{node.lineno} async def "
                            f"{stack[-1]} calls .{func.attr}()"
                        )
                self.generic_visit(node)

        Visitor().visit(tree)

    assert not offenders, (
        "Whole-store scan inside async def — this stalls the event loop that "
        "serves every SSE and WebSocket stream. Measured: get_all() 1.9ms "
        "mean / 7.6ms p99, export_yaml() 130ms mean, against a 900-key store.\n"
        "Fix: `await asyncio.to_thread(store.get_all)`. If the handler then "
        "writes back per row, move the whole read-modify-write into ONE "
        "threaded function -- splitting it puts the writes back on the loop.\n  "
        + "\n  ".join(offenders)
    )


# ── 12. CWD-relative data paths that ignore KAZMA_DATA_DIR ───────────────

#: Files that still hardcode a ``kazma-data/...`` string instead of going
#: through ``kazma_core.paths``. This is a DEBT REGISTER, not an exemption.
#:
#: ``paths.py`` is the single source of truth and offers 32 helpers
#: (``settings_db()``, ``checkpoints_db()``, ``swarm_tasks_db()``, …), each
#: resolving under ``data_dir()`` and therefore honouring ``KAZMA_DATA_DIR``.
#: A literal ``"kazma-data/x.db"`` resolves against the process CWD instead,
#: so the same logical store lands in different files depending on who opened
#: it and from where — a cron job, a systemd unit and the server can each get
#: their own copy, and the backup routine (which uses ``data_dir()``) copies
#: only one of them.
#:
#: This was not theoretical. ``KnowledgeStore`` and ``BookmarkStore`` both
#: hardcoded ``"kazma-data/settings.db"`` while ``ConfigStore`` resolved the
#: SAME filename through ``paths.settings_db()``. On any install with
#: ``KAZMA_DATA_DIR`` set they were different files. Fixed 2026-09-20; the
#: reference install had escaped it only because that variable is unset there
#: and the server's CWD happens to be the install root.
#:
#: The remaining entries are deliberately NOT migrated in the same change.
#: Repointing a default path moves where an existing install looks for its
#: data, and doing 28 of those at once, unverified, is how you turn a
#: correctness fix into a data-loss incident. They should be migrated in small
#: batches, each with a check that the old location is empty or the file is
#: moved. **Delete entries from this list as they are fixed; never add one.**
CWD_RELATIVE_DATA_PATH_DEBT: frozenset[str] = frozenset()
#: **Empty as of 2026-09-21 — all 40 literals across 20 files migrated.**
#: Verified by resolving each migrated constant and comparing against the
#: path the old literal produced: 14/14 identical. The migration is a
#: no-op wherever KAZMA_DATA_DIR is unset and the CWD is the repo root
#: (the reference install), and a correctness fix everywhere else.
#: Keep this empty. If an entry is ever needed, it needs a reason.


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """Ids of Constant nodes that are module/class/function docstrings."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def test_no_new_cwd_relative_data_paths():
    """New code must resolve data paths through ``kazma_core.paths``.

    A ``"kazma-data/x.db"`` literal is relative to the process CWD and blind
    to ``KAZMA_DATA_DIR``, so the store it names is a different file depending
    on who opened it and from where.

    Docstrings are excluded deliberately — the fixed modules quote the old
    literal in their own explanation of why it was wrong, and a gate that
    fires on its own tombstone teaches people to delete the explanation.
    """
    offenders: list[str] = []
    for path in _product_files():
        rel = _rel(path)
        if rel in CWD_RELATIVE_DATA_PATH_DEBT:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        docs = _docstring_node_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("kazma-data/")
                and id(node) not in docs
            ):
                offenders.append(f"{rel}:{node.lineno} -> {node.value!r}")

    assert not offenders, (
        "CWD-relative data path in new code. This resolves against the "
        "process working directory and ignores KAZMA_DATA_DIR, so a cron "
        "job, a systemd unit and the server can each open a DIFFERENT file "
        "for the same logical store — and the backup routine, which uses "
        "data_dir(), copies only one of them.\n"
        "Fix: use the matching helper in kazma_core.paths (settings_db(), "
        "checkpoints_db(), swarm_tasks_db(), exports_dir(), ...), resolved "
        "lazily inside a function rather than bound at import.\n  "
        + "\n  ".join(offenders)
    )


#: Files that still bet on a duration: `sleep(<2s)` immediately followed by an
#: `assert`, at statement level (a sleep INSIDE a loop is a poll, and correct).
#:
#: A ledger, not an endorsement. Two of these bets went red in CI on
#: 2026-09-21 and were converted to polling; the rest are untouched because
#: many are legitimate — where the sleep IS the stimulus, such as a watchdog
#: that must fire after N seconds, polling would test nothing. Telling those
#: apart needs reading each one, and none of them has failed.
#:
#: The gate exists so the number cannot GROW quietly. Deleting an entry when a
#: file is fixed is the intended direction; adding one needs a reason.
SLEEP_THEN_ASSERT_DEBT: dict[str, int] = {
    "tests/e2e/test_unified_turn_browser.py": 2,
    "tests/test_audit_deep_structure_fixes.py": 5,
    "tests/test_document_operations_phase9.py": 1,
    "tests/test_gateway.py": 1,
    "tests/test_hitl_gates.py": 1,
    "tests/test_loop_stall_watchdog.py": 2,
    "tests/test_procedural_recorder_worker.py": 1,
    "tests/test_swarm_engine_core.py": 1,
    "tests/test_swarm_notify.py": 1,
    "tests/test_swarm_reliability.py": 3,
    "tests/test_swarm_timeout_validation_concurrency.py": 2,
    "tests/test_typing_keepalive.py": 2,
    "tests/test_unrestricted_unified.py": 1,
}


def _sleep_seconds(node: ast.stmt) -> float | None:
    """Seconds, if *node* is a bare `sleep(...)` / `wait_for_timeout(...)`."""
    val = node.value if isinstance(node, ast.Expr) else None
    if isinstance(val, ast.Await):
        val = val.value
    if not isinstance(val, ast.Call) or not isinstance(val.func, ast.Attribute):
        return None
    name = val.func.attr
    if name not in ("sleep", "wait_for_timeout"):
        return None
    if not val.args or not isinstance(val.args[0], ast.Constant):
        return None
    raw = val.args[0].value
    if not isinstance(raw, (int, float)) or raw == 0:
        return None
    return float(raw) / (1000.0 if name == "wait_for_timeout" else 1.0)


def test_no_new_sleep_then_assert_in_tests():
    """A test must not wait a fixed duration and then assert once.

    Both CI flakes fixed on 2026-09-21 were this shape. The journaled-frames
    e2e waited for the client's render pipeline to mount and asserted once; the
    swarm checkpoint test waited 300ms for a 100ms timeout and asserted once.
    Neither was waiting for TIME — one waited for a mount, the other for the
    scheduler to reach a task — and neither has a fixed upper bound on a loaded
    runner. Both failed in CI, passed locally, and read as regressions in
    whatever commit happened to be running.

    A longer sleep is the same bet at better odds. Polling to a deadline
    removes the bet without weakening anything: the assertion is unchanged, and
    a condition that never arrives still fails, at the deadline.

    Not every sleep is wrong — where the sleep IS the stimulus it is exactly
    right, which is why the existing ones are ledgered rather than banned. This
    gate stops the count growing.
    """
    offenders: list[str] = []
    counts: dict[str, int] = {}

    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue

        in_loop: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.While, ast.For, ast.AsyncFor)):
                for d in ast.walk(node):
                    in_loop.add(id(d))

        rel = _rel(path)
        for parent in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(parent, field, None)
                if not isinstance(block, list):
                    continue
                for i, stmt in enumerate(block):
                    secs = _sleep_seconds(stmt)
                    if secs is None or secs > 2.0 or id(stmt) in in_loop:
                        continue
                    nxt = block[i + 1] if i + 1 < len(block) else None
                    if isinstance(nxt, ast.Assert):
                        counts[rel] = counts.get(rel, 0) + 1
                        if counts[rel] > SLEEP_THEN_ASSERT_DEBT.get(rel, 0):
                            offenders.append(f"{rel}:{stmt.lineno} sleep({secs})")

    assert not offenders, (
        "a test sleeps for a fixed duration and then asserts once. That is a "
        "bet that the work finishes in time — it passes on an idle machine and "
        "fails on a loaded CI runner, and the failure looks like a regression "
        "in whoever's commit is running.\n"
        "Fix: poll to a deadline instead. Keep the assertion; a condition that "
        "never arrives still fails, at the deadline, with a message saying "
        "which it was.\n"
        "If the sleep IS the stimulus (a watchdog that must fire after N "
        "seconds), add the file to SLEEP_THEN_ASSERT_DEBT with that reason.\n  "
        + "\n  ".join(offenders)
    )

    stale = {
        rel: n for rel, n in SLEEP_THEN_ASSERT_DEBT.items()
        if counts.get(rel, 0) < n
    }
    assert not stale, (
        "the ledger claims more sleep-then-assert sites than exist — someone "
        "fixed these and did not lower the number, which is how a ledger stops "
        "meaning anything:\n  "
        + "\n  ".join(f"{r}: ledger {n}, actual {counts.get(r, 0)}"
                      for r, n in stale.items())
    )


def test_the_write_veto_is_checked_by_every_caller():
    """A refusal returned as ``None`` must be honoured by whoever asked.

    ``_prepare_value_for_storage`` signals "do not write this" by returning
    ``None``. ``set()`` had always honoured that. ``atomic_update`` fed the
    result straight into ``json.dumps`` and wrote the string ``"null"`` over
    the row it had just refused to blank — **while logging the refusal**. A
    guard that fires, logs, and is overruled by its own caller is worse than
    no guard, because the log says it worked.

    That instance was fixed on 2026-09-14. The class was not: a sentinel
    return is only as good as the callers that check it, and ``KNOWN_GAPS``
    records that nothing lints for the ones that do not.

    So: every call must be followed immediately by a test of what came back —
    either ``is None`` directly, or ``_refused_the_write``, which exists to
    make the same decision in the two places that need to unwind a
    transaction first. Five call sites today, all five compliant.
    """
    path = REPO_ROOT / _CONFIG_STORE
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))

    def _is_the_call(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_prepare_value_for_storage"
        )

    offenders: list[str] = []
    checked = 0
    for parent in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(parent, field, None)
            if not isinstance(block, list):
                continue
            for i, stmt in enumerate(block):
                if not (
                    isinstance(stmt, ast.Assign)
                    and _is_the_call(stmt.value)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                ):
                    continue
                name = stmt.targets[0].id
                nxt = block[i + 1] if i + 1 < len(block) else None
                guarded = isinstance(nxt, ast.If) and any(
                    isinstance(n, ast.Name) and n.id == name
                    for n in ast.walk(nxt.test)
                )
                if guarded:
                    checked += 1
                else:
                    offenders.append(
                        f"{_CONFIG_STORE}:{stmt.lineno} -> {name!r} is used "
                        "without testing it first"
                    )

    assert checked, (
        "no checked call sites found — this gate has stopped matching the "
        "code it guards, which makes it decoration"
    )
    assert not offenders, (
        "the result of _prepare_value_for_storage is used without being "
        "checked. It returns None to REFUSE a write; using it unchecked "
        "writes the refusal itself to the database — that is how "
        "atomic_update once stored the string 'null' over a secret it had "
        "just declined to blank, while logging that it had declined.\n"
        "Fix: follow the call with `if x is None:` or "
        "`if self._refused_the_write(...)`.\n  " + "\n  ".join(offenders)
    )


#: SQL that writes a configuration row, in either backend's table.
_CONFIG_WRITE_RE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+REPLACE\s+)?INTO|UPDATE|DELETE\s+FROM)\s+"
    r"(?:settings|kazma_settings)\b",
    re.I,
)

#: The one module allowed to write configuration rows.
_CONFIG_STORE = "kazma-core/kazma_core/config_store.py"


def test_config_writes_stay_inside_the_chokepoint():
    """Only ``config_store`` may write a configuration row.

    Pressing **Test** on a provider once deleted every saved API key.
    ``set_provider_health`` was a read-modify-write over the whole provider
    list through the vault-*resolved* view, and a pointer that could not be
    decrypted resolved to ``None`` -> ``""``, so one write from a process
    without the key blanked every pointer on disk. Permanently, with a single
    WARNING as the only symptom, after which the UI truthfully reported that
    no key was stored.

    The fix put the refusal in ``_prepare_value_for_storage`` — the chokepoint
    every writer inside ``ConfigStore`` passes through. That protects the
    writers that exist. It does nothing about a future one that opens the
    database directly and never reaches the chokepoint at all, and
    ``KNOWN_GAPS`` recorded exactly that: "no test or lint asserts that a
    diagnostic path may not call a mutating one".

    This is that lint, in the form that is actually checkable: the guard
    cannot be bypassed if there is nowhere else to write from. Ten write
    sites exist today and all ten are inside ``config_store``, so this gate
    starts closed with no debt — the cheapest moment to install one.
    """
    offenders: list[str] = []
    for path in _product_files():
        rel = _rel(path)
        if rel == _CONFIG_STORE:
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except (SyntaxError, OSError):
            continue
        docs = _docstring_node_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docs
                and _CONFIG_WRITE_RE.search(node.value)
            ):
                snippet = " ".join(node.value.split())[:70]
                offenders.append(f"{rel}:{node.lineno} -> {snippet!r}")

    assert not offenders, (
        "configuration row written outside kazma_core.config_store. Every "
        "writer must go through ConfigStore so it passes "
        "_prepare_value_for_storage, which refuses to blank a stored secret. "
        "A writer that opens the database directly skips that refusal, and "
        "the failure mode is silent and permanent: a vault pointer that "
        "cannot be decrypted resolves to the empty string, and the UI then "
        "truthfully reports that no key is stored.\n"
        "Fix: call ConfigStore.set / atomic_update instead of writing SQL.\n  "
        + "\n  ".join(offenders)
    )


#: Extensions whose files are genuinely binary and must not be scanned.
_BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".docx", ".xlsx",
    ".ttf", ".otf", ".woff", ".woff2", ".zip", ".gz", ".db", ".sqlite",
    ".sqlite3", ".pyc", ".so", ".dll", ".exe", ".webp", ".mp4", ".wasm",
}


def test_no_literal_nul_bytes_in_source():
    """A source file must not contain a raw NUL byte.

    ``turn_preferences.js`` used U+0000 as a composite-key separator — a
    reasonable choice, since it cannot occur in a turn id — but wrote it as a
    LITERAL NUL in the file rather than as an escape. Git classifies any file
    with a NUL in its first 8000 bytes as binary, so that module shipped with
    no diffs, no line-ending normalisation and no merge support, and nobody
    noticed until a ``git ls-files --eol`` sweep on 2026-09-21 turned up one
    ``i/-text`` entry that was not an image or a font.

    The escape compiles to exactly the same string, so this costs nothing at
    runtime and keeps the file reviewable.

    Scans the whole file, not just the header: git's own detection stops at
    8000 bytes, so a NUL deeper in a large module would be invisible to it
    while still corrupting a copy/paste or an editor round-trip.
    """
    import subprocess

    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout

    offenders: list[str] = []
    for raw in out.split(b"\x00"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        path = REPO_ROOT / rel
        if path.suffix.lower() in _BINARY_EXT or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        count = data.count(b"\x00")
        if count:
            offenders.append(f"{rel} ({count} NUL byte(s))")

    assert not offenders, (
        "Literal NUL byte in a source file. Git treats the file as binary: "
        "no diff, no line-ending normalisation, no merge — the change is "
        "invisible in review.\n"
        "Fix: write the character as an escape (JavaScript and Python both "
        "accept a \\u0000 / \\x00 escape, which produces the identical "
        "string at runtime, so stored data stays compatible).\n  "
        + "\n  ".join(offenders)
    )
