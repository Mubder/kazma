"""The matrix cases of Spaces S1-09: memory (``tests/_space_matrix.py``).

Memory already has a gate that calls every memory route as one tenant with
another tenant's ids (``tests/test_memory_routes_tenant_scope.py``): its
requests, its two-tenant seed and its checks are reused here. What a case
adds is the rest of the path: Spaces enforced, a signed-in person who is a
member of the space ``alpha``, the real tenant middleware checking that on
every call, and the space named by the page's header. The seed's two tenants
are the spaces ``alpha`` (person A's) and ``beta`` (person B's); everything
of beta's carries the gate's marker.

Every case asserts that nothing of beta is shown or changed, and that
alpha's own rows are shown where the route shows rows.
"""

from __future__ import annotations

import io
import json
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import identity, scope
from kazma_ui import auth, space_binding

from tests import test_memory_routes_tenant_scope as gate
from tests._chat_space_cases import _boundary
from tests._space_matrix import Matrix, case
from tests._tool_answer import answer_now

ITEM = "S1-09"
SPACE_A, SPACE_B = "alpha", "beta"  # the gate's seed names its two tenants so


class _AsMember:
    """The test client, sending every request as a page of one space does."""

    def __init__(self, client: TestClient, space: str) -> None:
        self._client = client
        self._headers = {space_binding.SPACE_HEADER: space}

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {**self._headers, **(kwargs.pop("headers", None) or {})}
        return self._client.request(method, path, headers=headers, **kwargs)

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)


def _two_spaces(matrix: Matrix) -> None:
    for name, space_id, account in (("Alpha", SPACE_A, matrix.account_a), ("Beta", SPACE_B, matrix.account_b)):
        if not identity.is_member(account, space_id):
            identity.create_space(name, created_by=account, space_id=space_id)


@contextmanager
def memory_world(matrix: Matrix, *, who: str = "a", space: str = SPACE_A,
                 enforced: bool = True) -> Iterator[SimpleNamespace]:
    """The memory routes as the app registers them, behind the real tenant
    middleware, called by person *who* from a page of *space*. The value is
    what the reused gate's checks take as their context."""
    from kazma_ui.history_import_api import create_history_import_router
    from kazma_ui.memory_api import mount_memory_api, register_memory_page
    from kazma_ui.routes_direct import register_direct_routes

    from kazma_core.memory import dual_write

    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(
        ignore_cleanup_errors=True
    ) as tmp:
        _two_spaces(matrix)
        # The episode writer is one per process and keeps its database: it
        # must open this world's, and leave none behind for the next test.
        dual_write._reset_mirror()
        patch.setattr(scope, "_enforced", enforced)
        patch.delenv("KAZMA_MEMORY_STATE_DB", raising=False)
        patch.delenv("KAZMA_MEMORY_OPS_DB", raising=False)
        patch.delenv("KAZMA_MEMORY_ENFORCE_TENANT", raising=False)
        patch.setenv("KAZMA_DATA_DIR", tmp)
        patch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
        patch.setattr("kazma_ui.auth.admin_decision", lambda request: "forbidden")
        principal = matrix.principal(who)
        patch.setattr(auth, "get_request_principal", lambda request: principal)
        app = FastAPI()
        app.middleware("http")(auth.create_tenant_middleware())
        register_direct_routes(SimpleNamespace(app=app))
        mount_memory_api(app)
        register_memory_page(app, SimpleNamespace(), None)
        app.include_router(create_history_import_router())
        client = TestClient(app, raise_server_exceptions=False)
        try:
            yield SimpleNamespace(
                client=_AsMember(client, space), app=app, tmp=Path(tmp), monkeypatch=patch,
                on_loop=[], who={"tenant": space}, matrix=matrix,
            )
        finally:
            dual_write._reset_mirror()


# ── every memory route that takes or shows rows ──────────────────────────


def _route_case(route: tuple[str, str]) -> Callable[[Matrix], None]:
    def run(matrix: Matrix) -> None:
        with memory_world(matrix) as ctx:
            assert gate._problems(ctx, route) == []

    run.__name__ = f"memory_{route[0].lower()}_{route[1].strip('/').replace('/', '_')}"
    return run


for _route in sorted(gate.REQUESTS):
    case(f"{_route[0]} {_route[1]}", ITEM)(_route_case(_route))

# Routes that hold no rows (the page shell, three retired addresses): their
# space is the middleware's, and a request for another's never reaches them.
for _route in sorted(gate.NO_DATA):
    case(f"{_route[0]} {_route[1]}", ITEM)(_boundary(_route[0], _route[1]))


# ── bringing a history in ────────────────────────────────────────────────


