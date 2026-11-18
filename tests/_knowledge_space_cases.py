"""The matrix cases of Spaces S1-13: the Knowledge Library
(``tests/_space_matrix.py``).

Every case runs with Spaces enforced, as person A in ``matrix-a`` against
the libraries, chunks and crawls of person B's ``matrix-b``, through the
product's own Knowledge router behind the real tenant middleware, or the
product's own tool functions. Both people make a library of the same name
("docs"): each space's is its own id (``slug.<space mark>``), and a name
or another space's id never reaches the other's library.

Pages are planted through the store's own publication (``publish_page``);
nothing is fetched from the network. Meaning search is off (no embedder):
keyword search answers, which is what reaches every chunk.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import scope
from kazma_ui import auth, space_binding

from tests._chat_space_cases import _boundary
from tests._no_models import keep_models_out
from tests._space_matrix import Matrix, case

ITEM = "S1-13"
NAME = "docs"


@dataclass
class Library:
    """A planted library: its id and the marker its title and text hold."""

    id: str
    mark: str
    url: str


class World:
    """The Knowledge router in one app, with two signed-in people."""

    def __init__(self, matrix: Matrix, client: TestClient) -> None:
        self.matrix = matrix
        self.client = client

    def space_of(self, who: str) -> str:
        return {"a": Matrix.SPACE_A, "b": Matrix.SPACE_B}[who]

    def call(self, who: str, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"x-matrix-person": who, space_binding.SPACE_HEADER: self.space_of(who)}
        return self.client.request(method, path, headers=headers, **kwargs)

    def as_(self, who: str):
        return self.matrix.as_a() if who == "a" else self.matrix.as_b()

    @staticmethod
    def store() -> Any:
        from kazma_core.stores.knowledge import get_knowledge_store

        return get_knowledge_store()

    def library(self, who: str, mark: str, *, name: str = NAME) -> Library:
        """A library named *name* in *who*'s space, holding one page whose
        title and text carry *mark*."""
        url = f"https://{mark}.example/page"
        with self.as_(who):
            store = self.store()
            made = store.create_library(name, name=f"title {mark}")
            text = f"text {mark} about pumps"
            store.publish_page(made["id"], url, [{
                "library_id": made["id"], "source_url": url, "chunk_index": 0, "content": text,
                "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                "document_title": f"page {mark}", "section_header": "S0",
            }])
        return Library(id=made["id"], mark=mark, url=url)

    def pair(self, word: str) -> tuple[Library, Library]:
        marks = self.matrix.plant(word)
        return self.library("a", marks.a), self.library("b", marks.b)

    def held(self, lib: Library) -> dict[str, Any]:
        """A library as the store holds it, whoever asks (None when gone)."""
        row = self.store()._get_conn().execute(
            "SELECT id, name, archived, tenant_id FROM knowledge_libraries WHERE id = ?", (lib.id,)).fetchone()
        texts = [r[0] for r in self.store()._get_conn().execute(
            "SELECT content FROM knowledge_chunks WHERE library_id = ? AND active = 1", (lib.id,))]
        return {} if row is None else {"name": row[1], "archived": bool(row[2]), "tenant": row[3],
                                       "texts": texts}


@contextmanager
def world(matrix: Matrix) -> Iterator[World]:
    """Spaces enforced; the product's Knowledge router behind the real tenant
    middleware, on a private store. The person a request is signed in as is
    named by a test header: sign-in is S1-03's."""
    from kazma_core.stores.knowledge import reset_knowledge_store
    from kazma_core.stores.knowledge_index import reset_knowledge_index
    from kazma_ui.kb_api import create_kb_router

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(
        ignore_cleanup_errors=True
    ) as tmp:
        patch.setattr(scope, "_enforced", True)
        patch.setenv("KAZMA_DATA_DIR", tmp)
        keep_models_out(patch)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))
        reset_knowledge_index()
        reset_knowledge_store()
        app = FastAPI()
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_kb_router())
        try:
            with TestClient(app) as client:
                yield World(matrix, client)
        finally:
            reset_knowledge_index()
            reset_knowledge_store()


def _in_world(run: Callable[[World], None]) -> Callable[[Matrix], None]:
    def with_world(matrix: Matrix) -> None:
        with world(matrix) as made:
            run(made)

    with_world.__name__ = run.__name__
    return with_world


