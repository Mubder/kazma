"""The Knowledge Library follows the space (Spaces S1-13).

The matrix (``tests/_knowledge_space_cases.py``, run by
``tests/test_space_matrix.py``) drives every Knowledge route and tool as a
member of one space against another's libraries. This file holds what the
matrix does not:

* **negative controls**: the space filter taken out, the write's last check
  taken out, the crawl journal's filter taken out -- the cases that rest on
  each must fail;
* **a library's id in a space**: the owner's ids never change; every other
  space's carry a mark no typed name can produce;
* **search and the turn's injection never cross**;
* **the quota per space**: one space at its share is refused, another goes on;
* **work bound to no space** (a chat app's ``/kb`` before stage 3) is refused,
  and a crawl a restart cut off resumes in its library's space;
* **an install that does not enforce Spaces behaves as before**.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._knowledge_space_cases import ITEM, NAME, world
from tests._space_matrix import Matrix, load_cases

CASES = load_cases()

ROUTES = (
    "DELETE /api/kb/libraries/{library_id}", "GET /api/kb/jobs", "GET /api/kb/jobs/{job_id}",
    "GET /api/kb/libraries", "GET /api/kb/libraries/archived/list", "GET /api/kb/libraries/{library_id}",
    "GET /api/kb/libraries/{library_id}/chunks", "GET /api/kb/outcomes", "GET /knowledge",
    "PATCH /api/kb/libraries/{library_id}", "POST /api/kb/ingest", "POST /api/kb/jobs/{job_id}/resume",
    "POST /api/kb/libraries", "POST /api/kb/libraries/{library_id}/archive",
    "POST /api/kb/libraries/{library_id}/refresh", "POST /api/kb/libraries/{library_id}/unarchive",
    "POST /api/kb/preview", "POST /api/kb/search",
)
TOOLS = (
    "tool:knowledge_create_library", "tool:knowledge_ingest_site", "tool:knowledge_ingest_url",
    "tool:knowledge_list_libraries", "tool:knowledge_search",
)


@pytest.fixture
def matrix(monkeypatch) -> Matrix:
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


def _fails(target: str, matrix: Matrix) -> None:
    """The case of *target* fails on one of its own assertions: its guard is
    out, and the case saw another space's data."""
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        CASES[target].run(matrix)


def test_the_item_wrote_a_case_for_every_knowledge_route_and_tool() -> None:
    mine = sorted(target for target, registered in CASES.items() if registered.item == ITEM)
    assert mine == sorted([*ROUTES, *TOOLS])


# ── a library's id in a space ────────────────────────────────────────────


def test_the_owners_ids_never_change() -> None:
    from kazma_core.stores.knowledge import _library_id_in_space, _slugify_library_id

    for raw in ("docs", "ShipX API", "docs.0123abcd", "a" * 80):
        assert _library_id_in_space(raw, OWNER_SPACE_ID) == _slugify_library_id(raw)


def test_another_spaces_id_carries_its_mark_and_names_itself() -> None:
    from kazma_core.stores.knowledge import MAX_LIBRARY_ID, _library_id_in_space

    a = _library_id_in_space("docs", "space-a")
    b = _library_id_in_space("docs", "space-b")
    assert a != b and a.startswith("docs.") and b.startswith("docs.")
    assert _library_id_in_space(a, "space-a") == a  # an id the space holds names itself
    # Another space's id, or the owner's slug, is a name: never that library.
    assert _library_id_in_space(b, "space-a") not in (a, b)
    assert _library_id_in_space("docs", "space-a") != "docs"
    assert len(_library_id_in_space("x" * 200, "space-a")) <= MAX_LIBRARY_ID


