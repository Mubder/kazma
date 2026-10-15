"""A library's meaning search widens as the library grows (KB-Q01's
follow-up, 2026-10-15).

At 100,000 chunks in one library an exact search ranked all 40 planted
answers of the KB-Q01 corpus first while the HNSW index, searched with the
fixed breadth of 256, returned 32 of them. ``scripts/knowledge_soak.py
breadth`` measured recall and latency per size and breadth (the knowledge
guide's "Meaning search at scale"), and a collection's search breadth now
follows its size (``vector_store_global.search_breadth``).

What Chroma does that the design rests on (chromadb 1.5.9, measured here):
a collection's search breadth applies when Chroma loads its HNSW index;
every read loads it -- a query, a ``get``, a ``count``; and a loaded index
keeps the breadth it was loaded with while the client lives (Kazma's index
pool never evicts one). So:

* the size that decides the breadth is read from Chroma's catalog
  (``chroma_local.collection_size``), which loads nothing, and the breadth
  is set when a collection is opened, before anything reads it;
* the repair pass sets it for a collection that grew past a step while its
  index was loaded, and has the indexes loaded again
  (``reload_vector_indexes``);
* a rebuilt collection is made with its size's breadth.

The rule and the wiring are held without chromadb (a stand-in that loads an
index at its first read, as Chroma does); recall is held on the real
chromadb where the ``rag`` extra is installed. Each with its negative
control: the fixed breadth, a size read with ``count()``, no reload.
"""

from __future__ import annotations

import random
import types

import pytest
from kazma_core.memory import chroma_local
from kazma_core.memory import vector_store_global as vsg
from kazma_core.stores import knowledge_index as ki

from tests._module_stubs import stub_modules
from tests.test_knowledge_meaning_search import _Collection, _Embedder, _seed

# ── the rule ────────────────────────────────────────────────────────────────


def test_the_breadth_grows_with_the_collection():
    base = vsg._HNSW["ef_search"]
    assert vsg.search_breadth(0) == vsg.search_breadth(1) == base == vsg._BREADTH_GRID[0]
    # One candidate for every 12 vectors at least, at most 1.5 times that,
    # wherever the grid reaches (the measured envelope: 4,096 at 50,000,
    # 6,144 at 100,000, 16,384 at 200,000).
    top = vsg._VECTORS_PER_CANDIDATE * vsg._BREADTH_GRID[-1]
    for size in (3_072, 9_999, 10_000, 50_000, 100_000, 200_000, 480_000, top):
        assert size / 12 <= vsg.search_breadth(size) <= 1.5 * size / 12, size
    assert all(vsg.search_breadth(size) >= found_all
               for size, found_all in ((50_000, 4_096), (100_000, 6_144), (200_000, 16_384)))
    sizes = [0, *(at for at, _b in vsg._BREADTH_STEPS), 10**7]
    breadths = [vsg.search_breadth(n) for n in sizes]
    assert breadths == sorted(breadths) and len(set(breadths)) == len(vsg._BREADTH_STEPS) + 1
    for at_least, breadth in vsg._BREADTH_STEPS:
        assert vsg.search_breadth(at_least) == breadth
        assert vsg.search_breadth(at_least - 1) < breadth
    # Steps further apart than the share a breadth is kept within: a
    # collection is never held at a breadth two steps up.
    steps = [at for at, _b in vsg._BREADTH_STEPS]
    assert all(b > a / vsg._BREADTH_KEEP_SHARE for a, b in zip(steps, steps[1:]))


def test_a_breadth_is_kept_until_the_collection_is_a_fifth_past_its_step():
    (first, wide), (_second, wider) = vsg._BREADTH_STEPS[:2]
    base = vsg._HNSW["ef_search"]
    assert vsg._fitted_breadth(base, first) == wide  # raised at the step
    assert vsg._fitted_breadth(wide, first - 1) == wide  # kept just below it
    assert vsg._fitted_breadth(wide, int(first * 0.81)) == wide
    assert vsg._fitted_breadth(wide, int(first * 0.79)) == base  # lowered a fifth below
    assert vsg._fitted_breadth(100, 10) == base  # Chroma's default, before 2026-10-15
    assert vsg._fitted_breadth(wider, first) == wide  # never kept above the next step's share


# ── the wiring, on a stand-in that loads an index as Chroma does ────────────

_DISK: dict[str, dict[str, _Loaded]] = {}


