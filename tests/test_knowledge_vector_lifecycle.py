"""The Knowledge Library's meaning vectors on disk and in Chroma's index
pool (KB-Q01, 2026-10-15).

``scripts/knowledge_soak.py`` found, against chromadb 1.5.9 (the newest
release that day):

* every dropped collection left its vector folder on disk -- ~40 MB per
  10,000 chunks after each library delete and each model switch that
  changed the vector size (17 folders in a four-minute soak);
* a process sometimes could not read a small library at all ("Error
  creating hnsw segment reader: Nothing found on disk"): Chroma's index pool
  holds ``max_file_handles // 5`` indexes over 64 shards (102 on Windows),
  two collections in one shard evict each other, and an evicted index under
  ``sync_threshold`` records existed only in memory;
* meaning search returned 34-38 of 40 planted answers at 10,000 chunks with
  Chroma's default HNSW settings, while an exact search ranked every one
  first.

Each fix is held here against the real chromadb (installed with the ``rag``
extra, which CI does not install), each with its negative control: the
defect itself, reproduced on raw Chroma or with the fix taken away. The
folder rules are also held without chromadb.
"""

from __future__ import annotations

import ast
import random
import sqlite3
import sys
import threading
import time
import types
import uuid
from pathlib import Path

import pytest
from kazma_core.memory import chroma_local
from kazma_core.memory import vector_store_global as vsg
from kazma_core.stores import knowledge_index as ki

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def fresh_sweep(monkeypatch):
    monkeypatch.setattr(ki, "_SWEEP", {"report": None, "unavailable": False, "bad_passes": 0, "at": None})


def _catalog(root: Path, owned: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(root / chroma_local.CATALOG))
    try:
        conn.execute("CREATE TABLE segments (id TEXT PRIMARY KEY, type TEXT, scope TEXT, collection TEXT)")
        conn.executemany("INSERT INTO segments VALUES (?, 'hnsw', 'VECTOR', 'c')", [(o,) for o in owned])
        conn.commit()
    finally:
        conn.close()


def _folder(root: Path, name: str, size: int = 64) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "data_level0.bin").write_bytes(b"\0" * size)
    return path


def _folders_no_collection_owns(root: Path) -> list[str]:
    """Every folder the catalog does not name (the harness's rule: any
    folder, not only the ones the product would remove)."""
    owned: set[str] = set()
    if (root / chroma_local.CATALOG).is_file():
        conn = sqlite3.connect(f"file:{(root / chroma_local.CATALOG).as_posix()}?mode=ro", uri=True)
        try:
            owned = {str(r[0]) for r in conn.execute("SELECT id FROM segments")}
        finally:
            conn.close()
    return sorted(p.name for p in root.iterdir() if p.is_dir() and p.name not in owned)


# ── the folder rules (no chromadb needed) ──────────────────────────────────


def test_only_a_segment_folder_no_collection_owns_is_removed(tmp_path):
    root = tmp_path / "vector_memory"
    owned, orphan = str(uuid.uuid4()), str(uuid.uuid4())
    _catalog(root, [owned])
    _folder(root, owned)
    _folder(root, orphan, size=128)
    _folder(root, "operator-notes")  # not Chroma's: never touched
    report = chroma_local.remove_orphan_segment_folders(root)
    assert report == {"removed": 1, "removed_bytes": 128, "held": [], "unreadable": False}
    assert (root / owned).is_dir() and (root / "operator-notes").is_dir() and not (root / orphan).exists()
    # Nothing left to do: a second pass removes nothing.
    assert chroma_local.remove_orphan_segment_folders(root)["removed"] == 0


def test_a_catalog_that_cannot_be_read_removes_nothing(tmp_path):
    root = tmp_path / "vector_memory"
    root.mkdir()
    (root / chroma_local.CATALOG).write_bytes(b"this is not a database")
    folder = _folder(root, str(uuid.uuid4()))
    report = chroma_local.remove_orphan_segment_folders(root)
    assert report["unreadable"] is True and report["removed"] == 0 and folder.is_dir()


