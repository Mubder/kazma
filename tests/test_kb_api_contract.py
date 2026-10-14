"""The Knowledge API's contract (Knowledge audit 2026-10-13): stable codes
for every refusal (KB-S05), bounded and validated bodies (KB-S13), the
single-operator rule (KB-S04) and a library list with no storage side
effects (KB-S15).
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core import config_store
from kazma_core.stores import knowledge as kb_module
from kazma_core.stores import knowledge_index as ki
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode
from kazma_ui import kb_api

REPO = Path(__file__).resolve().parents[1]
KB_API = REPO / "kazma-ui/kazma_ui/kb_api.py"


class _NoVectors:
    available = False
    identity = ""

    def drop(self):
        return False

    def query(self, *a, **k):
        return []


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    journal = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: journal)
    opened: list[str] = []

    def _opened(self, library_id):
        opened.append(library_id)
        return _NoVectors()

    monkeypatch.setattr(ki.KnowledgeIndex, "_vector_store_for", _opened)
    monkeypatch.setattr(ki.KnowledgeIndex, "_open_vector_store", _opened)
    ki.reset_knowledge_index()
    store = kb_module.KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    monkeypatch.setattr(kb_module, "_knowledge_store", store)
    app = FastAPI()
    app.include_router(kb_api.create_kb_router())
    yield SimpleNamespace(client=TestClient(app), store=store, app=app, opened=opened)
    ki.reset_knowledge_index()
    store.close()
    journal.close()


def _catalog() -> dict[str, dict[str, str]]:
    from kazma_ui.i18n.catalog.knowledge import TRANSLATIONS

    return TRANSLATIONS


# ── KB-S05: every refusal is a stable code with words in both languages ───


@pytest.mark.parametrize("code", [c for c in FetchErrorCode], ids=lambda c: c.value)
def test_every_acquisition_refusal_is_a_stable_code(api, monkeypatch, caplog, code):
    from kazma_core.stores import knowledge_ingest

    async def refuse(*a, **k):
        raise AcquisitionError(code, f"refused: {code.value}", transient=code.value.endswith("throttled"))

    monkeypatch.setattr(knowledge_ingest, "_prepare_ingest_site", refuse)
    with caplog.at_level(logging.DEBUG):
        resp = api.client.post("/api/kb/ingest", json={"library_id": "docs", "mode": "site",
                                                       "url": "https://docs.example.test/"})
    body = resp.json()
    assert 400 <= resp.status_code < 600 and resp.status_code != 500
    assert body["ok"] is False and body["code"] == code.value
    assert isinstance(body["transient"], bool)
    entry = _catalog()[f"knowledge.err.{code.value}"]
    assert entry["en"] and entry["ar"] and entry["en"] != entry["ar"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not api.store.library_exists("docs")  # nothing created for a refused crawl


def test_a_second_crawl_of_a_library_is_its_own_code(api, monkeypatch):
    import kazma_core.security.ssrf as ssrf
    from kazma_core.web_acquire import crawl_jobs

    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ())
    running = crawl_jobs.start("docs", "https://docs.example.test/", {"mode": "local_only"})
    try:
        resp = api.client.post("/api/kb/ingest", json={"library_id": "docs", "mode": "site",
                                                       "url": "https://docs.example.test/"})
        assert resp.status_code == 409 and resp.json()["code"] == "library_busy"
    finally:
        running.finish("completed")


def _codes_answered(source: str) -> set[str]:
    """Every literal code the module passes to ``_fail``."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_fail" and node.args
                and isinstance(node.args[0], ast.Constant)):
            found.add(node.args[0].value)
    return found


def test_every_code_the_api_answers_is_declared_and_worded():
    answered = _codes_answered(KB_API.read_text(encoding="utf-8"))
    assert answered <= set(kb_api.KB_ERROR_CODES), answered - set(kb_api.KB_ERROR_CODES)
    catalog = _catalog()
    for code in list(kb_api.KB_ERROR_CODES) + ["library_busy"]:
        entry = catalog.get(f"knowledge.err.{code}")
        assert entry and entry["en"] and entry["ar"], code
    worded = {k.split("knowledge.err.", 1)[1] for k in catalog if k.startswith("knowledge.err.")}
    assert worded <= set(kb_api.KB_ERROR_CODES) | {c.value for c in FetchErrorCode}, "stale catalog keys"