def test_a_marked_id_has_a_collection_of_its_own() -> None:
    """The collection name kept "." apart from "_": "docs.<mark>" and an
    owner's "docs_<mark>" (a name the owner may type) were one collection."""
    from kazma_core.stores.knowledge import _library_id_in_space
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    marked = _library_id_in_space("docs", "space-a")
    typed = marked.replace(".", "_")
    index = object.__new__(KnowledgeIndex)
    index._persist_dir = None
    names = {index._open_vector_store(lib)._collection_name for lib in (marked, typed)}
    assert len(names) == 2, names


# ── negative controls ────────────────────────────────────────────────────


def _no_space_filter(monkeypatch) -> None:
    """The store as it read before S1-13 on an install that enforces Spaces:
    no filter, and an id read as given."""
    from kazma_core.stores.knowledge import KnowledgeStore

    monkeypatch.setattr(KnowledgeStore, "_tenant_filter_enabled", staticmethod(lambda: False))
    monkeypatch.setattr(KnowledgeStore, "own_library_id", lambda self, raw: str(raw or ""))


@pytest.mark.parametrize("target", ("GET /api/kb/libraries", "GET /api/kb/libraries/{library_id}",
                                    "GET /api/kb/libraries/{library_id}/chunks", "POST /api/kb/search",
                                    "tool:knowledge_list_libraries", "tool:knowledge_search"))
def test_control_a_search_with_no_space_filter_shows_anothers_library(target, matrix, monkeypatch) -> None:
    CASES[target].run(matrix)
    _no_space_filter(monkeypatch)
    _fails(target, matrix)


@pytest.mark.parametrize("target", ("GET /api/kb/jobs", "GET /api/kb/jobs/{job_id}", "GET /api/kb/outcomes"))
def test_control_the_crawl_journal_unfiltered_shows_anothers_crawls(target, matrix, monkeypatch) -> None:
    from kazma_core.stores.knowledge import KnowledgeStore

    CASES[target].run(matrix)
    monkeypatch.setattr(KnowledgeStore, "_tenant_filter_enabled", staticmethod(lambda: False))
    _fails(target, matrix)


def _page(library_id: str, url: str, text: str) -> list[dict[str, Any]]:
    return [{"library_id": library_id, "source_url": url, "chunk_index": 0, "content": text,
             "content_hash": hashlib.sha256(text.encode()).hexdigest(), "document_title": "T",
             "section_header": "S"}]


def _write_into_theirs(matrix: Matrix) -> tuple[Any, Any, Any]:
    from kazma_core.stores.knowledge import KnowledgeLibraryGone

    with world(matrix) as w:
        _mine, theirs = w.pair("backstop")
        url = "https://written.example/"
        try:
            with w.as_("a"):
                w.store().publish_page(theirs.id, url, _page(theirs.id, url, "written by A"))
        except KnowledgeLibraryGone as refused:
            return refused, w.held(theirs), theirs
        return None, w.held(theirs), theirs


def test_a_page_is_written_only_into_a_library_of_the_bound_space(matrix) -> None:
    refused, held, _theirs = _write_into_theirs(matrix)
    assert refused is not None and "not this space's" in str(refused)
    assert not any("written by A" in t for t in held["texts"])


def test_control_without_the_last_check_a_page_lands_in_anothers_library(matrix, monkeypatch) -> None:
    from kazma_core.stores.knowledge import KnowledgeStore

    monkeypatch.setattr(KnowledgeStore, "_writing_space", staticmethod(lambda: None))
    refused, held, _theirs = _write_into_theirs(matrix)
    assert refused is None and any("written by A" in t for t in held["texts"])


# ── the turn's injection ─────────────────────────────────────────────────


def _injected(matrix: Matrix) -> str:
    from kazma_core.stores.knowledge_index import build_knowledge_inject

    with world(matrix) as w:
        _mine, theirs = w.pair("inject")
        with w.as_("b"):
            w.store().update_library(theirs.id, auto_inject=True)
        with w.as_("a"):
            made = build_knowledge_inject(f"text {theirs.mark} about pumps")
        return made.block + "".join(str(h) for h in made.hits) + "|" + theirs.mark


