"""A result that says "it failed" is read where it is returned.

The full audit of 2026-10-26 ("Recurring patterns" 2) found the same bug in
seven places: a function answers whether it worked -- ``-> bool``, or a
result with an ``ok`` field -- and its caller drops the answer. The migration
importer backed up the live database with ``_backup_one(...)``, ignored the
False of a failed copy and swapped the file anyway, so a rollback deleted
data that no backup held (AUD-003). Chat apps answered "File sent" over an
attachment the platform refused (AUD-032), a swarm result routed to a dead
adapter was logged "routed" and its fallback never ran, and the cloud
upload's ``ok`` was dropped (AUD-077). Each was fixed where it was found;
this is the class gate.

**Scope.** The functions are the ones that answer for a backup, a migration,
a send or a delivery: every module-level function and method defined in
``SCOPE`` whose return annotation is ``bool`` or ``tuple[bool, ...]``, or
that returns a dict literal with an ``"ok"`` key or a call with an ``ok=``
keyword.

**Rule.** No product call of one of them may drop its result: a call that is
a statement of its own (``f(...)``, ``await f(...)``, the same through
``asyncio.to_thread(f, ...)`` or ``run_in_executor(None, f, ...)``), or whose
result is assigned to ``_``. A result assigned, returned, tested or passed
on is used. A call is resolved, never matched by name alone:

* a plain name through the module's imports (a function-level import, an
  alias, a relative import and a package re-export included), or a function
  of the same module the enclosing functions do not shadow;
* ``mod.f(...)`` through the module alias;
* ``self.f(...)`` / ``cls.f(...)`` through the enclosing class and its bases
  (by name, across the product), to the first class that defines ``f``;
* any other receiver of a method name a scoped class defines must be
  classified in ``RECEIVERS`` (which class it holds) or ``NOT_OURS`` (whose
  object it is). An unclassified receiver is a finding of its own, so the
  gate cannot go blind to a new holder of a manager, bus or store.

A dropped result is fixed (the failure handled where it happened) or
declared: ``REPORTS_ITS_OWN_FAILURE`` names a callee whose result no caller
has to read, ``DECLARED`` a site where dropping it is right, each with the
reason. A declaration that matches nothing fails, so the lists only shrink.

Fixed when the gate was built (each with a test below that fails on the old
code): the importer's document restore dropped both of its copies' answers --
a failed safety copy went on to overwrite the live catalog, and a failed
install deleted it (``_backup_one`` removes its destination) under "restored
documents.db"; the gateway logged "HITL interrupt surfaced" over an approval
card the platform refused.
"""

from __future__ import annotations

import ast
import fnmatch
import functools
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The code that answers for a backup, a migration, a send or a delivery.
SCOPE = (
    "kazma-core/kazma_core/backup/*.py",
    "kazma-core/kazma_core/migration/*.py",
    "kazma-core/kazma_core/memory/backup.py",
    "kazma-core/kazma_core/db/pg_backup.py",
    "kazma-core/kazma_core/tools/send_message.py",
    "kazma-core/kazma_core/observability/ops_alerts.py",
    "kazma-core/kazma_core/swarm/bus.py",
    "kazma-core/kazma_core/cron/scheduler.py",
    "kazma-core/kazma_core/cron/store.py",
    "kazma-gateway/kazma_gateway/gateway.py",
    "kazma-gateway/kazma_gateway/adapters/*.py",
    "kazma-gateway/kazma_gateway/agent_handler/swarm_output.py",
    "kazma-gateway/kazma_gateway/agent_handler/swarm_dispatch.py",
)

_THREAD_RUNNERS = {"to_thread"}  # asyncio.to_thread(f, ...)
_EXECUTOR_RUNNERS = {"run_in_executor"}  # loop.run_in_executor(None, f, ...)


# ── the index: which functions answer, and where they live ───────────────


def _module_of(rel: str) -> str:
    parts = rel.split("/")[1:]  # drop the distribution folder (kazma-core/...)
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _answers(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether a function's result says if it worked."""
    if fn.returns is not None:
        ann = ast.unparse(fn.returns).replace(" ", "")
        if ann == "bool" or ann.startswith("tuple[bool,"):
            return True
    for node in ast.walk(fn):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value = node.value
        if isinstance(value, ast.Dict) and any(
            isinstance(k, ast.Constant) and k.value == "ok" for k in value.keys
        ):
            return True
        if isinstance(value, ast.Call) and any(k.arg == "ok" for k in value.keywords):
            return True
    return False


@dataclass
class _Module:
    name: str
    rel: str
    tree: ast.Module
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)  # local -> (module, name)
    module_aliases: dict[str, str] = field(default_factory=dict)  # local -> module
    functions: set[str] = field(default_factory=set)
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)


