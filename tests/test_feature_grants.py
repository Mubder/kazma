"""Spaces S2-11: features per person (plan 4.8, decision N18).

One table (``kazma_core.spaces.features``) names each feature's pages,
routes, tools, chat commands, background work and connected accounts. The
gate reads what exists -- the real app's routes, the tool registry, the chat
commands, the background loops -- and every one belongs to a feature or is
declared always on (chat, memory, My account) or the install's own (the
admin's, Kazma's own loops); a new one with neither fails, and so does a
declaration that names nothing. Each gate finds a planted one.

With X Studio on for Y and off for G:

* G has no menu entry (the sidebar, the palette and the shortcuts follow the
  sidebar); every X route refuses G (403 ``feature_off``) and reaches Y;
* no X tool is offered or callable for G -- in chat, in a scheduled job, in
  a swarm worker -- and Y's runs;
* ``/x`` refuses G; X's background work (the mentions loop, a booked post)
  skips G;
* the control: a grant that only hides the menu entry lets G's route and
  tool through.

Also: the admin has every feature (only Development is theirs to switch,
N28); an install that does not enforce Spaces refuses nothing and reads
nothing; the work of every other feature is checked again when it runs
(a scheduled job, a swarm worker, a document job, a crawl's resume); People
switches features, recorded; the precondition of a second account (S1-33).
The browser half is ``tests/e2e/test_feature_grants_browser.py``.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from kazma_core.spaces import features, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._space_matrix import Matrix

# At collection: importing the swarm tests builds the app (their cases do),
# which reads the stored Spaces switch into the process -- mid-test it would
# undo this test's own switch.
from tests.test_swarm_spaces import _reply, _scripted_model  # noqa: E402

pytestmark = pytest.mark.real_features

REPO = Path(__file__).resolve().parents[1]
X_TOOLS = sorted(features.FEATURES["x_studio"].tools)


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    from tests._no_models import finish_turn_memory, keep_models_out

    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()


@pytest.fixture
def matrix(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture
def y_and_g(matrix, enforced):
    """Y (person A) has X Studio; G (person B) has what a new person has."""
    people.set_features(matrix.account_a, {"x_studio": True}, by=OWNER_ACCOUNT_ID)
    return matrix


def _registry():
    from tests._chat_space_cases import _registry as registry

    return registry()


# ── the one table covers everything that exists ─────────────────────────


@pytest.fixture(scope="module")
def routes() -> list[tuple[str, str]]:
    from kazma_ui.app import create_app

    from tests.test_route_scopes import served_routes

    return [r for r in served_routes(create_app()) if r[0] != "MOUNT"]


def route_findings(served: list[tuple[str, str]]) -> list[str]:
    from kazma_ui.route_scopes import ACCOUNT, SPACE, _feature_class_of_route, scope_of

    found = [f"{method} {path}" for method, path in served
             if scope_of(method, path) in (SPACE, ACCOUNT) and _feature_class_of_route(path, method) is None]
    return sorted(found)


def stale_route_patterns(served: list[tuple[str, str]]) -> list[str]:
    from kazma_ui.route_scopes import _feature_rules, _segments

    return sorted(
        f"{rule.pattern} ({label})" for rule, label in _feature_rules()
        if not any(rule.matches(method if method != "HEAD" else "GET", _segments(path))
                   for method, path in served)
    )


def test_every_space_and_account_route_belongs_to_a_feature_or_is_always_on(routes) -> None:
    missing = route_findings(routes)
    assert not missing, ("These routes of a space or an account belong to no feature and are not "
                         "declared always on (kazma_core/spaces/features.py):\n  " + "\n  ".join(missing))
    assert not stale_route_patterns(routes), stale_route_patterns(routes)


def test_the_route_gate_finds_a_planted_route(routes, monkeypatch) -> None:
    from kazma_ui import route_scopes

    monkeypatch.setattr(route_scopes, "scope_of",
                        lambda method, path, rules=None: route_scopes.SPACE if path == "/api/planted" else
                        route_scopes.INSTALL)
    assert route_findings([("GET", "/api/planted")]) == ["GET /api/planted"]


def _classified_tools() -> dict[str, list[str]]:
    from kazma_core.spaces.tools import ADMIN, DEVELOPMENT

    seen: dict[str, list[str]] = {}
    for fid, feature in features.FEATURES.items():
        for name in feature.tools:
            seen.setdefault(name, []).append(fid)
    for name in DEVELOPMENT:
        seen.setdefault(name, []).append("development")
    for aid, always in features.ALWAYS_ON.items():
        for name in always.tools:
            seen.setdefault(name, []).append(f"always:{aid}")
    for name in ADMIN:
        seen.setdefault(name, []).append("install")
    return seen


def tool_findings(names: set[str]) -> list[str]:
    seen = _classified_tools()
    found = [f"{name}: no feature" for name in sorted(names - set(seen))]
    found += [f"{name}: in {where}" for name, where in sorted(seen.items()) if len(where) > 1]
    return found


def test_every_tool_belongs_to_a_feature_or_is_always_on() -> None:
    names = set(_registry()._tools) | {"handoff_to_worker"}
    assert len(names) > 150
    assert tool_findings(names) == []
    stale = sorted(set(_classified_tools()) - names)
    assert not stale, f"declared tools that do not exist: {stale}"


def test_the_tool_gate_finds_a_planted_tool() -> None:
    assert tool_findings({"memory_search", "planted_new_tool"}) == ["planted_new_tool: no feature"]
    # A tool in no group (an MCP server's) is Development's, never guessed safe.
    assert features._feature_of_tool("mcp__fs__write_file") == "development"


def _classified(kind: str) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    for fid, feature in features.FEATURES.items():
        for name in getattr(feature, kind):
            seen.setdefault(name, []).append(fid)
    for aid, always in features.ALWAYS_ON.items():
        for name in getattr(always, kind):
            seen.setdefault(name, []).append(f"always:{aid}")
    prefix = {"commands": "command:", "background": "loop:"}[kind]
    for key in features.INSTALL:
        if key.startswith(prefix):
            seen.setdefault(key[len(prefix):], []).append("install")
    return seen


def findings(kind: str, names: set[str]) -> list[str]:
    seen = _classified(kind)
    found = [f"{name}: no feature" for name in sorted(names - set(seen))]
    found += [f"{name}: in {where}" for name, where in sorted(seen.items()) if len(where) > 1]
    found += [f"{name}: declared, does not exist" for name in sorted(set(seen) - names)]
    return found


def _commands() -> set[str]:
    from kazma_core.agent.command_catalog import COMMANDS

    return {c.name for c in COMMANDS}


def _loops() -> set[str]:
    from tests.test_database_availability import _product_loops

    return set(_product_loops())


def test_every_chat_command_belongs_to_a_feature_or_is_always_on() -> None:
    assert findings("commands", _commands()) == []


def test_every_background_loop_belongs_to_a_feature_or_is_declared() -> None:
    assert findings("background", _loops()) == []


def test_the_command_and_loop_gates_find_planted_ones() -> None:
    assert findings("commands", _commands() | {"planted"}) == ["planted: no feature"]
    planted = "kazma-core/kazma_core/planted.py::_loop"
    assert findings("background", _loops() | {planted}) == [f"{planted}: no feature"]
    assert findings("commands", _commands() - {"x"}) == ["x: declared, does not exist"]


def test_every_feature_page_in_the_sidebar_is_inside_its_rule() -> None:
    text = (REPO / "kazma-ui" / "kazma_ui" / "templates" / "components" / "sidebar.html").read_text(
        encoding="utf-8")
    for fid, feature in features.FEATURES.items():
        for page in feature.pages:
            href = f'href="{page}"'
            if href not in text or fid == "development":  # the Development pages: S1-20's rule
                continue
            before = text[: text.index(href)]
            rule = f"{{% if feature_here is not defined or feature_here('{fid}') %}}"
            assert before.rfind(rule) > before.rfind("{% endif %}"), (fid, page)


def test_both_command_paths_ask_the_feature_rule() -> None:
    for rel in ("kazma-ui/kazma_ui/sse_chat/__init__.py", "kazma-gateway/kazma_gateway/agent_handler/graph.py"):
        assert "command_refusal" in (REPO / rel).read_text(encoding="utf-8"), rel


# ── X Studio on for Y, off for G ─────────────────────────────────────────


def _app(record: list[str]):
    from fastapi import FastAPI
    from kazma_ui import auth

    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())

    @app.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def handler(rest: str) -> dict[str, Any]:
        record.append("/" + rest)
        return {"feature_here": features.feature_here("x_studio")}

    return app


def _as(matrix: Matrix, who: str, method: str, path: str) -> tuple[int, dict[str, Any], list[str]]:
    from fastapi.testclient import TestClient
    from kazma_ui import auth, space_binding

    reached: list[str] = []
    principal = matrix.principal(who)
    space = Matrix.SPACE_A if who == "a" else Matrix.SPACE_B
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(auth, "get_request_principal", lambda request: principal)
        with TestClient(_app(reached)) as client:
            answer = client.request(method, path, headers={space_binding.SPACE_HEADER: space})
    body = answer.json() if answer.headers.get("content-type", "").startswith("application/json") else {}
    return answer.status_code, body, reached


def _x_routes(routes: list[tuple[str, str]]) -> list[tuple[str, str]]:
    from kazma_ui.route_scopes import feature_of_route

    return [(m, p.replace("{", "").replace("}", "")) for m, p in routes
            if m not in ("WEBSOCKET", "HEAD") and feature_of_route(p, m) == "x_studio"]


def test_every_x_route_refuses_g_and_reaches_y(y_and_g, routes) -> None:
    x_routes = _x_routes(routes)
    assert len(x_routes) > 20, x_routes
    for method, path in x_routes:
        status, body, reached = _as(y_and_g, "b", method, path)
        assert status == 403 and body.get("code") == "feature_off" and not reached, (method, path, status)
        status, body, reached = _as(y_and_g, "a", method, path)
        assert body.get("code") != "feature_off" and reached == [path], (method, path, status, body)


def test_g_has_no_menu_entry_and_y_has_it(y_and_g) -> None:
    assert _as(y_and_g, "b", "GET", "/chat")[1] == {"feature_here": False}
    assert _as(y_and_g, "a", "GET", "/chat")[1] == {"feature_here": True}


def _offered(matrix: Matrix, bound: Any) -> set[str]:
    from kazma_core.skills.switches import offered

    with bound:
        return {d["function"]["name"] for d in offered(_registry().get_tool_definitions())}


def _call(matrix: Matrix, bound: Any, name: str, executor: Any = None) -> tuple[dict[str, Any], list[str]]:
    registry = _registry()
    entered: list[str] = []
    tool = registry.get_tool(name)

    async def recorder(*_a: Any, **_k: Any) -> str:
        entered.append(name)
        return "ran"

    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(registry._tools, name, replace(tool, func=recorder, is_async=True))
        with bound:
            answer = asyncio.run((executor(registry) if executor else registry).execute(name, {}))
    return answer, entered


def test_no_x_tool_is_offered_or_callable_for_g_in_chat(y_and_g) -> None:
    offered_g = _offered(y_and_g, y_and_g.as_b())
    assert not (offered_g & set(X_TOOLS)), sorted(offered_g & set(X_TOOLS))
    assert {"memory_search", "web_search", "generate_pdf"} <= offered_g  # N18's own
    assert set(X_TOOLS) <= _offered(y_and_g, y_and_g.as_a())
    for name in X_TOOLS:
        answer, entered = _call(y_and_g, y_and_g.as_b(), name)
        assert entered == [] and answer.get("refused") and "x_studio" in str(answer["content"]), (name, answer)
    _answer, entered = _call(y_and_g, y_and_g.as_a(), "x_status")
    assert entered == ["x_status"]


def test_no_x_tool_is_callable_for_g_in_a_scheduled_job(y_and_g) -> None:
    from kazma_core.agent_runner import DeclaredToolExecutor

    def job(registry):  # a task booked with the X tool among its own
        return DeclaredToolExecutor(registry, allowed={"x_status"})

    answer, entered = _call(y_and_g, y_and_g.as_b(), "x_status", job)
    assert entered == [] and answer.get("refused"), answer
    _answer, entered = _call(y_and_g, y_and_g.as_a(), "x_status", job)
    assert entered == ["x_status"]


def test_no_x_tool_is_offered_or_run_for_g_in_a_swarm_worker(y_and_g, monkeypatch) -> None:
    """A real in-process worker in G's space, its model scripted to call an
    X tool anyway."""
    from unittest.mock import AsyncMock

    import kazma_core.agent.tool_registry as tool_registry
    import kazma_core.model_registry as model_registry
    from kazma_core.llm_provider import ToolCall
    from kazma_core.swarm.task import WorkerCapabilities
    from kazma_core.swarm.worker import InProcessWorker

    registry = _registry()
    entered: list[str] = []

    async def recorder(*_a: Any, **_k: Any) -> str:
        entered.append("x_status")
        return "posted"

    monkeypatch.setitem(registry._tools, "x_status",
                        replace(registry.get_tool("x_status"), func=recorder, is_async=True))
    monkeypatch.setattr(tool_registry, "get_tool_registry", lambda: registry)
    sent = _scripted_model(monkeypatch, [
        _reply(calls=[ToolCall(id="c1", name="x_status", arguments={})]), _reply("done"),
    ])
    provider = model_registry.get_model_registry().get_client()
    offered: list[set[str]] = []
    real_chat = provider.chat.side_effect

    async def recording_chat(*args: Any, **kwargs: Any) -> Any:
        offered.append({t["function"]["name"] for t in kwargs.get("tools") or []})
        return await real_chat(*args, **kwargs)

    provider.chat = AsyncMock(side_effect=recording_chat)
    worker = InProcessWorker(name="probe", role="tester", capabilities=WorkerCapabilities(
        role="tester", tools=["x_status", "memory_search"]))
    with y_and_g.as_b():
        asyncio.run(worker.dispatch("post the news"))
    assert sent and offered, "the worker never called its model"
    assert "x_status" not in offered[0] and "memory_search" in offered[0], offered
    assert entered == [], "the worker ran an X tool for a person without X Studio"


def test_the_x_command_refuses_g(y_and_g) -> None:
    with y_and_g.as_b():
        refused = features.command_refusal("/x status")
        assert refused and "x_studio" in refused
        assert "الميزة" in features.command_refusal("/x status", "ar")
        assert features.command_refusal("/memory") is None  # always on
        assert features.command_refusal("/research deep tides") is None  # N18's own
    with y_and_g.as_a():
        assert features.command_refusal("/x status") is None


def test_x_background_work_skips_g(y_and_g) -> None:
    from kazma_core.x_api.ownership import x_work_scope

    assert callable(x_work_scope(Matrix.SPACE_A))
    with pytest.raises(scope.NoSpaceBound, match="X Studio is off"):
        x_work_scope(Matrix.SPACE_B)


def test_control_a_grant_that_only_hides_the_menu_entry(y_and_g, routes, monkeypatch) -> None:
    """The plan's control: the menu entry hidden, nothing else checked -- G's
    X route and X tool go through."""
    from kazma_ui import route_scopes

    monkeypatch.setattr(route_scopes, "feature_of_route", lambda path, method="GET": None)
    monkeypatch.setattr(features, "tool_refusal", lambda name, grants=None: None)
    assert _as(y_and_g, "b", "GET", "/chat")[1] == {"feature_here": False}  # hidden ...
    status, _body, reached = _as(y_and_g, "b", "GET", "/api/x/status")
    assert status == 200 and reached == ["/api/x/status"]  # ... and reached
    _answer, entered = _call(y_and_g, y_and_g.as_b(), "x_status")
    assert entered == ["x_status"]


# ── the admin, and an install that does not enforce Spaces ───────────────


def test_the_admin_has_every_feature_and_switches_only_development(matrix, enforced) -> None:
    from kazma_core.spaces import identity

    held = features.grants_of(identity.read_registry(), OWNER_ACCOUNT_ID)
    assert held.admin and held.on == frozenset(features.FEATURES)
    with pytest.raises(people.PeopleRefused) as refused:
        people.set_features(OWNER_ACCOUNT_ID, {"x_studio": False}, by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "admin_has_every_feature"
    people.set_features(OWNER_ACCOUNT_ID, {"development": False}, by=OWNER_ACCOUNT_ID)
    try:
        assert not identity.holds_development(OWNER_ACCOUNT_ID)
        assert "development" not in features.grants_of(identity.read_registry(), OWNER_ACCOUNT_ID).on
        with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
            assert features.command_refusal("/ide status")
    finally:
        people.set_features(OWNER_ACCOUNT_ID, {"development": True}, by=OWNER_ACCOUNT_ID)


def test_a_new_person_starts_with_n18s_features(matrix, enforced) -> None:
    from kazma_core.spaces import identity

    held = features.grants_of(identity.read_registry(), matrix.account_b).on
    assert held == {"documents", "knowledge", "research", "web", "images_voice"}


def test_without_spaces_nothing_is_refused_and_nothing_is_read(matrix, monkeypatch) -> None:
    from kazma_core.spaces import identity

    monkeypatch.setattr(identity, "read_registry", lambda: (_ for _ in ()).throw(
        AssertionError("the identity store was read")))
    with matrix.as_b():
        assert features.grants_here() is None
        assert features.command_refusal("/x status") is None
        assert features.work_refusal("x_studio", matrix.account_b) is None
        assert features.feature_here("x_studio")


def test_an_unreadable_identity_store_offers_no_feature_tool(y_and_g, monkeypatch) -> None:
    from kazma_core.spaces import identity
    from kazma_core.spaces.tools import tool_refusal

    def away():
        raise identity.IdentityUnavailable("away")

    monkeypatch.setattr(identity, "read_registry", away)
    with y_and_g.as_a():
        assert tool_refusal("x_status")


# ── the work of each feature, checked again when it runs ─────────────────


def test_a_scheduled_job_of_a_person_without_scheduled_does_not_fire(y_and_g) -> None:
    from kazma_core.cron.scheduler import _fire_binding
    from kazma_core.cron.store import ScheduledJob

    job = ScheduledJob(job_id="cron-feature", timing="2h", prompt="p", platform="telegram",
                       thread_id="t", kind="task", tenant_id=Matrix.SPACE_B, creator=y_and_g.account_b)
    assert _fire_binding(job).refusal == "feature_off"


def test_a_swarm_task_of_a_person_without_swarm_runs_no_worker(y_and_g) -> None:
    from kazma_core.swarm.worker_dispatch import _TaskOfNoSpace, _worker_scope

    with pytest.raises(_TaskOfNoSpace, match="Swarm is off"):
        _worker_scope(Matrix.SPACE_B, y_and_g.account_b)
    people.set_features(y_and_g.account_b, {"swarm": True}, by=OWNER_ACCOUNT_ID)
    assert callable(_worker_scope(Matrix.SPACE_B, y_and_g.account_b))


def test_a_document_job_of_a_person_without_documents_stops(y_and_g) -> None:
    from kazma_core.documents.worker import _documents_off_for

    assert not _documents_off_for(y_and_g.account_b)  # N18: on
    people.set_features(y_and_g.account_b, {"documents": False}, by=OWNER_ACCOUNT_ID)
    assert _documents_off_for(y_and_g.account_b)
    assert not _documents_off_for("operator")  # a record from before Spaces: the space's rules


def test_a_crawl_of_a_person_without_knowledge_waits(y_and_g, monkeypatch) -> None:
    from kazma_core.stores import knowledge
    from kazma_ui import kb_api

    class _Store:
        def library_space(self, library_id):
            return Matrix.SPACE_B

    monkeypatch.setattr(knowledge, "get_knowledge_store", lambda: _Store())
    monkeypatch.setattr("kazma_core.spaces.identity.acting_account", lambda space: y_and_g.account_b)
    people.set_features(y_and_g.account_b, {"knowledge": False}, by=OWNER_ACCOUNT_ID)
    resumed: list[str] = []

    async def route(job_id):
        resumed.append(job_id)
        return {"ok": True}

    answer = asyncio.run(kb_api._in_the_crawls_space({"job_id": "j1", "library_id": "lib"}, route))
    assert answer == {"ok": False, "code": "feature_off"} and resumed == []


# ── People, and the precondition of a second account (S1-33) ─────────────


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.people_api import create_people_router

    who: dict[str, Any] = {"principal": {"source": "secret", "role": "admin", "user_id": "",
                                         "actor": "secret", "username": "admin"}}
    monkeypatch.setattr(auth, "get_request_principal", lambda request: who["principal"])
    monkeypatch.setattr(auth, "admin_decision",
                        lambda request: "ok" if who["principal"].get("role") == "admin" else "forbidden")
    app = FastAPI()
    app.include_router(create_people_router())
    with TestClient(app) as made:
        made.who = who
        yield made


def test_people_switches_features_and_records_it(matrix, enforced, client) -> None:
    from kazma_core.spaces import identity

    url = f"/api/people/{matrix.account_b}/features"
    assert client.put(url, json={"features": {"x_studio": True, "web": False}}).json()["ok"]
    listed = next(p for p in client.get("/api/people").json()["people"] if p["id"] == matrix.account_b)
    on = {f["id"] for f in listed["features"] if f["on"]}
    assert "x_studio" in on and "web" not in on
    assert identity.read_registry()["events"][-1]["kind"] == "features_set"
    assert client.put(url, json={"features": {"teleport": True}}).json()["error"] == "unknown_feature"
    assert client.put(url, json={"features": {"web": "yes"}}).status_code == 400
    owner = next(p for p in client.get("/api/people").json()["people"] if p["owner"])
    assert all(f["fixed"] for f in owner["features"] if f["id"] != "development")
    client.who["principal"] = dict(matrix.principal_a)
    assert client.put(url, json={"features": {"x_studio": True}}).status_code == 403


@pytest.mark.real_preconditions
def test_the_precondition_holds_and_names_a_stored_unknown_feature(matrix, enforced) -> None:
    from kazma_core.spaces import identity, preconditions

    assert features.precondition()[0]
    assert preconditions._built("feature_grants", "features per person")[0]

    def plant(reg):
        reg["policies"][matrix.account_b] = {"features": {"teleport": True}}

    identity.update_registry(plant)
    holds, why = features.precondition()
    assert not holds and "teleport" in why