def test_a_turn_is_never_given_another_spaces_library(matrix) -> None:
    shown, mark = _injected(matrix).rsplit("|", 1)
    assert mark not in shown


def test_control_with_no_space_filter_another_spaces_library_is_injected(matrix, monkeypatch) -> None:
    _no_space_filter(monkeypatch)
    shown, mark = _injected(matrix).rsplit("|", 1)
    assert mark in shown


# ── the quota per space ──────────────────────────────────────────────────


def test_one_space_at_its_share_is_refused_and_another_goes_on(matrix, monkeypatch) -> None:
    from kazma_core.stores import knowledge
    from kazma_core.stores.knowledge import KnowledgeSpaceFull, KnowledgeStorageFull

    monkeypatch.setattr(knowledge, "_SPACE_BYTES_TTL_S", 0.0)  # measured afresh each time
    with world(matrix) as w:
        mine, _theirs = w.pair("quota")
        used = w.store().space_used_bytes(Matrix.SPACE_A)
        assert used > 0 and w.store().space_used_bytes(Matrix.SPACE_B) > 0
        monkeypatch.setattr(knowledge, "_SPACE_QUOTA_BYTES", used + 1)
        with w.as_("a"):
            w.store().publish_page(mine.id, "https://more.example/", _page(mine.id, "https://more.example/",
                                                                           "more text " * 10))
            with pytest.raises(KnowledgeSpaceFull) as refused:
                w.store().ensure_capacity(NAME)
            assert isinstance(refused.value, KnowledgeStorageFull)  # every surface refuses it already
        with w.as_("b"):
            w.store().ensure_capacity(NAME)  # B is far from its share
        page = {"library_id": NAME, "url": "https://x.example/", "mode": "page"}
        refused_page = w.call("a", "POST", "/api/kb/ingest", json=page)
        assert refused_page.status_code == 507 and refused_page.json()["code"] == "kb_storage_full", \
            refused_page.text
        # Control: with the share lifted, the same request is not refused for room.
        monkeypatch.setattr(knowledge, "_SPACE_QUOTA_BYTES", 10 * used + 10**6)
        assert w.call("a", "POST", "/api/kb/ingest", json=page).status_code != 507


def _hold_the_store_lock(store: Any) -> Any:
    """Another call holding the Knowledge store's lock (a publication)."""
    import threading

    held, release = threading.Event(), threading.Event()

    def holder() -> None:
        with store._lock:
            held.set()
            release.wait(10)

    threading.Thread(target=holder, daemon=True).start()
    assert held.wait(5)
    return release


def test_a_spaces_bytes_are_measured_outside_the_store_lock(matrix) -> None:
    """The measure reads every chunk of the space: under the store lock it
    stalled every Knowledge call for its length (S1-13 review)."""
    import threading

    with world(matrix) as w:
        w.pair("lockfree")
        store = w.store()
        release = _hold_the_store_lock(store)
        try:
            answer: list[int] = []
            reader = threading.Thread(target=lambda: answer.append(store.space_used_bytes(Matrix.SPACE_A)),
                                      daemon=True)
            reader.start()
            reader.join(5)
            assert answer and answer[0] > 0
        finally:
            release.set()


def test_control_measured_under_the_store_lock_it_waits_for_every_other_call(matrix) -> None:
    import threading

    def as_it_was(store: Any, space_id: str) -> int:
        with store._lock:
            row = store._get_conn().execute(
                "SELECT COALESCE(SUM(LENGTH(CAST(c.content AS BLOB))), 0) FROM knowledge_chunks c "
                "JOIN knowledge_libraries l ON l.id = c.library_id WHERE l.tenant_id = ?",
                (space_id,)).fetchone()
        return int(row[0])

    with world(matrix) as w:
        w.pair("locked")
        store = w.store()
        release = _hold_the_store_lock(store)
        answer: list[int] = []
        reader = threading.Thread(target=lambda: answer.append(as_it_was(store, Matrix.SPACE_A)), daemon=True)
        reader.start()
        reader.join(1)
        try:
            assert answer == []  # still waiting
        finally:
            release.set()
            reader.join(5)


