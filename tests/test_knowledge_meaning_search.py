"""The Knowledge Library searches by meaning again (2026-09-26).

``VectorStore`` imported its Chroma client from ``memory.chroma_client``,
deleted with V1 on 2026-07-31, inside a ``try`` that logged "chromadb not
installed" -- on an install where chromadb WAS installed. Every library was
keyword-only from then on (the live install: 6 libraries, 6,598 chunks, none
with a vector), and a chunk ingested in that time never got its vector, since
indexing embeds only when the store is up. Fixed here:

* the client is back: one per directory for the process;
* a failure names its cause ("chromadb not installed" only when chromadb is
  the missing module), and is retried after a pause rather than logged on
  every search;
* ``KnowledgeIndex.backfill_vectors`` -- a maintenance sweep -- makes each
  library's vectors match its chunks: every active chunk embedded, shortest
  first across libraries, every vector of a retired chunk removed, bounded
  per pass and resumable. On the live CPU the 32 longest chunks took 113 s
  where 64 of median length took 3.4 s: a vector is made from at most
  1,500 characters;
* collections compare by angle. Chroma's default is squared L2, which every
  collection had been given while ``query`` reported ``1 - distance`` as a
  cosine; vectors are unit length now, so an older L2 collection ranks and
  scores the same;
* deleting a library drops its vectors even when this process never
  searched it, and a store opened on another database (a test's) keeps its
  vectors beside that database, not in the install's;
* the client sends Chroma no telemetry, and the vectors are left out of
  backups: a file copy of a live Chroma database can be torn, and the
  repair sweep rebuilds them from the chunks, which are backed up.

An in-memory stand-in for chromadb runs these in CI (the ``rag`` extra is
not installed there); the last test uses the real one where it is.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import types
from pathlib import Path

import pytest

from kazma_core.memory import vector_store_global as vsg
from tests._module_stubs import stub_modules

# ── A small chromadb ──────────────────────────────────────────────────────


class _Collection:
    def __init__(self, name: str, metadata: dict | None = None) -> None:
        self.name = name
        self.metadata = dict(metadata or {})
        self.rows: dict[str, tuple[list[float], str, dict]] = {}

    def upsert(self, ids, embeddings, documents, metadatas):
        for meta in metadatas:
            for key, value in meta.items():
                if value is None:  # what Chroma does with a None value
                    raise ValueError(f"Expected metadata value for {key!r}, got None")
        for i, e, d, m in zip(ids, embeddings, documents, metadatas):
            self.rows[i] = (list(e), d, dict(m))

    def get(self, ids=None, include=None, limit=None, offset=None):
        keys = list(self.rows) if ids is None else [i for i in ids if i in self.rows]
        keys = keys[offset or 0:][:limit] if limit is not None else keys[offset or 0:]
        return {"ids": keys, "documents": [self.rows[k][1] for k in keys]}

    def count(self):
        return len(self.rows)

    def delete(self, ids):
        for i in ids:
            self.rows.pop(i, None)

    def query(self, query_embeddings, n_results, where=None):
        q = query_embeddings[0]
        space = self.metadata.get("hnsw:space", "l2")

        def matches(meta, cond):
            if not cond:
                return True
            if "$and" in cond:
                return all(matches(meta, c) for c in cond["$and"])
            return all(meta.get(k) == v for k, v in cond.items())

        def distance(v):
            if space == "l2":  # Chroma's default: squared euclidean
                return sum((a - b) ** 2 for a, b in zip(q, v))
            dot = sum(a * b for a, b in zip(q, v))
            nq = math.sqrt(sum(a * a for a in q)) or 1.0
            nv = math.sqrt(sum(b * b for b in v)) or 1.0
            return 1.0 - dot / (nq * nv)

        hits = sorted(((i, distance(r[0])) for i, r in self.rows.items() if matches(r[2], where)),
                      key=lambda x: x[1])[:n_results]
        return {"ids": [[i for i, _d in hits]], "distances": [[d for _i, d in hits]]}


class _Client:
    made: list[str] = []
    settings_seen: list[object] = []

    def __init__(self, path: str = ":memory:", settings: object = None) -> None:
        _Client.made.append(path)
        _Client.settings_seen.append(settings)
        self.collections: dict[str, _Collection] = {}

    def get_or_create_collection(self, name, metadata=None, embedding_function=None):
        assert embedding_function is None  # the store passes its own vectors
        if name not in self.collections:  # an existing one keeps its metadata
            self.collections[name] = _Collection(name, metadata)
        return self.collections[name]

    def delete_collection(self, name):
        del self.collections[name]


def _chromadb_stub() -> dict[str, types.ModuleType]:
    chromadb = types.ModuleType("chromadb")
    chromadb.PersistentClient = lambda path, settings=None: _Client(path, settings)  # type: ignore[attr-defined]
    chromadb.Client = lambda settings=None: _Client(":memory:", settings)  # type: ignore[attr-defined]
    config = types.ModuleType("chromadb.config")
    config.Settings = lambda **kw: kw  # type: ignore[attr-defined]
    chromadb.config = config  # type: ignore[attr-defined]
    return {"chromadb": chromadb, "chromadb.config": config}


# ── A toy embedder: synonyms share a direction ────────────────────────────

_CONCEPTS = [
    {"webhook", "webhooks", "notification", "notifications", "callback", "callbacks"},
    {"retry", "retries", "attempt", "attempts", "resend", "resends", "again"},
    {"token", "tokens", "credential", "credentials"},
    {"expire", "expires", "lapse", "lapses", "valid"},
]
DIM = 64


class _Embedder:
    dim = DIM

    def encode(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for word in re.findall(r"[a-z]+", text.lower()):
            slot = next((i for i, c in enumerate(_CONCEPTS) if word in c), None)
            if slot is None:
                slot = 8 + int(hashlib.sha256(word.encode()).hexdigest(), 16) % (DIM - 8)
            v[slot] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]


class _RawEmbedder:
    """Vectors of any length, as a remote endpoint may return them."""

    dim = 2

    def __init__(self, table: dict[str, list[float]]) -> None:
        self.table = table

    def encode(self, text: str) -> list[float]:
        return list(self.table[text])

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    monkeypatch.setenv("KAZMA_VECTOR_PATH", str(tmp_path / "vector_memory"))
    monkeypatch.setattr(vsg, "_CLIENTS", {})
    monkeypatch.setattr(_Client, "made", [])
    monkeypatch.setattr(_Client, "settings_seen", [])
    embedder = _Embedder()
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: embedder)
    store = KnowledgeStore(db_path=str(tmp_path / "settings.db"))
    with stub_modules(_chromadb_stub()):
        yield types.SimpleNamespace(store=store, index=KnowledgeIndex(store))
    store.close()


def _chunk(library: str, url: str, idx: int, text: str, **extra) -> dict:
    return {
        "id": f"{library}-{idx}",
        "library_id": library,
        "source_url": url,
        "chunk_index": idx,
        "content": text,
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "document_title": extra.get("document_title", "Docs"),
        "section_header": extra.get("section_header", ""),
    }


def _seed(kb, library: str, texts: list[str], url: str | None = None, start: int = 0) -> None:
    if kb.store.get_library(library) is None:
        kb.store.create_library(library, library.title())
    for i, text in enumerate(texts, start):
        assert kb.store.upsert_chunk(_chunk(library, url or f"https://docs/{library}", i, text))


def _client(kb):
    [client] = vsg._CLIENTS.values()
    return client


# ── the client and its errors ─────────────────────────────────────────────


def test_every_store_on_one_directory_shares_one_client(kb, tmp_path):
    a = vsg.VectorStore("kazma_kb_one", persist_dir=str(tmp_path / "vector_memory"))
    b = vsg.VectorStore("kazma_kb_two", persist_dir=str(tmp_path / "vector_memory"))
    assert a.available and b.available
    assert _Client.made == [str((tmp_path / "vector_memory").resolve())]


def test_the_client_sends_chroma_no_telemetry(kb, tmp_path):
    """Chroma's default posts usage events to PostHog."""
    assert vsg.VectorStore("kazma_kb_disk", persist_dir=str(tmp_path / "vector_memory")).available
    assert vsg.VectorStore("kazma_kb_ram", persist_dir=None).available
    assert _Client.settings_seen == [{"anonymized_telemetry": False}] * 2