class _Loaded(_Collection):
    """A collection whose index is loaded by its first read with the breadth
    its configuration holds then; ``modify`` changes the configuration and
    leaves a loaded index as it is (chromadb 1.5.9)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.loaded: int | None = None
        self.searched_with: list[int] = []
        self.events: list[str] = []

    def _load(self) -> None:
        if self.loaded is None:
            self.loaded = self.configuration_json["hnsw"]["ef_search"]
            self.events.append(f"load {self.loaded}")

    def count(self):
        self._load()
        return super().count()

    def get(self, *args, **kwargs):
        self._load()
        return super().get(*args, **kwargs)

    def upsert(self, *args, **kwargs):
        self._load()
        return super().upsert(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._load()
        return super().delete(*args, **kwargs)

    def query(self, *args, **kwargs):
        self._load()
        self.searched_with.append(self.loaded)
        return super().query(*args, **kwargs)

    def modify(self, name=None, metadata=None, configuration=None):
        if configuration:
            self.configuration_json["hnsw"].update(configuration.get("hnsw") or {})
            self.events.append(f"set {self.configuration_json['hnsw']['ef_search']}")


class _DiskClient:
    """Clients of one path share its collections; closing one drops the
    indexes it loaded (the pool goes with the client)."""

    def __init__(self, path: str = ":memory:", settings: object = None) -> None:
        self.collections = _DISK.setdefault(path, {})

    def get_or_create_collection(self, name, metadata=None, configuration=None, embedding_function=None):
        if name not in self.collections:
            self.collections[name] = _Loaded(name, metadata, configuration)
        return self.collections[name]

    def delete_collection(self, name):
        del self.collections[name]

    def close(self):
        for collection in self.collections.values():
            collection.loaded = None


def _stub() -> dict[str, types.ModuleType]:
    chromadb = types.ModuleType("chromadb")
    chromadb.PersistentClient = lambda path, settings=None: _DiskClient(path, settings)  # type: ignore[attr-defined]
    chromadb.Client = lambda settings=None: _DiskClient(":memory:", settings)  # type: ignore[attr-defined]
    config = types.ModuleType("chromadb.config")
    config.Settings = lambda **kw: kw  # type: ignore[attr-defined]
    chromadb.config = config  # type: ignore[attr-defined]
    return {"chromadb": chromadb, "chromadb.config": config}


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    from kazma_core.stores.knowledge import KnowledgeStore

    monkeypatch.setenv("KAZMA_VECTOR_PATH", str(tmp_path / "vector_memory"))
    monkeypatch.setattr(vsg, "_CLIENTS", {})
    monkeypatch.setattr(ki, "_SWEEP", {"report": None, "unavailable": False, "bad_passes": 0, "at": None})
    _DISK.clear()

    def size(persist_dir, name):  # the catalog's count: the rows, with no index loaded
        held = _DISK.get(str(persist_dir), {}).get(name)
        return 0 if held is None else len(held.rows)

    monkeypatch.setattr(chroma_local, "collection_size", size)
    monkeypatch.setattr(vsg, "_BREADTH_STEPS", ((5, 512),))
    embedder = _Embedder()
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: embedder)
    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    index = ki.KnowledgeIndex(store)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
    with stub_modules(_stub()):
        yield types.SimpleNamespace(store=store, index=index, dir=str((tmp_path / "vector_memory").resolve()))
    store.close()


def _existing(kb, name: str, rows: int, breadth: int = 256) -> _Loaded:
    """A collection already on disk, made with *breadth* (a store's restart)."""
    collection = _DiskClient(kb.dir).get_or_create_collection(
        name, configuration={"hnsw": {**vsg._HNSW, "ef_search": breadth}})
    collection.rows = {f"c{i}": ([1.0] + [0.0] * 63, None, {"model": "m"}) for i in range(rows)}
    return collection


def test_a_collection_is_opened_with_its_sizes_breadth_before_anything_reads_it(kb):
    held = _existing(kb, "kazma_kb_big", rows=8)
    vs = vsg.VectorStore("kazma_kb_big", persist_dir=kb.dir)
    assert vs.query("webhooks", limit=3)
    assert held.events[:2] == ["set 512", "load 512"] and held.searched_with == [512]


def test_negative_control_the_fixed_breadth_searches_a_big_collection_narrow(kb, monkeypatch):
    monkeypatch.setattr(vsg, "_BREADTH_STEPS", ())
    held = _existing(kb, "kazma_kb_big", rows=8)
    assert vsg.VectorStore("kazma_kb_big", persist_dir=kb.dir).query("webhooks", limit=3)
    assert held.searched_with == [256]


def test_negative_control_a_size_read_with_count_loads_the_index_first(kb, monkeypatch):
    """Chroma's own count loads the index: the breadth set after it waits."""
    monkeypatch.setattr(vsg.VectorStore, "_size", lambda self: self._collection.count())
    held = _existing(kb, "kazma_kb_big", rows=8)
    assert vsg.VectorStore("kazma_kb_big", persist_dir=kb.dir).query("webhooks", limit=3)
    assert held.events[:2] == ["load 256", "set 512"] and held.searched_with == [256]


def _grow_and_sweep(kb) -> tuple[_Loaded, dict]:
    """A library searched at 3 chunks (its index loaded at 256), then grown
    to 7, past the step at 5; the passes that follow."""
    _seed(kb, "api", ["Webhooks retry delivery", "Install the tool", "Tokens expire hourly"])
    assert ki.run_repair_sweep(time_budget_s=30)["embedded"] == 3
    kb.index.search_sync("webhooks retry", "api")
    held = _DISK[kb.dir]["kazma_kb_api"]
    assert held.searched_with == [256]
    _seed(kb, "api", ["Rotate keys monthly", "Backups run nightly", "Logs rotate daily", "Alerts page"],
          start=3)
    assert ki.run_repair_sweep(time_budget_s=30)["embedded"] == 4
    report = ki.run_repair_sweep(time_budget_s=30)
    kb.index.search_sync("webhooks retry", "api")
    return held, report


def test_the_repair_pass_widens_a_grown_library_and_loads_its_index_again(kb):
    held, report = _grow_and_sweep(kb)
    assert report["breadths_changed"] == 1 and report["indexes_reloaded"] is True
    assert held.configuration_json["hnsw"]["ef_search"] == 512
    assert held.searched_with[-1] == 512
    again = ki.run_repair_sweep(time_budget_s=30)  # once
    assert again["breadths_changed"] == 0 and again["indexes_reloaded"] is False


def test_negative_control_without_the_reload_a_loaded_index_keeps_its_breadth(kb, monkeypatch):
    monkeypatch.setattr(ki, "reload_vector_indexes", lambda *_a, **_k: False)
    held, report = _grow_and_sweep(kb)
    assert report["breadths_changed"] == 1
    assert held.configuration_json["hnsw"]["ef_search"] == 512  # set, and waiting
    assert held.searched_with[-1] == 256
    assert ki.run_repair_sweep(time_budget_s=30)["breadths_changed"] == 1  # still waiting next pass


def test_listing_the_libraries_reads_no_size_and_opens_no_collection(kb, monkeypatch):
    """KB-S15 stays true: health reads the recorded coverage only."""
    _seed(kb, "api", ["Webhooks retry delivery"])
    ki.run_repair_sweep(time_budget_s=30)
    kb.index._vector_stores.clear()
    monkeypatch.setattr(vsg, "_CLIENTS", {})
    asked: list[str] = []
    monkeypatch.setattr(chroma_local, "collection_size", lambda d, name: asked.append(name) or 0)
    assert kb.index.health("api")["state"] == "ok"
    assert asked == [] and vsg._CLIENTS == {}


# ── recall, on the real chromadb ───────────────────────────────────────────
#
# A sparse graph (4 neighbours, construction breadth 8) over random unit
# vectors of 256 dimensions passes over planted answers at a breadth of 256
# from a few thousand vectors on, where Kazma's graph needs tens of
# thousands of hash-embedded chunks (the harness's measurement): the same
# behaviour, in a second. A graph built in parallel differs from build to
# build, and now and then leaves a vector no search reaches, so recall is
# compared on one graph with the search that reaches everything reachable
# (a breadth of the collection's size). 4,000 vectors: Chroma writes an index
# to disk every 1,000 records and inserts the records after the last write
# again from its log at each load -- in parallel, so with a tail every load
# is another graph; with none, every load is the one written.

_N, _DIM, _QUESTIONS = 4_000, 256, 40
_SPARSE = {"max_neighbors": 4, "ef_construction": 8}


class _Table:
    """Text -> its vector: ``doc-<i>`` a random unit vector, ``q-<j>`` a
    noisy copy of a planted one (its exact nearest neighbour)."""

    dim = _DIM

    def __init__(self, seed: int = 7) -> None:
        rng = random.Random(seed)
        self.vectors = {f"doc-{i}": [rng.gauss(0.0, 1.0) for _ in range(_DIM)] for i in range(_N)}
        self.planted = rng.sample(range(_N), _QUESTIONS)
        for j, i in enumerate(self.planted):  # cosine ~0.7 to its answer, ~0 to the rest
            self.vectors[f"q-{j}"] = [x + rng.gauss(0.0, 1.0) for x in self.vectors[f"doc-{i}"]]

    def encode(self, text: str) -> list[float]:
        return list(self.vectors[text])

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]


