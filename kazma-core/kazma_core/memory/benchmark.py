"""Memory retrieval benchmark (Stage 2, R7): the real write path, the real recall().

A persona's life across dated chat sessions inside a large pool of unrelated
chat (``benchmark_data/dataset.json``, built by ``scripts/memory_bench.py``)
is written into a PRIVATE database the way the product writes it: chat turns
through ``dual_write.episode_row`` (the row, tier and embedded text the live
writer computes), and the facts extraction stores from them through
``mutate_belief`` (linked to their turn, superseding in the order they were
said, and kept private: nothing reaches a mirror or a shared index), and
(dataset v3) weekly topic summaries as ``topic_summaries`` stores them. Every
question then goes through ``recall()`` and is scored against the memories
that answer it -- a turn, a fact, or a weekly summary standing for one, in
the order recall shows them (facts, history, then weekly summaries).

Scores per category (single, paraphrase, assistant, multi, update, keyword,
noise, arabic, fact, overview, abstain):

* ``hit_rate`` -- every piece of the answer injected (for ``abstain``:
  nothing injected at all);
* ``mrr`` -- reciprocal rank of the first injected answer;
* ``precision`` -- the share of what recall injected that answers the question
  (what the model has to read past otherwise);
* ``order`` -- the answer ranks above the stale or near-miss memories named
  for the question.

It never touches live memory: its own temp database, its own tenant id, and
``recall(local_only=True)`` so no state mirror or remote index is consulted.
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

__all__ = [
    "DATASET_PATH",
    "RecordingEmbedder",
    "ReplayEmbedder",
    "gated_scores",
    "load_dataset",
    "run_benchmark",
]

DATASET_PATH = Path(__file__).with_name("benchmark_data") / "dataset.json"
BENCH_TENANT = "bench"
#: A fixed clock, so every run seeds the same created_at values.
BASE_TIME = 1780000000.0


def load_dataset(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DATASET_PATH).read_text(encoding="utf-8"))


def _seed(conn: sqlite3.Connection, dataset: dict[str, Any]) -> dict[str, str]:
    """Write every turn and fact as the product would; return ``ref -> row id``."""
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import episode_row
    from kazma_core.memory.embedder import encode_text_to_blob

    refs: dict[str, str] = {}
    for session in dataset["sessions"]:
        for i, turn in enumerate(session["turns"], 1):
            row = episode_row(
                session_id=f"bench-{session['id']}",
                turn_number=i,
                user_text=turn["user"],
                assistant_text=turn["assistant"],
                tenant_id=BENCH_TENANT,
                source="benchmark",
                created_at=BASE_TIME + session["day"] * 86400 + i * 60,
            )
            blob = encode_text_to_blob(row["embed_text"]) if row["embed_text"] else None
            conn.execute(
                "INSERT OR IGNORE INTO episodes (id, tenant_id, session_id, turn_number, "
                "user_text, assistant_text, summary_text, tier, structural_importance, "
                "created_at, metadata_json, embedding_model_version, embedding) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"], row["tenant_id"], row["session_id"], row["turn_number"],
                    row["user_text"], row["assistant_text"], row["summary_text"],
                    row["tier"], row["importance"], row["created_at"],
                    json.dumps(row["meta"], ensure_ascii=False),
                    row["embedding_model_version"], blob,
                ),
            )
            refs[f"{session['id']}#{i}"] = row["id"]
    conn.commit()
    for fact in dataset.get("facts", []):
        stated = BASE_TIME + fact["day"] * 86400 + (fact["turn"] or 0) * 60 + 30
        result = mutate_belief(
            conn,
            fact["subject"],
            fact["predicate"],
            fact["object"],
            predicate_type=fact["predicate_type"],
            confidence=0.9,
            importance=fact["importance"],
            extraction_method=fact["extraction_method"],
            tenant_id=BENCH_TENANT,
            source_session=f"bench-{fact['session']}" if fact["session"] else None,
            source_turn=fact["turn"],
            now=stated,
            private=True,
        )
        if not result.get("belief_id"):
            raise RuntimeError(f"fact {fact['id']} was not stored: {result}")
        refs[fact["id"]] = str(result["belief_id"])
    _seed_summaries(conn, dataset, refs)
    return refs


def _seed_summaries(conn: sqlite3.Connection, dataset: dict[str, Any], refs: dict[str, str]) -> None:
    """Write the weekly topic summaries (dataset v3) as ``topic_summaries``
    stores them: the same text embedded, the turns they came from linked."""
    from kazma_core.memory.embedder import encode_text_to_blob, get_embedding_model_name

    model = get_embedding_model_name()
    for s in dataset.get("summaries", []):
        start = BASE_TIME + s["day"] * 86400
        blob = encode_text_to_blob(f"{s['title']}\n{s['text']}")
        row_id = f"bench-{s['id']}"
        conn.execute(
            "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, "
            "title, summary_text, status, turn_count, chat_count, created_at, updated_at, "
            "embedding, embedding_model_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, 1, ?, ?, ?, ?)",
            (row_id, BENCH_TENANT, f"bench-week-{s['day'] // 7}", start, start + 7 * 86400,
             s["title"], s["text"], len(s["sources"]), start + 8 * 86400, start + 8 * 86400,
             blob, model if blob else None),
        )
        conn.executemany(
            "INSERT INTO memory_summary_sources (summary_id, episode_id) VALUES (?, ?)",
            [(row_id, refs[r]) for r in s["sources"]],
        )
        refs[s["id"]] = row_id
    conn.commit()


def _covers(dataset: dict[str, Any], refs: dict[str, str]) -> dict[str, set[str]]:
    """Summary row id -> the memories it stands for: its turns, and the facts
    extracted from them."""
    by_turn: dict[str, set[str]] = {}
    for f in dataset.get("facts", []):
        if f["session"]:
            by_turn.setdefault(f"{f['session']}#{f['turn']}", set()).add(refs[f["id"]])
    return {
        refs[s["id"]]: {refs[t] for t in s["sources"]} | set().union(*(by_turn.get(t, set()) for t in s["sources"]))
        for s in dataset.get("summaries", [])
    }


def _score(
    question: dict[str, Any], refs: dict[str, str], injected: list[str],
    covers: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Score one question against what recall injected, in the order it is shown.

    Each gold entry is one piece of the answer, "a|b" when either memory
    gives it (the turn, the fact extracted from it, or a weekly summary). A
    weekly summary that stands for an answering turn or fact (*covers*)
    answers too: the model reads the answer in it.
    """
    covers = covers or {}
    pieces = [[refs[a] for a in piece.split("|")] for piece in question["gold"]]
    below = [refs[r] for r in question.get("below", [])]
    rank: dict[str, int] = {}
    for i, item in enumerate(injected, 1):
        rank.setdefault(item, i)
    # Where each memory's content is shown: itself, or a summary standing for it.
    shown = dict(rank)
    for i, item in enumerate(injected, 1):
        for covered in covers.get(item, ()):
            shown.setdefault(covered, i)
    out: dict[str, Any] = {
        "id": question["id"],
        "category": question["category"],
        "injected": len(injected),
    }
    if not pieces:
        out["hit"] = not injected
        return out
    answers = {a for piece in pieces for a in piece}
    ranks = [shown[a] for a in answers if a in shown]
    out["hit"] = all(any(a in shown for a in piece) for piece in pieces)
    out["rr"] = 1.0 / min(ranks) if ranks else 0.0
    out["precision"] = (
        sum(1 for i in injected if i in answers or covers.get(i, set()) & answers) / len(injected)
        if injected else 0.0
    )
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
    lat = sorted(latencies)
    return {
        "categories": summary,
        "overall": {
            "questions": len(results),
            "hit_rate": round(statistics.fmean([s["hit_rate"] for s in summary.values()]), 4),
            "mrr": round(statistics.fmean([r["rr"] for r in answerable]), 4) if answerable else 0.0,
            "precision": round(statistics.fmean([r["precision"] for r in answerable]), 4)
            if answerable else 0.0,
            "latency_p50_ms": round(lat[len(lat) // 2] * 1000, 1) if lat else 0.0,
            "latency_p95_ms": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))] * 1000, 1)
            if lat else 0.0,
        },
    }