def test_a_failure_says_what_failed_and_is_not_retried_on_every_search(kb, caplog, monkeypatch):
    calls = []

    def broken(path):
        calls.append(path)
        raise ImportError("No module named 'kazma_core.memory.gone'", name="kazma_core.memory.gone")

    monkeypatch.setattr(vsg, "_shared_client", broken)
    store = vsg.VectorStore("kazma_kb_x", persist_dir="unused")
    with caplog.at_level(logging.WARNING, logger="kazma_core.memory.vector_store_global"):
        assert not store.available
        assert not store.available  # asked again at once: no retry, no second line
    assert len(calls) == 1
    [line] = [r.getMessage() for r in caplog.records]
    assert "kazma_core.memory.gone" in line and "not installed" not in line


def test_chromadb_missing_is_reported_as_such(kb, caplog):
    with stub_modules({"chromadb": None}), caplog.at_level(
        logging.WARNING, logger="kazma_core.memory.vector_store_global"
    ):
        assert not vsg.VectorStore("kazma_kb_y", persist_dir="unused").available
    assert any("chromadb not installed" in r.getMessage() for r in caplog.records)


# ── how vectors are compared ──────────────────────────────────────────────


def test_new_collections_compare_by_angle_and_an_old_l2_one_scores_the_same(kb, monkeypatch, tmp_path):
    """Unnormalised vectors, a new (cosine) collection and a pre-2026-09-26
    (L2) one: both rank by angle and report the true cosine."""
    q, far, near = [1.0, 0.0], [10.0, 10.0], [0.5, 0.6]  # angle: far wins; distance: near
    cos = {name: (v[0]) / math.hypot(*v) for name, v in (("far", far), ("near", near))}
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: _RawEmbedder(
        {"question": q, "far": far, "near": near}))
    path = str(tmp_path / "vector_memory")
    fresh = vsg.VectorStore("kazma_kb_new", persist_dir=path)
    assert fresh.available
    old = _client(kb).collections["kazma_kb_old"] = _Collection("kazma_kb_old", {"description": "x"})
    legacy = vsg.VectorStore("kazma_kb_old", persist_dir=path)
    assert legacy.available and vsg._collection_space(old) == "l2"
    assert vsg._collection_space(fresh._collection) == "cosine"
    for store in (fresh, legacy):
        assert store.index_many([("far", "far", {"k": 1}), ("near", "near", {"k": 2})]) == 2
        ranked = store.query("question", limit=2)
        assert [doc for doc, _s in ranked] == ["far", "near"]
        assert [round(s, 6) for _d, s in ranked] == [round(cos["far"], 6), round(cos["near"], 6)]
    # Negative control: the old collection searched with the raw vectors, as
    # before -- L2 on unnormalised vectors puts the wrong one first.
    old.rows.clear()
    old.upsert(ids=["far", "near"], embeddings=[far, near], documents=["far", "near"],
               metadatas=[{"k": 1}, {"k": 2}])
    assert old.query(query_embeddings=[q], n_results=2)["ids"][0] == ["near", "far"]