def test_with_no_catalog_every_segment_folder_is_an_orphan(tmp_path):
    """A fresh catalog owns nothing: a folder from a lost store is no one's."""
    root = tmp_path / "vector_memory"
    folder = _folder(root, str(uuid.uuid4()))
    assert chroma_local._orphan_segment_folders(root) == [folder]


def test_the_store_id_is_written_once_and_read_without_creating(tmp_path):
    root = tmp_path / "vector_memory"
    assert chroma_local.vector_store_id(root) == ""  # read only: nothing made
    assert not (root / chroma_local.STORE_ID_FILE).exists()
    first = chroma_local.vector_store_id(root, create=True)
    assert first and chroma_local.vector_store_id(root, create=True) == first == chroma_local.vector_store_id(root)


def test_the_open_file_limit_loaded_indexes_use_is_raised():
    """A loaded index holds four files open (C runtime streams on Windows,
    512 by default): every library's index must fit."""
    chroma_local.raise_stream_limits()
    if sys.platform == "win32":
        import ctypes

        assert ctypes.CDLL("ucrtbase")._getmaxstdio() >= 8192   # what the index files use
    else:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = 10240 if hard == resource.RLIM_INFINITY else min(hard, 10240)
        assert soft == resource.RLIM_INFINITY or soft >= target


def test_the_pool_holds_every_library_in_any_one_shard():
    """The pool Kazma gives Chroma: each of its 64 shards holds as many
    indexes as there may be libraries, plus the spares."""
    ceiling = chroma_local.library_ceiling()
    budget = chroma_local._file_budget()
    by_files = (budget - 128) // chroma_local.FILES_PER_INDEX - chroma_local.SPARE_COLLECTIONS
    assert ceiling == min(chroma_local.LIBRARY_CEILING, by_files)
    if sys.platform == "win32":  # 8,192 streams: room for every library
        assert ceiling == chroma_local.LIBRARY_CEILING
    assert chroma_local.index_pool_size() // chroma_local.POOL_SHARDS >= ceiling + chroma_local.SPARE_COLLECTIONS
    assert (ceiling + chroma_local.SPARE_COLLECTIONS) * chroma_local.FILES_PER_INDEX < budget


def test_a_process_that_opens_few_files_holds_fewer_libraries(monkeypatch):
    """At the default 512 streams the ceiling drops to what fits."""
    monkeypatch.setattr(chroma_local, "_file_budget", lambda: 512)
    assert chroma_local.library_ceiling() == (512 - 128) // 4 - chroma_local.SPARE_COLLECTIONS
    monkeypatch.setattr(chroma_local, "_file_budget", lambda: 20)
    assert chroma_local.library_ceiling() == 1


def test_the_gate_waits_for_calls_in_flight_and_holds_new_ones():
    gate = vsg._Gate()
    order: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    def call() -> None:
        with gate.shared():
            with gate.shared():  # re-entrant within a thread
                entered.set()
                release.wait(5)
                order.append("call")

    worker = threading.Thread(target=call)
    worker.start()
    assert entered.wait(5)
    assert gate.acquire_exclusive(timeout=0.05) is False  # a call is in flight
    closer = threading.Thread(target=lambda: (gate.acquire_exclusive(5), order.append("close"),
                                              gate.release_exclusive()))
    closer.start()
    time.sleep(0.05)
    release.set()
    worker.join(5)
    closer.join(5)
    assert order == ["call", "close"]
    with gate.shared():  # open again
        assert gate.acquire_exclusive(timeout=0.05) is False  # never from inside a call


def test_an_embedding_never_holds_up_a_close(tmp_path, monkeypatch):
    """The model call of a write runs outside the client's gate: a close
    that waits for the calls in flight does not wait for a model."""
    from tests._module_stubs import stub_modules
    from tests.test_knowledge_meaning_search import _chromadb_stub

    embedding = threading.Event()
    finish = threading.Event()

    class Slow:
        dim = 4

        def encode(self, text):
            return [1.0, 0.0, 0.0, 0.0]

        def encode_batch(self, texts):
            embedding.set()
            finish.wait(5)
            return [self.encode(t) for t in texts]

    monkeypatch.setattr(vsg, "_CLIENTS", {})
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: Slow())
    with stub_modules(_chromadb_stub()):
        store = vsg.VectorStore("kazma_kb_slow", persist_dir=str(tmp_path / "vector_memory"))
        assert store.available
        done: list[int] = []
        writer = threading.Thread(target=lambda: done.append(store.index_many([("a", "text", {"k": 1})])))
        writer.start()
        assert embedding.wait(5)
        gate = vsg._gate(store._key)
        assert gate.acquire_exclusive(timeout=1.0) is True  # not held up by the model call
        gate.release_exclusive()
        finish.set()
        writer.join(5)
        assert done == [1]