# ── work bound to no space; a crawl resumed after a restart ──────────────


def test_work_bound_to_no_space_is_refused_once_spaces_is_enforced(monkeypatch) -> None:
    from kazma_core.stores.knowledge_lifecycle import MULTI_USER_REFUSAL, NO_SPACE_REFUSAL, kb_refusal

    monkeypatch.setattr(scope, "_enforced", True)
    assert kb_refusal() == NO_SPACE_REFUSAL
    with scope.bind_scope("space-a", OWNER_ACCOUNT_ID):
        assert kb_refusal() is None
    monkeypatch.setattr(scope, "_enforced", False)
    monkeypatch.setattr("kazma_core.security.single_operator.single_operator_refusal",
                        lambda text: text)
    assert kb_refusal() == MULTI_USER_REFUSAL  # the older multi-user mode keeps its refusal


def test_a_crawl_resumed_after_a_restart_runs_in_its_librarys_space(matrix) -> None:
    import asyncio

    from kazma_ui.kb_api import _in_the_crawls_space

    seen: list[Any] = []

    async def route(job_id: str) -> dict[str, Any]:
        seen.append(scope.current_scope())
        return {"ok": True}

    with world(matrix) as w:
        _mine, theirs = w.pair("autoresume")
        assert scope.current_scope() is None
        asyncio.run(_in_the_crawls_space({"job_id": "j", "library_id": theirs.id}, route))
        assert seen == [scope.Scope(Matrix.SPACE_B, matrix.account_b)]
        gone = asyncio.run(_in_the_crawls_space({"job_id": "j", "library_id": "no.such"}, route))
        assert gone == {"ok": False, "code": "not_found"} and len(seen) == 1


def test_control_a_crawl_resumed_as_before_runs_as_no_one(matrix) -> None:
    """The resume as S1-13 found it: the route called with nothing bound.
    What the route reads asks for a scope, and is refused."""
    import asyncio

    async def route(job_id: str) -> dict[str, Any]:
        return {"ok": True, "scope": scope.require_scope()}

    with world(matrix) as w:
        _mine, theirs = w.pair("asbefore")
        with pytest.raises(scope.NoSpaceBound):
            asyncio.run(route("j"))  # what the auto-resume called before
        row = {"job_id": "j", "library_id": theirs.id}
        from kazma_ui.kb_api import _in_the_crawls_space

        assert asyncio.run(_in_the_crawls_space(row, route))["scope"].space_id == Matrix.SPACE_B


def _two_interrupted_crawls(monkeypatch: Any, libraries: dict[str, str]) -> list[str]:
    """The auto-resume's journal as two interrupted crawls due a resume;
    returns the job ids the resume route was asked for."""
    from kazma_core.web_acquire import crawl_jobs
    from kazma_ui import kb_api

    asked: list[str] = []

    async def call(fn: Any, *args: Any) -> Any:
        return fn(*args)

    def inspect_jobs(job_id: str | None = None) -> Any:
        if job_id is None:
            return [{"job_id": j, "status": "interrupted"} for j in libraries]
        return {"job_id": job_id, "library_id": libraries[job_id], "status": "interrupted"}

    async def resume(job_id: str) -> dict[str, Any]:
        asked.append(job_id)
        return {"ok": True}

    monkeypatch.setattr(crawl_jobs, "call", call)
    monkeypatch.setattr(crawl_jobs, "recover", lambda: None)
    monkeypatch.setattr(crawl_jobs, "inspect_jobs", inspect_jobs)
    monkeypatch.setattr(crawl_jobs, "auto_resume_due", lambda row: True)
    monkeypatch.setattr(crawl_jobs, "claim_auto_resume", lambda job_id: True)
    monkeypatch.setitem(kb_api._RESUME_ROUTE, "resume", resume)
    return asked