# ── the repair pass ───────────────────────────────────────────────────────


def test_the_backfill_gives_every_chunk_its_vector_once(kb):
    _seed(kb, "api", ["Webhooks retry delivery five times", "Access tokens expire after an hour"])
    _seed(kb, "guide", ["Install the command line tool", "Configure the workspace folder"])
    assert [kb.index.health(lib)["vector_chunks"] for lib in ("api", "guide")] == [0, 0]

    first = kb.index.backfill_vectors(time_budget_s=30)
    assert (first["embedded"], first["missing"], first["removed"], first["complete"]) == (4, 0, 0, True)
    assert [kb.index.health(lib)["vector_chunks"] for lib in ("api", "guide")] == [2, 2]
    # A chunk outside any document has document_id NULL: the key is left
    # out (Chroma refuses None), and so is an empty section header.
    meta = kb.index._vector_store_for("api")._collection.rows["api-0"][2]
    assert meta["library_id"] == "api"
    assert "document_id" not in meta and "section_header" not in meta

    second = kb.index.backfill_vectors(time_budget_s=30)
    assert (second["embedded"], second["missing"], second["removed"]) == (0, 0, 0)


def test_the_backfill_removes_vectors_whose_chunk_is_gone(kb):
    """A page purged or a document retired while the store was down left its
    vectors behind: they took search slots and joined to nothing."""
    _seed(kb, "api", ["Webhooks retry delivery five times"], url="https://docs/keep")
    _seed(kb, "api", ["Tokens expire after an hour", "Credentials lapse"], url="https://docs/gone", start=1)
    assert kb.index.backfill_vectors(time_budget_s=30)["embedded"] == 3
    kb.store.delete_chunks_for_source("api", "https://docs/gone")

    report = kb.index.backfill_vectors(time_budget_s=30)
    assert (report["removed"], report["embedded"]) == (2, 0)
    assert kb.index._vector_store_for("api").ids() == kb.store.active_chunk_ids("api")