def test_every_collection_drop_goes_through_the_one_path_that_cleans_up():
    """Class gate: a ``delete_collection`` in product code leaves a folder
    behind (chromadb 1.5.9), so every one is ``_drop_collection`` -- or the
    rebuild's own temporary copy, released by the sweep -- in
    ``vector_store_global``."""
    import subprocess

    files = subprocess.run(["git", "ls-files", "kazma-*/**.py"], cwd=REPO, capture_output=True,
                           text=True, check=True).stdout.split()
    sources = {rel: (REPO / rel).read_text(encoding="utf-8") for rel in files if (REPO / rel).exists()}
    assert _drop_sites(sources) == [
        "kazma-core/kazma_core/memory/vector_store_global.py:_drop_collection",
        "kazma-core/kazma_core/memory/vector_store_global.py:rebuild_collection",
    ]


def test_the_drop_gate_sees_a_second_path():
    """Negative control: the drop as it was before (straight on the client)."""
    planted = {"kazma-core/kazma_core/x.py": "def drop(self):\n    self._client.delete_collection(self.name)\n"}
    assert _drop_sites(planted) == ["kazma-core/kazma_core/x.py:drop"]


def _drop_sites(sources: dict[str, str]) -> list[str]:
    found = []
    for rel, source in sources.items():
        if "delete_collection" not in source:
            continue
        for fn in ast.walk(ast.parse(source)):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "delete_collection"):
                        found.append(f"{rel}:{fn.name}")
    return sorted(set(found))


# ── with the real chromadb ─────────────────────────────────────────────────


class _Embedder:
    """Unit vectors from hashed words: no model."""

    dim = 32

    def encode(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in text.lower().split():
            v[hash(word) % self.dim] += 1.0
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


def _library(kb, name: str, texts: list[str]) -> None:
    from tests.test_knowledge_meaning_search import _seed

    _seed(kb, name, texts)


TEXTS = ["Webhooks retry delivery five times", "Install the command line tool", "Tokens expire after an hour"]


def test_a_deleted_library_leaves_no_vector_folder(real):
    for lib in ("api", "guide"):
        _library(real, lib, TEXTS)
    ki.run_repair_sweep(time_budget_s=60)
    assert real.index.search_sync("webhooks retry", "api")  # this process loads api's index
    real.index.delete_library("api")
    ki.run_repair_sweep(time_budget_s=60)  # lets go of what it held, then removes it
    assert _folders_no_collection_owns(real.root) == []
    assert real.index.search_sync("install the command line tool", "guide")  # the live one is whole
    assert ki._last_repair_report()["vector_folders"]["held"] == []


def test_a_model_switch_of_another_size_leaves_no_vector_folder(real, monkeypatch):
    _library(real, "api", TEXTS)
    ki.run_repair_sweep(time_budget_s=60)

    class Narrow(_Embedder):
        dim = 16

    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: Narrow())
    fresh = ki.KnowledgeIndex(real.store)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: fresh)
    assert ki.run_repair_sweep(time_budget_s=60)["embedded"] == 3  # rebuilt at the new size
    assert _folders_no_collection_owns(real.root) == []


def test_raw_chroma_leaves_the_folder_of_a_dropped_collection(tmp_path):
    """Negative control: the defect -- chromadb's delete_collection removes
    the catalog rows and keeps the vectors."""
    chromadb = pytest.importorskip("chromadb")
    from chromadb.config import Settings

    client = chromadb.PersistentClient(path=str(tmp_path), settings=Settings(anonymized_telemetry=False))
    try:
        col = client.get_or_create_collection(name="dropped", embedding_function=None)
        col.upsert(ids=["a", "b"], embeddings=[[1.0, 0.0], [0.0, 1.0]], documents=["a", "b"])
        client.delete_collection("dropped")
        assert len(_folders_no_collection_owns(tmp_path)) == 1
    finally:
        client.close()


