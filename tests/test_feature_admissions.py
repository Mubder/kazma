"""Spaces S2-11 review: each feature is checked where its work is admitted.

The review of features per person found doors that did not pass the check:
the intent engine started deep research with Research off, the Scheduled
page booked X posts with X Studio off (and a booked post was checked
against the space's acting account, not the person who booked it), live
voice's socket was never refused, a crawl or a research run started before
a switch ran on, and a feature switched off was reported as "no space" and
failed a person's post for good.

The rule: every feature names the functions where its work is admitted
(``features.ADMISSIONS``), every door passes one of them, and each asks
``work_refusal`` for the person the work is for (a gate, with a planted
admission that does not ask as its control). Work that cannot run waits
(``FeatureOff``): nothing failed or held, it goes once the feature is on.

Also here: the intent engine's handlers each name their features; a
socket of a feature is refused at the handshake; every chat-app command
handler of a feature asks it, whatever spelling reached it; refusals name
the feature in the person's language and a page says so as a page;
indexing and searching libraries through Documents needs Knowledge too.
"""

from __future__ import annotations

import ast
import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import WebSocket
from kazma_core.spaces import features, people, scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._space_matrix import Matrix

pytestmark = pytest.mark.real_features

REPO = Path(__file__).resolve().parents[1]


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


def _off(matrix: Matrix, *names: str) -> None:
    people.set_features(matrix.account_b, {name: False for name in names}, by=OWNER_ACCOUNT_ID)


# ── the admission gate ──────────────────────────────────────────────────


def _function(path: str, qualname: str, source: str | None = None) -> ast.AST | None:
    tree = ast.parse(source if source is not None else (REPO / path).read_text(encoding="utf-8"))
    nodes: list[ast.AST] = [tree]
    for part in qualname.split("."):
        found = None
        for node in nodes:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == part:
                    found = child
        if found is None:
            return None
        nodes = [found]
    return nodes[0]


def _own_nodes(function: ast.AST):
    todo = list(ast.iter_child_nodes(function))
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield node
        todo.extend(ast.iter_child_nodes(node))


def asks_work_refusal(function: ast.AST) -> bool:
    """Whether *function*'s own body asks ``work_refusal`` (a call, or the
    function handed to a thread)."""
    return any((isinstance(n, ast.Name) and n.id == "work_refusal")
               or (isinstance(n, ast.Attribute) and n.attr == "work_refusal") for n in _own_nodes(function))


def admission_findings(admissions: dict[str, tuple[str, ...]], sources: dict[str, str] | None = None) -> list[str]:
    found = []
    for feature, places in admissions.items():
        for place in places:
            path, qualname = place.split("::", 1)
            function = _function(path, qualname, (sources or {}).get(path))
            if function is None:
                found.append(f"{feature}: {place} does not exist")
            elif not asks_work_refusal(function):
                found.append(f"{feature}: {place} does not ask work_refusal")
    return found


def test_every_admission_asks_work_refusal() -> None:
    assert admission_findings(features.ADMISSIONS) == []


def test_every_feature_names_its_admissions_or_why_it_has_none() -> None:
    assert set(features.ADMISSIONS) | set(features.NO_ADMISSION) == set(features.FEATURES)
    assert not set(features.ADMISSIONS) & set(features.NO_ADMISSION)
    assert all(features.ADMISSIONS.values())


def test_control_a_planted_admission_that_does_not_ask_is_found() -> None:
    planted = {"kazma-core/kazma_core/planted.py": (
        "async def admit(job):\n    return await run(job)\n\n"
        "async def asks(job):\n    from kazma_core.spaces.features import work_refusal\n"
        "    if work_refusal('x_studio', job.booker):\n        return None\n    return await run(job)\n")}
    found = admission_findings({"x_studio": ("kazma-core/kazma_core/planted.py::admit",
                                             "kazma-core/kazma_core/planted.py::asks")}, planted)
    assert found == ["x_studio: kazma-core/kazma_core/planted.py::admit does not ask work_refusal"]


