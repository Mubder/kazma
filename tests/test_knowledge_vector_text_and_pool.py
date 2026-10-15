"""The vector catalog keeps no text, Chroma's index pool never evicts, and a
search filters only when it must (KB-Q01 follow-up, 2026-10-15).

Three things ``scripts/knowledge_soak.py`` left open after the first KB-Q01
fixes, against chromadb 1.5.9:

* every vector was stored with a 2,000-character preview of its chunk and
  the chunk's title, heading and address -- read by nothing -- and SQLite
  keeps a deleted row's bytes in the file's free space: a deleted library's
  words stayed readable in ``chroma.sqlite3`` (9,429 copies of a dropped
  3,000-row collection's words in one probe). Kazma now stores the vector
  and the metadata it reads, rebuilds a collection an older build wrote
  text into, and rewrites the catalog after a deletion of text;
* past what one shard of Chroma's index pool holds, an evicted index of
  1,000 or more records reloaded from disk without the records added since
  its last write, with no error. Kazma now sizes the pool so a shard holds
  every library there may be, and refuses a library past that ceiling;
* Chroma's metadata filter cost more than the search (10,000 vectors: 40 ms
  a query against 4 ms; 100,000: 353 ms against 6 ms). A collection holds
  one library, so the filter on the embedder's tag is asked only while
  another embedder's vectors are still there.

Each fix with its negative control: the defect reproduced on raw Chroma, or
with the fix taken away. The tests that need the real chromadb skip
without it (CI does not install the ``rag`` extra); the rest use the
in-memory stand-in.
"""

from __future__ import annotations

import ast
import random
import subprocess
import types
from pathlib import Path
from typing import Any

import pytest
from kazma_core.memory import chroma_local
from kazma_core.memory import vector_store_global as vsg
from kazma_core.stores import knowledge_index as ki

REPO = Path(__file__).resolve().parents[1]
#: A word no generated text holds: planted in a chunk, looked for in every
#: byte of the vector catalog.
WORD = "zqvplantedword"


@pytest.fixture(autouse=True)
def fresh_sweep(monkeypatch):
    monkeypatch.setattr(ki, "_SWEEP", {"report": None, "unavailable": False, "bad_passes": 0, "at": None})