def test_a_folder_an_earlier_process_left_goes_when_the_next_client_starts(real):
    real.root.mkdir(parents=True, exist_ok=True)
    left = _folder(real.root, str(uuid.uuid4()))
    _library(real, "api", TEXTS)
    assert real.index._vector_store_for("api").available  # the first client of this process
    assert not left.exists()


def _small_collections(kb, n: int) -> list[str]:
    """*n* collections of 30 vectors (under Chroma's 1,000-record
    sync_threshold: never written to disk), then listed the way the repair
    sweep lists them -- which loads every index into the pool, so the ones
    that share a shard evict each other after their logs were replayed."""
    rng = random.Random(7)
    names = [f"lib{i:02d}" for i in range(n)]
    for name in names:
        vs = kb.index._vector_store_for(name)
        assert vs.available
        vs._collection.upsert(ids=[f"{name}-{i}" for i in range(30)],
                              embeddings=[[rng.gauss(0, 1) for _ in range(32)] for _ in range(30)],
                              documents=["d"] * 30, metadatas=[{"library_id": name}] * 30)
    for name in names:
        assert len(kb.index._vector_store_for(name).models()) == 30
    return names


def _tiny_pool(monkeypatch) -> None:
    """Chroma's pool at 64 entries: one index a shard, as at Windows' 102."""
    monkeypatch.setattr(chroma_local, "index_pool_size", lambda: 64)


def test_an_index_chroma_lost_is_rebuilt_from_its_log(real, monkeypatch):
    """Forty small collections in a pool of one index a shard: some evict
    each other, and every search still answers -- rebuilt from Chroma's log."""
    pytest.importorskip("chromadb")
    _tiny_pool(monkeypatch)
    names = _small_collections(real, 40)
    for name in names:
        hits = real.index._vector_store_for(name).query("x", limit=3, where={"library_id": name})
        assert len(hits) == 3, name
    assert sum(vsg.index_reloads().values()) > 0  # Chroma did lose some: the recovery ran


def test_without_the_rebuild_a_lost_index_cannot_be_read(real, monkeypatch):
    """Negative control: the same collections asked raw, as before."""
    pytest.importorskip("chromadb")
    _tiny_pool(monkeypatch)
    names = _small_collections(real, 40)
    failures = []
    for name in names:
        try:
            real.index._vector_store_for(name)._collection.query(query_embeddings=[[0.1] * 32], n_results=3)
        except Exception as exc:  # noqa: BLE001 - the defect being shown
            failures.append(str(exc))
    assert failures and all("Nothing found on disk" in f for f in failures), failures


def test_new_collections_are_made_with_the_measured_hnsw_settings(real):
    _library(real, "api", TEXTS)
    vs = real.index._vector_store_for("api")
    assert vs.available
    hnsw = vs._collection.configuration_json["hnsw"]
    assert {k: hnsw[k] for k in vsg._HNSW} == vsg._HNSW
    assert not vs.graph_outdated()


