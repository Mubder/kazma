"""Spaces S2-01, the second review: a tool switched off for a person is off
at every door to its effect, not only at the tool's call.

The review found the switch enforced at the call alone: X Studio's Post now
and Delete, a booking from the Scheduled page, a booked post when it fired,
the intent engine's deep research, the Swarm page's dispatch, the Research
page's start and the Documents page's actions all reached the same effects.

The rule (``spaces.features``): every door that is not a call of the tool
is declared -- a route door (``TOOL_ROUTE_DOORS``, refused by the tenant
middleware: 403 ``tool_off``) or a work door (``TOOL_WORK_DOORS``, a
function that asks ``tool_work_refusal`` for the person the work is for).
A gate holds both ways: every route that changes something in a space is a
door or declared not one (``NOT_A_TOOL_DOOR``), every work door asks, and
every call that asks is a declared door -- each with a planted door as its
control.

Also here (decision N4): a person whose cards go to approvers posts and
deletes on X only through the chat's card; their own click in X Studio is
refused with a line naming the approvers.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.spaces import features, identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._space_matrix import Matrix
from tests.test_feature_admissions import _function, _own_nodes

pytestmark = pytest.mark.real_features

REPO = Path(__file__).resolve().parents[1]
_PACKAGES = ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-skills", "kazma-tui")

#: The functions that ask the tool rule for a tool their caller names
#: (each holds the asking call itself); a work door calls one of them with
#: its tool as a string.
ASKERS = frozenset({"tool_work_refusal", "tool_door_refusal", "x_work_scope", "_work_scope",
                    "_tool_switched_off"})
PASS_THROUGH = {
    "kazma-core/kazma_core/spaces/features.py::tool_door_refusal",
    "kazma-core/kazma_core/x_api/ownership.py::x_work_scope",
    "kazma-core/kazma_core/x_api/publication_service.py::_work_scope",
    "kazma-core/kazma_core/x_api/booking.py::_tool_switched_off",
}


@pytest.fixture
def matrix(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


# ── the gate: every route that changes a space ──────────────────────────


@pytest.fixture(scope="module")
def changing_routes() -> list[tuple[str, str]]:
    """Every served route that changes something in a space: a space's
    route, or a feature's."""
    from kazma_ui.app import create_app
    from kazma_ui.route_scopes import SPACE, features_of_route, scope_of

    from tests.test_route_scopes import served_routes

    return sorted({(m, p) for m, p in served_routes(create_app())
                   if m in ("POST", "PUT", "PATCH", "DELETE")
                   and (scope_of(m, p) == SPACE or features_of_route(p, m))})


def _not_a_door_rules() -> list[Any]:
    from kazma_ui.route_scopes import SPACE, compile_rules

    patterns = [(method, path) if method else path for method, path in features.NOT_A_TOOL_DOOR]
    return list(compile_rules((pattern, SPACE) for pattern in patterns))


def undeclared(routes: list[tuple[str, str]]) -> list[str]:
    from kazma_ui.route_scopes import _segments, tools_of_route

    rules = _not_a_door_rules()
    return [f"{m} {p}" for m, p in routes
            if not tools_of_route(p, m) and not any(r.matches(m, _segments(p)) for r in rules)]


def test_every_route_that_changes_a_space_is_a_door_or_declared(changing_routes) -> None:
    assert len(changing_routes) > 150, "the app's routes were not read"
    assert undeclared(changing_routes) == []


def test_control_a_planted_route_is_reported(changing_routes) -> None:
    planted = [*changing_routes, ("POST", "/api/documents/{document_id}/planted")]
    assert undeclared(planted) == ["POST /api/documents/{document_id}/planted"]


def test_every_door_and_reason_names_a_served_route_and_real_tools(changing_routes) -> None:
    from kazma_ui.route_scopes import SPACE, _segments, compile_rules

    switchable = frozenset().union(*features._switchable_tools().values())
    for (method, pattern), tools in features.TOOL_ROUTE_DOORS.items():
        [rule] = compile_rules([((method, pattern), SPACE)])
        assert any(rule.matches(m, _segments(p)) for m, p in changing_routes), (method, pattern)
        assert tools and set(tools) <= switchable, (pattern, tools)
    for rule in _not_a_door_rules():
        assert any(rule.matches(m, _segments(p)) for m, p in changing_routes), rule.pattern
    assert set(features.TOOL_WORK_DOORS) <= switchable


# ── the gate: every work door asks, every asking call is a door ─────────


