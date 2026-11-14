"""A web session is named by the start of its hash, never by its cookie.

The ``kazma-session`` cookie's value is the sign-in credential. Four actor
labels -- the Memory page's trail, the shared ``web_actor``, Time Travel's
audit and the approve route -- were ``web:<role>:`` plus its first eight
characters, written to kazma.log and the audit tables (CodeQL
py/clear-text-logging-sensitive-data, 2026-11-14). ``actor_for_session`` is the
one builder now, naming a session as Settings lists it
(``web_sessions.session_label``).
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

from kazma_core.security import web_sessions as ws

REPO = Path(__file__).resolve().parents[1]
COOKIE = "Qz8vLong-Bearer-Session-Value-0123456789abcdef"


def _shares_a_run_of_the_cookie(actor: str, run: int = 6) -> bool:
    return any(COOKIE[i:i + run] in actor for i in range(len(COOKIE) - run + 1))


def _admin(monkeypatch):
    monkeypatch.setattr(ws, "get_session_payload", lambda sid: {"role": "admin"} if sid else None)


def test_web_actor_names_the_session_by_its_hash(monkeypatch):
    from kazma_ui.web_actor import web_actor

    _admin(monkeypatch)
    request = SimpleNamespace(cookies={ws.SESSION_COOKIE: COOKIE}, headers={})
    actor = web_actor(request)
    assert actor == f"web:admin:{ws.session_label(COOKIE)}"
    assert not _shares_a_run_of_the_cookie(actor)


def test_the_memory_trail_names_the_session_by_its_hash(monkeypatch):
    from kazma_ui import memory_actions

    _admin(monkeypatch)
    headers = [(b"cookie", f"{ws.SESSION_COOKIE}={COOKIE}".encode("latin-1"))]
    actor, tenant = memory_actions._actor_and_tenant(headers)
    assert actor == f"web:admin:{ws.session_label(COOKIE)}" and tenant == "default"
    assert not _shares_a_run_of_the_cookie(actor)


def test_the_approve_route_names_the_session_by_its_hash(monkeypatch):
    from kazma_ui.routes_direct import misc

    _admin(monkeypatch)
    request = SimpleNamespace(cookies={ws.SESSION_COOKIE: COOKIE})
    actor = asyncio.run(misc._request_actor(request))
    assert actor == f"web:admin:{ws.session_label(COOKIE)}"
    assert not _shares_a_run_of_the_cookie(actor)


def test_the_label_is_the_id_settings_lists():
    raw = ws.create_session(role="admin", username="owner")
    try:
        listed = ws.list_sessions(username="owner", current=raw)
        assert ws.session_label(raw) in {row["id"] for row in listed}
    finally:
        ws.revoke_session(raw)


def test_control_the_old_label_was_the_cookie():
    old = f"web:admin:{str({}.get('session_id') or COOKIE)[:8]}"
    assert _shares_a_run_of_the_cookie(old)


def _actor_fstrings(tree: ast.AST):
    """Every f-string that starts ``web:`` and interpolates ``role``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr) or not node.values:
            continue
        head = node.values[0]
        if not (isinstance(head, ast.Constant) and str(head.value).startswith("web:")):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        if "role" in names:
            yield node.lineno


def test_one_builder_names_web_sessions():
    builders = []
    for package in ("kazma-ui", "kazma-core", "kazma-gateway", "kazma-cli", "kazma-skills"):
        for path in (REPO / package).rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            builders += [f"{path.relative_to(REPO).as_posix()}:{line}" for line in _actor_fstrings(tree)]
    assert [b.split(":")[0] for b in builders] == ["kazma-ui/kazma_ui/web_actor.py"], builders


def test_control_the_gate_sees_an_old_builder():
    planted = ast.parse("def f(role, sid):\n    return f\"web:{role}:{sid[:8]}\"\n")
    assert list(_actor_fstrings(planted)) == [2]