def test_an_older_collection_is_rebuilt_from_its_own_vectors(real):
    """A collection made with Chroma's defaults (before 2026-10-15) gets the
    new graph by a copy of its vectors: nothing is embedded again."""
    _library(real, "api", TEXTS)
    ki.run_repair_sweep(time_budget_s=60)
    vs = real.index._vector_store_for("api")
    held = vs._collection.get(include=["embeddings", "metadatas", "documents"])
    vsg.release_dropped_vectors(real.index._persist_dir)  # let go, then make it the old way
    vs._client.delete_collection("kazma_kb_api")
    old = vs._client.get_or_create_collection(name="kazma_kb_api", metadata={"hnsw:space": "cosine"},
                                              embedding_function=None)
    old.upsert(ids=held["ids"], embeddings=held["embeddings"], metadatas=held["metadatas"],
               documents=held["documents"])
    assert old.configuration_json["hnsw"]["max_neighbors"] == 16
    fresh = ki.KnowledgeIndex(real.store)  # a restart

    def reembed(*_a, **_k):
        raise AssertionError("an index rebuild embeds nothing")

    real.embedder.encode_batch = reembed
    report = fresh.backfill_vectors(time_budget_s=60)
    assert report["graphs_rebuilt"] == 1 and report["embedded"] == 0 and report["missing"] == 0
    rebuilt = fresh._vector_store_for("api")._collection
    assert rebuilt.configuration_json["hnsw"]["max_neighbors"] == vsg._HNSW["max_neighbors"]
    assert sorted(rebuilt.get()["ids"]) == sorted(held["ids"])
    del real.embedder.encode_batch
    assert fresh.search_sync("webhooks retry delivery", "api")[0].chunk_id == "api-0"
    assert fresh.backfill_vectors(time_budget_s=60)["graphs_rebuilt"] == 0  # once
    vsg.release_dropped_vectors(fresh._persist_dir)
    assert _folders_no_collection_owns(real.root) == []


def test_an_older_collection_searches_wider(real):
    """The search breadth is changed in place (Chroma applies it at the
    index's next load)."""
    _library(real, "api", TEXTS)
    vs = real.index._vector_store_for("api")
    assert vs.available
    vs._client.delete_collection("kazma_kb_api")
    vs._client.get_or_create_collection(name="kazma_kb_api", metadata={"hnsw:space": "cosine"},
                                        embedding_function=None)
    reopened = vsg.VectorStore("kazma_kb_api", persist_dir=real.index._persist_dir)
    assert reopened.available
    after = reopened._client.get_collection("kazma_kb_api").configuration_json["hnsw"]
    assert after["ef_search"] == vsg._HNSW["ef_search"] and after["max_neighbors"] == 16
    assert reopened.graph_outdated()


# ── recall at 10,000 chunks (the KB-Q01 corpus) ────────────────────────────


def _recall(settings: dict, lang: str = "en", size: int = 10_000, questions: int = 40) -> int:
    """Planted answers in meaning search's first 20, on the harness's corpus
    and its hash embedder, with these HNSW settings."""
    import chromadb
    from chromadb.config import Settings

    sys.path.insert(0, str(REPO / "scripts"))
    import knowledge_soak as ks

    lib = f"scale-{size}-{lang}"
    planted = {p["chunk_no"]: p["fact"] for p in ks._planted(20261015, lib, lang, size, questions)}
    emb = ks.SoakHashEmbedder(1)
    import tempfile

    root = tempfile.mkdtemp(prefix="kb-recall-")
    client = chromadb.PersistentClient(path=root, settings=Settings(anonymized_telemetry=False))
    try:
        col = client.get_or_create_collection(name="recall", configuration={"hnsw": settings},
                                              embedding_function=None)
        for start in range(0, size, 500):
            rows = range(start, min(size, start + 500))
            col.upsert(ids=[f"c{n}" for n in rows],
                       embeddings=[vsg._unit(emb.encode(ks.scale_chunk(20261015, lib, lang, n, planted.get(n))[1]))
                                   for n in rows])
        found = 0
        for n, fact in planted.items():
            got = col.query(query_embeddings=[vsg._unit(emb.encode(fact["question"]))], n_results=20)
            found += f"c{n}" in got["ids"][0]
        return found
    finally:
        client.close()


#: Chroma inserts in parallel, so a graph differs a little from build to
#: build: on this English corpus the measured settings found 39-40 of 40,
#: the defaults 34-37 (every language: 39-40 against 31-39).
_RECALL_FLOOR = 39


def test_meaning_recall_at_ten_thousand_chunks_with_the_measured_settings():
    pytest.importorskip("chromadb")
    assert _recall(dict(vsg._HNSW)) >= _RECALL_FLOOR


def test_chromas_default_index_passes_over_planted_answers():
    """Negative control: Chroma's defaults on the same corpus."""
    pytest.importorskip("chromadb")
    assert _recall({"space": "cosine"}) < _RECALL_FLOOR