def test_control_the_document_stage_without_its_call_is_found() -> None:
    path = "kazma-core/kazma_core/documents/worker.py"
    source = (REPO / path).read_text(encoding="utf-8").replace(
        'await asyncio.to_thread(work_refusal, "documents", current.actor_id)', "None")
    assert admission_findings({"documents": (f"{path}::DocumentWorker._process_claimed",)}, {path: source})


# ── the intent engine (finding 1) ───────────────────────────────────────


def test_every_intent_handler_names_its_features() -> None:
    from kazma_core.agent.intent.registry import get_registry

    registry = get_registry()
    registered = set(registry._handlers) | {c.name for c in registry._composers.values()}
    assert registered == set(features.INTENT_HANDLERS), registered
    assert all(set(needed) <= set(features.FEATURES) for needed in features.INTENT_HANDLERS.values())


class _Model:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *args, **kwargs):
        from kazma_core.llm_provider import LLMResponse

        self.calls += 1
        return LLMResponse(content="An ordinary answer.", tool_calls=[], finish_reason="stop", model="stub",
                           usage={"total_tokens": 2}, cost_usd=0.0)


def _graph(model: _Model):
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.tracing import KazmaTracer

    return build_supervisor_graph(
        llm=model, system_prompt="You are Kazma.", tool_definitions=[], tool_executor=None,
        cost_breaker=create_cost_breaker(), authority=create_authority(model="test", window=128000),
        tracer=KazmaTracer(backend="console"),
    )


def _deep_research_turn(space: str, account: str, monkeypatch) -> tuple[list[str], int, str]:
    """A real turn of "deep research on the tides of the Gulf" in *space* by
    *account*: the research it started, the model's calls and the answer."""
    from kazma_core.agent.turn import run_agent_turn
    from kazma_core.tools import research_session

    started: list[str] = []

    async def spy(topic, **_kw):
        started.append(topic)
        return SimpleNamespace(id="rs-spy", status="running", stage="plan", error="")

    monkeypatch.setattr(research_session, "start_deep_research", spy)
    model = _Model()
    thread_id = f"t-intent-{uuid.uuid4().hex[:8]}"
    threads.record_thread(thread_id, space, account)
    result = asyncio.run(run_agent_turn(
        graph=_graph(model), thread_id=thread_id,
        state={"messages": [{"role": "user", "content": "deep research on the tides of the Gulf"}]},
        persist=False))
    assert not result.error, result.error
    return started, model.calls, str(result.text or "")


def test_the_intent_engine_starts_no_research_for_a_person_without_it(y_and_g, monkeypatch) -> None:
    _off(y_and_g, "research")
    started, calls, said = _deep_research_turn(Matrix.SPACE_B, y_and_g.account_b, monkeypatch)
    assert started == [] and calls >= 1 and said == "An ordinary answer."
    # Y has Research (N18): the same message starts it.
    started, calls, said = _deep_research_turn(Matrix.SPACE_A, y_and_g.account_a, monkeypatch)
    assert started and calls == 0 and "Deep research started" in said


def test_control_without_the_intent_check_g_s_message_starts_research(y_and_g, monkeypatch) -> None:
    _off(y_and_g, "research")
    monkeypatch.setattr(features, "intent_refusal", lambda handler: None)
    started, _calls, _said = _deep_research_turn(Matrix.SPACE_B, y_and_g.account_b, monkeypatch)
    assert started  # the review's finding: deep research ran with Research off


def test_research_admission_refuses_every_door(y_and_g, monkeypatch) -> None:
    """``_frozen`` is research's one admission: start_deep_research (the
    intent engine, the composer, the page, a chat command, a resume) refuses
    before a session row, and so does the pipeline (the tool)."""
    from kazma_core.tools import research_pipeline, research_session
    from kazma_core.web_acquire.result import AcquisitionError

    _off(y_and_g, "research")
    made: list[str] = []
    monkeypatch.setattr(research_session, "create_session", lambda *a, **k: made.append("row") or 1 / 0)
    with y_and_g.as_b(), pytest.raises(AcquisitionError, match="Research is off"):
        asyncio.run(research_session.start_deep_research("tides of the Gulf"))
    assert made == []
    with y_and_g.as_b():
        answer = asyncio.run(research_pipeline.run_research_pipeline("tides of the Gulf"))
    assert "Research is off" in str(answer)
    # Y passes the admission (stopped at the row, which this test refuses).
    with y_and_g.as_a(), pytest.raises(ZeroDivisionError):
        asyncio.run(research_session.start_deep_research("tides of the Gulf"))
    assert made == ["row"]