def test_a_huge_chunk_is_embedded_from_its_opening_only(kb, monkeypatch):
    """The live install has a 59,906-character chunk. The model would read
    8,192 tokens of it, and pad its whole batch to that."""
    seen: list[int] = []
    toy = _Embedder()

    class Recording(_Embedder):
        def encode_batch(self, texts):
            seen.extend(len(t) for t in texts)
            return toy.encode_batch(texts)

    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: Recording())
    _seed(kb, "api", ["Webhooks retry delivery five times " * 1800, "Install the command line tool"])
    assert kb.index.backfill_vectors(time_budget_s=30)["embedded"] == 2
    assert max(seen) == vsg._EMBED_MAX_CHARS and min(seen) == len("Install the command line tool")
    assert kb.store.get_chunks_by_ids(["api-0"])["api-0"]["content"].count("Webhooks") == 1800


def test_the_shortest_chunks_go_first_across_libraries(kb, monkeypatch):
    """So most of every library is searchable by meaning early, and a batch
    of like lengths wastes nothing on padding."""
    seen: list[int] = []
    toy = _Embedder()

    class Recording(_Embedder):
        def encode_batch(self, texts):
            seen.extend(len(t) for t in texts)
            return toy.encode_batch(texts)

    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: Recording())
    _seed(kb, "api", ["a" * 300, "b" * 10])
    _seed(kb, "guide", ["c" * 150, "d" * 50])
    assert kb.index.backfill_vectors(time_budget_s=30, batch=1)["embedded"] == 4
    assert seen == [10, 50, 150, 300]


def test_a_library_counts_its_chunks_when_it_is_read(kb):
    """The chunk_count column is a cache several writers keep; one missed and
    the live page listed 365 chunks for a library holding 366."""
    _seed(kb, "api", ["Webhooks retry delivery five times", "Install the command line tool"])
    assert kb.store.get_library("api")["chunk_count"] == 2
    assert [lib["chunk_count"] for lib in kb.store.list_libraries()] == [2]
    # Negative control: nothing above updated the cached column.
    conn = kb.store._get_conn()
    assert conn.execute("SELECT chunk_count FROM knowledge_libraries WHERE id='api'").fetchone()[0] == 0


def test_the_backfill_stops_at_its_budget_and_the_next_pass_continues(kb, monkeypatch):
    _seed(kb, "api", [f"Endpoint number {i} returns a list" for i in range(10)])
    clock = iter([0.0, 0.0, 0.0, 99.0] + [199.0] * 50)
    monkeypatch.setattr("kazma_core.stores.knowledge_index.time.monotonic", lambda: next(clock))
    part = kb.index.backfill_vectors(time_budget_s=30, batch=4)
    assert part["complete"] is False and 0 < part["embedded"] < 10

    monkeypatch.undo()
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: _Embedder())
    rest = kb.index.backfill_vectors(time_budget_s=30, batch=4)
    assert part["embedded"] + rest["embedded"] == 10 and rest["complete"] is True


def test_meaning_search_finds_what_shares_no_word_with_the_question(kb):
    """The point of the vector: no keyword of the question is in the answer."""
    _seed(kb, "api", ["Webhooks retry delivery five times", "Install the command line tool"])
    question = "how many attempts are made to resend callbacks"
    assert kb.store.fts_search(question, "api", limit=5) == []  # words alone: nothing

    kb.index.backfill_vectors(time_budget_s=30)
    hits = kb.index.search_sync(question, "api", top_k=3)
    assert hits and hits[0].chunk_id == "api-0"


def test_the_backfill_reports_an_unavailable_store(kb):
    _seed(kb, "api", ["Webhooks retry delivery five times"])
    with stub_modules({"chromadb": None}):
        report = kb.index.backfill_vectors(time_budget_s=5)
    assert report["unavailable"] is True and report["embedded"] == 0


# ── where vectors live, and when they go ──────────────────────────────────