def _export(mark: str) -> bytes:
    """A small ChatGPT export whose one turn holds *mark*."""
    def node(nid: str, parent: str | None, role: str, text: str, at: float, children: list[str]) -> dict:
        return {"id": nid, "parent": parent, "children": children, "message": {
            "id": nid, "author": {"role": role}, "create_time": at, "recipient": "all",
            "content": {"content_type": "text", "parts": [text]}, "metadata": {}}}

    t0 = 1_700_000_000.0
    mapping = {
        "root": {"id": "root", "message": None, "parent": None, "children": ["u1"]},
        "u1": node("u1", "root", "user", f"a question about {mark}", t0 + 10, ["a1"]),
        "a1": node("a1", "u1", "assistant", f"an answer about {mark}", t0 + 20, []),
    }
    held = io.BytesIO()
    with zipfile.ZipFile(held, "w") as archive:
        archive.writestr("conversations.json", json.dumps([{
            "title": f"chat {mark}", "create_time": t0, "conversation_id": f"c-{mark}",
            "current_node": "a1", "mapping": mapping}]))
        archive.writestr("user.json", "{}")
    return held.getvalue()


def _wait(client: Any) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    status = client.get("/api/import/history/status").json()
    while status["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
        status = client.get("/api/import/history/status").json()
    return status


def _tenants_holding(root: Path, mark: str) -> set[str]:
    import sqlite3

    conn = sqlite3.connect(root / "memory_state.db")
    try:
        return {str(r[0]) for r in conn.execute(
            "SELECT DISTINCT tenant_id FROM episodes WHERE user_text LIKE ?", (f"%{mark}%",))}
    finally:
        conn.close()


def _import_case(check: Callable[[Any, Any, Path, Any], None]) -> Callable[[Matrix], None]:
    """Person B uploads an export in beta; *check* then acts as person A."""

    def run(matrix: Matrix) -> None:
        from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
        from kazma_ui import history_import_api

        marks = matrix.plant("import")
        with memory_world(matrix, who="b", space=SPACE_B) as theirs:
            root = theirs.tmp
            import sqlite3

            for name, ensure in (("memory_state.db", ensure_primary_schema), ("memory_ops.db", ensure_ops_schema)):
                made = sqlite3.connect(root / name)
                ensure(made)
                made.close()
            theirs.monkeypatch.setattr(history_import_api, "_JOBS", {})
            theirs.monkeypatch.setattr(history_import_api, "_UPLOADS", {})
            uploaded = theirs.client.post("/api/import/history/preview", content=_export(marks.b))
            assert uploaded.status_code == 200, uploaded.text
            token = uploaded.json()["token"]
            # The same app and stores, now called by person A from alpha.
            principal = matrix.principal("a")
            theirs.monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)
            mine = _AsMember(theirs.client._client, SPACE_A)
            check(mine, theirs.client, root, SimpleNamespace(token=token, marks=marks, matrix=matrix,
                                                              patch=theirs.monkeypatch))

    return run


def _as_b(ctx: Any) -> None:
    principal = ctx.matrix.principal("b")
    ctx.patch.setattr(auth, "get_request_principal", lambda request: principal)


def _as_a(ctx: Any) -> None:
    principal = ctx.matrix.principal("a")
    ctx.patch.setattr(auth, "get_request_principal", lambda request: principal)


def _import_start(mine: Any, theirs: Any, root: Path, ctx: Any) -> None:
    # B's upload is no upload in alpha: it cannot be started from there.
    refused = mine.post("/api/import/history/start", json={"token": ctx.token})
    assert refused.status_code == 404 and refused.json()["code"] == "unknown_upload"
    assert _tenants_holding(root, ctx.marks.b) == set()
    # B starts it: the turns become memories of beta, and of beta only.
    _as_b(ctx)
    assert theirs.post("/api/import/history/start", json={"token": ctx.token}).status_code == 200
    assert _wait(theirs)["state"] == "done"
    assert _tenants_holding(root, ctx.marks.b) == {SPACE_B}


def _import_status(mine: Any, theirs: Any, root: Path, ctx: Any) -> None:
    _as_b(ctx)
    assert theirs.post("/api/import/history/start", json={"token": ctx.token}).status_code == 200
    done = _wait(theirs)
    assert done["state"] == "done" and done["summary"] and done["report"]["written"] == 1
    # A sees nothing of B's import: no summary, no report, no count.
    _as_a(ctx)
    seen = mine.get("/api/import/history/status")
    assert seen.json()["state"] == "idle" and ctx.marks.b not in seen.text
    assert seen.json()["summary"] is None and seen.json()["report"] is None