# ── X Studio through Scheduled, and its booked posts (findings 2, 4) ────


def test_the_scheduled_page_s_x_booking_is_x_studio_s() -> None:
    from kazma_ui.route_scopes import _feature_of_route

    assert _feature_of_route("/api/scheduled/x", "POST") == "x_studio"
    assert _feature_of_route("/api/scheduled/cron", "POST") == "scheduled"


@pytest.fixture
def booking(monkeypatch):
    from kazma_core.x_api import publication_service, schedule

    booked: list[dict[str, Any]] = []

    def capture(**kwargs):
        booked.append(kwargs["metadata"])
        return True, {"scheduled": True, "id": "op-1"}

    monkeypatch.setattr(schedule, "x_schedule_enabled", lambda: True)
    monkeypatch.setattr(publication_service, "schedule", capture)
    return booked


def test_book_x_post_refuses_a_person_without_x_and_records_the_booker(y_and_g, booking) -> None:
    from kazma_core.x_api.booking import book_x_post

    with y_and_g.as_b():
        ok, payload = book_x_post(text="tides", when="2h", tenant_id=Matrix.SPACE_B)
    assert not ok and payload["code"] == "feature_off" and "X Studio" in payload["error"] and booking == []
    with y_and_g.as_a():
        ok, payload = book_x_post(text="tides", when="2h", tenant_id=Matrix.SPACE_A)
    assert ok and booking[0]["booker"] == y_and_g.account_a


def test_control_without_its_check_g_books_an_x_post(y_and_g, booking, monkeypatch) -> None:
    from kazma_core.x_api.booking import book_x_post

    monkeypatch.setattr(features, "work_refusal", lambda feature, account: None)
    with y_and_g.as_b():
        ok, _payload = book_x_post(text="tides", when="2h", tenant_id=Matrix.SPACE_B)
    assert ok  # the review's finding


class _Publications:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row, self.held, self.sent = row, [], []

    def recover(self):
        return None

    def unconfirmed_tenants(self):
        return []

    def repair_tenants(self):
        return []

    def due(self):
        return [self.row]

    def hold(self, op_id, *, tenant_id, reason):
        self.held.append((op_id, reason))


def _fire(monkeypatch, row: dict[str, Any]) -> _Publications:
    from kazma_core.x_api import notifications, publication_service, schedule

    store = _Publications(row)
    monkeypatch.setattr(publication_service, "get_publication_store", lambda: store)
    monkeypatch.setattr(schedule, "get_x_scheduled_store", lambda: SimpleNamespace(legacy_tenants=lambda: []))
    monkeypatch.setattr(schedule, "x_schedule_enabled", lambda: True)
    monkeypatch.setattr(publication_service, "_retain", lambda: None)

    async def drained():
        return None

    async def dispatch(op_id):
        store.sent.append((op_id, scope.bound_asker()))

    monkeypatch.setattr(notifications, "drain_notifications", drained)
    monkeypatch.setattr(publication_service, "_dispatch_operation", dispatch)
    asyncio.run(publication_service.fire_due_operations())
    return store


def test_a_booked_post_waits_while_its_booker_has_no_x_and_goes_after(y_and_g, monkeypatch) -> None:
    """G booked it while G had X Studio; the admin turned it off. The post
    is neither held nor failed (it waits), and goes as G once it is on."""
    people.set_features(y_and_g.account_b, {"x_studio": False}, by=OWNER_ACCOUNT_ID)
    row = {"id": "op-g", "tenant_id": Matrix.SPACE_B, "metadata": json.dumps({"booker": y_and_g.account_b})}
    store = _fire(monkeypatch, row)
    assert store.held == [] and store.sent == []
    people.set_features(y_and_g.account_b, {"x_studio": True}, by=OWNER_ACCOUNT_ID)
    store = _fire(monkeypatch, row)
    assert store.held == [] and store.sent == [("op-g", y_and_g.account_b)]