def kb_case(target: str) -> Callable[[Callable[[World], None]], Callable[[Matrix], None]]:
    def register(run: Callable[[World], None]) -> Callable[[Matrix], None]:
        return case(target, ITEM)(_in_world(run))

    return register


def _never_shown(w: World, response: Any, theirs: Library) -> None:
    assert theirs.mark not in response.text and theirs.id not in response.text, response.text


# ── libraries ────────────────────────────────────────────────────────────


@kb_case("GET /api/kb/libraries")
def kb_list(w: World) -> None:
    mine, theirs = w.pair("list")
    assert mine.id != theirs.id  # one name, two ids
    listed = w.call("a", "GET", "/api/kb/libraries")
    assert mine.id in listed.text and mine.mark in listed.text
    _never_shown(w, listed, theirs)


@kb_case("POST /api/kb/libraries")
def kb_create(w: World) -> None:
    _mine, theirs = w.pair("create")
    before = w.held(theirs)
    for named in ("other", theirs.id):
        made = w.call("a", "POST", "/api/kb/libraries", json={"id": named, "name": "mine"})
        assert made.status_code == 200, made.text
        new_id = made.json()["library"]["id"]
        assert new_id != theirs.id and w.store().library_space(new_id) == Matrix.SPACE_A
    assert w.held(theirs) == before


@kb_case("GET /api/kb/libraries/{library_id}")
def kb_get(w: World) -> None:
    mine, theirs = w.pair("get")
    refused = w.call("a", "GET", f"/api/kb/libraries/{theirs.id}")
    assert refused.status_code == 404
    _never_shown(w, refused, theirs)
    own = w.call("a", "GET", f"/api/kb/libraries/{NAME}")  # the name: A's own
    assert own.status_code == 200 and own.json()["library"]["id"] == mine.id


@kb_case("GET /api/kb/libraries/archived/list")
def kb_archived(w: World) -> None:
    mine, theirs = w.pair("archived")
    for who, lib in (("a", mine), ("b", theirs)):
        with w.as_(who):
            assert w.store().archive_library(lib.id)
    listed = w.call("a", "GET", "/api/kb/libraries/archived/list")
    assert mine.id in listed.text
    _never_shown(w, listed, theirs)


def _flag_case(verb: str) -> None:
    @kb_case(f"POST /api/kb/libraries/{{library_id}}/{verb}")
    def flag(w: World) -> None:
        mine, theirs = w.pair(verb)
        if verb == "unarchive":
            for who, lib in (("a", mine), ("b", theirs)):
                with w.as_(who):
                    w.store().archive_library(lib.id)
        before = w.held(theirs)
        refused = w.call("a", "POST", f"/api/kb/libraries/{theirs.id}/{verb}")
        assert refused.status_code == 404, refused.text
        assert w.held(theirs) == before
        done = w.call("a", "POST", f"/api/kb/libraries/{NAME}/{verb}")
        assert done.status_code == 200, done.text
        assert w.held(mine)["archived"] is (verb == "archive")
        assert w.held(theirs) == before

    flag.__name__ = f"kb_{verb}"


_flag_case("archive")
_flag_case("unarchive")


@kb_case("PATCH /api/kb/libraries/{library_id}")
def kb_update(w: World) -> None:
    mine, theirs = w.pair("update")
    before = w.held(theirs)
    refused = w.call("a", "PATCH", f"/api/kb/libraries/{theirs.id}", json={"name": "taken"})
    assert refused.status_code == 404
    assert w.held(theirs) == before
    done = w.call("a", "PATCH", f"/api/kb/libraries/{NAME}", json={"name": "renamed"})
    assert done.status_code == 200 and w.held(mine)["name"] == "renamed"
    assert w.held(theirs) == before


@kb_case("DELETE /api/kb/libraries/{library_id}")
def kb_delete(w: World) -> None:
    mine, theirs = w.pair("delete")
    before = w.held(theirs)
    assert w.call("a", "DELETE", f"/api/kb/libraries/{theirs.id}").status_code == 404
    assert w.held(theirs) == before
    done = w.call("a", "DELETE", f"/api/kb/libraries/{NAME}")
    assert done.status_code == 200, done.text
    assert w.held(mine) == {} and w.held(theirs) == before