@pytest.fixture()
def chroma(tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    table = _Table()
    monkeypatch.setattr(vsg, "_CLIENTS", {})
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: table)
    for key, value in _SPARSE.items():
        monkeypatch.setitem(vsg._HNSW, key, value)
    root = str(tmp_path / "vector_memory")
    try:
        yield types.SimpleNamespace(table=table, root=root)
    finally:
        vsg.release_dropped_vectors(root)
        vsg.reload_vector_indexes(root)


def _build(chroma, upto: int, start: int = 0) -> vsg.VectorStore:
    vs = vsg.VectorStore("kazma_kb_breadth", persist_dir=chroma.root)
    for first in range(start, upto, 1000):
        rows = range(first, min(upto, first + 1000))
        assert vs.index_many([(f"doc-{i}", f"doc-{i}", {"model": "table"}) for i in rows]) == len(rows)
    return vs


def _found(vs: vsg.VectorStore, chroma) -> int:
    """Planted answers among a library search's 20 meaning candidates."""
    return sum(f"doc-{i}" in {cid for cid, _s in vs.query(f"q-{j}", limit=20)}
               for j, i in enumerate(chroma.table.planted))


def _reopened(chroma, breadth: int | None = None) -> vsg.VectorStore:
    """A fresh client (a restart); *breadth*, when given, set before the index loads."""
    assert vsg.reload_vector_indexes(chroma.root)
    vs = vsg.VectorStore("kazma_kb_breadth", persist_dir=chroma.root)
    assert vs.available
    if breadth is not None:
        vs._collection.modify(configuration={"hnsw": {"ef_search": breadth}})
    return vs