def test_control_x_work_scope_of_before_held_the_post_as_no_space(y_and_g, monkeypatch) -> None:
    from kazma_core.x_api import ownership

    real = ownership.x_work_scope

    def before(tenant, booker=None):
        try:
            return real(tenant, booker)
        except features.FeatureOff as exc:
            raise scope.NoSpaceBound(str(exc)) from exc

    monkeypatch.setattr(ownership, "x_work_scope", before)
    row = {"id": "op-g", "tenant_id": Matrix.SPACE_B, "metadata": json.dumps({"booker": y_and_g.account_b})}
    store = _fire(monkeypatch, row)
    assert store.held and "No space" in store.held[0][1]  # the review's finding 4


def test_a_legacy_post_of_a_person_without_x_waits(y_and_g, monkeypatch) -> None:
    from kazma_core.x_api import scheduled_fire

    failed: list[Any] = []
    fired: list[Any] = []
    monkeypatch.setattr(scheduled_fire, "get_x_scheduled_store",
                        lambda: SimpleNamespace(mark_failed=lambda *a: failed.append(a)))

    async def inner(post):
        fired.append(post.id)

    monkeypatch.setattr(scheduled_fire, "_fire_post_inner", inner)
    post = SimpleNamespace(id=7, tenant_id=Matrix.SPACE_B)
    asyncio.run(scheduled_fire._fire_post(post))
    assert failed == [] and fired == []
    people.set_features(y_and_g.account_b, {"x_studio": True}, by=OWNER_ACCOUNT_ID)
    asyncio.run(scheduled_fire._fire_post(post))
    assert failed == [] and fired == [7]


# ── a crawl: its starter, and every page (findings 4, 5) ────────────────


def test_a_crawl_records_who_started_it_and_resumes_as_them(y_and_g, monkeypatch) -> None:
    from kazma_core.stores import knowledge
    from kazma_core.web_acquire import crawl_jobs
    from kazma_ui import kb_api

    with y_and_g.as_b():
        job = crawl_jobs.start("lib-b", "https://tides.example/", {"max_pages": 3}, job_id=f"j-{uuid.uuid4().hex}")
    row = crawl_jobs.inspect_jobs(job.job_id)
    assert row["asker"] == y_and_g.account_b
    monkeypatch.setattr(knowledge, "get_knowledge_store",
                        lambda: SimpleNamespace(library_space=lambda library: Matrix.SPACE_B))
    resumed: list[str | None] = []

    async def route(job_id):
        resumed.append(scope.bound_asker())
        return {"ok": True}

    _off(y_and_g, "knowledge")
    assert asyncio.run(kb_api._in_the_crawls_space(row, route)) == {"ok": False, "code": "feature_off"}
    assert resumed == []
    people.set_features(y_and_g.account_b, {"knowledge": True}, by=OWNER_ACCOUNT_ID)
    assert asyncio.run(kb_api._in_the_crawls_space(row, route)) == {"ok": True}
    assert resumed == [y_and_g.account_b]


@pytest.fixture
def journal(tmp_path, monkeypatch):
    from kazma_core import config_store

    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    yield store
    store.close()


@pytest.fixture
def quick_waits(monkeypatch):
    real = asyncio.sleep

    async def sleep(delay, result=None):
        return await real(0 if delay >= 1 else delay, result)

    monkeypatch.setattr(asyncio, "sleep", sleep)