def test_deleting_a_library_drops_vectors_this_process_never_opened(kb):
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    _seed(kb, "api", ["Webhooks retry delivery five times"])
    kb.index.backfill_vectors(time_budget_s=30)
    assert "kazma_kb_api" in _client(kb).collections

    after_restart = KnowledgeIndex(kb.store)  # nothing cached, as after a reboot
    assert after_restart._vector_stores == {}
    assert after_restart.delete_library("api") is True
    assert "kazma_kb_api" not in _client(kb).collections
    assert kb.store.get_library("api") is None


def test_vectors_live_beside_their_database_unless_the_operator_moves_them(tmp_path, monkeypatch):
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    store = KnowledgeStore(db_path=str(tmp_path / "db" / "settings.db"))
    try:
        monkeypatch.delenv("KAZMA_VECTOR_PATH", raising=False)
        assert Path(KnowledgeIndex(store)._persist_dir) == (tmp_path / "db" / "vector_memory").resolve()
        monkeypatch.setenv("KAZMA_VECTOR_PATH", str(tmp_path / "elsewhere"))
        assert Path(KnowledgeIndex(store)._persist_dir) == (tmp_path / "elsewhere").resolve()
    finally:
        store.close()


def test_the_install_store_keeps_its_vectors_where_they_always_were(tmp_path, monkeypatch):
    """For the install's own database the rule gives the old path."""
    from kazma_core import paths
    from kazma_core.stores.knowledge import KnowledgeStore, _default_db
    from kazma_core.stores.knowledge_index import _vector_dir_for

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("KAZMA_VECTOR_PATH", raising=False)
    store = KnowledgeStore(db_path=_default_db())
    try:
        assert Path(_vector_dir_for(store)) == Path(paths.vector_memory_path())
    finally:
        store.close()


def test_the_vectors_are_rebuilt_not_backed_up(tmp_path, monkeypatch):
    from kazma_core.backup.universal import _copy_assets
    from kazma_core.store_registry import STORES, store_name_for

    data = tmp_path / "data"
    (data / "vector_memory").mkdir(parents=True)
    (data / "vector_memory" / "chroma.sqlite3").write_bytes(b"live chroma")
    (data / "attachments").mkdir()
    (data / "attachments" / "a.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.delenv("KAZMA_VECTOR_PATH", raising=False)
    out = tmp_path / "backup"
    out.mkdir()

    copied = {entry["path"]: entry["type"] for entry in _copy_assets(data, out)}
    assert copied == {"attachments": "dir", "vector_memory": "rebuilt"}
    assert not (out / "vector_memory").exists()
    # Negative control: the folder beside it is copied as ever.
    assert (out / "attachments" / "a.txt").read_text(encoding="utf-8") == "keep"
    declared = store_name_for(data / "vector_memory" / "chroma.sqlite3")
    assert declared and STORES[declared].migration == "rebuilt"


# ── the real client, where it is installed ────────────────────────────────


def test_with_the_real_chromadb(tmp_path, monkeypatch):
    real = pytest.importorskip("chromadb")
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    assert real.__name__ == "chromadb"
    monkeypatch.setenv("KAZMA_VECTOR_PATH", str(tmp_path / "vector_memory"))
    monkeypatch.setattr(vsg, "_CLIENTS", {})
    toy = _Embedder()
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: toy)
    store = KnowledgeStore(db_path=str(tmp_path / "settings.db"))
    try:
        kb = types.SimpleNamespace(store=store, index=KnowledgeIndex(store))
        _seed(kb, "api", ["Webhooks retry delivery five times", "Install the command line tool"])
        assert kb.index.backfill_vectors(time_budget_s=60)["embedded"] == 2
        question = "how many attempts are made to resend callbacks"
        hits = kb.index.search_sync(question, "api")
        assert hits and hits[0].chunk_id == "api-0"
        vs = kb.index._vector_store_for("api")
        assert vsg._collection_space(vs._collection) == "cosine"
        [(doc, similarity), *_rest] = vs.query(question, limit=2)
        expected = sum(a * b for a, b in zip(toy.encode(question), toy.encode("Webhooks retry delivery five times")))
        assert doc == "api-0" and similarity == pytest.approx(expected, abs=1e-5)
        assert Path(tmp_path / "vector_memory").is_dir()
    finally:
        store.close()