def test_chroma_applies_a_breadth_when_it_loads_an_index_and_count_loads_it(chroma, monkeypatch):
    """The behaviour the design rests on, on the real chromadb; and the
    catalog's size is the collection's count, read without loading."""
    monkeypatch.setattr(vsg, "_BREADTH_STEPS", ())
    _build(chroma, _N)
    reach = _found(_reopened(chroma, breadth=_N), chroma)
    narrow = _found(_reopened(chroma, breadth=16), chroma)
    assert narrow < reach
    vs = _reopened(chroma)  # 256 in the catalog
    assert chroma_local.collection_size(vs._key, vs._collection_name) == vs._collection.count() == _N
    vs._collection.modify(configuration={"hnsw": {"ef_search": 16}})  # after the count loaded it
    assert _found(vs, chroma) == _found(_reopened(chroma, breadth=256), chroma)
    vs = _reopened(chroma)
    assert chroma_local.collection_size(vs._key, vs._collection_name) == _N  # loads nothing
    vs._collection.modify(configuration={"hnsw": {"ef_search": 16}})
    assert _found(vs, chroma) == narrow


def test_the_breadth_for_the_size_finds_what_the_fixed_breadth_misses(chroma, monkeypatch):
    monkeypatch.setattr(vsg, "_BREADTH_STEPS", ())
    _build(chroma, _N)
    reach = _found(_reopened(chroma, breadth=_N), chroma)
    fixed = _found(_reopened(chroma), chroma)  # negative control: 256 at every size
    assert _reopened(chroma)._hnsw()["ef_search"] == 256
    monkeypatch.setattr(vsg, "_BREADTH_STEPS", ((_N // 2, _N),))
    vs = _reopened(chroma)
    assert vs._hnsw()["ef_search"] == _N  # set by the rule, before the index loaded
    assert _found(vs, chroma) == reach > fixed


def test_a_collection_that_grew_is_searched_wider_after_the_indexes_load_again(chroma, monkeypatch):
    monkeypatch.setattr(vsg, "_BREADTH_STEPS", ((_N // 2, _N),))
    vs = _build(chroma, _N // 4)
    assert vs.query("doc-0", limit=1)  # its index loaded at 256
    _build(chroma, _N, start=_N // 4)
    fixed = _found(vs, chroma)
    assert vs.fit_search_breadth() is True  # set in the catalog: the loaded index waits
    assert _found(vs, chroma) == fixed  # negative control: no reload, no change
    assert vsg.reload_vector_indexes(chroma.root)
    assert vs.fit_search_breadth() is False
    assert _found(vs, chroma) == _found(_reopened(chroma, breadth=_N), chroma) > fixed