def _site() -> tuple[dict[str, Any], list[str]]:
    origin = "https://tides.example"
    seed = origin + "/guide/"
    pages = [f"{seed}page-{n}" for n in range(6)]

    class Elsewhere(dict):
        def get(self, key, default=None):
            return super().get(key, {"status": 404, "headers": {"content-type": "text/html"},
                                     "text": "<html><body>Not found</body></html>"})

    responses = Elsewhere()
    responses[origin + "/robots.txt"] = {"status": 200, "headers": {"content-type": "text/plain"},
                                         "text": f"User-agent: *\nAllow: /\nSitemap: {origin}/sitemap.xml\n"}
    locs = "".join(f"<url><loc>{url}</loc></url>" for url in pages)
    responses[origin + "/sitemap.xml"] = {
        "status": 200, "headers": {"content-type": "application/xml"},
        "text": f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'}
    for n, url in enumerate(pages):
        words = " ".join(f"Tide table {n} explains the Gulf's tide number {n} step {k}." for k in range(20))
        responses[url] = {"status": 200, "headers": {"content-type": "text/html"},
                          "text": f"<html><head><title>Tides {n}</title></head><body><main><h1>Tides {n}</h1>"
                                  f"<p>{words}</p></main></body></html>"}
    return {"responses": responses}, pages


async def _crawl(tmp_path, monkeypatch, refuse_after: int | None) -> tuple[dict[str, Any], list[str], list]:
    """The product's whole crawl of a replayed site (``tests/_crawl_replay``),
    Knowledge switched off for its person after *refuse_after* pages."""
    from kazma_core.web_acquire import crawl_jobs

    from tests import _crawl_replay as cr

    asked: list[tuple[str, Any]] = []

    def work_refusal(feature, account):
        asked.append((feature, account))
        if refuse_after is not None and len(asked) > refuse_after:
            return "Not available to you: Knowledge is off."
        return None

    monkeypatch.setattr(features, "work_refusal", work_refusal)
    recording, pages = _site()
    site = {"name": "tides", "seed": pages[0].rsplit("/", 1)[0] + "/", "max_pages": len(pages) + 1,
            "scope": "section"}
    with cr.replaying(monkeypatch, recording) as transport:
        await cr.crawl(site, tmp_path / "kb", monkeypatch)
    fetched = sorted(url for url in set(transport.served) if url in pages)
    return crawl_jobs.inspect_jobs("tides:corpus"), fetched, asked


@pytest.mark.asyncio
async def test_a_crawl_stops_before_the_next_page_once_knowledge_is_off(journal, quick_waits, tmp_path,
                                                                         monkeypatch) -> None:
    row, fetched, asked = await _crawl(tmp_path, monkeypatch, refuse_after=2)
    # Asked before every page: the seed's own address (the frontier's first,
    # which the site answers 404) and page 0 ran; the third ask stopped it.
    assert asked and {feature for feature, _account in asked} == {"knowledge"} and len(asked) == 3
    assert len(fetched) == 1, fetched
    assert row["status"] == "interrupted" and row["interrupted_by"] == "feature_off"
    assert [p for p in row["pages"] if p["status"] == "pending"]  # waiting for a Resume
    from kazma_core.web_acquire import crawl_jobs

    assert "Knowledge is off" in crawl_jobs.failure_reason(row)


@pytest.mark.asyncio
async def test_control_a_crawl_that_never_asks_runs_to_its_end(journal, quick_waits, tmp_path, monkeypatch) -> None:
    row, fetched, _asked = await _crawl(tmp_path, monkeypatch, refuse_after=None)
    assert row["status"] == "completed" and len(fetched) == 6


# ── a document stage, the real worker (findings 5, 10) ──────────────────


def _document_service(root):
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.ingestion import DocumentIngestionService

    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=root, security_malware_scan="off", security_local_salvage=False, ocr_enabled=False))
    svc.jobs._jitter = lambda _base: 0.0
    return svc


def _stage(svc, matrix: Matrix) -> Any:
    import io

    from kazma_core.documents.worker import DocumentWorker

    with matrix.as_b():
        result = svc.ingest_stream(io.BytesIO(b"tides of the Gulf\n"), filename="tides.txt",
                                   tenant_id=Matrix.SPACE_B, actor_id=matrix.account_b, workspace_id="w")
    claimed = svc.jobs.claim_next(owner="worker", lease_seconds=60)
    asyncio.run(DocumentWorker(svc.jobs, svc.stage_handlers())._process_claimed(claimed, "worker"))
    return svc.jobs.get(tenant_id=Matrix.SPACE_B, job_id=result.job_id)