def _space_unreadable_for(monkeypatch: Any, store: Any, library_id: str, error: BaseException) -> None:
    """The library's space cannot be read (the store answers *error*)."""
    real = store.library_space

    def library_space(lib: str) -> Any:
        if lib == library_id:
            raise error
        return real(lib)

    monkeypatch.setattr(store, "library_space", library_space)


def test_one_crawl_whose_space_cannot_be_read_leaves_the_others_resumed(matrix, monkeypatch, caplog) -> None:
    import asyncio
    import logging
    import sqlite3

    from kazma_ui.kb_api import resume_interrupted_crawls

    with world(matrix) as w:
        mine, theirs = w.pair("resumeboth")
        asked = _two_interrupted_crawls(monkeypatch, {"j-a": mine.id, "j-b": theirs.id})
        _space_unreadable_for(monkeypatch, w.store(), mine.id, sqlite3.OperationalError("database is locked"))
        with caplog.at_level(logging.WARNING, logger="kazma_ui.kb_api"):
            resumed = asyncio.run(resume_interrupted_crawls())
    assert resumed == ["j-b"] and asked == ["j-b"]
    assert any("j-a could not resume" in r.getMessage() for r in caplog.records)


class _NotAStoreError(Exception):
    """An error the auto-resume's catch does not cover."""


def test_control_an_error_the_pass_does_not_catch_stops_it(matrix, monkeypatch) -> None:
    """What every unreadable space did before the catch: the pass stopped
    there, and the crawls after it were not tried."""
    import asyncio

    from kazma_ui.kb_api import resume_interrupted_crawls

    with world(matrix) as w:
        mine, theirs = w.pair("resumestop")
        asked = _two_interrupted_crawls(monkeypatch, {"j-a": mine.id, "j-b": theirs.id})
        _space_unreadable_for(monkeypatch, w.store(), mine.id, _NotAStoreError("stand-in"))
        with pytest.raises(_NotAStoreError):
            asyncio.run(resume_interrupted_crawls())
    assert asked == []


# ── every name built from a library id keeps the space apart ─────────────


def _built_names(library_id: str) -> dict[str, str]:
    """Every name the install builds from a library id."""
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    from kazma_core.stores.knowledge_index import KnowledgeIndex
    from kazma_core.stores.knowledge_ingest import _provenance_dir

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import knowledge_soak

    index = KnowledgeIndex._open_vector_store(SimpleNamespace(_persist_dir="unused"), library_id)
    return {"collection": index._collection_name, "folder": _provenance_dir(library_id).name,
            "soak": knowledge_soak._safe(library_id)}


def test_every_name_built_from_a_library_id_keeps_the_space_apart() -> None:
    """A space's "docs.<mark>" and an owner's library named "docs.<mark>"
    (whose id is ``docs_<mark>``) share no collection and no folder: the
    owner's delete removed the other space's fetched pages (S1-13 review)."""
    from kazma_core.stores.knowledge import _library_id_in_space

    theirs = _library_id_in_space("docs", "matrix-a")
    owners = _library_id_in_space(theirs, OWNER_SPACE_ID)
    assert owners != theirs
    mine, other = _built_names(theirs), _built_names(owners)
    assert all(mine[kind] != other[kind] for kind in mine), (mine, other)
    # Every id from before is named as it always was.
    assert _built_names("shipx_api") == {"collection": "kazma_kb_shipx_api", "folder": "shipx_api",
                                         "soak": "shipx_api"}
    from kazma_core.stores.knowledge import library_name_part

    assert library_name_part("..") == "library"  # never a folder above its own