def _asks(function: ast.AST, tool: str) -> bool:
    """Whether *function*'s own body calls an asker and names *tool*."""
    nodes = list(_own_nodes(function))
    calls = any(isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id in ASKERS)
        or (isinstance(n.func, ast.Attribute) and n.func.attr in ASKERS)
        or any(isinstance(a, ast.Name) and a.id in ASKERS for a in n.args)
        or any(isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute) and a.func.attr == "partial"
               and any(isinstance(x, ast.Name) and x.id in ASKERS for x in a.args) for a in n.args))
        for n in nodes)
    return calls and any(isinstance(n, ast.Constant) and n.value == tool for n in nodes)


def door_findings(doors: dict[str, tuple[str, ...]], sources: dict[str, str] | None = None) -> list[str]:
    found = []
    for tool, places in doors.items():
        for place in places:
            path, qualname = place.split("::", 1)
            function = _function(path, qualname, (sources or {}).get(path))
            if function is None:
                found.append(f"{tool}: {place} does not exist")
            elif not _asks(function, tool):
                found.append(f"{tool}: {place} does not ask the tool rule")
    return found


def test_every_work_door_asks_the_tool_rule() -> None:
    assert door_findings(features.TOOL_WORK_DOORS) == []


def test_control_a_planted_work_door_that_does_not_ask_is_found() -> None:
    planted = {"kazma-core/kazma_core/planted.py": (
        "async def post(text):\n    return await send(text)\n\n"
        "async def asks(text):\n    from kazma_core.spaces.features import tool_door_refusal\n"
        "    if tool_door_refusal('x_post'):\n        return None\n    return await send(text)\n")}
    found = door_findings({"x_post": ("kazma-core/kazma_core/planted.py::post",
                                      "kazma-core/kazma_core/planted.py::asks")}, planted)
    assert found == ["x_post: kazma-core/kazma_core/planted.py::post does not ask the tool rule"]