def test_a_document_job_of_a_person_without_documents_stops_in_the_real_worker(y_and_g, tmp_path) -> None:
    svc = _document_service(tmp_path / "documents")
    _off(y_and_g, "documents")
    job = _stage(svc, y_and_g)
    assert job.error_code == "feature_off" and job.state.value in ("rejected", "dead_letter"), job


def test_control_the_worker_without_its_check_processes_the_job(y_and_g, tmp_path, monkeypatch) -> None:
    svc = _document_service(tmp_path / "documents")
    _off(y_and_g, "documents")
    monkeypatch.setattr(features, "work_refusal", lambda feature, account: None)
    job = _stage(svc, y_and_g)
    assert job.error_code != "feature_off"


def test_an_identity_outage_in_a_stage_is_retried_as_an_outage(y_and_g, tmp_path, monkeypatch) -> None:
    from kazma_core.spaces import identity

    svc = _document_service(tmp_path / "documents")

    def away(feature, account):
        raise identity.IdentityUnavailable("away")

    monkeypatch.setattr(features, "work_refusal", away)
    job = _stage(svc, y_and_g)
    assert job.error_code == "metadata_unavailable" and job.state.value == "retry_wait", job


# ── live voice's socket (finding 3) ─────────────────────────────────────


@pytest.fixture(scope="module")
def socket_routes() -> list[str]:
    from kazma_ui.app import create_app

    from tests.test_route_scopes import served_routes

    return sorted(path for method, path in served_routes(create_app()) if method == "WEBSOCKET")


#: Every socket of a feature, and the test that drives it as a person
#: without the feature.
SOCKET_DRIVEN = {"/ws/voice": "test_live_voice_refuses_a_person_without_it"}


def socket_findings(sockets: list[str]) -> list[str]:
    from kazma_ui.route_scopes import features_of_route

    return sorted(path for path in sockets if features_of_route(path, "WEBSOCKET") and path not in SOCKET_DRIVEN)


def test_every_socket_of_a_feature_is_driven_as_a_person_without_it(socket_routes) -> None:
    assert socket_findings(socket_routes) == []
    assert set(SOCKET_DRIVEN) <= set(socket_routes)
    assert all(name in globals() for name in SOCKET_DRIVEN.values())


def test_control_a_planted_socket_of_a_feature_is_found(socket_routes, monkeypatch) -> None:
    from kazma_ui import route_scopes

    monkeypatch.setattr(route_scopes, "features_of_route",
                        lambda path, method="GET": ("images_voice",) if path == "/ws/planted" else ())
    assert socket_findings([*socket_routes, "/ws/planted"]) == ["/ws/planted"]


def _voice(matrix: Matrix, who: str, monkeypatch) -> tuple[int, str]:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth, routes_voice_ws
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setattr(auth, "websocket_principal", lambda websocket: matrix.principal(who))
    monkeypatch.setattr(auth, "principal_allows", lambda principal, path, method: True)

    async def handled(websocket, *args, **kwargs):
        await websocket.accept()
        await websocket.close(code=1000)

    monkeypatch.setattr(routes_voice_ws, "handle_voice_websocket", handled)
    app = FastAPI()

    @app.websocket("/ws/voice")
    async def voice(websocket: WebSocket) -> None:
        await routes_voice_ws.serve_voice_websocket(websocket)

    space = Matrix.SPACE_A if who == "a" else Matrix.SPACE_B
    with TestClient(app) as client:
        try:
            with client.websocket_connect(f"/ws/voice?space={space}") as socket:
                socket.receive_text()
        except WebSocketDisconnect as closed:
            return closed.code, closed.reason or ""
    return 0, ""


def test_live_voice_refuses_a_person_without_it(y_and_g, monkeypatch) -> None:
    _off(y_and_g, "images_voice")
    assert _voice(y_and_g, "b", monkeypatch) == (4403, "feature_off")
    assert _voice(y_and_g, "a", monkeypatch) == (1000, "")  # Y has it (N18)


def test_control_the_handshake_without_its_check_lets_g_in(y_and_g, monkeypatch) -> None:
    from kazma_ui import space_binding

    _off(y_and_g, "images_voice")
    monkeypatch.setattr(space_binding, "_socket_feature_refusal", lambda path, binding: None)
    assert _voice(y_and_g, "b", monkeypatch) == (1000, "")  # the review's finding 3