class _Embedder:
    """Vectors from hashed words: no model."""

    dim = 32

    def encode(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in text.lower().split():
            v[sum(map(ord, word)) % self.dim] += 1.0
        return v

    def encode_batch(self, texts):
        return [self.encode(t) for t in texts]


@pytest.fixture()
def real(tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    from kazma_core.stores.knowledge import KnowledgeStore

    monkeypatch.setenv("KAZMA_VECTOR_PATH", str(tmp_path / "vector_memory"))
    monkeypatch.setattr(vsg, "_CLIENTS", {})
    embedder = _Embedder()
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: embedder)
    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    index = ki.KnowledgeIndex(store)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
    try:
        yield types.SimpleNamespace(store=store, index=index, root=Path(index._persist_dir), embedder=embedder)
    finally:
        vsg.release_dropped_vectors(index._persist_dir)
        store.close()


def _catalog_bytes(root: Path) -> bytes:
    """Every byte of the vector catalog: the file, and its journal or WAL
    when there is one (Chroma 1.5.9 runs the rollback journal)."""
    return b"".join(p.read_bytes() for p in (root / "chroma.sqlite3", root / "chroma.sqlite3-journal",
                                             root / "chroma.sqlite3-wal") if p.is_file())


def _seed(kb, library: str, texts: list[str], url: str | None = None) -> None:
    from tests.test_knowledge_meaning_search import _seed as seed

    seed(kb, library, texts, url=url)


def _write_as_before(kb, library: str) -> None:
    """The library's vectors as a build from before 2026-10-15 wrote them:
    the chunk's first 2,000 characters as the document, and its title,
    heading and address beside the vector."""
    vs = kb.index._vector_store_for(library)
    assert vs.available
    rows = kb.store.get_chunks_by_ids([f"{library}-{i}" for i in range(10)])
    ids = sorted(rows)
    vs._collection.upsert(
        ids=ids,
        embeddings=[vsg._unit(kb.embedder.encode(rows[i]["content"])) for i in ids],
        documents=[rows[i]["content"][:2000] for i in ids],
        metadatas=[{"model": vs.identity, "library_id": library, "source_url": rows[i]["source_url"],
                    "section_header": "Setup", "document_title": f"Guide {WORD}",
                    "chunk_index": int(rows[i]["chunk_index"]), "content_hash": rows[i]["content_hash"]}
                   for i in ids])


TEXTS = [f"Webhooks retry delivery five times {WORD}", "Install the command line tool",
         "Tokens expire after an hour"]


# ── no text is stored beside a vector ──────────────────────────────────────


def test_a_vector_is_stored_with_no_text(real):
    _seed(real, "api", TEXTS)
    report = ki.run_repair_sweep(time_budget_s=60)
    assert report["embedded"] == 3
    assert chroma_local.catalog_text_rows(real.root, vsg.KEPT_METADATA) == {"metadata": 0, "log": 0, "fulltext": 0}
    assert WORD.encode() not in _catalog_bytes(real.root)
    assert real.index.search_sync("webhooks retry delivery", "api")[0].chunk_id == "api-0"
    assert not real.index._vector_store_for("api").holds_text()


def test_the_write_before_put_the_text_in_the_catalog(real):
    """Negative control: the same chunks written as before."""
    _seed(real, "api", TEXTS)
    _write_as_before(real, "api")
    rows = chroma_local.catalog_text_rows(real.root, vsg.KEPT_METADATA)
    assert rows["metadata"] and rows["fulltext"] == 3
    assert WORD.encode() in _catalog_bytes(real.root)
    assert real.index._vector_store_for("api").holds_text()


def test_only_the_metadata_kazma_reads_is_kept(real):
    vs = real.index._vector_store_for("api")
    assert vs.index_many([("a", "some text", {"model": "m", "library_id": "api", "document_title": "Secret plan",
                                              "source_url": "https://x", "chunk_index": 3})]) == 1
    assert vs.index("b", "more text", {"model": "m", "section_header": "Secret heading"})
    held = vs._collection.get(ids=["a", "b"], include=["metadatas", "documents"])
    assert dict(zip(held["ids"], held["metadatas"])) == {"a": {"model": "m", "library_id": "api"}, "b": {"model": "m"}}
    assert held["documents"] == [None, None]


# ── a deletion leaves no readable copy ─────────────────────────────────────


def test_a_deleted_librarys_text_leaves_the_catalog(real):
    _seed(real, "api", TEXTS)
    _seed(real, "guide", ["Install the command line tool"])
    _write_as_before(real, "api")
    real.index.delete_library("api")
    assert chroma_local.compaction_due(real.root)  # the drop marked it
    report = ki.run_repair_sweep(time_budget_s=60)
    assert report["vector_compaction"]["compacted"] is True
    assert not chroma_local.compaction_due(real.root)
    assert WORD.encode() not in _catalog_bytes(real.root)
    assert real.index.search_sync("install the command line tool", "guide")  # the client still serves


def test_without_the_compaction_a_deleted_librarys_text_stays(real, monkeypatch):
    """Negative control: Chroma's drop deletes the rows; their bytes stay
    in the file's free space."""
    monkeypatch.setattr(chroma_local, "compact_catalog", lambda *a, **k: {"compacted": False})
    _seed(real, "api", TEXTS)
    _write_as_before(real, "api")
    real.index.delete_library("api")
    ki.run_repair_sweep(time_budget_s=60)
    assert chroma_local.catalog_text_rows(real.root, vsg.KEPT_METADATA)["metadata"] == 0  # no live row
    assert WORD.encode() in _catalog_bytes(real.root)  # and still readable


def _replace_page(kb) -> None:
    """The page that held the planted word, published again without it: its
    old chunks retire and their vectors are deleted."""
    from tests.test_knowledge_meaning_search import _chunk

    url = "https://docs/api"
    chunks = [_chunk("api", url, i, text) for i, text in enumerate(
        ["Webhooks retry delivery five times", "Install the command line tool", "Tokens expire after a day"])]
    new, _unchanged = kb.index.index("api", chunks)
    assert new == 3


def test_a_replaced_pages_old_text_leaves_the_catalog(real):
    _seed(real, "api", TEXTS)
    _write_as_before(real, "api")
    _replace_page(real)
    report = ki.run_repair_sweep(time_budget_s=60)
    assert report["text_removed"] == 1 and report["vector_compaction"]["compacted"] is True
    assert WORD.encode() not in _catalog_bytes(real.root)
    assert chroma_local.catalog_text_rows(real.root, vsg.KEPT_METADATA) == {"metadata": 0, "log": 0, "fulltext": 0}
    assert real.index.search_sync("tokens expire after a day", "api")[0].content == "Tokens expire after a day"


def test_without_the_compaction_a_replaced_pages_old_text_stays(real, monkeypatch):
    """Negative control."""
    monkeypatch.setattr(chroma_local, "compact_catalog", lambda *a, **k: {"compacted": False})
    _seed(real, "api", TEXTS)
    _write_as_before(real, "api")
    _replace_page(real)
    ki.run_repair_sweep(time_budget_s=60)
    assert WORD.encode() in _catalog_bytes(real.root)


def test_an_older_collection_loses_its_text_and_nothing_is_embedded_again(real):
    _seed(real, "api", TEXTS)
    _write_as_before(real, "api")
    fresh = ki.KnowledgeIndex(real.store)  # a restart

    def reembed(*_a, **_k):
        raise AssertionError("removing the text embeds nothing")

    real.embedder.encode_batch = reembed
    report = fresh.backfill_vectors(time_budget_s=60)
    assert report["text_removed"] == 1 and report["embedded"] == 0 and report["missing"] == 0
    vs = fresh._vector_store_for("api")
    assert not vs.holds_text()
    assert sorted(vs._collection.get()["ids"]) == ["api-0", "api-1", "api-2"]
    del real.embedder.encode_batch
    assert fresh.search_sync("webhooks retry delivery", "api")[0].chunk_id == "api-0"
    assert fresh.backfill_vectors(time_budget_s=60)["text_removed"] == 0  # once


def test_the_write_log_is_read_for_text_too(real):
    """Rows deleted from the metadata segment keep their text in Chroma's
    write log until the index is written to disk (every 1,000 records)."""
    _seed(real, "api", TEXTS)
    _write_as_before(real, "api")
    vs = real.index._vector_store_for("api")
    vs._collection.delete(ids=["api-0", "api-1", "api-2"])
    rows = chroma_local.catalog_text_rows(real.root, vsg.KEPT_METADATA)
    assert rows["metadata"] == 0 and rows["log"] == 3  # only the log still holds it
    assert chroma_local.holds_text(real.root, "kazma_kb_api", vsg.KEPT_METADATA) is True


def test_an_unreadable_catalog_counts_as_holding_text(tmp_path):
    root = tmp_path / "vector_memory"
    root.mkdir()
    (root / chroma_local.CATALOG).write_bytes(b"not a database")
    assert chroma_local.holds_text(root, "kazma_kb_x", vsg.KEPT_METADATA) is None


def test_no_product_code_stores_a_document_beside_a_vector():
    """Class gate: a ``documents=`` in a Chroma write puts text in the
    catalog that no deletion clears."""
    files = subprocess.run(["git", "ls-files", "kazma-*/**.py"], cwd=REPO, capture_output=True,
                           text=True, check=True).stdout.split()
    sources = {rel: (REPO / rel).read_text(encoding="utf-8") for rel in files if (REPO / rel).exists()}
    assert _document_writes(sources) == []


def test_the_document_gate_sees_a_write_with_text():
    """Negative control: the write as it was."""
    planted = {"kazma-core/kazma_core/x.py":
               "def index(self, i, v, t):\n    self._collection.upsert(ids=[i], embeddings=[v], documents=[t])\n"}
    assert _document_writes(planted) == ["kazma-core/kazma_core/x.py:2"]


def _document_writes(sources: dict[str, str]) -> list[str]:
    found = []
    for rel, source in sources.items():
        if "documents" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("upsert", "add", "update")
                    and any(k.arg == "documents" and not (isinstance(k.value, ast.Constant) and k.value.value is None)
                            for k in node.keywords)):
                found.append(f"{rel}:{node.lineno}")
    return sorted(found)


