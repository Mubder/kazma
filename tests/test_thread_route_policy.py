"""Every route that takes a thread declares whose threads it may touch.

The 2026-09-22 audit found the same gap in three route families at once. The
approval route checked that the caller's tenant owns the thread; its siblings —
the replay reads and deletes, the chat stop/steer/abort controls, and the
dashboard's list/delete/clear-all — did not, and nothing compared them.

So this gate enumerates every route handler in the web and gateway packages
that takes a ``thread_id`` (path, query or body) and requires an entry in
``POLICY``. A new thread-taking route fails here until someone decides, in
writing, which rule it follows:

``owner``        acts only on a thread the caller's tenant owns — the handler
                 must call the shared check (``kazma_ui.thread_ownership``) or
                 ``get_by_thread_id``. ``tests/test_thread_ownership_routes.py``
                 is the behavioural half for replay and the chat controls.
``admin``        an instance-wide view; the handler must require admin.
``admin+owner``  both: admin-only, and still filtered to owned threads.
``session``      the thread comes from the caller's own tenant-scoped session
                 and nowhere else. Not checkable by AST, so the reason is the
                 review, and it is mandatory.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "api_route", "websocket"}
_OWNER_CHECKS = {
    "require_thread_owned",
    "_require_thread_owned",
    "resolve_caller_thread",
    "owned_threads",
    "owned_threads_async",
    "get_by_thread_id",
}
_ADMIN_CHECKS = {"require_admin", "admin_decision", "_is_caller_admin", "_require_admin"}

SSE = "kazma-ui/kazma_ui/sse_chat/__init__.py"
REPLAY = "kazma-ui/kazma_ui/replay_routes.py"
MISC = "kazma-ui/kazma_ui/routes_direct/misc.py"
DASH = "kazma-ui/kazma_ui/dashboard.py"

POLICY: dict[tuple[str, str, str], tuple[str, str]] = {
    # Replay: reads and rewrites conversation state.
    ("GET", "/api/replay/snapshots/{thread_id}", REPLAY): ("owner", ""),
    ("GET", "/api/replay/snapshots/{thread_id}/{iteration}", REPLAY): ("owner", ""),
    ("POST", "/api/replay/restore", REPLAY): ("owner", ""),
    ("POST", "/api/replay/fork", REPLAY): ("owner", ""),
    ("POST", "/api/replay/compare", REPLAY): ("owner", ""),
    ("DELETE", "/api/replay/threads/{thread_id}", REPLAY): ("owner", ""),
    # Chat controls act on the caller's own running turn.
    ("POST", "/api/chat/stop", SSE): ("owner", ""),
    ("POST", "/api/chat/steer", SSE): ("owner", ""),
    ("POST", "/api/chat/abort", SSE): ("owner", ""),
    ("GET", "/api/chat/capacity", SSE): ("owner", ""),
    ("GET", "/api/chat/sessions/{session_id}/messages", SSE): ("owner", ""),
    # HITL approvals.
    ("POST", "/api/approve/{thread_id}", MISC): ("owner", ""),
    ("GET", "/api/pending-approvals", MISC): ("owner", ""),
    (
        "GET",
        "/api/pending-approvals",
        "kazma-ui/kazma_ui/hitl_approval.py",
    ): ("owner", "test-only factory, not mounted; filtered the same way"),
    ("POST", "/api/pending-approvals/clear", MISC): ("admin+owner", ""),
    ("DELETE", "/api/pending-approvals", MISC): ("admin+owner", ""),
    # Dashboard: the instance's whole checkpoint store.
    ("GET", "/api/sessions", DASH): ("admin", ""),
    ("DELETE", "/api/sessions/{thread_id}", DASH): ("admin", ""),
    # Thread resolved from the caller's own session.
    ("POST", "/api/chat/stream", SSE): (
        "session",
        "_resolve_session loads or creates the session inside the caller's "
        "tenant; the thread id comes from that session, never from the body",
    ),
    ("GET", "/api/chat/sessions/{session_id}/status", SSE): (
        "session",
        "reads the thread from the caller's tenant-scoped session; an unknown "
        "session yields no thread",
    ),
    ("DELETE", "/api/chat/sessions/{session_id}", SSE): (
        "session",
        "looks the session up in the caller's tenant and deletes its thread's "
        "checkpoints only when that lookup succeeds",
    ),
    ("WEBSOCKET", "/ws/chat/{session_id}", "kazma-ui/kazma_ui/routes/ws_chat.py"): (
        "session",
        "the socket's session is the caller's; approve_tool honours a requested "
        "thread id only when it equals that session's own thread",
    ),
}


def _route_paths(fn: ast.AST) -> list[tuple[str, str]]:
    out = []
    for dec in getattr(fn, "decorator_list", []):
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in _ROUTE_METHODS
            and dec.args
            and isinstance(dec.args[0], ast.Constant)
            and isinstance(dec.args[0].value, str)
        ):
            out.append((dec.func.attr.upper(), dec.args[0].value))
    return out


def _takes_a_thread(fn: ast.AST, paths: list[tuple[str, str]]) -> bool:
    args = fn.args  # type: ignore[attr-defined]
    params = {a.arg for a in (*args.args, *args.kwonlyargs)}
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    strings = {n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    return (
        "thread_id" in params
        or "thread_id" in names
        or "thread_id" in strings
        or any("{thread_id}" in p for _, p in paths)
    )


def _referenced(fn: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)
    }


def thread_routes(sources: dict[str, str]) -> dict[tuple[str, str, str], set[str]]:
    """``(METHOD, path, file) -> names the handler references``."""
    found: dict[tuple[str, str, str], set[str]] = {}
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            paths = _route_paths(fn)
            if paths and _takes_a_thread(fn, paths):
                for method, path in paths:
                    found[(method, path, rel)] = _referenced(fn)
    return found


def policy_violations(
    found: dict[tuple[str, str, str], set[str]],
    policy: dict[tuple[str, str, str], tuple[str, str]],
) -> list[str]:
    problems: list[str] = []
    for key, refs in sorted(found.items()):
        if key not in policy:
            problems.append(f"undeclared: {' '.join(key)}")
            continue
        rule, reason = policy[key]
        if "owner" in rule and not refs & _OWNER_CHECKS:
            problems.append(f"declared {rule} but never checks ownership: {' '.join(key)}")
        if "admin" in rule and not refs & _ADMIN_CHECKS:
            problems.append(f"declared {rule} but never requires admin: {' '.join(key)}")
        if rule == "session" and len(reason) < 40:
            problems.append(f"'session' needs the reviewed reason: {' '.join(key)}")
        if rule not in {"owner", "admin", "admin+owner", "session"}:
            problems.append(f"unknown rule {rule!r}: {' '.join(key)}")
    for key in sorted(set(policy) - set(found)):
        problems.append(f"stale entry (route gone or no longer takes a thread): {' '.join(key)}")
    return problems


def _product_sources() -> dict[str, str]:
    files = subprocess.run(
        ["git", "ls-files", "kazma-ui/*.py", "kazma-gateway/*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return {
        rel: (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for rel in files
        if "_tests" not in rel and "/tests/" not in rel and (REPO_ROOT / rel).is_file()
    }


def test_every_thread_route_declares_and_follows_its_policy():
    problems = policy_violations(thread_routes(_product_sources()), POLICY)
    assert not problems, (
        "A route that takes a thread must say whose threads it may touch "
        "(see this module's docstring), and do it.\n  " + "\n  ".join(problems)
    )


def test_policy_gate_catches_an_unchecked_thread_route():
    """Negative control (§28): the pre-fix replay read and an undeclared route."""
    source = '''
@router.get("/api/replay/snapshots/{thread_id}")
async def list_snapshots(thread_id: str):
    return recorder.list_snapshots(thread_id)

@router.post("/api/new/thing")
async def new_thing(body: dict):
    return act_on(body.get("thread_id"))

@router.get("/api/owned/{thread_id}")
async def owned(thread_id: str):
    if (denied := await require_thread_owned(thread_id)) is not None:
        return denied
'''
    found = thread_routes({"x.py": source})
    policy = {
        ("GET", "/api/replay/snapshots/{thread_id}", "x.py"): ("owner", ""),
        ("GET", "/api/owned/{thread_id}", "x.py"): ("owner", ""),
        ("GET", "/api/removed", "x.py"): ("owner", ""),
    }
    assert policy_violations(found, policy) == [
        "declared owner but never checks ownership: GET /api/replay/snapshots/{thread_id} x.py",
        "undeclared: POST /api/new/thing x.py",
        "stale entry (route gone or no longer takes a thread): GET /api/removed x.py",
    ]