@dataclass
class Index:
    modules: dict[str, _Module]
    scoped: set[str]  # module names in SCOPE
    #: (module, qualname) of every function that answers, in a scoped module
    answering: set[tuple[str, str]]
    #: class simple name -> [(module, ClassDef)] over every product module
    classes_by_name: dict[str, list[tuple[str, ast.ClassDef]]]
    #: method names some scoped class defines and that answer
    answering_methods: set[str]


def _resolve_relative(mod: _Module, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    is_pkg = mod.rel.endswith("__init__.py")
    base = mod.name.split(".")
    if not is_pkg:
        base = base[:-1]
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def build_index(sources: dict[str, str]) -> Index:
    """``sources``: repo-relative path -> source, for every product file."""
    modules: dict[str, _Module] = {}
    for rel, src in sources.items():
        name = _module_of(rel)
        modules[name] = _Module(name=name, rel=rel, tree=ast.parse(src))
    for mod in modules.values():
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.ImportFrom):
                base = _resolve_relative(mod, node)
                for alias in node.names:
                    local = alias.asname or alias.name
                    full = f"{base}.{alias.name}"
                    if full in modules:
                        mod.module_aliases[local] = full
                    else:
                        mod.imports[local] = (base, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        mod.module_aliases[alias.asname] = alias.name
                    else:
                        mod.module_aliases[alias.name.split(".")[0]] = alias.name.split(".")[0]
        for node in mod.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mod.functions.add(node.name)
            elif isinstance(node, ast.ClassDef):
                mod.classes[node.name] = node

    scoped = {
        m.name for m in modules.values() if any(fnmatch.fnmatch(m.rel, p) for p in SCOPE)
    }
    answering: set[tuple[str, str]] = set()
    answering_methods: set[str] = set()
    classes_by_name: dict[str, list[tuple[str, ast.ClassDef]]] = {}
    for mod in modules.values():
        for cname, cls in mod.classes.items():
            classes_by_name.setdefault(cname, []).append((mod.name, cls))
        if mod.name not in scoped:
            continue
        for node in mod.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _answers(node):
                answering.add((mod.name, node.name))
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and _answers(item):
                        answering.add((mod.name, f"{node.name}.{item.name}"))
                        answering_methods.add(item.name)
    return Index(modules, scoped, answering, classes_by_name, answering_methods)


def _resolve_function(index: Index, module: str, name: str, depth: int = 0) -> tuple[str, str] | None:
    """(module, name) of a module-level function, following re-exports."""
    mod = index.modules.get(module)
    if mod is None or depth > 5:
        return None
    if name in mod.functions:
        return (module, name)
    if name in mod.imports:
        return _resolve_function(index, *mod.imports[name], depth=depth + 1)
    return None


def _method_owner(index: Index, module: str, cls_name: str, method: str,
                  seen: set[str] | None = None) -> tuple[str, str] | None:
    """(module, Class.method) of the first class, from ``cls_name`` up its
    bases, that defines ``method``."""
    seen = seen if seen is not None else set()
    if cls_name in seen:
        return None
    seen.add(cls_name)
    mod = index.modules.get(module)
    candidates = (
        [(module, mod.classes[cls_name])] if mod is not None and cls_name in mod.classes
        else index.classes_by_name.get(cls_name, [])
    )
    for owner_mod, cls in candidates:
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method:
                return (owner_mod, f"{cls_name}.{method}")
        for base in cls.bases:
            base_name = base.id if isinstance(base, ast.Name) else (
                base.attr if isinstance(base, ast.Attribute) else "")
            if base_name:
                found = _method_owner(index, owner_mod, base_name, method, seen)
                if found is not None:
                    return found
    return None


def _short(target: tuple[str, str]) -> str:
    module, qual = target
    return f"{module.split('.', 1)[-1]}.{qual}"


# ── receivers of a scoped method that are not ``self`` ───────────────────

#: (file glob, receiver as written) -> the scoped class it holds.
RECEIVERS: dict[tuple[str, str], str] = {
    # Every gateway handler is handed the GatewayManager as ``manager``.
    ("kazma-gateway/kazma_gateway/agent_handler/*.py", "manager"): "GatewayManager",
    # SwarmMessageBus._adapter is annotated ``BusAdapter``.
    ("kazma-core/kazma_core/swarm/bus.py", "self._adapter"): "BusAdapter",
    # ``bus = get_message_bus()`` (-> SwarmMessageBus).
    ("kazma-core/kazma_core/swarm/safety.py", "bus"): "SwarmMessageBus",
    # CronScheduler is built with the SQLiteCronStore.
    ("kazma-core/kazma_core/cron/scheduler.py", "self._store"): "SQLiteCronStore",
}

#: (file glob, receiver as written) -> whose object it is (not a scoped class).
NOT_OURS: dict[tuple[str, str], str] = {
    ("kazma-gateway/kazma_gateway/adapters/*.py", "ws"):
        "a websockets connection: ws.send writes a frame and returns None",
    ("kazma-core/kazma_core/web_acquire/browser_network.py", "self.cdp"):
        "Playwright's CDPSession: send is a protocol command, its failures raise",
}


def _lookup(table: dict[tuple[str, str], str], rel: str, receiver: str,
            used: set[tuple[str, str]]) -> str | None:
    for key, value in table.items():
        glob, recv = key
        if recv == receiver and fnmatch.fnmatch(rel, glob):
            used.add(key)
            return value
    return None


# ── the call sites ───────────────────────────────────────────────────────


def _bound_names(fn: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> set[str]:
    """Names a function binds locally (parameters and assignments), not imports."""
    names: set[str] = set()
    args = fn.args
    for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
        if a is not None:
            names.add(a.arg)
    body = fn.body if isinstance(fn.body, list) else [fn.body]
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
    return names


@dataclass(frozen=True)
class Finding:
    rel: str
    line: int
    function: str  # enclosing qualname, "<module>" at top level
    callee: str  # what the call resolves to, or "?<receiver>.<method>"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.rel, self.function, self.callee)

    def __str__(self) -> str:
        return f"{self.rel}:{self.line} {self.function}() drops {self.callee}()"


def _callee_expr(value: ast.expr) -> ast.expr | None:
    """The function a dropped statement calls (unwrapping await and the
    thread runners), or None when it is not a call."""
    if isinstance(value, ast.Await):
        value = value.value
    if not isinstance(value, ast.Call):
        return None
    func = value.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    if name in _THREAD_RUNNERS and value.args:
        return value.args[0]
    if name in _EXECUTOR_RUNNERS and len(value.args) >= 2:
        return value.args[1]
    return func


class _Walker(ast.NodeVisitor):
    def __init__(self, index: Index, mod: _Module) -> None:
        self.index = index
        self.mod = mod
        self.stack: list[ast.AST] = []
        self.findings: list[Finding] = []
        self.used: set[tuple[str, str]] = set()  # RECEIVERS / NOT_OURS entries matched

    # scope tracking
    def _visit_scope(self, node: ast.AST) -> None:
        self.stack.append(node)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 (ast.NodeVisitor's name)
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_scope(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._visit_scope(node)

    def _qualname(self) -> str:
        names = [n.name for n in self.stack if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        return ".".join(names) or "<module>"

    def _enclosing_class(self) -> str | None:
        for node in reversed(self.stack):
            if isinstance(node, ast.ClassDef):
                return node.name
        return None

    def _shadowed(self, name: str) -> bool:
        return any(
            name in _bound_names(n) for n in self.stack
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        )

    def _module_named(self, recv: ast.expr) -> str | None:
        """The product module a receiver names (``mod`` from ``import a.b as
        mod`` / ``from a import b``, or a dotted ``a.b.c``), or None."""
        parts: list[str] = []
        node = recv
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id not in self.mod.module_aliases:
            return None
        if self._shadowed(node.id):
            return None
        name = ".".join([self.mod.module_aliases[node.id], *reversed(parts)])
        return name if name in self.index.modules else None

    def _resolve(self, func: ast.expr) -> str | None:
        """The answering function a callee expression names, ``?recv.meth``
        for an unclassified receiver, or None."""
        index, mod = self.index, self.mod
        if isinstance(func, ast.Name):
            if self._shadowed(func.id):
                return None
            target = None
            if func.id in mod.functions:
                target = (mod.name, func.id)
            elif func.id in mod.imports:
                target = _resolve_function(index, *mod.imports[func.id])
            return _short(target) if target in index.answering else None
        if not isinstance(func, ast.Attribute):
            return None
        recv = func.value
        module = self._module_named(recv)
        if module is not None:
            target = _resolve_function(index, module, func.attr)
            return _short(target) if target in index.answering else None
        if func.attr not in index.answering_methods:
            return None
        if isinstance(recv, ast.Name) and recv.id in ("self", "cls"):
            cls = self._enclosing_class()
            target = _method_owner(index, mod.name, cls, func.attr) if cls else None
            return _short(target) if target in index.answering else None
        receiver = ast.unparse(recv)
        if _lookup(NOT_OURS, mod.rel, receiver, self.used) is not None:
            return None
        cls = _lookup(RECEIVERS, mod.rel, receiver, self.used)
        if cls is None:
            return f"?{receiver}.{func.attr}"
        target = _method_owner(index, "", cls, func.attr)
        return _short(target) if target in index.answering else None

    def _check(self, value: ast.expr, line: int) -> None:
        func = _callee_expr(value)
        if func is None:
            return
        callee = self._resolve(func)
        if callee is not None:
            self.findings.append(Finding(self.mod.rel, line, self._qualname(), callee))

    def visit_Expr(self, node: ast.Expr) -> None:
        self._check(node.value, node.lineno)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if all(isinstance(t, ast.Name) and t.id == "_" for t in node.targets):
            self._check(node.value, node.lineno)
        self.generic_visit(node)


def dropped_results(index: Index, rel: str, used: set[tuple[str, str]] | None = None) -> list[Finding]:
    """The calls in ``rel`` that drop an answering function's result.
    ``used`` collects the RECEIVERS / NOT_OURS entries the file matched."""
    mod = index.modules[_module_of(rel)]
    walker = _Walker(index, mod)
    walker.visit(mod.tree)
    if used is not None:
        used |= walker.used
    return walker.findings


def _product_sources() -> dict[str, str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard",
         "kazma-*/*.py", "kazma-*/**/*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return {
        rel: (ROOT / rel).read_text(encoding="utf-8")
        for rel in sorted(set(out))
        if "/tests/" not in rel and "_tests/" not in rel and (ROOT / rel).exists()
    }


# ── what may drop a result, and why ──────────────────────────────────────

#: Callees whose result no caller has to read: the callee reports its own
#: failure, and a caller has nothing else to do with it.
REPORTS_ITS_OWN_FAILURE: dict[str, str] = {
    "observability.ops_alerts.alert": (
        "True means only that delivery was requested (False: alerts off, throttled, or it "
        "raised). alert() logs every occurrence at WARNING ('[ops_alert] key | title'), the "
        "delivery logs 'alert was NOT delivered anywhere', and on_delivery carries the real "
        "outcome to a caller that needs it (§33). Callers raise it from a failure path with "
        "nothing else to do."
    ),
    "backup.restic_repo.alert_missing_password": (
        "answers whether it raised the alert, and logs a WARNING when it could not; the "
        "snapshot it reports is skipped by the caller, whose own result says so"
    ),
    "backup.restic_repo.alert_read_only_remote": (
        "answers whether it raised the alert, and logs a WARNING when it could not; _run "
        "returns the failed ResticResult beside it"
    ),
}

_CHAT_REPLY = (
    "a reply in the chat that asked, with no other route to it: the adapter logs a refused "
    "send and GatewayManager.send counts it as an error (gateway metrics); nothing after it "
    "claims the reply arrived"
)
_SWARM_NOTICE = (
    "a notice in the chat that asked: _send_swarm_reply logs a WARNING itself when the "
    "platform does not take it, and there is no other route to that chat"
)
_BUSY_NOTICE = (
    "the 'Kazma is busy' notice to a slash command the full queue refused: the command is "
    "already recorded dropped ('queue_full') in the adapter's receive record (§42), and the "
    "notice is best effort on an interaction token that may have expired"
)
_CARD_EDIT = (
    "the outcome edit of an approval card: the decision is the shared row's "
    "(shared_approvals), and the edit logs a WARNING itself when the platform refuses it (SW-S15)"
)

#: (file, enclosing function, callee) -> why dropping the result is right.
DECLARED: dict[tuple[str, str, str], str] = {
    # ── cron: the alert outbox ──
    ("kazma-core/kazma_core/cron/scheduler.py", "CronScheduler._drain_alerts",
     "cron.store.SQLiteCronStore.mark_alert_sent"):
        "False means the row was already marked (a mark is idempotent); a store failure "
        "raises sqlite3.Error, which the pass handles",
    ("kazma-core/kazma_core/cron/scheduler.py", "CronScheduler._raise_alert",
     "cron.store.SQLiteCronStore.mark_alert_sent"):
        "False means the row was already marked (a mark is idempotent); a store failure "
        "raises sqlite3.Error to _drain_alerts",
    # ── ops alerts ──
    ("kazma-core/kazma_core/observability/ops_alerts.py", "deliver_acknowledged",
     "observability.ops_alerts._deliver"):
        "the routes that took the message are read from the outcome dict _deliver fills "
        "('sent'), which is stricter than its bool",
    # ── the swarm bus ──
    ("kazma-core/kazma_core/swarm/bus.py", "SwarmMessageBus.stream", "swarm.bus.BusAdapter.send"):
        "a worker's status line: each platform sender logs a refused post, the panel's "
        "subscribers get the line below it, and a NullBusAdapter refuses every line by design",
    ("kazma-core/kazma_core/swarm/bus.py", "SwarmMessageBus.send_alert", "swarm.bus.BusAdapter.send"):
        "the plain-text fallback of an alert card: each platform sender logs a refused post, "
        "and the subscribers get the alert below it",
    ("kazma-core/kazma_core/swarm/safety.py", "SafetyMiddleware._ask_the_bus",
     "swarm.bus.SwarmMessageBus.request_approval"):
        "the decision is read from the request's one shared row (shared_approvals.get) in "
        "the finally below, never from a platform's answer (SW-S04, SW-S05)",
    ("kazma-gateway/kazma_gateway/adapters/discord_bus.py", "DiscordBusAdapter.request_approval._edit",
     "adapters.discord_bus.DiscordBusAdapter._patch_message"): _CARD_EDIT,
    ("kazma-gateway/kazma_gateway/adapters/slack_bus.py", "SlackBusAdapter.request_approval._edit",
     "adapters.slack_bus.SlackBusAdapter._update_message"): _CARD_EDIT,
    # ── chat adapters ──
    ("kazma-gateway/kazma_gateway/adapters/discord_commands.py", "handle",
     "adapters.discord_commands.send"): _BUSY_NOTICE,
    ("kazma-gateway/kazma_gateway/adapters/slack_commands.py", "handle",
     "adapters.slack_commands.send"): _BUSY_NOTICE,
    ("kazma-gateway/kazma_gateway/adapters/telegram.py", "TelegramAdapter.listen",
     "adapters.telegram.TelegramAdapter._dispatch_update"):
        "False is not a failure: the update was already dispatched and Telegram redelivered "
        "it before its offset was committed, and skipping it IS the handling (AUD-065); a "
        "failed dispatch raises, logged by the except around the call",
    ("kazma-gateway/kazma_gateway/adapters/telegram.py", "TelegramAdapter._send_tts_reply",
     "adapters.telegram.TelegramAdapter.send_voice_reply"):
        "the spoken copy of a reply already sent as text; send_voice_reply logs a refused "
        "sendVoice at WARNING",
    ("kazma-gateway/kazma_gateway/gateway.py", "GatewayManager._consume", "gateway.GatewayManager.send"):
        "the rate-limit notice: the message itself is recorded left unanswered "
        "(note_left_unanswered), and GatewayManager.send counts a refusal",
    ("kazma-gateway/kazma_gateway/gateway.py", "GatewayManager._consume._handle_one",
     "gateway.GatewayManager.send"):
        "a '💡' tool hint after the turn's own answer: optional, and GatewayManager.send "
        "counts a refusal",
    # ── gateway handlers: replies in the chat that asked ──
    ("kazma-gateway/kazma_gateway/agent_handler/graph.py", "create_graph_handler._handler_body",
     "gateway.GatewayManager.send"): _CHAT_REPLY,
    ("kazma-gateway/kazma_gateway/agent_handler/graph.py",
     "create_graph_handler._handler_body._long_progress", "gateway.GatewayManager.send"):
        "a long task's progress line between steps: " + _CHAT_REPLY,
    ("kazma-gateway/kazma_gateway/agent_handler/hitl.py", "_deliver_resume",
     "gateway.GatewayManager.send"):
        _CHAT_REPLY + "; a chained card that does not arrive stays decidable on the Web "
        "approvals panel and with /hitl, and the watchdog expires it",
    ("kazma-gateway/kazma_gateway/agent_handler/hitl.py", "make_timeout_resumer.resume_after_timeout",
     "gateway.GatewayManager.send"): _CHAT_REPLY,
    ("kazma-gateway/kazma_gateway/agent_handler/hitl.py", "_handle_hitl_resume",
     "gateway.GatewayManager.send"): _CHAT_REPLY,
    ("kazma-gateway/kazma_gateway/agent_handler/session_commands.py", "try_session_command._reply",
     "gateway.GatewayManager.send"): _CHAT_REPLY,
    ("kazma-gateway/kazma_gateway/agent_handler/commands.py", "_try_model_command",
     "gateway.GatewayManager.send"): _CHAT_REPLY,
    ("kazma-gateway/kazma_gateway/agent_handler/commands.py", "_send_model_reply",
     "gateway.GatewayManager.send"): _CHAT_REPLY,
    ("kazma-gateway/kazma_gateway/agent_handler/commands.py", "_try_swarm_command",
     "agent_handler.swarm_dispatch._send_swarm_reply"): _SWARM_NOTICE,
    ("kazma-gateway/kazma_gateway/agent_handler/commands.py", "_handle_swarm_config_command",
     "agent_handler.swarm_dispatch._send_swarm_reply"): _SWARM_NOTICE,
    ("kazma-gateway/kazma_gateway/agent_handler/swarm_dispatch.py", "_dispatch_auto_route",
     "agent_handler.swarm_dispatch._send_swarm_reply"): _SWARM_NOTICE,
    ("kazma-gateway/kazma_gateway/agent_handler/swarm_dispatch.py", "_dispatch_swarm_from_chat",
     "agent_handler.swarm_dispatch._send_swarm_reply"):
        _SWARM_NOTICE + " (the result's own reply is read: it decides the output routes' dedupe)",
    ("kazma-gateway/kazma_gateway/agent_handler/swarm_dispatch.py", "_dispatch_swarm_from_chat",
     "agent_handler.swarm_dispatch._maybe_send_to_output_target"):
        "the mirror of a result to the operator's output routes: False also means no route "
        "is configured, and every route that refuses is logged at WARNING by "
        "swarm_output._send_via_manager or the direct Telegram send",
    # ── the Web UI ──
    ("kazma-ui/kazma_ui/routes_direct/backup.py", "register_backup_routes._backup_now._run",
     "backup.universal.perform_universal_backup"):
        "the outcome is the progress record's result (_set_progress('done', result=summary)), "
        "which the Settings page polls and shows (settings_ops.js backupResult)",
}


@functools.lru_cache(maxsize=1)
def _scan() -> tuple[list[Finding], frozenset[tuple[str, str]], Index]:
    sources = _product_sources()
    index = build_index(sources)
    found: list[Finding] = []
    used: set[tuple[str, str]] = set()
    for rel in sources:
        found += dropped_results(index, rel, used)
    return found, frozenset(used), index


def _findings() -> list[Finding]:
    return _scan()[0]


def test_the_scope_is_real() -> None:
    """Every SCOPE pattern names product code that answers: a renamed file
    must not quietly empty the gate."""
    index = _scan()[2]
    for pattern in SCOPE:
        modules = {m.name for m in index.modules.values() if fnmatch.fnmatch(m.rel, pattern)}
        assert modules, f"{pattern} matches no product file"
        assert any(module == m for m, _q in index.answering for module in modules), (
            f"{pattern}: no function there answers whether it worked")
    # The functions this gate was built from are in it.
    for target in (("kazma_core.memory.backup", "_backup_one"),
                   ("kazma_gateway.gateway", "GatewayManager.send"),
                   ("kazma_core.backup.cloud_sync", "S3Sync.upload_file"),
                   ("kazma_gateway.adapters.telegram", "TelegramAdapter._send_attachment"),
                   ("kazma_core.cron.scheduler", "CronScheduler._send")):
        assert target in index.answering, target


def test_no_failure_result_is_dropped() -> None:
    findings = _findings()
    open_ = [
        f for f in findings
        if f.callee not in REPORTS_ITS_OWN_FAILURE and f.key not in DECLARED
    ]
    assert open_ == [], (
        "A result that says whether it worked is dropped. Handle the failure "
        "where it happened, or declare the site in DECLARED with why:\n"
        + "\n".join(str(f) for f in open_)
    )


def test_every_declaration_still_matches() -> None:
    findings = _findings()
    keys = {f.key for f in findings}
    callees = {f.callee for f in findings}
    stale: list[object] = [k for k in DECLARED if k not in keys]
    stale += [c for c in REPORTS_ITS_OWN_FAILURE if c not in callees]
    used = _scan()[1]
    stale += [k for k in (*RECEIVERS, *NOT_OURS) if k not in used]
    assert stale == [], f"declared but no longer found (remove them): {stale}"


# ── negative control: the gate sees a dropped result ─────────────────────

_PLANTED_BACKUP = '''
def _backup_one(src, dest) -> bool:
    return True
'''

#: The importer's swap before AUD-003: the safety copy's False was dropped and
#: the live file swapped anyway (the witness this gate is built from).
_ORIGINAL_IMPORTER = '''
from kazma_core.memory.backup import _backup_one


def _back_up_live_dbs(swaps, backup_dir):
    for dest, arc_name in swaps:
        if dest.exists():
            _backup_one(dest, backup_dir / arc_name)
'''

_FIXED_IMPORTER = '''
from kazma_core.memory.backup import _backup_one


def _back_up_live_dbs(swaps, backup_dir):
    for dest, arc_name in swaps:
        if dest.exists():
            if not _backup_one(dest, backup_dir / arc_name):
                raise RuntimeError(dest)
'''

_PLANTED_CALLERS = '''
import asyncio
import kazma_core.memory.backup
from kazma_core.memory import backup as mb
from kazma_core.memory.backup import _backup_one as copy


async def threaded(a, b):
    await asyncio.to_thread(copy, a, b)


def via_module(a, b):
    mb._backup_one(a, b)


def dotted(a, b):
    kazma_core.memory.backup._backup_one(a, b)


def underscore(a, b):
    _ = copy(a, b)


def used(a, b):
    ok = copy(a, b)
    return ok and copy(b, a)


def shadowed(copy, a):
    copy(a)
'''

_PLANTED_GATEWAY = '''
class BaseAdapter:
    async def send(self, outbound) -> bool:
        return True


class GatewayManager:
    async def send(self, outbound) -> bool:
        return True

    async def relay(self, o):
        await self.send(o)
'''

_PLANTED_ADAPTER = '''
from kazma_gateway.gateway import BaseAdapter


class TelegramAdapter(BaseAdapter):
    async def reply(self, o, ws):
        await self.send(o)
        await ws.send("frame")
'''

_PLANTED_HANDLER = '''
async def reply(manager, mgr, o):
    await manager.send(o)
    await mgr.send(o)
    sent = await manager.send(o)
    if not sent:
        raise RuntimeError(o)
'''


def _planted(sources: dict[str, str]) -> list[tuple[str, str, str]]:
    index = build_index(sources)
    found: list[tuple[str, str, str]] = []
    for rel in sources:
        found += [(f.rel.rsplit("/", 1)[-1], f.function, f.callee) for f in dropped_results(index, rel)]
    return sorted(found)


def test_the_original_importer_swap_is_caught() -> None:
    backup = {"kazma-core/kazma_core/memory/backup.py": _PLANTED_BACKUP}
    original = _planted({**backup, "kazma-core/kazma_core/migration/importer.py": _ORIGINAL_IMPORTER})
    fixed = _planted({**backup, "kazma-core/kazma_core/migration/importer.py": _FIXED_IMPORTER})
    assert original == [("importer.py", "_back_up_live_dbs", "memory.backup._backup_one")]
    assert fixed == []


def test_the_gate_sees_every_way_of_dropping_a_result() -> None:
    found = _planted({
        "kazma-core/kazma_core/memory/backup.py": _PLANTED_BACKUP,
        "kazma-core/kazma_core/tools/planted.py": _PLANTED_CALLERS,
        "kazma-gateway/kazma_gateway/gateway.py": _PLANTED_GATEWAY,
        "kazma-gateway/kazma_gateway/adapters/telegram.py": _PLANTED_ADAPTER,
        "kazma-gateway/kazma_gateway/agent_handler/planted.py": _PLANTED_HANDLER,
    })
    assert found == sorted([
        ("planted.py", "threaded", "memory.backup._backup_one"),  # through to_thread
        ("planted.py", "via_module", "memory.backup._backup_one"),  # through a module alias
        ("planted.py", "dotted", "memory.backup._backup_one"),  # a dotted module path
        ("planted.py", "underscore", "memory.backup._backup_one"),  # assigned to _
        ("gateway.py", "GatewayManager.relay", "gateway.GatewayManager.send"),  # self
        ("telegram.py", "TelegramAdapter.reply", "gateway.BaseAdapter.send"),  # self, a base's
        ("planted.py", "reply", "gateway.GatewayManager.send"),  # a classified receiver
        ("planted.py", "reply", "?mgr.send"),  # an unclassified receiver
    ])
    # Not findings: a result assigned, returned or tested; a parameter that
    # shadows the import; ws.send (NOT_OURS).


def test_a_function_out_of_scope_is_not_judged() -> None:
    """Only code in SCOPE answers for a backup or a send: the same function
    elsewhere is somebody else's (control for the index)."""
    found = _planted({
        "kazma-core/kazma_core/tools/elsewhere.py": _PLANTED_BACKUP,
        "kazma-core/kazma_core/migration/importer.py": _ORIGINAL_IMPORTER.replace(
            "kazma_core.memory.backup", "kazma_core.tools.elsewhere"),
    })
    assert found == []


# ── the importer's document restore (fixed with this gate) ───────────────


def _documents_db(path: Path, row: str) -> None:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(path)) as con:
        con.execute("CREATE TABLE d (x TEXT)")
        con.execute("INSERT INTO d VALUES (?)", (row,))
        con.commit()


def _rows(path: Path) -> list[str]:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(path)) as con:
        return [r[0] for r in con.execute("SELECT x FROM d")]


def _restore(tmp_path: Path, monkeypatch, *, staged: bytes | str, backup_path: str) -> tuple[list[str], Path]:
    from kazma_core.migration import importer

    staging = tmp_path / "staging"
    staged_data = staging / "data"
    staged_data.mkdir(parents=True)
    if isinstance(staged, bytes):
        (staged_data / "documents.db").write_bytes(staged)
    else:
        _documents_db(staged_data / "documents.db", staged)
    store = tmp_path / "document-store"
    store.mkdir()
    _documents_db(store / "documents.db", "the target's own document")
    monkeypatch.setattr("kazma_core.documents.backup.installed_document_backends", lambda: ("sqlite", "sqlite"))
    monkeypatch.setattr("kazma_core.documents.config.get_document_storage_root", lambda: store)
    report = importer.ImportReport(backup_path=backup_path)
    failed = importer._restore_documents_reported(staging, staged_data, report, lambda _m: None)
    return failed, store / "documents.db"


def test_a_bundle_catalog_that_cannot_be_copied_leaves_the_live_one(tmp_path, monkeypatch) -> None:
    """The install copy failed and ``_backup_one`` removed its destination --
    the live documents.db -- while the import logged "restored documents.db"
    and reported nothing."""
    backups = tmp_path / "backup"
    backups.mkdir()
    failed, live = _restore(tmp_path, monkeypatch, staged=b"not a database " * 64, backup_path=str(backups))
    assert failed == ["document store"]
    assert live.exists() and _rows(live) == ["the target's own document"]


def test_a_catalog_that_cannot_be_backed_up_is_not_overwritten(tmp_path, monkeypatch) -> None:
    """The safety copy failed (a backup folder that cannot be written) and
    the live catalog was overwritten anyway, with no copy of it anywhere."""
    not_a_folder = tmp_path / "backup-is-a-file"
    not_a_folder.write_text("x", encoding="utf-8")
    failed, live = _restore(tmp_path, monkeypatch, staged="the bundle's document",
                            backup_path=str(not_a_folder))
    assert failed == ["document store"]
    assert _rows(live) == ["the target's own document"]


def test_a_document_restore_that_can_copy_installs_the_bundle(tmp_path, monkeypatch) -> None:
    """Control: the same restore with both copies possible installs the
    bundle's catalog and keeps the target's in the backup."""
    backups = tmp_path / "backup"
    backups.mkdir()
    failed, live = _restore(tmp_path, monkeypatch, staged="the bundle's document", backup_path=str(backups))
    assert failed == []
    assert _rows(live) == ["the bundle's document"]
    assert _rows(backups / "documents.db") == ["the target's own document"]
    assert not live.with_name("documents.db.importing").exists()


# ── the gateway's approval card (fixed with this gate) ───────────────────


async def _surface_a_card(monkeypatch, caplog, *, delivered: bool) -> list[str]:
    import logging
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from kazma_gateway.agent_handler import graph as gw_graph
    from kazma_gateway.agent_handler import hitl
    from kazma_gateway.agent_handler.store import _InMemoryStore
    from kazma_gateway.gateway import IncomingMessage

    tid = f"card-{uuid4().hex[:8]}"
    payload = {"tool": "file_write", "args": {"path": "fixture.txt", "content": "x"}}

    async def initial_state(msg, store):
        return {"messages": [{"role": "user", "content": msg.text}]}

    async def turn(**_kwargs):
        return SimpleNamespace(state={"messages": []}, interrupted=True, interrupt_payload=payload,
                               error=None, text="")

    monkeypatch.setattr(hitl, "_recent_cards", {})
    monkeypatch.setattr(gw_graph, "_build_initial_state", initial_state)
    monkeypatch.setattr(gw_graph, "_rebuild_turn_messages", AsyncMock(return_value=None))
    monkeypatch.setattr(gw_graph, "_sync_platform_session_to_web", lambda *a, **k: None)
    monkeypatch.setattr(gw_graph, "_persist_hitl_pause", AsyncMock())
    monkeypatch.setattr("kazma_core.agent.turn.run_agent_turn", turn)
    monkeypatch.setattr("kazma_ui.hitl_gate_bridge.gate_pending_from_payload", AsyncMock())
    manager = SimpleNamespace(send=AsyncMock(return_value=delivered), adapters=[])
    handler = gw_graph.create_graph_handler(graph=SimpleNamespace(checkpointer=None), manager=manager,
                                            store=_InMemoryStore())
    with caplog.at_level(logging.INFO, logger="kazma_gateway.agent_handler.graph"):
        await handler(IncomingMessage("telegram", f"telegram:{uuid4().hex}", "Write the approved fixture",
                                      context_metadata={"thread_id": tid, "chat_id": 42}))
    manager.send.assert_awaited()
    gw_graph._persist_hitl_pause.assert_awaited()  # paused either way
    return [r.levelname + " " + r.getMessage() for r in caplog.records
            if "surfaced" in r.getMessage() or "NOT delivered" in r.getMessage()]


async def test_an_approval_card_the_platform_refused_is_not_logged_surfaced(monkeypatch, caplog) -> None:
    lines = await _surface_a_card(monkeypatch, caplog, delivered=False)
    assert [ln.split(" ", 1)[0] for ln in lines] == ["WARNING"], lines
    assert "approval card NOT delivered on telegram" in lines[0]


async def test_an_approval_card_the_platform_took_is_logged_surfaced(monkeypatch, caplog) -> None:
    """Control: the same turn with the card delivered."""
    lines = await _surface_a_card(monkeypatch, caplog, delivered=True)
    assert len(lines) == 1 and lines[0].startswith("INFO") and "HITL interrupt surfaced" in lines[0]