# ── Chroma's index pool: no shard overflows ───────────────────────────────


def _raw_client(path: Path, pool: int) -> Any:
    chromadb = pytest.importorskip("chromadb")
    from chromadb.config import Settings

    settings = Settings(anonymized_telemetry=False)
    chroma_local.start_system(str(path), settings, pool=pool)
    return chromadb.PersistentClient(path=str(path), settings=settings)


def _readable(col: Any) -> bool:
    try:
        col.query(query_embeddings=[[0.1] * 8], n_results=1)
        return True
    except Exception as exc:  # noqa: BLE001 - the eviction being found
        if "nothing found on disk" not in str(exc).lower():
            raise
        return False


def _small_collections_until_one_is_evicted(client: Any, alive: list[Any], *, start: int = 0,
                                            most: int = 40) -> tuple[Any, Any, int]:
    """Collections of 30 vectors (never written to disk), made one at a
    time after *alive*; after each, every earlier one is read. The first
    that cannot be read ("Nothing found on disk") was evicted by the one
    just made: the two share a shard. Returns (evicted, evictor, the next
    collection number); (None, None, ...) when no read failed. *alive*
    keeps the readable ones."""
    rng = random.Random(11 + start)
    for k in range(start, start + most):
        col = client.create_collection(f"probe{k:03d}", embedding_function=None)
        col.upsert(ids=[f"p{k}-{i}" for i in range(30)],
                   embeddings=[[rng.gauss(0, 1) for _ in range(8)] for _ in range(30)])
        for earlier in list(alive):
            if not _readable(earlier):
                alive.remove(earlier)
                return earlier, col, k + 1
        alive.append(col)
    return None, None, start + most