@kb_case("GET /api/kb/libraries/{library_id}/chunks")
def kb_chunks(w: World) -> None:
    mine, theirs = w.pair("chunks")
    refused = w.call("a", "GET", f"/api/kb/libraries/{theirs.id}/chunks")
    assert refused.status_code == 404
    _never_shown(w, refused, theirs)
    own = w.call("a", "GET", f"/api/kb/libraries/{NAME}/chunks")
    assert own.status_code == 200 and mine.mark in own.text
    _never_shown(w, own, theirs)


# ── a page in, a search out ──────────────────────────────────────────────


@contextmanager
def _fetches(text: str) -> Iterator[None]:
    """The page fetch answers *text* (nothing reaches the network)."""

    async def fetched(url: str, *, scope_check: Any = None) -> tuple[str, str, str]:
        return text, "ok", ""

    async def public(url: str, **_: Any) -> None:
        return None

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("kazma_core.stores.knowledge_ingest._extract_page", fetched)
        patch.setattr("kazma_core.web_acquire.dns_worker.validate_public_url", public)
        yield


@kb_case("POST /api/kb/ingest")
def kb_ingest(w: World) -> None:
    mine, theirs = w.pair("ingest")
    before = w.held(theirs)
    page = w.matrix.plant("page").a
    with _fetches(f"text {page} on valves"):
        for named in (theirs.id, NAME):
            done = w.call("a", "POST", "/api/kb/ingest", json={
                "library_id": named, "url": f"https://{page}.example/", "mode": "page"})
            assert done.status_code == 200, done.text
            assert done.json()["library_id"] != theirs.id
    # Whatever A named, the page went into A's own libraries; B's is as it was.
    assert w.held(theirs) == before
    assert any(page in t for t in w.held(mine)["texts"])


@kb_case("POST /api/kb/search")
def kb_search(w: World) -> None:
    mine, theirs = w.pair("search")
    refused = w.call("a", "POST", "/api/kb/search", json={"query": f"text {theirs.mark}",
                                                          "library_id": theirs.id})
    assert refused.status_code == 404
    _never_shown(w, refused, theirs)
    own = w.call("a", "POST", "/api/kb/search", json={"query": f"text {theirs.mark} {mine.mark}",
                                                      "library_id": NAME})
    assert own.status_code == 200 and mine.mark in own.text
    _never_shown(w, own, theirs)


@kb_case("POST /api/kb/libraries/{library_id}/refresh")
def kb_refresh(w: World) -> None:
    _mine, theirs = w.pair("refresh")
    with w.as_("b"):
        w.store().update_library(theirs.id, seed_url=theirs.url)
    refused = w.call("a", "POST", f"/api/kb/libraries/{theirs.id}/refresh")
    assert refused.status_code == 404
    _never_shown(w, refused, theirs)


# ── crawls ───────────────────────────────────────────────────────────────


def _crawl(lib: Library, status: str = "completed") -> str:
    from kazma_core.web_acquire import crawl_jobs

    job = crawl_jobs.start(lib.id, lib.url, {"mode": "local_only", "max_pages": 1},
                           job_id=f"{lib.id}:{secrets.token_hex(6)}", kind="site")
    job.finish(status)
    return job.job_id


@kb_case("GET /api/kb/jobs")
def kb_jobs(w: World) -> None:
    mine, theirs = w.pair("jobs")
    my_job, their_job = _crawl(mine), _crawl(theirs)
    listed = w.call("a", "GET", "/api/kb/jobs")
    assert my_job in listed.text and their_job not in listed.text
    _never_shown(w, listed, theirs)


@kb_case("GET /api/kb/jobs/{job_id}")
def kb_job(w: World) -> None:
    mine, theirs = w.pair("job")
    my_job, their_job = _crawl(mine), _crawl(theirs)
    refused = w.call("a", "GET", f"/api/kb/jobs/{their_job}")
    assert refused.status_code == 404
    _never_shown(w, refused, theirs)
    assert w.call("a", "GET", f"/api/kb/jobs/{my_job}").status_code == 200