def test_the_code_gate_sees_an_undeclared_code():
    """Negative control: a route answering a code nobody declared."""
    assert "made_up" in _codes_answered('_fail("made_up", "x", 400)')


def test_the_old_mapping_lost_the_code():
    """Negative control: ``safe_error`` turned every refusal into a generic
    code, logged as an ERROR with a traceback."""
    from kazma_core.errors import safe_error

    text = safe_error(AcquisitionError(FetchErrorCode.ROBOTS, "robots.txt disallows this"), log=False)
    assert FetchErrorCode.ROBOTS.value not in str(text)


def test_the_page_words_a_refusal_from_its_code():
    script = (REPO / "kazma-ui/kazma_ui/static/js/kb.js").read_text(encoding="utf-8")
    assert "'knowledge.err.' + " in script or '"knowledge.err." + ' in script


# ── KB-S13: bodies are bounded and validated ───────────────────────────────


def test_no_route_reads_its_body_unbounded():
    tree = ast.parse(KB_API.read_text(encoding="utf-8"))
    assert _unbounded_reads(tree) == []


def _unbounded_reads(tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name == "Body" or (name in ("json", "body", "form") and isinstance(node.func, ast.Attribute)
                                  and getattr(node.func.value, "id", "") == "request"):
                found.append(ast.unparse(node))
    return found


def test_the_body_gate_sees_body_and_request_json():
    """Negative control: the old routes' ``Body(...)`` and a raw read."""
    assert len(_unbounded_reads(ast.parse("def f(body = Body(...)): return request.json()"))) == 2


BODY_ROUTES = [("POST", "/api/kb/libraries"), ("PATCH", "/api/kb/libraries/docs"),
               ("POST", "/api/kb/ingest"), ("POST", "/api/kb/search")]


@pytest.mark.parametrize(("method", "path"), BODY_ROUTES)
def test_malformed_and_oversized_bodies_are_refused(api, method, path):
    api.store.create_library("docs", "Docs")
    c = api.client
    huge = json.dumps({"id": "x", "name": "n" * (70 * 1024)})
    assert c.request(method, path, content=huge, headers={"content-type": "application/json"}).status_code == 413

    def chunks():
        yield b'{"id": "x", "name": "'
        for _ in range(80):
            yield b"n" * 1024
        yield b'"}'

    resp = c.request(method, path, content=chunks(), headers={"content-type": "application/json"})
    assert resp.status_code == 413 and resp.json()["code"] == "request_too_large"
    for raw, code in ((b"{not json", "invalid_json"), (b"[1, 2]", "invalid_json"), (b'"text"', "invalid_json"),
                      (b"[" * 100_000 + b"]" * 100_000, None)):
        resp = c.request(method, path, content=raw, headers={"content-type": "application/json"})
        assert resp.status_code in (413, 422), (raw[:20], resp.status_code)
        if code and resp.status_code == 422:
            assert resp.json()["code"] == code


@pytest.mark.parametrize(("body", "ok"), [
    ({"library_id": "docs", "query": "x", "top_k": 0}, False),
    ({"library_id": "docs", "query": "x", "top_k": 21}, False),
    ({"library_id": "docs", "query": "x", "top_k": 20}, True),
    ({"library_id": "docs", "query": "x", "top_k": "lots"}, False),
    ({"library_id": "docs", "query": "q" * 2001}, False),
    ({"library_id": "docs", "query": "q" * 2000}, True),
    ({"library_id": "docs", "query": ""}, False),
])
def test_search_bounds(api, body, ok):
    api.store.create_library("docs", "Docs")
    resp = api.client.post("/api/kb/search", json=body)
    assert (resp.status_code == 200) is ok, resp.json()
    if not ok:
        assert resp.status_code == 422 and resp.json()["code"] == "invalid_request"


def test_create_bounds(api):
    c = api.client
    assert c.post("/api/kb/libraries", json={"id": "x", "name": "n" * 501}).status_code == 422
    assert c.post("/api/kb/libraries", json={"id": "x", "description": "d" * 501}).status_code == 422
    assert c.post("/api/kb/libraries",
                  json={"id": "x", "seed_url": "https://e.test/" + "p" * 2040}).status_code == 422
    long_id = c.post("/api/kb/libraries", json={"id": "Long Library " * 12})
    assert long_id.status_code == 200 and len(long_id.json()["library"]["id"]) <= kb_module.MAX_LIBRARY_ID
    # An id with no letter or digit takes the fallback slug, never an empty id.
    assert c.post("/api/kb/libraries", json={"id": "!!!"}).json()["library"]["id"] == "library"


def test_the_agent_tool_clamps_top_k(api, monkeypatch):
    import asyncio

    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict

    api.store.create_library("docs", "Docs")
    for n in range(30):
        chunks = [chunk_to_dict(c) for c in chunk_markdown_doc(
            f"# Webhooks {n}\n\nWebhooks retry, page {n}.\n", source_url=f"https://e.test/{n}", library_id="docs")]
        ki.get_knowledge_index().index("docs", chunks)
    seen: list[int] = []
    real = ki.KnowledgeIndex.search_sync

    def spy(self, query, library_id, *, top_k=5, **kw):
        seen.append(top_k)
        return real(self, query, library_id, top_k=top_k, **kw)

    monkeypatch.setattr(ki.KnowledgeIndex, "search_sync", spy)
    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    out = asyncio.run(registry.get_tool("knowledge_search").func(query="webhooks retry", library="docs",
                                                                  top_k=500))
    assert out.count("## [") == ki.MAX_TOP_K
    assert all(k <= ki.MAX_TOP_K for k in seen)


def test_ingest_has_one_resume_path(api, monkeypatch):
    from kazma_core.stores import knowledge_ingest

    calls: list[dict] = []

    async def prepare(library_id, url, **kwargs):
        calls.append(kwargs)
        raise AcquisitionError(FetchErrorCode.POLICY, "stop here")

    monkeypatch.setattr(knowledge_ingest, "_prepare_ingest_site", prepare)
    api.client.post("/api/kb/ingest", json={"library_id": "docs", "mode": "site", "url": "https://e.test/",
                                            "resume_job_id": "docs:old"})
    assert calls and calls[0]["resume"] is False and calls[0]["job_id"] != "docs:old"
    assert "/api/kb/jobs/{job_id}/resume" in {r.path for r in kb_api.create_kb_router().routes}


def test_a_page_that_was_not_stored_is_not_ok(api, monkeypatch):
    from kazma_core.stores import knowledge_ingest
    from kazma_core.stores.knowledge_ingest import IngestResult

    async def failed(library_id, url, **kw):
        return IngestResult(pages_failed=1, errors=["HTTP 404"])

    monkeypatch.setattr(knowledge_ingest, "ingest_url", failed)
    resp = api.client.post("/api/kb/ingest", json={"library_id": "docs", "url": "https://e.test/x"})
    assert resp.status_code == 422 and resp.json()["code"] == "page_not_ingested"


def _walk_requests(app=None) -> list[tuple[str, str]]:
    """Every method of every Knowledge route, from the router's own table."""
    out = []
    for route in kb_api.create_kb_router().routes:
        for method in sorted(getattr(route, "methods", ()) or ()):
            if method in ("HEAD", "OPTIONS"):
                continue
            path = route.path.replace("{library_id}", "x" * 300).replace("{job_id}", "%00" + "j" * 300)
            out.append((method, path))
    return out


def test_no_route_answers_500_to_hostile_input(api):
    hostile = [None, {}, {"id": None}, {"top_k": 1e308}, {"query": {"$ne": 1}}, {"seed_url": ["x"]},
               {"library_id": "../../etc", "url": "https://e.test/", "mode": "nope"}]
    walked = _walk_requests(api.app)
    assert len(walked) >= 16
    for method, path in walked:
        for body in hostile:
            kwargs = {} if method in ("GET", "DELETE") else {"json": body}
            resp = api.client.request(method, path, **kwargs)
            assert resp.status_code < 500 or resp.status_code in (503, 507), (method, path, body, resp.text)


# ── KB-S04: the single-operator rule ────────────────────────────────────────


def test_a_multi_user_install_refuses_every_route(api, monkeypatch):
    import kazma_core.security.platform_rbac as rbac

    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [{"username": "a"}])
    walked = _walk_requests(api.app)
    for method, path in walked:
        resp = api.client.request(method, path, **({} if method in ("GET", "DELETE") else {"json": {}}))
        assert resp.status_code == 409 and resp.json()["code"] == "kb_single_operator", (method, path)
    entry = _catalog()["knowledge.err.kb_single_operator"]
    assert entry["en"] and entry["ar"]