def _grow_past_a_disk_write(col: Any) -> int:
    """Make *col* readable again and grow it past Chroma's 1,000-record
    disk write, with records after it (only in memory). Returns the
    records it holds."""
    rng = random.Random(5)
    col.delete(ids=[vsg._RELOAD_PROBE_ID])  # rebuilt from its log
    for start in range(0, 1100, 100):
        col.upsert(ids=[f"grow-{i}" for i in range(start, start + 100)],
                   embeddings=[[rng.gauss(0, 1) for _ in range(8)] for _ in range(100)])
    col.upsert(ids=[f"late-{i}" for i in range(50)],
               embeddings=[[rng.gauss(0, 1) for _ in range(8)] for _ in range(50)])
    return col.count()


def _fewest_found_while_others_load(col: Any, held: int, others: list[Any], rounds: int = 8,
                                    loads: int = 30) -> int:
    """Load each of *others* *loads* times -- Chroma's pool keeps an index by
    how often it was used, so one load does not evict an index just written
    a thousand records to -- then search *col* for every record it holds;
    over *rounds*, the fewest found."""
    fewest = held
    for _round in range(rounds):
        for other in others:
            for _ in range(loads):
                other.delete(ids=[vsg._RELOAD_PROBE_ID])  # loads it (from its log if it was lost)
        found = len(col.query(query_embeddings=[[0.1] * 8], n_results=held, include=[])["ids"][0])
        fewest = min(fewest, found)
        if fewest < held:
            break
    return fewest


def test_a_full_shard_reloads_an_index_without_its_newest_records(tmp_path):
    """Negative control: a pool of one index a shard (as at Windows' 102 by
    default): an evicted index of 1,000+ records comes back from its last
    disk write, without what was added since -- and no error says so."""
    client = _raw_client(tmp_path / "small_pool", pool=64)
    try:
        alive: list[Any] = []
        start, held, found = 0, 0, 0
        # Chroma's pool keeps an index by how often it was used, so a pair
        # of shard mates does not always give way; another pair is tried.
        for _pair in range(5):
            evicted, evictor, start = _small_collections_until_one_is_evicted(client, alive, start=start)
            assert evicted is not None, "40 collections in 64 shards always share one"
            held = _grow_past_a_disk_write(evicted)
            assert held == 1180
            found = _fewest_found_while_others_load(evicted, held, [evictor])
            if found < held:
                break
            alive[:] = [c for c in alive if _readable(c)]
        assert found < held, (found, held)
    finally:
        client.close()


