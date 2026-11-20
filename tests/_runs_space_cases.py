"""The matrix cases of Spaces S1-21: approvals and running work.

Every case runs with Spaces enforced, as person A in ``matrix-a`` against
person B's ``matrix-b``.

* Running work: the product's runs router behind the real tenant middleware
  and the auth middleware's role check, over a kind of run planted in both
  spaces (the registry's real listing and filter, the real incident store on
  a private file, the real decision path). A sees and acts on A's runs and
  incidents only; B's are neither shown nor touched.
* Approvals: the product's approval routes (``routes_direct.misc``) over the
  real gate registry and chat store: B's paused chat, B's bridge gate and an
  MCP server's sampling request (the machine's) are neither listed for A nor
  decided by A.
* The agent's run tools, as A's turn calls them.

Nothing here builds the app or decides the vault while pytest imports it
(``tests/test_order_independence.py``).
"""

from __future__ import annotations

import asyncio
import json
import secrets
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import scope, threads
from kazma_ui import auth, space_binding

from tests._space_matrix import Matrix, case

ITEM = "S1-21"

_XHR = {"x-requested-with": "XMLHttpRequest"}


# ── running work ─────────────────────────────────────────────────────────


class _Kind:
    """A kind of run planted in both spaces; records what it was asked to do."""

    STATE_MAP: dict[str, str] = {}

    def __init__(self) -> None:
        self.runs: list[Any] = []
        self.acted: list[tuple[str, str]] = []

    async def list_runs(self, now: float, *, window_s: float) -> list[Any]:
        return list(self.runs)

    async def act(self, native_id: str, action: str, *, actor: str) -> Any:
        from kazma_core.runs.contract import ActionResult

        self.acted.append((native_id, action))
        return ActionResult(True, "cancelled", "cancelled")


@contextmanager
def running_work(matrix: Matrix) -> Iterator[SimpleNamespace]:
    """Spaces enforced; one kind of run (``planted``) holding a failed run of
    A and one of B, each with its incident, behind the runs router."""
    from kazma_core.runs import registry
    from kazma_core.runs.contract import KindSpec, Run, RunState
    from kazma_core.runs.incidents import IncidentStore, reset_incident_store
    from kazma_core.runs.kinds import KINDS

    from kazma_ui.runs_api import create_runs_router

    kind = _Kind()
    marks = matrix.plant("run")
    people = {"a": matrix.principal_a, "b": matrix.principal_b}

    def run(who: str) -> Any:
        space = Matrix.SPACE_A if who == "a" else Matrix.SPACE_B
        asker = matrix.account_a if who == "a" else matrix.account_b
        mark = marks.a if who == "a" else marks.b
        return Run(run_id=f"planted:{mark}", kind="planted", native_id=mark, title=f"job {mark}",
                   state=RunState.FAILED, native_state="failed", started_at=1.0, ended_at=2.0,
                   version="1", actions=("cancel",), detail=f"why {mark}", space=space, asker=asker)

    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(
        ignore_cleanup_errors=True
    ) as tmp:
        patch.setattr(scope, "_enforced", True)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))
        for name in list(KINDS):
            patch.delitem(KINDS, name)
        patch.setitem(KINDS, "planted", KindSpec(kind="planted", adapter="x:y", label="Planted",
                                                 label_ar="مزروع", source="test"))
        patch.setitem(registry._ADAPTERS, "planted", kind)
        store = IncidentStore(Path(tmp) / "incidents.db")
        reset_incident_store(store)
        try:
            kind.runs = [run("a"), run("b")]
            incident_a, _ = store.open_incident(kind.runs[0], "failed", origin={}, notify="suppressed")
            incident_b, _ = store.open_incident(kind.runs[1], "failed", origin={}, notify="suppressed")
            app = FastAPI()
            Matrix.role_checked(app)
            app.middleware("http")(auth.create_tenant_middleware())
            app.include_router(create_runs_router())
            with TestClient(app) as client:
                yield SimpleNamespace(client=client, kind=kind, marks=marks, store=store,
                                      incident_a=incident_a["id"], incident_b=incident_b["id"])
        finally:
            reset_incident_store(None)


def _as(who: str) -> dict[str, str]:
    space = Matrix.SPACE_A if who == "a" else Matrix.SPACE_B
    return {"x-matrix-person": who, space_binding.SPACE_HEADER: space, **_XHR}


