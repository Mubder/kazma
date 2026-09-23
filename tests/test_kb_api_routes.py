"""The knowledge-base API: behaviour, and no SQLite on the event loop.

The 2026-09-22 audit found no route test for ``kazma_ui/kb_api.py`` at all,
and every handler was ``async def`` calling the synchronous SQLite knowledge
store — and the ConfigStore-backed job registry, once per crawl progress
update — directly on the loop that serves every SSE and WebSocket stream
(AGENTS.md §26E).

The fakes below RECORD every call made on the event loop instead of raising:
the handlers catch ``Exception`` and would turn a raise into a quiet
``ok: false``. The walk drives every route in the router's own table, so a new
route is covered the day it is added — and fails here, asking for a request
body, if the walk does not know how to call it.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class _Recorder:
    def __init__(self) -> None:
        self.on_loop: list[str] = []

    def __call__(self, name: str) -> None:
        if _on_loop():
            self.on_loop.append(name)


class _Store:
    def __init__(self, seen: _Recorder) -> None:
        self.seen = seen
        self.libs: dict[str, dict[str, Any]] = {}
        self.archived: set[str] = set()

    def list_libraries(self):
        self.seen("store.list_libraries")
        return [v for k, v in self.libs.items() if k not in self.archived]

    def list_archived_libraries(self):
        self.seen("store.list_archived_libraries")
        return [v for k, v in self.libs.items() if k in self.archived]

    def get_library(self, lib_id):
        self.seen("store.get_library")
        return self.libs.get(lib_id)

    def create_library(self, lib_id, *, name="", description="", seed_url=""):
        self.seen("store.create_library")
        self.libs[lib_id] = {"id": lib_id, "name": name, "description": description, "seed_url": seed_url}
        return self.libs[lib_id]

    def update_library(self, lib_id, **fields):
        self.seen("store.update_library")
        if lib_id not in self.libs:
            return None
        self.libs[lib_id].update({k: v for k, v in fields.items() if v is not None})
        return self.libs[lib_id]

    def archive_library(self, lib_id, archived=True):
        self.seen("store.archive_library")
        if lib_id not in self.libs:
            return False
        (self.archived.add if archived else self.archived.discard)(lib_id)
        return True

    def list_chunks(self, lib_id, *, limit=100, offset=0):
        self.seen("store.list_chunks")
        chunks = [
            {
                "id": f"{lib_id}-c{i}", "source_url": "https://example.com/", "document_title": "T",
                "section_header": "", "chunk_index": i, "char_count": 1, "has_code": False, "content": "x",
            }
            for i in range(2)
        ]
        return chunks[offset : offset + limit]

    def count_chunks(self, lib_id):
        self.seen("store.count_chunks")
        return 2


class _Index:
    def __init__(self, store: _Store) -> None:
        self.store = store

    def delete_library(self, lib_id):
        self.store.seen("index.delete_library")
        return self.store.libs.pop(lib_id, None) is not None

    async def search(self, query, lib_id, *, top_k):
        from kazma_core.stores.knowledge_index import KnowledgeHit

        return [
            KnowledgeHit(
                chunk_id="c1", content=f"hit for {query}", score=1.0, library_id=lib_id,
                source_url="https://example.com/", document_title="T", section_header="",
                chunk_index=0, has_code=False,
            )
        ][:top_k]


@pytest.fixture
def kb(monkeypatch: pytest.MonkeyPatch):
    import kazma_core.stores.kb_jobs as kb_jobs
    import kazma_core.stores.knowledge as knowledge
    import kazma_core.stores.knowledge_index as knowledge_index
    import kazma_core.stores.knowledge_ingest as knowledge_ingest
    import kazma_ui.kb_api as kb_api

    seen = _Recorder()
    store = _Store(seen)
    index = _Index(store)
    jobs: dict[str, dict[str, Any]] = {}

    def upsert_job(job_id, **fields):
        seen("kb_jobs.upsert_job")
        jobs.setdefault(job_id, {}).update(fields)
        return jobs[job_id]

    def get_job(job_id):
        seen("kb_jobs.get_job")
        return jobs.get(job_id)

    async def ingest_url(lib_id, url):
        return SimpleNamespace(pages_fetched=1, chunks_new=1, chunks_skipped=0, errors=[])

    async def ingest_site(lib_id, url, max_pages=None):
        for phase in ("crawling", "done"):
            yield SimpleNamespace(
                phase=phase, discovered=1, fetched=1, ingested=1, skipped=0, failed=0,
                pages_unchanged=0, pruned_urls=0, current_url=url, message="", errors=[],
            )

    monkeypatch.setattr(knowledge, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(knowledge_index, "get_knowledge_index", lambda: index)
    monkeypatch.setattr(knowledge_ingest, "ingest_url", ingest_url)
    monkeypatch.setattr(knowledge_ingest, "ingest_site", ingest_site)
    monkeypatch.setattr(kb_jobs, "upsert_job", upsert_job)
    monkeypatch.setattr(kb_jobs, "get_job", get_job)
    monkeypatch.setattr(kb_api, "_kb_jobs_bootstrapped", True)
    monkeypatch.setattr(kb_api, "_kb_api_jobs", type(kb_api._kb_api_jobs)())

    app = FastAPI()
    router = kb_api.create_kb_router()
    app.include_router(router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, store=store, seen=seen, jobs=jobs, router=router)


def _wait_for_job(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/api/kb/jobs/{job_id}").json().get("job") or {}
        if job.get("finished_at"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished: {job}")


def test_library_lifecycle(kb):
    c = kb.client
    assert c.post("/api/kb/libraries", json={"id": "Docs Lib", "name": "Docs"}).json()["ok"]
    (lib_id,) = kb.store.libs
    assert [lib["id"] for lib in c.get("/api/kb/libraries").json()["libraries"]] == [lib_id]
    assert c.get(f"/api/kb/libraries/{lib_id}").json()["library"]["name"] == "Docs"
    duplicate = c.post("/api/kb/libraries", json={"id": "Docs Lib"}).json()
    assert duplicate["ok"] is False and "already exists" in duplicate["error"]
    assert c.patch(f"/api/kb/libraries/{lib_id}", json={"auto_inject": "true"}).json()["ok"]
    assert kb.store.libs[lib_id]["auto_inject"] is True
    assert c.post(f"/api/kb/libraries/{lib_id}/archive").json()["ok"]
    assert c.get("/api/kb/libraries").json()["libraries"] == []
    archived = c.get("/api/kb/libraries/archived/list").json()["libraries"]
    assert [lib["id"] for lib in archived] == [lib_id]
    assert c.post(f"/api/kb/libraries/{lib_id}/unarchive").json()["ok"]
    chunks = c.get(f"/api/kb/libraries/{lib_id}/chunks").json()
    assert chunks["ok"] and chunks["total"] == 2 and chunks["chunks"][0]["preview"] == "x"
    assert c.delete(f"/api/kb/libraries/{lib_id}").json()["ok"]
    assert c.get(f"/api/kb/libraries/{lib_id}").json() == {"ok": False, "error": "Not found"}
    assert kb.seen.on_loop == []


def test_unknown_library_is_not_found_everywhere(kb):
    c = kb.client
    assert c.get("/api/kb/libraries/nope").json() == {"ok": False, "error": "Not found"}
    assert c.post("/api/kb/libraries/nope/archive").json() == {"ok": False, "error": "Not found"}
    assert c.patch("/api/kb/libraries/nope", json={"name": "x"}).json() == {"ok": False, "error": "Not found"}
    assert c.delete("/api/kb/libraries/nope").json() == {"ok": False, "error": "Not found"}
    assert c.post("/api/kb/libraries/nope/refresh").json() == {"ok": False, "error": "Not found"}
    assert c.get("/api/kb/jobs/nope").json()["ok"] is False


def test_search_returns_hits(kb):
    body = kb.client.post("/api/kb/search", json={"query": "rlimits", "library_id": "docs"}).json()
    assert body["ok"] and body["hits"][0]["content"] == "hit for rlimits"


def test_site_ingest_records_progress_off_the_loop(kb):
    """One ConfigStore write per progress update used to run on the loop."""
    body = kb.client.post(
        "/api/kb/ingest", json={"library_id": "Site Lib", "url": "https://example.com/", "mode": "site"}
    ).json()
    assert body["ok"] and body["mode"] == "site"
    job = _wait_for_job(kb.client, body["job_id"])
    assert job["phase"] == "done" and job["library_id"] == "site_lib"
    assert kb.jobs[body["job_id"]]["finished_at"]  # the durable copy saw every update
    assert kb.seen.on_loop == []


def test_refresh_recrawls_from_the_seed_url(kb):
    kb.store.create_library("lib1", name="L", seed_url="https://example.com/")
    body = kb.client.post("/api/kb/libraries/lib1/refresh").json()
    assert body["ok"]
    job = _wait_for_job(kb.client, body["job_id"])
    assert job["phase"] == "done" and job["kind"] == "refresh" and job["url"] == "https://example.com/"
    assert kb.seen.on_loop == []


#: Request bodies for the routes that need one. A new body-taking route fails
#: the walk with a 422 until it is listed here.
_BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/kb/libraries"): {"id": "walked"},
    ("PATCH", "/api/kb/libraries/{library_id}"): {"description": "d"},
    ("POST", "/api/kb/ingest"): {"library_id": "lib1", "url": "https://example.com/", "mode": "page"},
    ("POST", "/api/kb/search"): {"library_id": "lib1", "query": "q"},
}


def test_no_route_touches_a_store_on_the_event_loop(kb):
    kb.store.create_library("lib1", name="L", seed_url="https://example.com/")
    calls = sorted(
        ((method, route.path) for route in kb.router.routes for method in route.methods),
        key=lambda call: call[0] == "DELETE",  # deletes last: they remove lib1
    )
    assert len(calls) >= 13, calls
    started_jobs = []
    for method, path in calls:
        url = path.replace("{library_id}", "lib1").replace("{job_id}", "lib1:none")
        resp = kb.client.request(method, url, json=_BODIES.get((method, path)))
        assert resp.status_code == 200, (method, path, resp.text)
        if resp.json().get("job_id"):
            started_jobs.append(resp.json()["job_id"])
    for job_id in started_jobs:  # background crawls record progress too
        _wait_for_job(kb.client, job_id)
    assert started_jobs, "the walk no longer starts a background crawl"
    assert kb.seen.on_loop == [], f"blocking store calls on the event loop: {sorted(set(kb.seen.on_loop))}"


def test_the_recorder_sees_the_event_loop():
    """Negative control (§28): the walk is only worth something if this fires."""
    seen = _Recorder()
    seen("off-loop")

    async def on_loop() -> None:
        seen("on-loop")
        await asyncio.to_thread(seen, "to-thread")

    asyncio.run(on_loop())
    assert seen.on_loop == ["on-loop"]