def test_control_the_old_folder_rule_made_two_spaces_one_folder() -> None:
    from kazma_core.stores.knowledge import _library_id_in_space

    def as_it_was(library_id: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in library_id)[:80] or "library"

    theirs = _library_id_in_space("docs", "matrix-a")
    assert as_it_was(theirs) == as_it_was(_library_id_in_space(theirs, OWNER_SPACE_ID))


def test_only_one_rule_builds_a_name_from_a_library_id() -> None:
    """A gate: no Knowledge module or script filters an id's characters
    itself; the names come from ``knowledge.library_name_part``."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    files = [*root.glob("kazma-core/kazma_core/stores/knowledge*.py"), root / "kazma-ui/kazma_ui/kb_api.py",
             *root.glob("scripts/knowledge_*.py")]
    found = []
    for path in files:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "isalnum()" in line and path.name != "knowledge.py":
                found.append(f"{path.name}:{n}")
    assert found == [], found
    source = (root / "kazma-core/kazma_core/stores/knowledge.py").read_text(encoding="utf-8")
    assert source.count("isalnum()") == 1  # in library_name_part


# ── production mode with Spaces not enforced: as before ─────────────────


def test_production_mode_without_spaces_lists_every_crawl(tmp_path, monkeypatch) -> None:
    """Live sets ``KAZMA_PRODUCTION``, which turns the store's tenant filter
    on. The crawl journal is filtered only where Spaces is enforced: a
    deleted library's crawl stays listed, a research job is named as one,
    and the Dashboard's crawl card counts what /metrics does."""
    import secrets

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.stores.knowledge import get_knowledge_store, reset_knowledge_store
    from kazma_core.stores.knowledge_index import reset_knowledge_index
    from kazma_core.web_acquire import crawl_jobs
    from kazma_ui.kb_api import create_kb_router

    monkeypatch.setattr(scope, "_enforced", False)
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    reset_knowledge_index()
    reset_knowledge_store()
    try:
        assert get_knowledge_store()._tenant_filter_enabled()  # what the old gate keyed on: on here
        gone = crawl_jobs.start("deleted-library", "https://gone.example/", {"mode": "local_only", "max_pages": 1},
                                job_id=f"deleted-library:{secrets.token_hex(4)}", kind="site")
        gone.finish("completed")
        crawl = dict(mode="local_only", schema=1, workspace=str(tmp_path), output="research/reports/paper",
                     max_pages=5, max_depth=1, path_prefix="")
        research = crawl_jobs.start("agent-crawl", "https://paper.example/", crawl, kind="research_crawl")
        app = FastAPI()
        app.include_router(create_kb_router())
        with TestClient(app) as client:
            assert gone.job_id in client.get("/api/kb/jobs").text
            assert client.get(f"/api/kb/jobs/{gone.job_id}").status_code == 200
            answer = client.get(f"/api/kb/jobs/{research.job_id}")
            assert answer.status_code == 404 and "belongs to research" in answer.text
            assert sum(client.get("/api/kb/outcomes").json()["summary"]["statuses"].values()) >= 1
    finally:
        reset_knowledge_index()
        reset_knowledge_store()


# ── an install that does not enforce Spaces behaves as before ────────────


def test_not_enforced_ids_lookups_and_listings_are_as_before(tmp_path, monkeypatch) -> None:
    from kazma_core.stores.knowledge import KnowledgeStore

    monkeypatch.setattr(scope, "_enforced", False)
    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    try:
        made = store.create_library("ShipX API", name="ShipX")
        assert made["id"] == "shipx_api" and made["tenant_id"] == OWNER_SPACE_ID
        assert store.own_library_id("Shipx_API") == "Shipx_API"  # read as given
        assert store.library_id_for("ShipX API") == "shipx_api"
        assert store._writing_space() is None
        assert [lib["id"] for lib in store.list_libraries()] == ["shipx_api"]
        store.ensure_capacity("another")  # no space share is asked
    finally:
        store.close()
