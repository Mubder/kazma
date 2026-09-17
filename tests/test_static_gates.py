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


def test_security_env_vars_are_documented():
    """Every security-weakening ``KAZMA_*`` switch appears in .env.example.

    The 2026-09-16 audit found the code reading 272 ``KAZMA_*`` variables
    while ``.env.example`` documented 43 — and none of the sixteen that turn a
    safety default off. A switch nobody can discover is a switch nobody can
    audit, including the operator who set it two years ago.
    """
    documented = (REPO_ROOT / ".env.example").read_text(encoding="utf-8", errors="replace")
    found: set[str] = set()
    for path in _product_files():
        src = path.read_text(encoding="utf-8", errors="replace")
        for name in re.findall(r"KAZMA_[A-Z0-9_]+", src):
            if any(marker in name for marker in SECURITY_ENV_MARKERS):
                found.add(name)

    missing = sorted(n for n in found if n not in documented)
    assert not missing, (
        "Security-relevant env var read by the code but absent from "
        ".env.example (audit 2026-09-16 F-8). Each of these weakens a "
        "default; document it with what it turns off and why you would.\n  "
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