# ── chat commands, whatever reached them (finding 6) ────────────────────


#: Each chat app's command handler and the feature it belongs to; every
#: ``_try_*_command`` is here or always on.
COMMAND_HANDLERS = {
    "_try_swarm_command": "swarm",
    "_try_ide_command": "development",
    "_try_kb_command": "knowledge",
    "_try_documents_command": "documents",
    "_try_research_command": "research",
    "_try_skill_command": "development",
    "_try_x_command": "x_studio",
}
ALWAYS_ON_HANDLERS = {"_try_model_command": "a chat's own model (/model): chat"}


def handler_findings(source: str) -> list[str]:
    found = []
    for node in ast.parse(source).body:
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("_try_") and node.name.endswith("_command")):
            continue
        if node.name in ALWAYS_ON_HANDLERS:
            continue
        feature = COMMAND_HANDLERS.get(node.name)
        if feature is None:
            found.append(f"{node.name}: no feature")
            continue
        asks = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_command_claimed"
                   and any(isinstance(a, ast.Constant) and a.value == feature for a in n.args)
                   for n in ast.walk(node))
        if not asks:
            found.append(f"{node.name}: does not ask _command_claimed for {feature}")
    return found


COMMANDS_PY = REPO / "kazma-gateway" / "kazma_gateway" / "agent_handler" / "commands.py"


def test_every_chat_command_handler_asks_its_feature() -> None:
    assert handler_findings(COMMANDS_PY.read_text(encoding="utf-8")) == []


def test_control_a_planted_handler_and_one_that_does_not_ask_are_found() -> None:
    source = COMMANDS_PY.read_text(encoding="utf-8").replace(
        '_command_claimed(msg, store, manager, thread_id, "x_studio",', '_unchecked(msg, store, manager, thread_id,')
    source += "\n\nasync def _try_planted_command(msg):\n    return False\n"
    assert handler_findings(source) == ["_try_x_command: does not ask _command_claimed for x_studio",
                                        "_try_planted_command: no feature"]


@pytest.mark.parametrize(("text", "feature"), [
    ("/kbx list", "knowledge"), ("/researchfoo tides", "research"), ("/research@kazma_bot deep tides", "research"),
    ("/KB list", "knowledge"), ("/x@kazma_bot status", "x_studio"),
])
def test_a_command_is_read_as_the_handlers_read_it(y_and_g, text, feature) -> None:
    _off(y_and_g, "knowledge", "research")
    with y_and_g.as_b():
        refused = features.command_refusal(text)
    assert refused and features.NAMES[feature][0] in refused, (text, refused)


def test_always_on_commands_are_never_read_as_a_feature_s(y_and_g) -> None:
    _off(y_and_g, "swarm", "scheduled", "x_studio")
    with y_and_g.as_b():
        for text in ("/status", "/switch s1", "/steer x", "/sessions", "/memory list", "/help"):
            assert features.command_refusal(text) is None, text


class _Manager:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message) -> None:
        self.sent.append(message.text)


class _Store:
    async def get(self, thread_id):
        return {}


def test_a_bare_swarm_request_is_refused_by_its_handler(y_and_g, monkeypatch) -> None:
    from kazma_core.swarm import engine
    from kazma_gateway.agent_handler.commands import _try_swarm_command
    from kazma_gateway.gateway import IncomingMessage

    monkeypatch.setattr(engine, "get_swarm_engine", lambda: 1 / 0)
    manager = _Manager()
    msg = IncomingMessage(platform="telegram", sender_id="telegram:1", text="use the swarm to map the tides")
    with y_and_g.as_b():
        handled = asyncio.run(_try_swarm_command(msg, {}, _Store(), manager, "t-1"))
    assert handled and manager.sent and "Swarm" in manager.sent[0]


def test_a_refusal_is_in_the_message_s_language(y_and_g) -> None:
    with y_and_g.as_b():
        arabic = features.feature_refusal("x_studio", "انشر تغريدة عن المد")
        english = features.feature_refusal("x_studio", "post a tweet about the tides")
    assert "استوديو X" in arabic and "X Studio" not in arabic
    assert "X Studio" in english and "استوديو" not in english


