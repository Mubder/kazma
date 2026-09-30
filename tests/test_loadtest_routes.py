"""The load tests call routes that exist, with bodies the routes read.

The locustfiles and k6 script were written in July against an API that did
not exist: 8 paths 404ed (``/api/session/create``, ``/api/swarm/status/{id}``,
``/ws/swarm/{id}``, ``/api/approve/pending``...), every dispatch sent
``prompt`` where the route reads ``task`` (a 400 on every call), and the HITL
flows read response keys no route returns. So the load tests measured error
paths and nobody could tell (audit AUD-010, 2026-09-30).

This gate enumerates every path the load tests call and matches it against
the real app's route table, and checks the request bodies (built once, in
``loadtests/kazma_api.py``) against the keys the routes actually read — all
without dispatching anything.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LOADTESTS = REPO / "loadtests"
_P = "{p}"


def _load_kazma_api():
    spec = importlib.util.spec_from_file_location("kazma_api", LOADTESTS / "kazma_api.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ── the load tests' calls ────────────────────────────────────────────────

def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else _P for v in node.values)
    return None


def _python_calls(source: str) -> list[tuple[str, str]]:
    """(METHOD, path) for every ``<client>.get/post/.../connect(path, ...)``."""
    tree = ast.parse(source)
    assigned = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            lit = _literal(node.value)
            if lit and lit.startswith("/"):
                assigned[node.targets[0].id] = lit
    calls = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args):
            continue
        method = node.func.attr.upper()
        if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "CONNECT"}:
            continue
        arg = node.args[0]
        path = _literal(arg) or (assigned.get(arg.id) if isinstance(arg, ast.Name) else None)
        if path and path.startswith("/"):
            calls.append(("WS" if method == "CONNECT" else method, path.split("?")[0]))
    return calls


_JS_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\\])//[^\n]*", re.S)
_JS_PATH = re.compile(r"(/(?:api|ws|health)(?:/[^`'\"\s?)]*)?)")


def _k6_paths(source: str) -> list[str]:
    """Every route-shaped literal in the script (comments stripped)."""
    code = _JS_COMMENT.sub("", source)
    return [re.sub(r"\$\{[^}]+\}", _P, m.group(1)) for m in _JS_PATH.finditer(code)]


def _all_calls() -> list[tuple[str, str, str]]:
    out = []
    for f in sorted(LOADTESTS.glob("locustfile_*.py")):
        out += [(f.name, m, p) for m, p in _python_calls(f.read_text(encoding="utf-8"))]
    for f in sorted(LOADTESTS.glob("*.js")):
        out += [(f.name, "ANY", p) for p in _k6_paths(f.read_text(encoding="utf-8"))]
    return out


# ── the real app's routes ────────────────────────────────────────────────

def _all_routes(routes) -> list:
    out = []
    for route in routes:
        inner = getattr(route, "original_router", None)
        out.extend(_all_routes(inner.routes) if inner is not None else [route])
    return out


@pytest.fixture(scope="module")
def route_table() -> list[tuple[frozenset[str], re.Pattern]]:
    from kazma_ui.app import create_app

    table = []
    for route in _all_routes(create_app().routes):
        path = getattr(route, "path", None)
        if not path:
            continue
        methods = frozenset(getattr(route, "methods", None) or {"WS"})
        rx = re.compile("^" + re.sub(r"\\\{[^/]+?\\\}", "[^/]+", re.escape(path)) + "$")
        table.append((methods, rx))
    return table


def _exists(table, method: str, path: str) -> bool:
    probe = path.replace(_P, "x1")
    return any(
        rx.match(probe) and (method == "ANY" or method in methods)
        for methods, rx in table
    )


def test_the_enumeration_is_not_blind() -> None:
    calls = _all_calls()
    files = {f for f, _, _ in calls}
    assert {"locustfile_swarm.py", "locustfile_websocket.py", "k6_swarm.js"} <= files
    assert ("POST", "/api/swarm/dispatch") in {(m, p) for _, m, p in calls}
    assert ("WS", "/ws/dashboard") in {(m, p) for _, m, p in calls}


def test_every_route_a_load_test_calls_exists(route_table) -> None:
    dead = sorted({c for c in _all_calls() if not _exists(route_table, c[1], c[2])})
    assert not dead, (
        "Load tests call routes the app does not serve (they would measure "
        f"404/405s): {dead}"
    )


def test_the_route_matcher_rejects_dead_paths(route_table) -> None:
    """Negative control: the paths the load tests used to call are refused."""
    for method, path in (
        ("POST", "/api/session/create"),
        ("GET", f"/api/swarm/status/{_P}"),
        ("WS", f"/ws/swarm/{_P}"),
        ("GET", "/api/approve/pending"),
        ("GET", f"/api/approve/{_P}/status"),
        ("GET", "/api/config"),
    ):
        assert not _exists(route_table, method, path), (method, path)


def test_k6_scan_sees_a_path_in_a_const() -> None:
    """Negative control for the k6 scanner: a socket URL built into a const
    (how the dead /ws/swarm path hid from a call-only scan) is found."""
    src = "const wsUrl = `${WS_URL}/ws/swarm/loadtest-${__VU}`;\nws.connect(wsUrl);"
    assert f"/ws/swarm/loadtest-{_P}" in _k6_paths(src)


# ── the request bodies against what the routes read ─────────────────────

def _dispatch_keys_read() -> set[str]:
    """Every key the dispatch module reads from its payload (the route body
    and its ``_coerce_*`` helpers)."""
    from kazma_ui.swarm_panel import routes_tasks

    return set(re.findall(r'payload\.get\("([a-z_]+)"', inspect.getsource(routes_tasks)))


def test_dispatch_body_uses_only_keys_the_route_reads() -> None:
    api = _load_kazma_api()
    read = _dispatch_keys_read()
    assert "task" in read, "the dispatch route no longer reads `task` — update kazma_api"
    for pattern in api.DISPATCH_PATTERNS:
        body = api.dispatch_body("x", pattern=pattern)
        assert "task" in body and body["task"]
        unread = set(body) - read
        assert not unread, f"dispatch body sends keys the route ignores: {unread}"


def test_the_old_dispatch_body_would_be_caught() -> None:
    """Negative control: the July body sent `prompt` / `task_type`, which the
    route never reads, and no `task` — exactly what this gate refuses."""
    read = _dispatch_keys_read()
    old = {"prompt": "x", "workers": ["coder"], "task_type": "SWARM"}
    assert "task" not in old
    assert set(old) - read == {"prompt", "task_type"}


def test_dispatch_patterns_map_to_their_task_types() -> None:
    """Each pattern the load tests send selects its own task type — not the
    fallback the route uses for an unrecognised value."""
    from kazma_ui.swarm_panel import routes_tasks

    TaskType = routes_tasks.TaskType
    if TaskType is None:
        pytest.skip("swarm core not importable")
    api = _load_kazma_api()
    expected = {"dispatch": "DISPATCH", "fan_out": "FAN_OUT", "broadcast": "BROADCAST"}
    assert set(api.DISPATCH_PATTERNS) == set(expected)
    for pattern, name in expected.items():
        body = api.dispatch_body("x", pattern=pattern)
        got = routes_tasks._coerce_task_type(body, body["workers"])
        assert got == getattr(TaskType, name), (pattern, got)


def test_k6_dispatch_bodies_send_task_not_prompt() -> None:
    src = _JS_COMMENT.sub("", (LOADTESTS / "k6_swarm.js").read_text(encoding="utf-8"))
    bodies = re.findall(r"const payload = \{(.*?)\};", src, re.S)
    assert bodies, "no dispatch payload found in k6_swarm.js"
    for body in bodies:
        assert re.search(r"\btask\s*:", body), body
        assert not re.search(r"\bprompt\s*:", body), body


def test_chat_body_uses_only_keys_the_chat_route_reads() -> None:
    api = _load_kazma_api()
    src = inspect.getsource(__import__("kazma_ui.sse_chat", fromlist=["x"]))
    read = set(re.findall(r'body\.get\("([a-z_]+)"', src))
    body = api.chat_body("hello", "s1")
    assert {"message", "session_id"} <= read
    assert not set(body) - read, set(body) - read