def gated_scores(report: dict[str, Any]) -> dict[str, float]:
    """The scores the ratchet holds (``tests/fixtures/memory_bench/thresholds.json``).

    Overall hit rate, MRR and precision, and each category's hit rate, MRR,
    precision and order. Latency is not gated: it is the machine's, not the
    code's.
    """
    out = {f"overall.{k}": float(v) for k, v in report["overall"].items()
           if k in ("hit_rate", "mrr", "precision")}
    for cat, row in report["categories"].items():
        for k in ("hit_rate", "mrr", "precision", "order"):
            if k in row:
                out[f"{cat}.{k}"] = float(row[k])
    return dict(sorted(out.items()))


def run_benchmark(
    dataset: dict[str, Any] | None = None,
    *,
    limit: int = 5,
    keep_results: bool = False,
) -> dict[str, Any]:
    """Seed a private database, ask every question, return the scores."""
    from kazma_core.memory.recall import recall
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    data = dataset or load_dataset()
    fd, path = tempfile.mkstemp(prefix="kazma-bench-", suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_primary_schema(conn)
        refs = _seed(conn, data)
        covers = _covers(data, refs)
        results, latencies = [], []
        for q in data["questions"]:
            started = time.perf_counter()
            res = recall(q["text"], conn=conn, tenant_id=BENCH_TENANT, limit=limit,
                         local_only=True)
            latencies.append(time.perf_counter() - started)
            # Facts, history, then weekly summaries: the order format_recall_block shows.
            injected = (
                [h.id for h in res.beliefs[:limit]] + [h.id for h in res.episodes[:limit]]
                + [h.id for h in res.summaries]
            )
            results.append(_score(q, refs, injected, covers))
        report = _summarise(results, latencies)
        report["memories"] = len(refs)
        if keep_results:
            report["results"] = results
        return report
    finally:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass


# ── Recorded vectors: the real model's answers, replayed without the model ──
#
# CI cannot load bge-m3 (torch). ``scripts/memory_bench.py vectors`` runs the
# benchmark once with the real embedder inside a RecordingEmbedder and saves
# every text it was asked for; ReplayEmbedder answers the same texts from that
# file (int8 per vector, with its scale). A text the file does not hold means
# the dataset or the embedded text changed: regenerate, do not guess.


def _text_key(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class RecordingEmbedder:
    """Wraps a real embedder and keeps every vector it returns."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.dim = getattr(inner, "dim", None)
        self.seen: dict[str, list[float]] = {}

    def encode(self, text: str) -> list[float]:
        vec = self.inner.encode(text)
        if vec:
            self.seen[text] = [float(x) for x in vec]
        return vec

    def save(self, path: str | os.PathLike[str], *, model: str) -> int:
        import numpy as np

        texts = sorted(self.seen)
        vecs = np.asarray([self.seen[t] for t in texts], dtype=np.float32)
        scale = np.maximum(np.abs(vecs).max(axis=1), 1e-12) / 127.0
        quant = np.clip(np.rint(vecs / scale[:, None]), -127, 127).astype(np.int8)
        np.savez_compressed(
            path,
            keys=np.asarray([_text_key(t) for t in texts]),
            vecs=quant,
            scale=scale.astype(np.float32),
            model=np.asarray(model),
        )
        return len(texts)


class ReplayEmbedder:
    """Answers the recorded texts with the recorded vectors."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        import numpy as np

        data = np.load(path)
        self._vecs = data["vecs"]
        self._scale = data["scale"]
        self._index = {str(k): i for i, k in enumerate(data["keys"])}
        self.model = str(data["model"])
        self.dim = int(self._vecs.shape[1])

    def encode(self, text: str) -> list[float]:
        i = self._index.get(_text_key(text))
        if i is None:
            raise KeyError(
                f"no recorded vector for {text[:60]!r}: the benchmark's texts changed; "
                "run: python scripts/memory_bench.py vectors"
            )
        return (self._vecs[i].astype("float32") * self._scale[i]).tolist()