def test_a_check_that_cannot_answer_refuses(api, monkeypatch):
    import kazma_core.security.platform_rbac as rbac

    def broken():
        raise RuntimeError("settings store is down")

    monkeypatch.delenv("KAZMA_MULTI_USER", raising=False)
    monkeypatch.setattr(rbac, "_load_users_from_store", broken)
    assert api.client.get("/api/kb/libraries").json()["code"] == "kb_single_operator"


def test_one_operator_is_served(api, monkeypatch):
    import kazma_core.security.platform_rbac as rbac

    monkeypatch.delenv("KAZMA_MULTI_USER", raising=False)
    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [])
    assert api.client.get("/api/kb/libraries").json()["ok"] is True


def test_every_knowledge_surface_asks_the_rule():
    """The API (its route class), the page, the agent's tools and ``/kb``."""
    sources = {
        "kazma-ui/kazma_ui/kb_api.py": "kb_refusal",
        "kazma-ui/kazma_ui/routes_direct/misc.py": "kb_refusal",
        "kazma-core/kazma_core/agent/tool_builtins/knowledge.py": "kb_refusal",
        "kazma-gateway/kazma_gateway/agent_handler/commands.py": "kb_refusal",
    }
    for rel, name in sources.items():
        assert name in (REPO / rel).read_text(encoding="utf-8"), rel
    template = (REPO / "kazma-ui/kazma_ui/templates/knowledge_base.html").read_text(encoding="utf-8")
    assert "{% if kb_single_operator %}" in template and "t('knowledge.single_operator')" in template
    assert all(_catalog()["knowledge.single_operator"].get(lang) for lang in ("en", "ar"))
    assert all(r.__class__ is kb_api._KbRoute or not hasattr(r, "endpoint")
               for r in kb_api.create_kb_router().routes)


