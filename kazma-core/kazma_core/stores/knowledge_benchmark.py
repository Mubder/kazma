"""Knowledge Library retrieval benchmark (KQ01): the real publication path,
the real searches.

Four documentation libraries (``knowledge_benchmark_data/dataset.json``,
built by ``scripts/knowledge_bench.py``) -- an English messaging API, an
English object store whose topics sit beside it, an Arabic invoicing help
centre and an operations runbook in Arabic mixed with English -- are written
into a PRIVATE Knowledge store the way the product ingests a page: chunked by
``chunk_markdown_doc`` and published through
:meth:`~kazma_core.stores.knowledge_index.KnowledgeIndex.index` (one
``publish_page`` transaction a page, then its vectors), on the store's own
index (:func:`~kazma_core.stores.knowledge_index.knowledge_index_for`).
Every question is then asked on both of the product's paths:

* ``explicit`` -- what ``knowledge_search`` reads with no library named: one
  fused search over every active library
  (:meth:`~kazma_core.stores.knowledge_index.KnowledgeIndex.search_all_sync`)
  at the tool's default of 5 hits;
* ``inject`` -- what a turn injects:
  :func:`~kazma_core.stores.knowledge_index.build_knowledge_inject`, the
  function the supervisor's turn note calls, at its default of 3 chunks.

Scores per category (exact, paraphrase, arabic, arabic_paraphrase, mixed,
cross, multi, abstain, abstain_ar) and path:

* ``hit_rate`` -- every piece of the answer shown (for ``abstain``: nothing
  shown at all);
* ``mrr`` -- reciprocal rank of the first answer shown;
* ``precision`` -- the share of what was shown that answers the question;
* ``order`` -- the answer ranks above the near misses named for it (another
  product's rate limit, another system's backup).

Two profiles: ``hybrid`` (meaning search on, vectors from the embedder the
caller gives) and ``keyword`` (no embedder: a fresh install without the
``rag`` extra, or a vector store that is down). ``heldout`` questions are
scored apart: no threshold was chosen on them.

Meaning search runs on :class:`_ExactVectorClient`, a Chroma-shaped client
that compares every stored vector by cosine: CI has no chromadb, and
``scripts/knowledge_bench.py run --chroma`` gave the same scores on the real
one (1.5.9, 2026-10-15). It serves only this benchmark's private vector
folder (``vector_store_global.directory_client``). CI replays the vectors
bge-m3 returned for exactly these texts (``ReplayEmbedder``,
``tests/fixtures/knowledge_bench/vectors.npz``); ``run --real`` with the
live model gave the same scores too.

It never touches the install's Knowledge Library: its own temporary
database and vector folder, refused when ``KAZMA_VECTOR_PATH`` would send the
vectors elsewhere. Run it from a test or the script, never inside the server:
for the run's duration the embedder and the operator's inject switches are
the instrument's.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest import mock

__all__ = [
    "DATASET_PATH",
    "EXPLICIT_TOP_K",
    "INJECT_TOP_K",
    "gated_scores",
    "load_dataset",
    "run_benchmark",
    "run_profiles",
]

DATASET_PATH = Path(__file__).with_name("knowledge_benchmark_data") / "dataset.json"
#: ``knowledge_search``'s default hit count.
EXPLICIT_TOP_K = 5
#: ``KAZMA_KB_AUTO_INJECT_TOP_K``'s default.
INJECT_TOP_K = 3
_PATHS = ("explicit", "inject")


def load_dataset(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DATASET_PATH).read_text(encoding="utf-8"))


# ── An exact, in-process vector client ─────────────────────────────────────


class _ExactCollection:
    """The part of a Chroma collection ``VectorStore`` uses, exact cosine."""

    def __init__(self, name: str, metadata: dict[str, Any] | None = None,
                 configuration: dict[str, Any] | None = None) -> None:
        self.name = name
        self.metadata = dict(metadata or {})
        # Chroma's own shape: the HNSW settings the collection was made with
        # (the space among them, which ``VectorStore`` reads).
        self.configuration_json: dict[str, Any] = {"hnsw": dict((configuration or {}).get("hnsw") or {})}
        self._rows: dict[str, tuple[list[float], str, dict[str, Any]]] = {}
        self._client: _ExactVectorClient | None = None

    def modify(self, name: str | None = None, configuration: dict[str, Any] | None = None,
               metadata: dict[str, Any] | None = None) -> None:
        """Rename, or change settings (exact search has no index to tune)."""
        if configuration:
            self.configuration_json["hnsw"].update((configuration or {}).get("hnsw") or {})
        if metadata is not None:
            self.metadata = dict(metadata)
        if name and name != self.name and self._client is not None:
            self._client._rename(self, name)

    def upsert(self, ids, embeddings, documents, metadatas) -> None:
        for doc_id, vec, doc, meta in zip(ids, embeddings, documents, metadatas):
            self._rows[str(doc_id)] = ([float(x) for x in vec], doc, dict(meta))

    def count(self) -> int:
        return len(self._rows)

    def delete(self, ids) -> None:
        for doc_id in ids:
            self._rows.pop(str(doc_id), None)

    def get(self, ids=None, include=None, limit=None, offset=None, where=None) -> dict[str, Any]:
        keys = list(self._rows) if ids is None else [str(i) for i in ids if str(i) in self._rows]
        keys = [k for k in keys if self._matches(self._rows[k][2], where)]
        keys = keys[offset or 0:]
        if limit is not None:
            keys = keys[:limit]
        out: dict[str, Any] = {"ids": keys, "documents": [self._rows[k][1] for k in keys]}
        include = include or []
        if "metadatas" in include:
            out["metadatas"] = [dict(self._rows[k][2]) for k in keys]
        if "embeddings" in include:
            out["embeddings"] = [list(self._rows[k][0]) for k in keys]
        return out

    @classmethod
    def _matches(cls, meta: dict[str, Any], cond: dict[str, Any] | None) -> bool:
        if not cond:
            return True
        if "$and" in cond:
            return all(cls._matches(meta, c) for c in cond["$and"])
        return all(meta.get(k) == v for k, v in cond.items())

    def query(self, query_embeddings, n_results, where=None) -> dict[str, Any]:
        import numpy as np

        q = np.asarray(query_embeddings[0], dtype=np.float64)
        q = q / (np.linalg.norm(q) or 1.0)
        scored = []
        for doc_id, (vec, _doc, meta) in self._rows.items():
            if len(vec) != len(q) or not self._matches(meta, where):
                continue
            v = np.asarray(vec, dtype=np.float64)
            cos = float(q @ v) / float(np.linalg.norm(v) or 1.0)
            scored.append((doc_id, 1.0 - cos))  # Chroma's cosine distance
        scored.sort(key=lambda row: (row[1], row[0]))
        scored = scored[:n_results]
        return {"ids": [[d for d, _ in scored]], "distances": [[x for _, x in scored]]}


class _ExactVectorClient:
    """A Chroma-shaped client whose collections compare every vector exactly."""

    def __init__(self) -> None:
        self._collections: dict[str, _ExactCollection] = {}

    def get_or_create_collection(self, name, metadata=None, configuration=None,
                                 embedding_function=None) -> _ExactCollection:
        if name not in self._collections:
            return self.create_collection(name, metadata=metadata, configuration=configuration)
        return self._collections[name]

    def create_collection(self, name, metadata=None, configuration=None,
                          embedding_function=None) -> _ExactCollection:
        if name in self._collections:
            raise ValueError(f"collection {name} already exists")
        collection = _ExactCollection(name, metadata, configuration)
        collection._client = self
        self._collections[name] = collection
        return collection

    def delete_collection(self, name) -> None:
        self._collections.pop(name, None)

    def _rename(self, collection: _ExactCollection, name: str) -> None:
        self._collections.pop(collection.name, None)
        collection.name = name
        self._collections[name] = collection


# ── Seeding and scoring ────────────────────────────────────────────────────


def _seed(store: Any, index: Any, dataset: dict[str, Any]) -> tuple[dict[str, str], int]:
    """Publish every page as an ingest does; return ``section ref -> chunk id``
    and the chunk count."""
    from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict

    refs: dict[str, str] = {}
    chunks_total = 0
    for lib in dataset["libraries"]:
        store.create_library(lib["id"], lib["name"], seed_url=lib["seed"])
        store.update_library(lib["id"], auto_inject=True)
        for page in lib["pages"]:
            markdown = "\n\n".join(f"## {s['heading']}\n\n{s['body']}" for s in page["sections"]) + "\n"
            chunks = [chunk_to_dict(c) for c in chunk_markdown_doc(
                markdown, source_url=page["url"], library_id=lib["id"], document_title=page["title"])]
            if len(chunks) != len(page["sections"]):
                raise RuntimeError(f"{page['url']}: {len(chunks)} chunks for "
                                   f"{len(page['sections'])} sections")
            new, _unchanged = index.index(lib["id"], chunks)
            if new != len(chunks):
                raise RuntimeError(f"{page['url']}: {new} of {len(chunks)} chunks published")
            for section, chunk in zip(page["sections"], chunks):
                refs[section["ref"]] = chunk["id"]
            chunks_total += len(chunks)
    return refs, chunks_total


def _score(question: dict[str, Any], refs: dict[str, str], shown: list[str]) -> dict[str, Any]:
    """One question against what a path showed, in its order."""
    pieces = [[refs[a] for a in piece.split("|")] for piece in question["gold"]]
    below = [refs[r] for r in question.get("below", [])]
    rank: dict[str, int] = {}
    for i, item in enumerate(shown, 1):
        rank.setdefault(item, i)
    out: dict[str, Any] = {"id": question["id"], "category": question["category"], "shown": len(shown)}
    if not pieces:
        out["hit"] = not shown
        return out
    answers = {a for piece in pieces for a in piece}
    ranks = [rank[a] for a in answers if a in rank]
    out["hit"] = all(any(a in rank for a in piece) for piece in pieces)
    out["rr"] = 1.0 / min(ranks) if ranks else 0.0
    out["precision"] = sum(1 for i in shown if i in answers) / len(shown) if shown else 0.0
    if below:
        best = min(ranks) if ranks else None
        out["order"] = best is not None and all(best < rank[b] for b in below if b in rank)
    return out


def _summarise(results: list[dict[str, Any]], latencies: list[float]) -> dict[str, Any]:
    cats: dict[str, dict[str, Any]] = {}
    for r in results:
        c = cats.setdefault(r["category"], {"n": 0, "hits": 0, "rr": [], "precision": [], "order": []})
        c["n"] += 1
        c["hits"] += int(bool(r["hit"]))
        if "rr" in r:
            c["rr"].append(r["rr"])
            c["precision"].append(r["precision"])
        if "order" in r:
            c["order"].append(bool(r["order"]))
    summary: dict[str, Any] = {}
    for name, c in sorted(cats.items()):
        row: dict[str, Any] = {"n": c["n"], "hit_rate": round(c["hits"] / c["n"], 4)}
        if c["rr"]:
            row["mrr"] = round(statistics.fmean(c["rr"]), 4)
            row["precision"] = round(statistics.fmean(c["precision"]), 4)
        if c["order"]:
            row["order"] = round(sum(c["order"]) / len(c["order"]), 4)
        summary[name] = row
    answerable = [r for r in results if "rr" in r]
    unanswerable = [r for r in results if "rr" not in r]
    lat = sorted(latencies)
    return {
        "categories": summary,
        "overall": {
            "questions": len(results),
            "hit_rate": round(statistics.fmean([s["hit_rate"] for s in summary.values()]), 4),
            "mrr": round(statistics.fmean([r["rr"] for r in answerable]), 4) if answerable else 0.0,
            "precision": round(statistics.fmean([r["precision"] for r in answerable]), 4)
            if answerable else 0.0,
            "abstention": round(sum(1 for r in unanswerable if r["hit"]) / len(unanswerable), 4)
            if unanswerable else 0.0,
            "latency_p50_ms": round(lat[len(lat) // 2] * 1000, 1) if lat else 0.0,
            "latency_p95_ms": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))] * 1000, 1)
            if lat else 0.0,
        },
    }


def gated_scores(report: dict[str, Any]) -> dict[str, float]:
    """The scores the ratchet holds (``tests/fixtures/knowledge_bench/thresholds.json``).

    For each profile and path, tuning and held out: overall hit rate, MRR,
    precision and abstention, and each category's hit rate, MRR, precision
    and order. Latency is the machine's, not the code's: not gated.
    """
    out: dict[str, float] = {}
    for profile, part in report["profiles"].items():
        for prefix, source in (("", part), ("heldout.", part["heldout"])):
            for path in _PATHS:
                body = source[path]
                head = f"{profile}.{prefix}{path}"
                out.update({f"{head}.overall.{k}": float(v) for k, v in body["overall"].items()
                            if k in ("hit_rate", "mrr", "precision", "abstention")})
                for cat, row in body["categories"].items():
                    for k in ("hit_rate", "mrr", "precision", "order"):
                        if k in row:
                            out[f"{head}.{cat}.{k}"] = float(row[k])
    return dict(sorted(out.items()))


def _patches(embedder: Any) -> list[Any]:
    """The run's instrument: this embedder, inject on, smart search off and
    the shipped inject size -- whatever the operator set."""
    from kazma_core.stores import knowledge_index

    return [
        mock.patch("kazma_core.memory.vector_store_global.get_embedder", lambda: embedder),
        mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder),
        mock.patch.object(knowledge_index, "kb_auto_inject_enabled", lambda: True),
        mock.patch.object(knowledge_index, "kb_smart_search_enabled", lambda: False),
        mock.patch.object(knowledge_index, "_auto_inject_top_k", lambda: INJECT_TOP_K),
    ]


def run_benchmark(
    dataset: dict[str, Any] | None = None,
    *,
    embedder: Any = None,
    client: Any = None,
    keep_results: bool = False,
) -> dict[str, Any]:
    """Seed a private store, ask every question on both paths, return the scores.

    *embedder* None runs keyword search alone. *client* serves the vectors
    (default: a fresh :class:`_ExactVectorClient`).
    """
    from kazma_core.memory.vector_store_global import directory_client
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import (
        _vector_dir_for,
        build_knowledge_inject,
        drop_knowledge_index,
        knowledge_index_for,
    )

    data = dataset or load_dataset()
    root = tempfile.mkdtemp(prefix="kazma-kb-bench-")
    store = KnowledgeStore(db_path=str(Path(root) / "knowledge.db"))
    try:
        vector_dir = _vector_dir_for(store)
        if Path(vector_dir).resolve().parent != Path(root).resolve():
            raise RuntimeError("KAZMA_VECTOR_PATH is set: the benchmark refuses to write its "
                               "vectors outside its own folder")
        with ExitStack() as stack:
            stack.enter_context(directory_client(vector_dir, client or _ExactVectorClient()))
            for patch in _patches(embedder):
                stack.enter_context(patch)
            index = knowledge_index_for(store)
            refs, chunks = _seed(store, index, data)
            library_ids = [lib["id"] for lib in store.list_libraries()]

            def ask(questions: list[dict[str, Any]]) -> dict[str, Any]:
                results: dict[str, list[dict[str, Any]]] = {p: [] for p in _PATHS}
                latencies: dict[str, list[float]] = {p: [] for p in _PATHS}
                for q in questions:
                    started = time.perf_counter()
                    hits = index.search_all_sync(q["text"], library_ids, top_k=EXPLICIT_TOP_K)
                    latencies["explicit"].append(time.perf_counter() - started)
                    results["explicit"].append(_score(q, refs, [h.chunk_id for h in hits]))
                    started = time.perf_counter()
                    inject = build_knowledge_inject(q["text"], store=store)
                    latencies["inject"].append(time.perf_counter() - started)
                    results["inject"].append(_score(q, refs, [str(h["id"]) for h in inject.hits]))
                part = {p: _summarise(results[p], latencies[p]) for p in _PATHS}
                if keep_results:
                    for p in _PATHS:
                        part[p]["results"] = results[p]
                return part

            report = ask(data["questions"])
            report["heldout"] = ask(data.get("heldout", []))
            report["chunks"] = chunks
            report["meaning"] = embedder is not None
            report["version"] = data["version"]
            return report
    finally:
        drop_knowledge_index(store)
        store.close()
        shutil.rmtree(root, ignore_errors=True)


def run_profiles(
    dataset: dict[str, Any] | None = None,
    *,
    embedder: Any,
    client: Any = None,
    keep_results: bool = False,
) -> dict[str, Any]:
    """Both profiles: ``hybrid`` on *embedder*, ``keyword`` with none."""
    data = dataset or load_dataset()
    return {
        "version": data["version"],
        "profiles": {
            "hybrid": run_benchmark(data, embedder=embedder, client=client, keep_results=keep_results),
            "keyword": run_benchmark(data, embedder=None, keep_results=keep_results),
        },
    }