@case("GET /api/runs", ITEM)
def runs_list(matrix: Matrix) -> None:
    with running_work(matrix) as w:
        got = w.client.get("/api/runs", headers=_as("a"))
    assert got.status_code == 200, got.text
    text = got.text
    assert w.marks.a in text and w.marks.b not in text, text[:400]
    assert [r["native_id"] for r in got.json()["runs"]] == [w.marks.a]
    assert [i["id"] for i in got.json()["incidents"]] == [w.incident_a]


@case("POST /api/runs/incidents/{incident_id}/act", ITEM)
def runs_act(matrix: Matrix) -> None:
    with running_work(matrix) as w:
        theirs = w.client.post(f"/api/runs/incidents/{w.incident_b}/act", json={"action": "cancel"},
                               headers=_as("a"))
        assert theirs.status_code == 404 and theirs.json()["code"] == "unknown_incident", theirs.text
        assert w.marks.b not in theirs.text
        assert w.kind.acted == []
        assert w.store.get(w.incident_b)["state"] == "open"
        mine = w.client.post(f"/api/runs/incidents/{w.incident_a}/act", json={"action": "cancel"},
                             headers=_as("a"))
    assert mine.status_code == 200 and mine.json()["ok"], mine.text
    assert w.kind.acted == [(w.marks.a, "cancel")]


# ── approvals ────────────────────────────────────────────────────────────


@contextmanager
def approvals(matrix: Matrix) -> Iterator[SimpleNamespace]:
    """Spaces enforced; the approval routes over the real gate registry and
    chat store, with a paused chat and a bridge gate in each space and an
    MCP server's sampling request waiting."""
    from kazma_core.mcp import spec_client
    from kazma_core.safety import hitl_gates
    from kazma_core.safety.hitl_gates import GateRow, register_gate

    from kazma_ui.routes_direct import misc
    from kazma_ui.session_manager import get_session_manager

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    marks = matrix.plant("gate")
    sampling_mark = f"mxsampling{secrets.token_hex(4)}"

    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(
        ignore_cleanup_errors=True
    ) as tmp:
        patch.setattr(scope, "_enforced", True)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))

        async def _no_secret() -> str:
            return ""

        patch.setattr(auth, "aget_kazma_secret", _no_secret)
        patch.setattr(hitl_gates, "_db_path_override", str(Path(tmp) / "hitl_gates.db"))
        patch.setattr(hitl_gates, "_schema_ready", False)
        patch.setattr(spec_client, "list_sampling_pending",
                      lambda: [{"thread_id": "sampling-1", "tool": "sampling", "message": sampling_mark}])

        planted: dict[str, dict[str, str]] = {}
        for who, mark in (("a", marks.a), ("b", marks.b)):
            space = Matrix.SPACE_A if who == "a" else Matrix.SPACE_B
            account = matrix.account_a if who == "a" else matrix.account_b
            thread_id, bridge = f"t{secrets.token_hex(8)}", f"bridge-{secrets.token_hex(6)}"
            with scope.bind_scope(space, account):
                store = get_session_manager()
                session = store.get_or_create(f"s{secrets.token_hex(8)}", thread_id=thread_id)
                session.add_message("user", f"question {mark}")
                store.put(session)
                threads.record_thread(thread_id, space, account)
                chat_gate = register_gate(GateRow(gate_id=f"g-{mark}", thread_id=thread_id, tool="file_write",
                                                  args_json=json.dumps({"path": f"{mark}.txt"})))
                bridge_gate = register_gate(GateRow(gate_id=f"b-{mark}", thread_id=bridge, tool="shell_exec",
                                                    mechanism="bus_bridge", tenant_id=space,
                                                    args_json=json.dumps({"command": f"echo {mark}"})))
            planted[who] = {"thread": thread_id, "bridge": bridge, "chat_gate": chat_gate.gate_id,
                            "bridge_gate": bridge_gate.gate_id}

        class _Checkpointer:
            """Records the paused threads the clear route deletes."""

            def __init__(self) -> None:
                self.deleted: list[str] = []

            async def adelete_thread(self, thread_id: str) -> None:
                self.deleted.append(thread_id)

        checkpointer = _Checkpointer()

        async def _paused(_graph: Any, _checkpointer: Any) -> list[dict[str, Any]]:
            # Both spaces' chats are paused on an approval.
            return [{"thread_id": planted[who]["thread"]} for who in ("a", "b")]

        from kazma_ui import hitl_approval

        patch.setattr(hitl_approval, "_get_pending_approvals", _paused)
        holder = SimpleNamespace(
            app=FastAPI(), templates=None, agent=None, gateway=None, chat_adapters=None,
            _init_errors=[], session_store=None, _graph_holder={},
            _hitl_state={"graph": object(), "checkpointer": checkpointer},
        )
        holder.app.middleware("http")(auth.create_tenant_middleware())
        misc.register_misc_routes(holder)
        with TestClient(holder.app) as client:
            yield SimpleNamespace(client=client, marks=marks, planted=planted, sampling=sampling_mark,
                                  gates=hitl_gates, checkpointer=checkpointer)