def _asking_functions(sources: dict[str, str]) -> set[str]:
    """Every function whose own body calls an asker with a tool named."""
    out: set[str] = set()
    for path, source in sources.items():
        tree = ast.parse(source)

        def walk(node: ast.AST, prefix: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = f"{prefix}{child.name}"
                    if not isinstance(child, ast.ClassDef) and any(
                            _asks(child, tool) for tool in _tool_constants(child)):
                        out.add(f"{path}::{name}")
                    walk(child, name + ".")

        walk(tree, "")
    return out


def _tool_constants(function: ast.AST) -> set[str]:
    switchable = frozenset().union(*features._switchable_tools().values())
    return {n.value for n in _own_nodes(function) if isinstance(n, ast.Constant) and n.value in switchable}


def _product_sources() -> dict[str, str]:
    out = {}
    for package in _PACKAGES:
        for path in (REPO / package).rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel.endswith("conftest.py"):
                continue
            text = path.read_text(encoding="utf-8")
            if any(name in text for name in ASKERS):
                out[rel] = text
    return out


def undeclared_askers(sources: dict[str, str]) -> list[str]:
    declared = {place for places in features.TOOL_WORK_DOORS.values() for place in places} | PASS_THROUGH
    return sorted(_asking_functions(sources) - declared)


def test_every_function_that_asks_the_tool_rule_is_a_declared_door() -> None:
    assert undeclared_askers(_product_sources()) == []


def test_control_a_planted_asking_function_is_reported() -> None:
    planted = {**_product_sources(), "kazma-core/kazma_core/planted.py": (
        "def sneak():\n    from kazma_core.spaces.features import tool_door_refusal\n"
        "    return tool_door_refusal('x_post')\n")}
    assert undeclared_askers(planted) == ["kazma-core/kazma_core/planted.py::sneak"]


# ── the rule ────────────────────────────────────────────────────────────


@pytest.fixture
def y_posting_off(matrix, enforced):
    """Person A has X Studio, research and the rest, with x_post,
    x_delete_post, x_schedule_post, start_deep_research and dispatch_swarm
    switched off; person B has them on."""
    every = {fid: True for fid in features.FEATURES if fid != "development"}
    for account in (matrix.account_a, matrix.account_b):
        people.set_features(account, every, by=OWNER_ACCOUNT_ID)
    people.set_tools(matrix.account_a, ["x_post", "x_delete_post", "x_schedule_post", "start_deep_research",
                                        "dispatch_swarm", "generate_pdf", "knowledge_ingest_site"],
                     by=OWNER_ACCOUNT_ID)
    return matrix


def test_the_rule_answers_for_the_person(y_posting_off) -> None:
    m = y_posting_off
    assert "x_post" in (features.tool_work_refusal("x_post", m.account_a, "en") or "")
    assert features.tool_work_refusal("x_post", m.account_b) is None
    assert features.tool_work_refusal("x_status", m.account_a) is None
    with m.as_a():
        assert features.tool_door_refusal("x_post", "انشر") and "أداة" in features.tool_door_refusal("x_post", "انشر")


def test_nothing_is_read_with_spaces_off(matrix, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)

    def asked() -> Any:
        raise AssertionError("no identity read while Spaces is off")

    monkeypatch.setattr(identity, "read_registry", asked)
    assert features.tool_work_refusal("x_post", matrix.account_a) is None


# ── route doors, through the real tenant middleware ─────────────────────


def _middleware_app(matrix: Matrix) -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth

    people_ = {"a": matrix.principal_a, "b": matrix.principal_b}
    app = FastAPI()
    reached: list[str] = []

    @app.post("/api/swarm/dispatch")
    def dispatch() -> dict[str, Any]:
        reached.append("dispatch")
        return {"ok": True}

    @app.post("/api/research/sessions")
    def research() -> dict[str, Any]:
        reached.append("research")
        return {"ok": True}

    app.middleware("http")(auth.create_tenant_middleware())
    client = TestClient(app)
    client.reached = reached
    client.people = people_
    return client


def _post(client: Any, path: str, who: str) -> Any:
    from kazma_ui import space_binding

    space = Matrix.SPACE_A if who == "a" else Matrix.SPACE_B
    return client.post(path, json={}, headers={"x-matrix-person": who, space_binding.SPACE_HEADER: space})


@pytest.mark.parametrize("path", ["/api/swarm/dispatch", "/api/research/sessions"])
def test_a_route_door_is_refused_for_the_person_whose_tool_is_off(y_posting_off, monkeypatch, path) -> None:
    from kazma_ui import auth

    client = _middleware_app(y_posting_off)
    monkeypatch.setattr(auth, "get_request_principal",
                        lambda request: client.people.get(request.headers.get("x-matrix-person", "")))
    with client:
        refused = _post(client, path, "a")
        assert refused.status_code == 403 and refused.json()["code"] == "tool_off", refused.text
        assert client.reached == []
        assert _post(client, path, "b").status_code == 200 and client.reached


def test_control_without_route_doors_the_page_reached_the_effect(y_posting_off, monkeypatch) -> None:
    from kazma_ui import auth, route_scopes

    client = _middleware_app(y_posting_off)
    monkeypatch.setattr(auth, "get_request_principal",
                        lambda request: client.people.get(request.headers.get("x-matrix-person", "")))
    monkeypatch.setattr(route_scopes, "tools_of_route", lambda *_a, **_k: ())
    with client:
        assert _post(client, "/api/swarm/dispatch", "a").status_code == 200 and client.reached


# ── work doors ──────────────────────────────────────────────────────────


def test_the_x_choke_points_refuse_a_switched_off_tool(y_posting_off, monkeypatch) -> None:
    from kazma_core.x_api import booking, publication_service, threads

    sent: list[str] = []

    async def publish(**_k: Any) -> Any:
        sent.append("post")
        return True, {}

    async def delete(**_k: Any) -> Any:
        sent.append("delete")
        return True, {}

    monkeypatch.setattr(publication_service, "publish", publish)
    monkeypatch.setattr(publication_service, "delete", delete)
    with y_posting_off.as_a():
        ok, posted = asyncio.run(booking.publish_x_post(text="hello"))
        assert not ok and posted["code"] == "tool_off"
        ok, deleted = asyncio.run(booking.delete_x_post(tweet_id="1"))
        assert not ok and deleted["code"] == "tool_off"
        ok, booked = booking.book_x_post(text="later", when="2h")
        assert not ok and booked["code"] == "feature_off" and "x_schedule_post" in booked["error"]
        with pytest.raises(ValueError, match="x_post"):
            asyncio.run(threads.publish_thread("t", revision=1, token="x", actor="a"))
    assert sent == []
    with y_posting_off.as_b():
        assert asyncio.run(booking.publish_x_post(text="hello"))[0]
    assert sent == ["post"]


def test_a_booked_post_waits_when_its_tool_is_off_for_its_booker(y_posting_off) -> None:
    from kazma_core.x_api import publication_service
    from kazma_core.x_api.ownership import x_work_scope

    m = y_posting_off
    with pytest.raises(features.ToolOff):
        x_work_scope(Matrix.SPACE_A, m.account_a, tool="x_schedule_post")
    assert callable(x_work_scope(Matrix.SPACE_A, m.account_a))  # receipts and the rest go on
    waited = asyncio.run(publication_service._work_scope(Matrix.SPACE_A, m.account_a, tool="x_schedule_post"))
    assert waited is publication_service._WAIT


def test_the_intent_engine_starts_no_research_for_a_switched_off_tool(y_posting_off, monkeypatch) -> None:
    from kazma_core.agent.intent.handlers import research
    from kazma_core.agent.intent.types import ActKind
    from kazma_core.tools import research_session

    started: list[str] = []

    async def start(topic: str, **_k: Any) -> Any:
        started.append(topic)
        return SimpleNamespace(status="running", id="s1", error="", stage="plan")

    monkeypatch.setattr(research_session, "start_deep_research", start)
    decision = SimpleNamespace(primary=SimpleNamespace(kind=ActKind.RESEARCH_DEEP, slots={"topic": "tides"}))
    with y_posting_off.as_a():
        refused = asyncio.run(research.run_research_deep(decision, {"messages": []}))
    assert refused.escalate and refused.message == "tool_off" and started == []
    with y_posting_off.as_b():
        asyncio.run(research.run_research_deep(decision, {"messages": []}))
    assert started == ["tides"]


# ── decision N4: X Studio's own click ───────────────────────────────────


def _approvers(account: str, approvers: list[str]) -> None:
    from kazma_core.spaces import approvals

    def change(reg: dict[str, Any]) -> None:
        policy = dict(reg["policies"].get(account) or {})
        policy[approvals.APPROVERS_POLICY] = approvers
        reg["policies"][account] = policy

    identity.update_registry(change)


def _with_x_studio(matrix: Matrix, monkeypatch: Any) -> None:
    """Both people have every feature (the X world plants each one's X
    account through its routes)."""
    monkeypatch.setattr(scope, "_enforced", True)
    every = {fid: True for fid in features.FEATURES if fid != "development"}
    for account in (matrix.account_a, matrix.account_b):
        people.set_features(account, every, by=OWNER_ACCOUNT_ID)


def test_a_person_whose_cards_go_to_approvers_does_not_post_by_their_own_click(matrix, monkeypatch) -> None:
    from tests._x_space_cases import world

    _with_x_studio(matrix, monkeypatch)
    with world(matrix) as w:
        _approvers(matrix.account_a, [OWNER_ACCOUNT_ID])
        for path, body in (("/api/x/post", {"text": "my own click"}), ("/api/x/delete", {"tweet_id": "12345"})):
            refused = w.call("a", "POST", path, json=body)
            assert refused.status_code == 403 and refused.json()["code"] == "approvers_decide", refused.text
            assert "approval" in refused.json()["error"]
        assert w.x_calls == []
        _approvers(matrix.account_a, [])
        w.call("a", "POST", "/api/x/post", json={"text": "my own click"})
        assert w.x_calls, "without approvers the click reaches X"


def test_control_x_studio_without_the_rule_posted_by_the_click(matrix, monkeypatch) -> None:
    from kazma_core.spaces import approvals

    from tests._x_space_cases import world

    monkeypatch.setattr(approvals, "own_click_refusal", lambda *_a, **_k: None)
    _with_x_studio(matrix, monkeypatch)
    with world(matrix) as w:
        _approvers(matrix.account_a, [OWNER_ACCOUNT_ID])
        w.call("a", "POST", "/api/x/post", json={"text": "my own click"})
        assert w.x_calls


# ── the identity store away: fail closed ────────────────────────────────


def _store_away(monkeypatch: Any) -> None:
    def away() -> Any:
        raise identity.IdentityUnavailable("the accounts store is away")

    monkeypatch.setattr(identity, "read_registry", away)


def test_no_tool_runs_while_the_accounts_cannot_be_read(y_posting_off, monkeypatch) -> None:
    from kazma_core.spaces import tools

    with y_posting_off.as_a():
        _store_away(monkeypatch)
        refused = tools.tool_refusal("file_read")  # always-on: refused too
        offered = tools.offered_here([{"function": {"name": "file_read"}}, {"function": {"name": "memory_search"}}])
    assert refused and "cannot read who may use it" in refused, refused
    assert offered == []


def test_control_an_empty_grant_let_an_always_on_tool_run(y_posting_off, monkeypatch) -> None:
    from kazma_core.spaces import tools

    people.set_tools(y_posting_off.account_a, ["file_read"], by=OWNER_ACCOUNT_ID)
    with y_posting_off.as_a():
        _store_away(monkeypatch)
        empty = features.Grants(y_posting_off.account_a, frozenset())
        assert tools.tool_refusal("file_read", grants=empty) is None  # switched off, and it ran
