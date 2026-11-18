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
        refused_page = w.call("a", "POST", "/api/kb/ingest", json={
            "library_id": NAME, "url": "https://x.example/", "mode": "page"})
        assert refused_page.status_code in (409, 507, 422), refused_page.text


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