def _state(gates: Any, gate_id: str) -> str:
    row = gates.gate_for(gate_id)
    return row.state if row is not None else "missing"


@case("GET /api/pending-approvals", ITEM)
def approvals_list(matrix: Matrix) -> None:
    with approvals(matrix) as w:
        got = w.client.get("/api/pending-approvals", headers=_as("a"))
    assert got.status_code == 200, got.text
    assert w.marks.b not in got.text and w.sampling not in got.text, got.text[:400]
    listed = {item.get("thread_id") for item in got.json()["pending"]}
    assert listed == {w.planted["a"]["thread"], w.planted["a"]["bridge"]}, listed


@case("POST /api/approve/{thread_id}", ITEM)
def approvals_decide(matrix: Matrix) -> None:
    with approvals(matrix) as w:
        for kind in ("thread", "bridge"):
            got = w.client.post(f"/api/approve/{w.planted['b'][kind]}",
                                json={"action": "approve"}, headers=_as("a"))
            assert got.status_code == 404, (kind, got.status_code, got.text[:200])
            assert w.marks.b not in got.text
        assert _state(w.gates, w.planted["b"]["chat_gate"]) == "pending"
        assert _state(w.gates, w.planted["b"]["bridge_gate"]) == "pending"
        # A's own bridge gate is A's to decide.
        mine = w.client.post(f"/api/approve/{w.planted['a']['bridge']}",
                             json={"action": "approve"}, headers=_as("a"))
        assert mine.status_code == 200, mine.text
        assert _state(w.gates, w.planted["a"]["bridge_gate"]) != "pending"


@case("POST /api/pending-approvals/clear", ITEM)
def approvals_clear(matrix: Matrix) -> None:
    with approvals(matrix) as w:
        # An install with no shared secret has no admin role to require (the
        # admin rule's own answer): the clear runs for A, and clears A's
        # paused chat alone.
        got = w.client.post("/api/pending-approvals/clear", headers=_as("a"))
        assert got.status_code == 200 and got.json()["cleared"] == 1, got.text
        assert w.checkpointer.deleted == [w.planted["a"]["thread"]]
        assert _state(w.gates, w.planted["a"]["chat_gate"]) != "pending"
        assert _state(w.gates, w.planted["b"]["chat_gate"]) == "pending"
        assert _state(w.gates, w.planted["b"]["bridge_gate"]) == "pending"


# ── the agent's run tools ────────────────────────────────────────────────


@contextmanager
def _tool_world(matrix: Matrix) -> Iterator[SimpleNamespace]:
    with running_work(matrix) as w:
        yield w


def _tool(name: str, **arguments: Any) -> str:
    from tests._chat_space_cases import _registry

    tool = _registry().get_tool(name)
    assert tool is not None, name
    return str(asyncio.run(tool.func(**arguments)))


@case("tool:list_runs", ITEM)
def tool_list_runs(matrix: Matrix) -> None:
    with _tool_world(matrix) as w, matrix.as_a():
        answer = _tool("list_runs", include_finished=True)
    assert w.marks.a in answer and w.marks.b not in answer, answer[:400]


@case("tool:run_status", ITEM)
def tool_run_status(matrix: Matrix) -> None:
    with _tool_world(matrix) as w, matrix.as_a():
        theirs = _tool("run_status", run_id=f"planted:{w.marks.b}")
        mine = _tool("run_status", run_id=f"planted:{w.marks.a}")
    assert w.marks.b not in theirs.replace(f"planted:{w.marks.b}", ""), theirs
    assert "is listed now" in theirs, theirs
    assert f"why {w.marks.a}" in mine, mine