# ── names and pages in the person's language (finding 7) ────────────────


def test_every_feature_is_named_as_the_people_page_names_it() -> None:
    from kazma_ui.i18n import t

    assert set(features.NAMES) == set(features.FEATURES)
    for fid, (english, arabic) in features.NAMES.items():
        assert t(f"settings.feature.{fid}", "en") == english
        assert t(f"settings.feature.{fid}", "ar") == arabic


def _page(matrix: Matrix, lang: str, path: str) -> tuple[int, str, str]:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth, space_binding
    from kazma_ui.i18n import set_request_language

    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())

    @app.middleware("http")
    async def language(request, call_next):
        set_request_language(request.cookies.get("kazma-lang", "en"))
        return await call_next(request)

    @app.get("/{rest:path}")
    def handler(rest: str) -> dict[str, Any]:
        return {"reached": rest}

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(auth, "get_request_principal", lambda request: matrix.principal("b"))
        with TestClient(app, cookies={"kazma-lang": lang}) as client:
            answer = client.get(path, headers={space_binding.SPACE_HEADER: Matrix.SPACE_B})
    return answer.status_code, answer.headers.get("content-type", ""), answer.text


@pytest.mark.parametrize(("lang", "words"), [("en", "X Studio is off for you"), ("ar", "استوديو X")])
def test_a_page_of_a_feature_that_is_off_is_a_page_in_its_language(y_and_g, lang, words) -> None:
    status, kind, text = _page(y_and_g, lang, "/x")
    assert status == 403 and kind.startswith("text/html") and 'id="feature-off"' in text
    assert words in text and f'lang="{lang}"' in text
    status, kind, text = _page(y_and_g, lang, "/api/x/status")
    assert status == 403 and json.loads(text)["code"] == "feature_off" and words in json.loads(text)["error"]


def test_a_refused_scheduled_job_says_so_in_its_own_language() -> None:
    from kazma_core.cron.scheduler import _fire_refusal_text

    assert "Scheduled is off" in _fire_refusal_text("feature_off", "Remind me about the tide table")
    assert "المجدولة" in _fire_refusal_text("feature_off", "ذكرني بجدول المد")
    assert _fire_refusal_text("no_space", "ذكرني").startswith("Error:")


# ── Knowledge through Documents (finding 8) ─────────────────────────────


def test_indexing_and_searching_libraries_through_documents_needs_knowledge(y_and_g) -> None:
    from kazma_core.spaces.tools import tool_grants
    from kazma_ui.route_scopes import features_of_route

    _off(y_and_g, "knowledge")
    with y_and_g.as_b():
        grants = tool_grants()
    for name in ("document_index", "document_search"):
        refused = features.tool_refusal(name, grants)
        assert refused and "Knowledge" in refused, name
    assert features.tool_refusal("document_read", grants) is None  # Documents' own
    assert features_of_route("/api/documents/index", "POST") == ("documents", "knowledge")
    assert features_of_route("/api/documents/search", "POST") == ("documents", "knowledge")
    assert features_of_route("/api/documents/abc", "GET") == ("documents",)


def test_a_person_without_knowledge_gets_no_library_text_in_chat(y_and_g, monkeypatch) -> None:
    from kazma_core.agent import graph_supervisor
    from kazma_core.stores import knowledge_index

    monkeypatch.setattr(knowledge_index, "build_knowledge_inject", lambda text: "LIBRARY TEXT")
    with y_and_g.as_b():
        assert graph_supervisor._knowledge_inject_for_turn("tides") == "LIBRARY TEXT"  # N18: on
    _off(y_and_g, "knowledge")
    with y_and_g.as_b():
        assert graph_supervisor._knowledge_inject_for_turn("tides") is None


def test_the_page_grants_helper_of_before_is_gone() -> None:
    assert not hasattr(features, "features_off_here")
    assert "features_off_here" not in (REPO / "kazma-ui" / "kazma_ui" / "app.py").read_text(encoding="utf-8")