def test_kazmas_pool_keeps_every_record_through_the_same_reads(tmp_path):
    """The pool Kazma starts Chroma with: the same forty collections and the
    same writes and reads lose nothing, because no index is ever evicted."""
    client = _raw_client(tmp_path / "kazma_pool", pool=chroma_local.index_pool_size())
    try:
        alive: list[Any] = []
        evicted, _evictor, _next = _small_collections_until_one_is_evicted(client, alive)
        assert evicted is None and len(alive) == 40
        held = _grow_past_a_disk_write(alive[0])
        assert _fewest_found_while_others_load(alive[0], held, alive[1:], rounds=2, loads=3) == held == 1180
    finally:
        client.close()


def test_the_vector_store_starts_chroma_with_kazmas_pool(real):
    _seed(real, "api", TEXTS)
    assert real.index._vector_store_for("api").available
    [client] = vsg._CLIENTS.values()
    assert client._server.hnsw_cache_size == chroma_local.index_pool_size()


# ── the library ceiling ────────────────────────────────────────────────────


@pytest.fixture()
def capped(tmp_path, monkeypatch):
    from kazma_core.stores import knowledge as kb_module

    monkeypatch.setattr(kb_module, "library_ceiling", lambda: 3)
    store = kb_module.KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    yield store
    store.close()


def test_a_library_past_the_ceiling_is_refused(capped):
    from kazma_core.stores.knowledge import KnowledgeLibraryLimit, KnowledgeStorageFull

    for name in ("one", "two", "three"):
        capped.create_library(name, name)
    with pytest.raises(KnowledgeLibraryLimit) as refused:
        capped.create_library("four", "four")
    assert isinstance(refused.value, KnowledgeStorageFull) and refused.value.ceiling == 3
    assert "limit of 3 libraries" in str(refused.value)
    assert capped.get_library("four") is None
    with pytest.raises(KnowledgeLibraryLimit):
        capped.ensure_capacity("four")  # asked before anything is fetched
    capped.ensure_capacity("two")  # an existing library keeps its work
    capped.ensure_capacity()
    capped.delete_library("one")
    capped.create_library("four", "four")  # room again


def test_the_web_api_names_the_ceiling(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core import config_store
    from kazma_core.stores import knowledge as kb_module
    from kazma_ui import kb_api

    from tests.test_kb_api_contract import _catalog

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    journal = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: journal)
    monkeypatch.setattr(kb_module, "library_ceiling", lambda: 1)
    store = kb_module.KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    monkeypatch.setattr(kb_module, "_knowledge_store", store)
    app = FastAPI()
    app.include_router(kb_api.create_kb_router())
    client = TestClient(app)
    try:
        assert client.post("/api/kb/libraries", json={"id": "one", "name": "One"}).json()["ok"] is True
        refused = client.post("/api/kb/libraries", json={"id": "two", "name": "Two"})
        assert refused.status_code == 409
        body = refused.json()
        assert body["code"] == "kb_library_limit" and body["limit"] == 1
        crawl = client.post("/api/kb/ingest", json={"library_id": "three", "url": "https://example.com/docs",
                                                    "mode": "page"})
        assert crawl.status_code == 409 and crawl.json()["code"] == "kb_library_limit"
        words = _catalog()["knowledge.err.kb_library_limit"]
        assert words["en"] and words["ar"] and words["en"] != words["ar"]
    finally:
        store.close()
        journal.close()