def test_without_the_route_class_a_multi_user_install_was_served(api, monkeypatch):
    """Negative control: the same router built with FastAPI's own route
    class serves a multi-user install."""
    import kazma_core.security.platform_rbac as rbac
    from fastapi.routing import APIRoute

    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [{"username": "a"}])
    monkeypatch.setattr(kb_api, "_KbRoute", APIRoute)
    app = FastAPI()
    app.include_router(kb_api.create_kb_router())
    assert TestClient(app).get("/api/kb/libraries").json()["ok"] is True


# ── KB-S15: listing libraries changes nothing ──────────────────────────────


def test_listing_libraries_opens_no_vector_store_and_loads_no_model(api, monkeypatch):
    from kazma_core.memory import vector_store_global as vsg

    loaded: list[str] = []
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: loaded.append("model"))
    for n in range(3):
        api.store.create_library(f"lib-{n}", f"Lib {n}")
    api.opened.clear()
    body = api.client.get("/api/kb/libraries").json()
    assert body["ok"] and len(body["libraries"]) == 3
    assert api.opened == [] and loaded == []
    assert {lib["meaning_search"]["state"] for lib in body["libraries"]} == {"unknown"}


def test_the_old_health_opened_every_collection(api):
    """Negative control: health read the collection's raw count."""
    api.store.create_library("lib-0", "Lib 0")
    index = ki.get_knowledge_index()
    api.opened.clear()

    def old_health(library_id):
        vs = index._vector_store_for(library_id)
        return {"vector_chunks": 0 if not vs.available else vs.count()}

    old_health("lib-0")
    assert api.opened == ["lib-0"]