@kb_case("POST /api/kb/jobs/{job_id}/resume")
def kb_resume(w: World) -> None:
    from kazma_core.web_acquire import crawl_jobs

    _mine, theirs = w.pair("resume")
    their_job = _crawl(theirs, "interrupted")
    refused = w.call("a", "POST", f"/api/kb/jobs/{their_job}/resume")
    assert refused.status_code == 404, refused.text
    assert crawl_jobs.inspect_jobs(their_job)["status"] == "interrupted"


@kb_case("GET /api/kb/outcomes")
def kb_outcomes(w: World) -> None:
    mine, theirs = w.pair("outcomes")
    _crawl(mine)
    for _ in range(3):
        _crawl(theirs)
    summary = w.call("a", "GET", "/api/kb/outcomes").json()["summary"]
    # A is shown its one crawl, never B's three.
    assert sum(summary["statuses"].values()) == 1, summary


# ── routes that keep nothing of a space ──────────────────────────────────

# (The provider's credit refresh is the install's account, not a space's:
# route_scopes says so, and the matrix leaves it out.)
for _method, _path in (("GET", "/knowledge"), ("POST", "/api/kb/preview")):
    case(f"{_method} {_path}", ITEM)(_boundary(_method, _path))


# ── tools ────────────────────────────────────────────────────────────────


def _call(tool_name: str, **arguments: Any) -> str:
    from tests._chat_space_cases import _registry

    tool = _registry().get_tool(tool_name)
    assert tool is not None, tool_name
    result = tool.func(**arguments)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    return str(result)


def _tool_case(target: str) -> Callable[[Callable[[World], None]], Callable[[Matrix], None]]:
    return kb_case(f"tool:{target}")


@_tool_case("knowledge_list_libraries")
def tool_list(w: World) -> None:
    mine, theirs = w.pair("toollist")
    with w.as_("a"):
        listed = _call("knowledge_list_libraries")
    assert mine.id in listed and theirs.id not in listed and theirs.mark not in listed


@_tool_case("knowledge_create_library")
def tool_create(w: World) -> None:
    _mine, theirs = w.pair("toolcreate")
    before = w.held(theirs)
    with w.as_("a"):
        answer = _call("knowledge_create_library", library_id=theirs.id, name="mine")
    assert theirs.mark not in answer
    assert w.held(theirs) == before


@_tool_case("knowledge_ingest_url")
def tool_ingest_url(w: World) -> None:
    mine, theirs = w.pair("toolurl")
    before = w.held(theirs)
    page = w.matrix.plant("toolpage").a
    with _fetches(f"text {page} on valves"), w.as_("a"):
        _call("knowledge_ingest_url", library_id=theirs.id, url=f"https://{page}.example/")
        _call("knowledge_ingest_url", library_id=NAME, url=f"https://{page}.example/two")
    assert w.held(theirs) == before
    assert any(page in t for t in w.held(mine)["texts"])


@_tool_case("knowledge_ingest_site")
def tool_ingest_site(w: World) -> None:
    from kazma_core.agent.tool_refusal import RefusedBeforeEffect
    from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

    _mine, theirs = w.pair("toolsite")
    asked: list[str] = []

    async def admitted(lib_id: str, *_a: Any, **_k: Any) -> Any:
        asked.append(lib_id)
        raise AcquisitionError(FetchErrorCode.LIMIT, "stopped by the test")

    with pytest.MonkeyPatch.context() as patch, w.as_("a"):
        patch.setattr("kazma_core.stores.knowledge_ingest._prepare_ingest_site", admitted)
        with pytest.raises(RefusedBeforeEffect):
            _call("knowledge_ingest_site", library_id=theirs.id, seed_url="https://x.example/")
    # The crawl was asked for in A's own library, never B's.
    assert asked and asked[0] != theirs.id
    assert w.store().library_space(asked[0]) in (None, Matrix.SPACE_A)


@_tool_case("knowledge_search")
def tool_search(w: World) -> None:
    mine, theirs = w.pair("toolsearch")
    with w.as_("a"):
        named = _call("knowledge_search", query=f"text {theirs.mark}", library=theirs.id)
        everywhere = _call("knowledge_search", query=f"text {theirs.mark} {mine.mark}")
    assert theirs.mark not in named and theirs.mark not in everywhere
    assert mine.mark in everywhere