def test_without_the_ceiling_the_store_takes_any_number_of_libraries(capped, monkeypatch):
    """Negative control: the count is what refuses (a ceiling no store
    reaches lets the fourth in)."""
    from kazma_core.stores import knowledge as kb_module

    monkeypatch.setattr(kb_module, "library_ceiling", lambda: 10**9)
    for name in ("one", "two", "three", "four"):
        capped.create_library(name, name)
    assert len(capped.all_library_ids()) == 4


# ── the filter on the embedder's tag ──────────────────────────────────────


def _spy_where(monkeypatch) -> list[Any]:
    seen: list[Any] = []
    real_query = vsg.VectorStore.query

    def query(self, text, limit=10, where=None, tenant_id=None, *, embedding=None):
        seen.append(where)
        return real_query(self, text, limit=limit, where=where, tenant_id=tenant_id, embedding=embedding)

    monkeypatch.setattr(vsg.VectorStore, "query", query)
    return seen


def _stub_kb(tmp_path, monkeypatch):
    from kazma_core.stores.knowledge import KnowledgeStore

    from tests.test_knowledge_meaning_search import _Embedder as ToyEmbedder

    monkeypatch.setenv("KAZMA_VECTOR_PATH", str(tmp_path / "vector_memory"))
    monkeypatch.setattr(vsg, "_CLIENTS", {})
    embedder = ToyEmbedder()
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: embedder)
    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    return types.SimpleNamespace(store=store, index=ki.KnowledgeIndex(store), embedder=embedder)


def test_a_collection_of_one_embedder_is_searched_without_a_filter(tmp_path, monkeypatch):
    from tests._module_stubs import stub_modules
    from tests.test_knowledge_meaning_search import _chromadb_stub

    with stub_modules(_chromadb_stub()):
        kb = _stub_kb(tmp_path, monkeypatch)
        try:
            _seed(kb, "api", ["Webhooks retry delivery five times", "Install the command line tool"])
            kb.index.backfill_vectors(time_budget_s=60)
            seen = _spy_where(monkeypatch)
            assert kb.index.search_sync("webhooks retry delivery", "api")[0].chunk_id == "api-0"
            assert seen == [None]
            # Another embedder's vector arrives (a model switch the repair
            # has not finished): the filter is back, and it is never shown.
            vs = kb.index._vector_store_for("api")
            vs.index_many([("api-old", "webhooks retry delivery five times", {"model": "an older model",
                                                                                "library_id": "api"})])
            hits = kb.index.search_sync("webhooks retry delivery", "api")
            assert seen[-1] == {"model": vs.identity}
            assert "api-old" not in [h.chunk_id for h in hits]
        finally:
            kb.store.close()


def test_without_the_filter_a_mixed_collection_answers_with_another_embedders_vector(tmp_path, monkeypatch):
    """Negative control: the check matters -- unfiltered, the older model's
    vector comes back."""
    from tests._module_stubs import stub_modules
    from tests.test_knowledge_meaning_search import _chromadb_stub

    with stub_modules(_chromadb_stub()):
        kb = _stub_kb(tmp_path, monkeypatch)
        try:
            _seed(kb, "api", ["Webhooks retry delivery five times"])
            kb.index.backfill_vectors(time_budget_s=60)
            vs = kb.index._vector_store_for("api")
            vs.index_many([("api-old", "webhooks retry delivery five times", {"model": "an older model",
                                                                                "library_id": "api"})])
            assert "api-old" in [i for i, _s in vs.query("webhooks retry delivery", limit=5, where=None)]
        finally:
            kb.store.close()


def test_a_vector_without_a_tag_counts_as_another_embedders(real):
    """Chroma's ``$ne`` matches a vector with no ``model`` (one written
    before 2026-09-27): the real chromadb, as ``only_model`` relies on."""
    _seed(real, "api", TEXTS)
    ki.run_repair_sweep(time_budget_s=60)
    vs = real.index._vector_store_for("api")
    assert vs.only_model(vs.identity)
    vs._collection.upsert(ids=["untagged"], embeddings=[vsg._unit(real.embedder.encode("x y"))],
                          metadatas=[{"library_id": "api"}])
    vs._one_model = None
    assert not vs.only_model(vs.identity)
