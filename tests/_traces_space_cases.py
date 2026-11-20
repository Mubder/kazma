"""The matrix cases of Spaces S1-22: the record of each tool and model call.

Every case runs with Spaces enforced, as person A in ``matrix-a`` against
person B's ``matrix-b``. The trace feed -- the Agents page and its API, and
the Dashboard's live socket -- holds a turn's tool and model calls, the start
of its prompt in each model call's details. A trace of each space is planted
through the product's trace store, in that space's own scope; A is shown A's
traces and totals and never B's.

Nothing here builds the app or decides the vault while pytest imports it.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import scope
from kazma_ui import auth, space_binding

from tests._space_matrix import Matrix, case

ITEM = "S1-22"


class _PageData:
    """Stands in for the page templates: the answer is the context the
    router hands its template."""

    def TemplateResponse(self, request: Any, name: str, context: dict[str, Any]) -> Any:  # noqa: N802
        from fastapi.responses import JSONResponse

        return JSONResponse({"template": name, "last_activity": context.get("last_activity"),
                             "metrics": context.get("metrics")})


@contextmanager
def traces(matrix: Matrix) -> Iterator[SimpleNamespace]:
    """Spaces enforced; a private trace store holding a tool call and a
    model call of each space, each added in its own space's scope; the
    Agents router and the Dashboard socket behind the real tenant middleware."""
    import kazma_core.tracing as tracing
    from kazma_core.tracing import TraceEntry, TraceStore

    from kazma_ui.agents import create_agents_router
    from kazma_ui.routes_direct import misc

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    marks = matrix.plant("trace")
    store = TraceStore()

    def entry(kind: str, mark: str, tokens: int) -> TraceEntry:
        return TraceEntry(timestamp=time.time(), trace_type=kind, label=f"{kind} {mark}", status="success",
                          duration_ms=1.0, tokens=tokens, cost=0.0, details=f"prompt {mark}")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "_enforced", True)
        patch.setattr(tracing, "_trace_store", store)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))
        patch.setattr(auth, "websocket_principal", lambda ws: people.get(ws.query_params.get("person", "")))
        with matrix.as_b():
            store.add(entry("tool", marks.b, 0))
            store.add(entry("llm", marks.b, 900))
        with matrix.as_a():
            store.add(entry("tool", marks.a, 0))
            store.add(entry("llm", marks.a, 7))
        app = FastAPI()
        Matrix.role_checked(app)
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_agents_router(SimpleNamespace(), _PageData()))
        holder = SimpleNamespace(app=app, templates=None, agent=None, gateway=None, chat_adapters=None,
                                 _init_errors=[], session_store=None, _graph_holder={}, _hitl_state={})
        misc.register_misc_routes(holder)
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, store=store, marks=marks, entry=entry)


def _as(who: str) -> dict[str, str]:
    space = Matrix.SPACE_A if who == "a" else Matrix.SPACE_B
    return {"x-matrix-person": who, space_binding.SPACE_HEADER: space}


def _metrics(path: str, body: dict[str, Any]) -> dict[str, Any] | None:
    """The totals a route answers, or None for one that answers none."""
    if path == "/api/agents":
        return body["agents"][0]["metrics"]
    if path in ("/agents", "/api/agents/status"):
        return body["metrics"]
    return None


def _route(path: str) -> None:
    def run(matrix: Matrix) -> None:
        with traces(matrix) as w:
            got = w.client.get(path, headers=_as("a"))
        assert got.status_code == 200, got.text
        assert w.marks.b not in got.text, (path, got.text[:400])
        assert w.marks.a in got.text, (path, got.text[:400])
        metrics = _metrics(path, got.json())
        if metrics is not None:
            # A's own totals: B's 900 tokens are not in them.
            assert metrics["total_tokens"] == 7 and metrics["total_tool_calls"] == 1, metrics

    run.__name__ = f"traces_{path.strip('/').replace('/', '_')}"
    case(f"GET {path}", ITEM)(run)


for _path in ("/agents", "/api/agents", "/api/agents/status", "/api/agents/reasoning"):
    _route(_path)


@case("GET /api/agents/tools", ITEM)
def traces_tools(matrix: Matrix) -> None:
    with traces(matrix) as w:
        got = w.client.get("/api/agents/tools", headers=_as("a"))
    assert got.status_code == 200, got.text
    labels = [t["label"] for t in got.json()["tools"]]
    assert labels == [f"tool {w.marks.a}"], labels


@case("WEBSOCKET /ws/dashboard", ITEM)
def traces_socket(matrix: Matrix) -> None:
    with traces(matrix) as w:
        theirs, mine = w.entry("tool", w.marks.b, 0), w.entry("tool", w.marks.a, 0)
        theirs.space, theirs.asker = Matrix.SPACE_B, matrix.account_b
        mine.space, mine.asker = Matrix.SPACE_A, matrix.account_a
        with w.client.websocket_connect(f"/ws/dashboard?person=a&space={Matrix.SPACE_A}") as socket:
            assert socket.receive_json()["type"] == "connected"
            # B's call first, then A's: the socket is sent A's alone.
            w.client.portal.call(w.store._broadcast, theirs)
            w.client.portal.call(w.store._broadcast, mine)
            sent = socket.receive_json()
        assert sent["data"]["label"] == f"tool {w.marks.a}", sent
        assert w.marks.b not in str(sent)
        assert sent["metrics"]["total_tokens"] == 7, sent["metrics"]
        # A socket that names another space is refused at the handshake.
        with w.client.websocket_connect(f"/ws/dashboard?person=a&space={Matrix.SPACE_B}") as socket:
            with pytest.raises(Exception):  # noqa: B017 -- the close is the refusal
                socket.receive_json()