def _import_stop(mine: Any, theirs: Any, root: Path, ctx: Any) -> None:
    from kazma_ui import history_import_api

    _as_b(ctx)
    assert theirs.get("/api/import/history/status").status_code == 200  # beta's job exists
    their_job = history_import_api._job(SPACE_B)
    _as_a(ctx)
    assert mine.post("/api/import/history/stop").json() == {"ok": True}
    # A's stop is for A's own import: B's is not told to stop.
    assert not their_job.stop.is_set()
    _as_b(ctx)
    assert theirs.post("/api/import/history/stop").json() == {"ok": True}
    assert their_job.stop.is_set()


def _import_preview(mine: Any, theirs: Any, root: Path, ctx: Any) -> None:
    # A's own upload is A's: its token starts an import into alpha.
    own = ctx.matrix.plant("own")
    _as_a(ctx)
    uploaded = mine.post("/api/import/history/preview", content=_export(own.a))
    assert uploaded.status_code == 200 and ctx.marks.b not in uploaded.text
    token = uploaded.json()["token"]
    # ... and B cannot start it.
    _as_b(ctx)
    assert theirs.post("/api/import/history/start", json={"token": token}).status_code == 404
    _as_a(ctx)
    assert mine.post("/api/import/history/start", json={"token": token}).status_code == 200
    assert _wait(mine)["state"] == "done"
    assert _tenants_holding(root, own.a) == {SPACE_A}
    assert _tenants_holding(root, ctx.marks.b) == set()


case("POST /api/import/history/preview", ITEM)(_import_case(_import_preview))
case("POST /api/import/history/start", ITEM)(_import_case(_import_start))
case("GET /api/import/history/status", ITEM)(_import_case(_import_status))
case("POST /api/import/history/stop", ITEM)(_import_case(_import_stop))


# ── the agent's memory tools ─────────────────────────────────────────────

#: For each tool: calls that name beta's ids or beta's words. The gate's own
#: calls (``TOOL_CALLS``: alpha's rows) run too.
THEIR_IDS: dict[str, list[dict[str, Any]]] = {
    "memory_search": [{"query": "BETA9 Berlin Bob"}],
    "memory_read": [{"id": "beta9_e0"}],
    "memory_admin": [
        {"action": "list_beliefs", "q": "BETA9"},
        {"action": "list_entities", "q": "BETA9"},
        {"action": "invalidate", "id": "beta9_lives"},
        {"action": "delete_entity", "id": "beta9_shell"},
        {"action": "purge_empty_entities"},
        {"action": "merge", "id": "beta9_shell", "target": "beta9_bob"},
    ],
    "memory_merge_entities": [{"source_id": "beta9_shell", "target_id": "beta9_bob"}],
    "memory_link_entities": [{"subject": "alice", "object": "Carol"}],
    "memory_list_beliefs": [{"q": "BETA9"}, {"q": "Berlin"}],
    "memory_invalidate": [{"belief_id": "beta9_lives"}, {"belief_id": "beta9_edge"}],
    "memory_forget_mentions": [{"text": "BETA9 Berlin"}],
    "memory_list_entities": [{"q": "BETA9"}, {"q": "Bob"}],
    "memory_delete_entity": [{"entity_id": "beta9_bob"}, {"entity_id": "beta9_shell"}],
    "memory_purge_empty_entities": [{"confirm": True}],
    "memory_store": [{"text": "Alice likes green tea"}],
}


def _sent_values(kwargs: dict[str, Any]) -> list[str]:
    return [str(v) for v in kwargs.values() if isinstance(v, str) and gate.MARK in v.lower()]


def _tool_case(name: str) -> Callable[[Matrix], None]:
    def run(matrix: Matrix) -> None:
        with memory_world(matrix) as ctx:
            tool = gate._memory_tools()[name]
            for n, kwargs in enumerate([*gate.TOOL_CALLS[name], *THEIR_IDS[name]]):
                root = gate._fresh(ctx, f"tool{n}")
                before = gate._beta_rows(root)
                with scope.bind_scope(SPACE_A, matrix.account_a):
                    answer = str(answer_now(tool, **kwargs))
                assert gate._beta_rows(root) == before, f"{name}({kwargs}) changed space beta's rows"
                shown = answer
                for echoed in _sent_values(kwargs):
                    shown = shown.replace(echoed, "")
                assert gate.MARK not in shown.lower(), f"{name}({kwargs}) showed space beta's data: {answer[:300]}"

    run.__name__ = f"tool_{name}"
    return run


for _name in sorted(THEIR_IDS):
    case(f"tool:{_name}", ITEM)(_tool_case(_name))
