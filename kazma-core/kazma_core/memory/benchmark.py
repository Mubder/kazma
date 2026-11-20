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

The second set (dataset v5, Memory audit M24): ``second`` questions are
scored the same way and reported apart (``report["second"]``, gated as
``second.*``). No threshold is swept on them, but decisions were taken by
looking at them, so they are a second check, not an unseen test
(benchmark audit R8, renamed 2026-11-20). The extraction benchmark
(:func:`run_extraction_benchmark`) scores what the heuristic pass -- and,
from recorded replies, the model's deep pass -- keeps from single turns in
English, Arabic and mixed.
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
    "EDGE_DRAWS",
    "EDGE_MOVE",
    "EDGE_SEED",
    "EDGE_WATCH",
    "edge_check",
    "SEALED_CATEGORIES",
    "SEALED_PER_CATEGORY",
    "sealed_dataset",
    "sealed_problems",
    "EXTRACTION_PATH",
    "EXTRACTION_SCORES",
    "RECORD_AGAIN",
    "RecordingEmbedder",
    "ReplayEmbedder",
    "StaleRecordingError",
    "extraction_rests_on",
    "extraction_scores",
    "gated_scores",
    "load_dataset",
    "record_llm_extraction",
    "WRITES_PATH",
    "run_benchmark",
    "run_extraction_benchmark",
    "run_write_scenarios",
    "write_scores",
]

DATASET_PATH = Path(__file__).with_name("benchmark_data") / "dataset.json"
EXTRACTION_PATH = Path(__file__).with_name("benchmark_data") / "extraction.json"
WRITES_PATH = Path(__file__).with_name("benchmark_data") / "writes.json"
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


def _margin(
    question: dict[str, Any], refs: dict[str, str], covers: dict[str, set[str]],
    trace: list[dict[str, Any]],
) -> float | None:
    """How far the question's hit stands from changing, in evidence.

    Each memory's distance to the cut recall held it to (``trace_evidence``:
    the floor, or the gap behind the best), positive when it clears it. A
    piece of the answer stands by its best alternative (a summary standing
    for it counts); the question by its weakest piece. For a question
    nothing answers, by the best memory's distance below the cut. Positive:
    the hit holds by that much; negative: it misses by that much. None: an
    answer that was never a candidate (not near the cut). The limit on how
    many are shown is not counted (benchmark audit R4, the edge rule).
    """
    dist: dict[str, float] = {}
    for row in trace:
        for mid, ev in row["evidence"].items():
            d = ev - row["cut"]
            dist[mid] = max(d, dist.get(mid, d))
    pieces = [{refs[a] for a in piece.split("|")} for piece in question["gold"]]
    if not pieces:
        return round(-max(dist.values()), 4) if dist else None
    margins = []
    for piece in pieces:
        alternatives = piece | {s for s, covered in covers.items() if covered & piece}
        here = [dist[a] for a in alternatives if a in dist]
        if not here:
            return None
        margins.append(max(here))
    return round(min(margins), 4)


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
    out: dict[str, float] = {}
    for prefix, part in (("", report), ("second.", report.get("second"))):
        if not part:
            continue
        out.update({f"{prefix}overall.{k}": float(v) for k, v in part["overall"].items()
                    if k in ("hit_rate", "mrr", "precision")})
        for cat, row in part["categories"].items():
            for k in ("hit_rate", "mrr", "precision", "order"):
                if k in row:
                    out[f"{prefix}{cat}.{k}"] = float(row[k])
    return dict(sorted(out.items()))


def run_benchmark(
    dataset: dict[str, Any] | None = None,
    *,
    limit: int = 5,
    keep_results: bool = False,
) -> dict[str, Any]:
    """Seed a private database, ask every question, return the scores."""
    from kazma_core.memory.recall import recall, trace_evidence
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
        def ask(questions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[float]]:
            results, latencies = [], []
            for q in questions:
                started = time.perf_counter()
                with trace_evidence() as trace:
                    res = recall(q["text"], conn=conn, tenant_id=BENCH_TENANT, limit=limit,
                                 local_only=True)
                latencies.append(time.perf_counter() - started)
                # Facts, history, then weekly summaries: the order format_recall_block shows.
                injected = (
                    [h.id for h in res.beliefs[:limit]] + [h.id for h in res.episodes[:limit]]
                    + [h.id for h in res.summaries]
                )
                scored = _score(q, refs, injected, covers)
                if keep_results:
                    scored["margin"] = _margin(q, refs, covers, trace)
                results.append(scored)
            return results, latencies

        results, latencies = ask(data["questions"])
        report = _summarise(results, latencies)
        report["memories"] = len(refs)
        if data.get("second"):
            held, held_latencies = ask(data["second"])
            report["second"] = _summarise(held, held_latencies)
            if keep_results:
                report["second"]["results"] = held
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


# ── The edge (benchmark audit R4; the owner's decision 3, 2026-11-18) ───────
#
# The recording keeps one byte a number, so the model's real vector is
# anywhere within half a rounding step of the recorded one, and another
# machine's model differs from it by about that much (audit F5: bge-m3's own
# vectors moved 2 of the 78 scores). A hit that holds on the fourth decimal
# of one similarity holds on this machine's recording only. ``lock`` replays
# the benchmark with the vectors moved inside their rounding (EDGE_DRAWS
# seeded draws, the same move for the same text) and refuses when a hit
# flips, unless the question is on the edge list with its margin named: the
# only exit. Never a threshold moved to save one question.

#: Seeded draws the lock replays; the seed makes every lock see the same moves.
EDGE_DRAWS = 10
EDGE_SEED = 20261120
#: How far each recorded number moves, in its own rounding steps (uniform in
#: plus or minus this): half a step, the band the model's value lies in.
EDGE_MOVE = 0.5
#: CI asks the draws again for the questions this close to recall's cut (in
#: evidence), so a change that brings a question to the edge without moving a
#: score is seen: at least three times the largest move a lock measured
#: (``largest_move`` in the lock, 0.0023 on 2026-11-20).
EDGE_WATCH = 0.01


class _MovedEmbedder(ReplayEmbedder):
    """The recorded vectors, each number moved at random by at most
    :data:`EDGE_MOVE` of its rounding step; the same move for the same text
    in one draw."""

    def __init__(self, path: str | os.PathLike[str], seed: int, move: float = EDGE_MOVE) -> None:
        super().__init__(path)
        if str(self._vecs.dtype) != "int8":
            # A float recording has no rounding step: its edge needs the
            # model's measured spread, not this one. Never a quiet pass.
            raise ValueError(
                f"the edge moves an int8 recording inside its rounding; this one is "
                f"{self._vecs.dtype}: measure its spread and give the edge check that"
            )
        self._seed = int(seed)
        self._move = float(move)

    def encode(self, text: str) -> list[float]:
        import hashlib

        import numpy as np

        key = _text_key(text)
        i = self._index.get(key)
        if i is None:
            return super().encode(text)  # the replay's own refusal
        digest = hashlib.sha256(f"{self._seed}:{key}".encode()).digest()
        noise = np.random.default_rng(int.from_bytes(digest[:8], "big")).uniform(
            -self._move, self._move, self._vecs.shape[1])
        return ((self._vecs[i].astype("float32") + noise.astype("float32")) * self._scale[i]).tolist()


def _all_results(report: dict[str, Any]) -> list[dict[str, Any]]:
    return list(report.get("results", [])) + list((report.get("second") or {}).get("results", []))


def edge_check(
    vectors: str | os.PathLike[str],
    base: dict[str, Any],
    *,
    draws: int = EDGE_DRAWS,
    only: set[str] | None = None,
    dataset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay the benchmark *draws* times with the recorded vectors moved
    inside their rounding, against *base* (``run_benchmark(keep_results=
    True)`` on the vectors as recorded).

    Returns the draws' settings, ``flips`` (question id -> in how many draws
    its hit changed), ``shown`` (question id -> in how many draws what it
    showed changed while its hit held: one memory more or less, another
    order), and ``largest_move`` (the most a question's margin moved in any
    draw). *only* asks just those questions (CI's check of the questions near
    the edge); the lock asks every one.
    """
    from unittest import mock

    data = dataset or load_dataset()
    if only is not None:
        data = {**data, "questions": [q for q in data["questions"] if q["id"] in only],
                "second": [q for q in data.get("second", []) if q["id"] in only]}
    was = {r["id"]: r for r in _all_results(base)}
    flips: dict[str, int] = {}
    shown: dict[str, int] = {}
    largest = 0.0
    for i in range(draws):
        moved = _MovedEmbedder(vectors, EDGE_SEED + i)
        with mock.patch("kazma_core.memory.embedder.get_embedder", lambda e=moved: e), \
                mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda m=moved.model: m):
            report = run_benchmark(data, keep_results=True)
        for r in _all_results(report):
            b = was[r["id"]]
            if bool(r["hit"]) != bool(b["hit"]):
                flips[r["id"]] = flips.get(r["id"], 0) + 1
            elif any(r.get(k) != b.get(k) for k in ("injected", "rr", "precision", "order")):
                shown[r["id"]] = shown.get(r["id"], 0) + 1
            if r.get("margin") is not None and b.get("margin") is not None:
                largest = max(largest, abs(r["margin"] - b["margin"]))
    return {"draws": draws, "seed": EDGE_SEED, "move": EDGE_MOVE,
            "flips": dict(sorted(flips.items())), "shown": dict(sorted(shown.items())),
            "largest_move": round(largest, 4)}


# ── The sealed set (benchmark audit R8; the owner's decision 4) ─────────────
#
# Questions written by a session that does not build or tune recall
# (docs/plans/MEMORY_SEALED_SET.md), kept out of the per-commit gate: CI checks
# the file's shape only (:func:`sealed_problems`), and the set is scored only
# by ``scripts/memory_bench.py sealed --look "why"``, every look logged in the
# lock, a deciding look spending it.

#: The categories the sealed set covers, each with at least this many
#: questions (the benchmark audit's R6 minimum for a locked category).
SEALED_CATEGORIES = ("single", "paraphrase", "assistant", "multi", "update", "keyword", "noise", "fact",
                     "overview", "arabic", "mixed", "abstain")
SEALED_PER_CATEGORY = 8
#: Categories other than these hold at least two Arabic questions.
_SEALED_NOT_ARABIC = ("arabic", "mixed", "keyword")


def _folded_question(text: str) -> str:
    import re

    return " ".join(re.findall(r"\w+", str(text or "").casefold()))


def sealed_problems(sealed: dict[str, Any], dataset: dict[str, Any]) -> list[str]:
    """Why *sealed* is not the set the specification asks for (empty: it is).

    Its shape only: never a score. Counts per category, unique ``sNNN`` ids,
    languages, every gold and ``below`` id present in *dataset*, nothing to
    show for ``abstain``, no repeat of a tuning or second-set question.
    """
    import re

    problems: list[str] = []
    for key in ("version", "dataset_version", "written_by", "written_on", "questions"):
        if not sealed.get(key):
            problems.append(f"no {key}")
    if sealed.get("dataset_version") not in (None, dataset.get("version")):
        problems.append(f"written for dataset v{sealed.get('dataset_version')}, the dataset is "
                        f"v{dataset.get('version')}: check its ids, and have it rewritten if they moved")
    refs = {f"{s['id']}#{i}" for s in dataset["sessions"] for i in range(1, len(s["turns"]) + 1)}
    refs |= {f["id"] for f in dataset.get("facts", [])} | {s["id"] for s in dataset.get("summaries", [])}
    # The second set under either key: "heldout" until the rename reached the
    # dataset everywhere (2026-11-20).
    second = dataset.get("second") or dataset.get("heldout") or []
    existing = {_folded_question(q["text"]) for q in dataset["questions"] + second}
    seen_ids: set[str] = set()
    seen_texts: set[str] = set()
    counts: dict[str, int] = {}
    arabic: dict[str, int] = {}
    dialects: dict[str, int] = {}
    for q in sealed.get("questions") or []:
        qid = str(q.get("id") or "")
        where = qid or "a question with no id"
        if not re.fullmatch(r"s\d{3}", qid):
            problems.append(f"{where}: an id is s001 to s096")
        if qid in seen_ids:
            problems.append(f"{where}: the id is used twice")
        seen_ids.add(qid)
        category = q.get("category")
        if category not in SEALED_CATEGORIES:
            problems.append(f"{where}: no category {category!r}")
            continue
        counts[category] = counts.get(category, 0) + 1
        lang = q.get("lang")
        if lang not in ("en", "ar", "mixed"):
            problems.append(f"{where}: lang is en, ar or mixed, not {lang!r}")
        if lang == "ar":
            arabic[category] = arabic.get(category, 0) + 1
            if q.get("dialect") not in ("gulf", "msa"):
                problems.append(f"{where}: an Arabic question names its dialect (gulf or msa)")
            elif category == "arabic":
                dialects[q["dialect"]] = dialects.get(q["dialect"], 0) + 1
        # A mixed question may be asked wholly in one language about a memory
        # said in the other (the specification): its lang is the question's.
        if category == "arabic" and lang != "ar":
            problems.append(f"{where}: an arabic question has lang ar")
        folded = _folded_question(q.get("text"))
        if not folded:
            problems.append(f"{where}: no text")
        elif folded in existing:
            problems.append(f"{where}: repeats a question the benchmark already asks")
        elif folded in seen_texts:
            problems.append(f"{where}: asked twice in the sealed set")
        seen_texts.add(folded)
        gold = q.get("gold")
        if not isinstance(gold, list):
            problems.append(f"{where}: gold is a list")
            gold = []
        if category == "abstain" and gold:
            problems.append(f"{where}: an abstain question has no gold (nothing should be shown)")
        if category != "abstain" and not gold:
            problems.append(f"{where}: an answerable question names the memories that answer it")
        for piece in gold:
            unknown = [a for a in str(piece).split("|") if a not in refs]
            if unknown:
                problems.append(f"{where}: no memory {', '.join(unknown)} in the dataset")
        unknown_below = [b for b in q.get("below") or [] if b not in refs]
        if unknown_below:
            problems.append(f"{where}: no memory {', '.join(unknown_below)} in the dataset (below)")
        if not str(q.get("why") or "").strip():
            problems.append(f"{where}: no why")
    for category in SEALED_CATEGORIES:
        if counts.get(category, 0) < SEALED_PER_CATEGORY:
            problems.append(f"{category}: {counts.get(category, 0)} questions, at least {SEALED_PER_CATEGORY}")
        if category not in _SEALED_NOT_ARABIC and arabic.get(category, 0) < 2:
            problems.append(f"{category}: {arabic.get(category, 0)} Arabic questions, at least 2")
    for dialect in ("gulf", "msa"):
        if dialects.get(dialect, 0) < 3:
            problems.append(f"arabic: {dialects.get(dialect, 0)} {dialect} questions, about half of the category")
    return problems


def sealed_dataset(sealed: dict[str, Any], dataset: dict[str, Any] | None = None) -> dict[str, Any]:
    """The benchmark's memories, asked the sealed questions only."""
    data = dataset or load_dataset()
    return {**data, "questions": list(sealed["questions"]), "second": []}


# ── Extraction (Memory audit M24; scored by value since 2026-11) ───────────
#
# What the two extraction passes keep from one chat turn, scored against the
# facts a reader would keep (``benchmark_data/extraction.json``, built by
# ``scripts/memory_bench.py build``). A gold fact lists the acceptable
# (subject, predicate names, value); a turn with no gold fact should give
# nothing. Each fact a pass keeps is one of:
#
# * *found*: a gold fact's value under its subject -- or the same relation
#   stated from the other side ("lulwa child_of user" for "user daughter
#   Lulwa") -- under any predicate name. A reader calls that fact kept.
# * *an extra*: true, and not asked for (``extras``: "moved from Ooredoo").
# * *unwanted*: anything else, and anything the turn says must not be kept
#   (``avoid``: a sister's job filed as the user's own).
#
# Scores per pass and language: ``found`` (the gold facts kept, under any
# name), ``named`` (of those, the share kept under a listed name, by the
# same-words rule ``predicates._predicate_key``: the name is what varies
# between askings, and two names for one fact keep both current), ``right``
# (the kept facts that are not unwanted) and ``quiet`` (the turns with
# nothing to keep that kept nothing). Until 2026-11 a fact counted only under
# a listed name, so a fact kept under another name was "not found" and
# "wrong" at once (benchmark audit F1); each row still carries that reading
# as ``recall`` and ``precision``, ungated, for comparison.
#
# The model's pass replays recorded replies
# (``tests/fixtures/memory_bench/extraction_llm.json``, recorded with
# ``scripts/memory_extraction_record.py`` on the configured model). A
# recording says what it is: the hash of the system prompt it answered, the
# model, the provider, the date and the askings a turn
# (:func:`_recording_identity`). A recording of another prompt is refused
# (:class:`StaleRecordingError`), and a turn it does not hold is an error, never a
# guess: either way, record again.

#: What to run when a recording does not answer what the code asks.
RECORD_AGAIN = "python scripts/memory_extraction_record.py --out <fixture>"


class StaleRecordingError(ValueError):
    """The recorded replies do not answer what the extractor asks today."""


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extraction_prompt_sha256() -> str:
    """The hash of the system prompt the deep pass sends."""
    from kazma_core.memory.belief_extractor import _EXTRACT_SYSTEM

    return _sha256(_EXTRACT_SYSTEM)


def _recording_identity(llm_replies: dict[str, Any]) -> dict[str, Any]:
    """What a recording is. ``sha256`` covers its replies alone, so the
    description beside them can be completed without making it another
    recording."""
    replies = llm_replies.get("replies") or {}
    return {
        "sha256": _sha256(json.dumps(replies, ensure_ascii=False, sort_keys=True)),
        "turns": len(replies),
        "askings": int(llm_replies.get("askings") or 1),
        "model": llm_replies.get("model"),
        "provider": llm_replies.get("provider"),
        "recorded_on": llm_replies.get("recorded_on"),
        "prompt_sha256": llm_replies.get("prompt_sha256"),
        "message_shape": _recording_shape(llm_replies),
    }


#: How a prompt change is declared until it is recorded (framework audit part
#: C2: the prompt changes land first and are recorded once, with the owner's
#: yes, after them).
DECLARE_PENDING = 'python scripts/memory_bench.py lock --prompt-changed "why"'


def _recording_shape(llm_replies: dict[str, Any]) -> int:
    """The form of the user message the recording was asked in (form 1 when
    it does not say: every recording made before the form was named)."""
    return int(llm_replies.get("message_shape") or 1)


def _check_recording(llm_replies: dict[str, Any], pending: dict[str, Any] | None = None) -> str:
    """How a recording can be replayed against today's extractor:

    * ``"current"``: it answered today's prompt, in today's message form.
    * ``"previous"``: it answered the prompt before a change the lock
      declares (``extraction.pending_recording``, written by
      :data:`DECLARE_PENDING`): the change is named, dated and waits for its
      recording. The replay is then a drift check -- the recorded replies
      through today's parser, sanitizer and scorer -- never a measure of the
      new prompt.

    Anything else is refused (benchmark audit F4: with the prompt replaced by
    "Keep nothing", every deep-pass score used to stay the same)."""
    from kazma_core.memory.belief_extractor import MESSAGE_SHAPE

    recorded = llm_replies.get("prompt_sha256")
    if not recorded:
        raise StaleRecordingError(f"the recording does not say which extraction prompt it answered: {RECORD_AGAIN}")
    current = _extraction_prompt_sha256()
    if recorded == current and _recording_shape(llm_replies) == MESSAGE_SHAPE:
        return "current"
    if (pending and pending.get("prompt_sha256") == current
            and int(pending.get("message_shape") or 1) == MESSAGE_SHAPE
            and pending.get("replaces_prompt_sha256") == recorded
            and int(pending.get("replaces_message_shape") or 1) == _recording_shape(llm_replies)):
        return "previous"
    if recorded == current:
        raise StaleRecordingError(f"the extractor's message changed since the recording: {RECORD_AGAIN}, "
                                  f"or declare the change until then: {DECLARE_PENDING}")
    raise StaleRecordingError(f"the extraction prompt changed since the recording: {RECORD_AGAIN}, "
                              f"or declare the change until then: {DECLARE_PENDING}")


def _slug(text: str) -> str:
    return "_".join(str(text or "").strip().lower().replace("-", " ").split())


def _folded(text: str) -> str:
    """A value as search reads it: folded, and an underscore separates words
    (``memory/query_terms``, FTS5) -- the extraction prompt slugs values, so
    ``kuwait_city`` is the user's "Kuwait City"."""
    from kazma_core.documents.arabic import fold_for_search

    return " ".join(fold_for_search(str(text or "").replace("_", " ")).lower().split())


def _holds(gold: str, value: str) -> bool:
    """The same value, both folded: equal, or one is whole words of the other
    ("kuwait" in "kuwait city", "hiking" in "hiking and"); never a few letters
    of it (a value cut down to "l" does not hold "lisbon")."""
    if not gold or not value:
        return False
    if gold == value:
        return True
    short, long_ = sorted((gold.split(), value.split()), key=len)
    return any(long_[i:i + len(short)] == short for i in range(len(long_) - len(short) + 1))


#: Words that make a predicate say the opposite, or the past, of a plain one:
#: "user dislikes Lisbon" or "user former_employer Google" keeps the value of
#: "user lives_in Lisbon" or "user works_at Google" and says something else.
_TURNED_WORDS = frozenset({"not", "no", "never", "dislike", "dislikes", "disliked", "hate", "hates", "hated",
                           "avoid", "avoids", "avoided", "dont", "doesnt", "didnt", "stopped", "quit", "left",
                           "former", "formerly", "previously", "ex", "used"})


def _turned(predicate: str) -> bool:
    import re

    return any(w in _TURNED_WORDS for w in re.split(r"[_\s'-]+", str(predicate or "").lower().replace("n't", "nt")))


def _value_matches(alt: dict[str, Any], got: dict[str, Any]) -> bool:
    """*got* keeps *alt*'s value under its subject -- or states the same
    relation from the other side (subject and value swapped) -- under any
    name that does not turn it around (:func:`_turned`: a negative or past
    name finds only a negative or past fact)."""
    if _turned(got.get("predicate") or "") != any(_turned(p) for p in alt["predicates"]):
        return False
    gold = _folded(alt["object"])
    subject = _slug(alt["subject"])
    if _slug(got.get("subject")) == subject and _holds(gold, _folded(got.get("object"))):
        return True
    return _slug(got.get("object")) == subject and _holds(gold, _folded(got.get("subject")))


def _fact_matches(alternatives: list[dict[str, Any]], got: dict[str, Any]) -> bool:
    """*got* keeps one of the gold fact's alternatives under its subject AND a
    listed name (the reading before 2026-11, now the ``named`` score)."""
    from kazma_core.memory.predicates import _predicate_key

    subject, predicate, value = _slug(got.get("subject")), got.get("predicate") or "", _folded(got.get("object"))
    if not value:
        return False
    for alt in alternatives:
        if _slug(alt["subject"]) != subject:
            continue
        if _predicate_key(predicate) not in {_predicate_key(p) for p in alt["predicates"]}:
            continue
        if _holds(_folded(alt["object"]), value):
            return True
    return False


def _avoided(entry: dict[str, Any], got: dict[str, Any]) -> bool:
    """*got* is a fact the turn says must not be kept: the entry's subject and
    value, under one of its names (any name when it lists none)."""
    from kazma_core.memory.predicates import _predicate_key

    if _slug(got.get("subject")) != _slug(entry["subject"]):
        return False
    names = entry.get("predicates")
    if names and _predicate_key(got.get("predicate") or "") not in {_predicate_key(p) for p in names}:
        return False
    return _holds(_folded(entry["object"]), _folded(got.get("object")))


def _score_extraction(turns: list[dict[str, Any]], extracted: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_lang: dict[str, dict[str, Any]] = {}
    for turn in turns:
        row = by_lang.setdefault(turn["lang"], {
            "turns": 0, "gold": 0, "gold_found": 0, "gold_named": 0, "kept": 0, "kept_unwanted": 0,
            "kept_listed": 0, "quiet_turns": 0, "quiet_kept_nothing": 0, "unwanted": []})
        got = extracted.get(turn["id"], [])
        gold = turn["facts"]
        allowed = gold + (turn.get("extras") or [])
        avoid = turn.get("avoid") or []
        # A fact the turn says must not be kept finds nothing, whatever value
        # it holds: "user studies_at Bayan School" holds the value of "user
        # daughter_school Bayan School", and says something false.
        good = [g for g in got if not any(_avoided(e, g) for e in avoid)]
        row["turns"] += 1
        row["gold"] += len(gold)
        row["kept"] += len(got)
        row["gold_found"] += sum(1 for alts in gold if any(_value_matches(a, g) for a in alts for g in good))
        row["gold_named"] += sum(1 for alts in gold if any(_fact_matches(alts, g) for g in good))
        row["kept_listed"] += sum(1 for g in got if any(_fact_matches(alts, g) for alts in gold))
        for g in got:
            if g not in good or not any(_value_matches(a, g) for alts in allowed for a in alts):
                row["kept_unwanted"] += 1
                row["unwanted"].append(f"{turn['id']}: {g.get('subject')} {g.get('predicate')} {g.get('object')}")
        if not gold:
            row["quiet_turns"] += 1
            row["quiet_kept_nothing"] += int(not got)
    out = {}
    for lang, row in sorted(by_lang.items()):
        found, kept = row["gold_found"], row["kept"]
        out[lang] = {
            **row,
            "found": round(found / row["gold"], 4) if row["gold"] else 0.0,
            "named": round(row["gold_named"] / found, 4) if found else 1.0,
            "right": round((kept - row["kept_unwanted"]) / kept, 4) if kept else 1.0,
            "quiet": round(row["quiet_kept_nothing"] / row["quiet_turns"], 4) if row["quiet_turns"] else 1.0,
            # The reading before 2026-11 (a fact counts only under a listed
            # name), kept for comparison; not gated.
            "recall": round(row["gold_named"] / row["gold"], 4) if row["gold"] else 0.0,
            "precision": round(row["kept_listed"] / kept, 4) if kept else 1.0,
        }
    return out


def _llm_blob(user_text: str) -> str:
    """The user message ``belief_extractor._llm_extract`` sends for a turn
    with no vocabulary (the benchmark asks with none): the product's own
    builder, :func:`belief_extractor.turn_message`."""
    from kazma_core.memory.belief_extractor import turn_message

    return turn_message(user_text)


def _recorded_message(user_text: str, shape: int) -> str:
    """The user message a recording asked in form *shape* was asked for a
    turn. Form 1 is the turn's text and an empty answer; today's form is the
    product's builder; any other form is a recording Kazma can no longer
    replay."""
    from kazma_core.memory.belief_extractor import MESSAGE_SHAPE
    from kazma_core.memory.limits import DEEP_PASS_QUESTION

    if shape == MESSAGE_SHAPE:
        return _llm_blob(user_text)
    if shape == 1:
        return f"User: {user_text[:DEEP_PASS_QUESTION]}\nAssistant: "
    raise StaleRecordingError(f"the recording was asked in message form {shape}, which Kazma no longer "
                              f"builds: {RECORD_AGAIN}")


def _reply_key(user_text: str, llm_replies: dict[str, Any], mode: str) -> str:
    """The key of a turn's recorded reply: the message today's extractor
    sends (``current``), or the one the recording was asked (``previous``)."""
    if mode == "current":
        return _text_key(_llm_blob(user_text))
    return _text_key(_recorded_message(user_text, _recording_shape(llm_replies)))


class _ReplayClient:
    """Answers the extraction prompt from recorded replies; a prompt nobody
    recorded is an error (the dataset changed: record again). It also checks
    the system message it is sent against the prompt it expects: a prompt
    built another way at run time is a recording of something else.

    In the ``previous`` mode (:func:`_check_recording`) it expects today's prompt
    and answers each turn with the reply recorded for it (``answer``), not by
    the message it is sent: the recording was asked another message."""

    def __init__(self, replies: dict[str, str], prompt_sha256: str) -> None:
        self._replies = replies
        self._prompt = prompt_sha256
        self.answer: str | None = None
        self.stale = False
        self.unrecorded: list[str] = []

    async def chat(self, messages: list[dict[str, Any]], **_kw: Any) -> str:
        # Each problem is recorded and raised after the run: the extractor
        # reads any failed call as "the model is down", and a prompt that
        # drifted would read as "extraction got worse".
        system = next((m["content"] for m in messages if m.get("role") == "system"), "")
        if _sha256(system) != self._prompt:
            self.stale = True
            return ""
        key = self.answer or _text_key(messages[-1]["content"])
        if key not in self._replies:
            self.unrecorded.append(str(messages[-1]["content"])[:80])
            return ""
        return self._replies[key]


def _replayed_extraction(
    turns: list[dict[str, Any]], llm_replies: dict[str, Any], *, mode: str = "current",
) -> dict[str, list[dict[str, Any]]]:
    """What the deep pass keeps from each turn, from the recorded replies,
    read by the product's own parser and sanitizer."""
    import asyncio
    from unittest import mock

    from kazma_core.memory.belief_extractor import _llm_extract, _sanitize_belief

    missing = [t["id"] for t in turns if _reply_key(t["user"], llm_replies, mode) not in llm_replies["replies"]]
    if missing:
        # Checked here: the extractor reads any failed call as "the model is
        # down", which would score a stale recording as an outage.
        raise KeyError(f"no recorded reply for {missing}: {RECORD_AGAIN}")
    expected = llm_replies["prompt_sha256"] if mode == "current" else _extraction_prompt_sha256()
    client = _ReplayClient(llm_replies["replies"], expected)
    registry = type("_Registry", (), {"get_client": lambda self: client})()

    async def ask_all() -> dict[str, list[dict[str, Any]]]:
        got = {}
        for t in turns:
            client.answer = None if mode == "current" else _reply_key(t["user"], llm_replies, mode)
            beliefs, _down = await _llm_extract(t["user"], "")
            got[t["id"]] = [b for b in (_sanitize_belief(x) for x in beliefs or []) if b]
        return got

    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry):
        extracted = asyncio.run(ask_all())
    if client.stale:
        raise StaleRecordingError(f"the extractor sent another system prompt than the recording answered: {RECORD_AGAIN}")
    if client.unrecorded:
        raise StaleRecordingError(f"no recorded reply for the message the extractor sent "
                                  f"({client.unrecorded[0]!r}): {RECORD_AGAIN}")
    return extracted


def run_extraction_benchmark(
    dataset: dict[str, Any] | None = None, *, llm_replies: dict[str, Any] | None = None,
    pending: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score the heuristic pass, and the model's pass when *llm_replies* (the
    recorded file) is given. *pending* is the lock's declared prompt change
    (``extraction.pending_recording``), if any: with it, a recording of the
    prompt before the change is replayed as a drift check (:func:`_check_recording`),
    and the report says so (``llm_replay``)."""
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic

    data = dataset or json.loads(EXTRACTION_PATH.read_text(encoding="utf-8"))
    turns = data["turns"]
    report: dict[str, Any] = {
        "version": data["version"],
        "heuristic": _score_extraction(turns, {t["id"]: extract_beliefs_heuristic(t["user"]) for t in turns}),
    }
    if not llm_replies:
        report["llm"] = {"recorded": False}
        return report
    mode = _check_recording(llm_replies, pending)
    report["llm"] = _score_extraction(turns, _replayed_extraction(turns, llm_replies, mode=mode))
    report["llm_recording"] = _recording_identity(llm_replies)
    report["llm_replay"] = mode
    return report


#: The extraction scores the lock holds, per pass and language.
EXTRACTION_SCORES = ("found", "named", "right", "quiet")


def extraction_rests_on(report: dict[str, Any]) -> dict[str, dict[str, dict[str, int]]]:
    """What each extraction row rests on, per pass and language: the turns,
    the gold facts behind ``found``, the turns with nothing to keep behind
    ``quiet``, and for the model's pass the askings (turns times the
    recording's askings a turn). The lock writes it; the benchmarks page
    prints it beside the scores (benchmark audit R12, the owner's decision 6).
    """
    askings = int((report.get("llm_recording") or {}).get("askings") or 0)
    out: dict[str, dict[str, dict[str, int]]] = {}
    for pass_name in ("heuristic", "llm"):
        part = report.get(pass_name) or {}
        for lang, row in sorted(part.items()):
            if not isinstance(row, dict):
                continue
            entry = {"turns": int(row["turns"]), "gold": int(row["gold"]), "quiet_turns": int(row["quiet_turns"])}
            if pass_name == "llm":
                entry["askings"] = int(row["turns"]) * askings
            out.setdefault(pass_name, {})[lang] = entry
    return out


def extraction_scores(report: dict[str, Any]) -> dict[str, float]:
    """The extraction scores the lock holds: per pass and language, ``found``,
    ``named``, ``right`` and ``quiet``."""
    out = {}
    for pass_name in ("heuristic", "llm"):
        part = report.get(pass_name) or {}
        for lang, row in part.items():
            if isinstance(row, dict):
                for k in EXTRACTION_SCORES:
                    out[f"{pass_name}.{lang}.{k}"] = float(row[k])
    return dict(sorted(out.items()))


# ── Write scenarios (2026-11: the memory framework audit, F1 and F8) ─────────
#
# A few turns of one chat through the write path, in the order a live turn
# takes it: the turn's own quick pass (``extract_and_apply_beliefs_sync``,
# as ``consolidator._v2_extract_sync`` runs it), then the model's deep pass as
# the memory worker runs it (``extract_beliefs_for_turn`` with the names in
# use, applied by ``_apply_beliefs_to_v2`` at the turn's time). Then the facts
# that must be current, and must not, are read from the private database.
# The model's reply to a turn is the scenario's: the model's recorded reply to
# an extraction turn of the same text, or written in the model's shape
# (``benchmark_data/writes.json``, built by ``scripts/memory_bench.py
# build``). So a scenario measures Kazma's rules around the model, never the
# model. Scores: per category and language, the share of scenarios that pass.

#: The sources that are the user's own words (``memory.provenance``).
_USER_SOURCES = frozenset({"user_explicit", "user_stated"})


class _ScriptedClient:
    """The deep pass's model: answers with the reply the scenario gives the
    turn being read, whatever it is asked."""

    def __init__(self) -> None:
        self.reply = ""

    async def chat(self, messages: list[dict[str, Any]], **_kw: Any) -> str:
        return self.reply


def _turn_reply(turn: dict[str, Any], recorded: dict[str, str], llm_replies: dict[str, Any] | None = None,
                mode: str = "current") -> str:
    if "recorded" in turn:
        key = _reply_key(turn["user"], llm_replies or {}, mode)
        if key not in recorded:
            raise KeyError(f"no recorded reply for {turn['recorded']}: {RECORD_AGAIN}")
        return recorded[key]
    return json.dumps({"beliefs": turn["written"]}, ensure_ascii=False)


def _expected_holds(expected: dict[str, Any], row: dict[str, Any]) -> bool:
    """*row*, a current fact, is the one *expected* names: its subject, its
    value (folded, or letter for letter with ``exact``), and the user's own
    words as its source when ``trust`` is ``"user"``."""
    if _slug(row["subject"]) != _slug(expected["subject"]):
        return False
    if expected.get("exact"):
        if row["object"] != expected["object"]:
            return False
    elif not _holds(_folded(expected["object"]), _folded(row["object"])):
        return False
    return expected.get("trust") != "user" or row["extraction_method"] in _USER_SOURCES


def _run_scenario(scenario: dict[str, Any], client: _ScriptedClient, recorded: dict[str, str],
                  llm_replies: dict[str, Any] | None = None, mode: str = "current") -> dict[str, Any]:
    import asyncio

    from kazma_core.memory.belief_extractor import (
        _apply_beliefs_to_v2,
        extract_and_apply_beliefs_sync,
        extract_beliefs_for_turn,
    )
    from kazma_core.memory.entity_resolution import entity_vocabulary
    from kazma_core.memory.predicates import predicate_vocabulary
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    paths = []
    for name in ("primary", "ops"):
        fd, path = tempfile.mkstemp(prefix=f"kazma-writes-{name}-", suffix=".db")
        os.close(fd)
        paths.append(path)
    # Autocommit, as the live writers open them: mutate_belief owns its
    # transactions.
    primary = sqlite3.connect(paths[0], isolation_level=None)
    primary.row_factory = sqlite3.Row
    ops = sqlite3.connect(paths[1], isolation_level=None)
    try:
        ensure_primary_schema(primary)
        ensure_ops_schema(ops)
        session = f"writes-{scenario['id']}"
        for number, turn in enumerate(scenario["turns"], 1):
            if "answer" in turn:
                # The user answers the newest change waiting for them, on the
                # Memory page or with /memory confirm (memory/changes.py).
                from kazma_core.memory import changes

                waiting = [c for c in changes.list_changes(primary, tenant_id=BENCH_TENANT)
                           if c["kind"] == "pending"]
                if waiting and turn["answer"] == "confirm":
                    changes.confirm(primary, waiting[0]["id"], tenant_id=BENCH_TENANT, actor="benchmark",
                                    private=True)
                elif waiting:
                    changes.dismiss(primary, waiting[0]["id"], tenant_id=BENCH_TENANT, actor="benchmark")
                continue
            at = BASE_TIME + number * 86400
            extract_and_apply_beliefs_sync(primary, ops, turn["user"], session_id=session, turn=number,
                                           tenant_id=BENCH_TENANT, now=at, private=True)
            client.reply = _turn_reply(turn, recorded, llm_replies, mode)
            raw, stats = asyncio.run(extract_beliefs_for_turn(
                turn["user"], "", vocabulary=predicate_vocabulary(primary, tenant_id=BENCH_TENANT),
                entities=entity_vocabulary(primary, tenant_id=BENCH_TENANT)))
            if raw:
                _apply_beliefs_to_v2(raw, primary, ops, stats=stats, session_id=session, turn=number,
                                     tenant_id=BENCH_TENANT, now=at, private=True, user_text=turn["user"])
        rows = [dict(r) for r in primary.execute(
            "SELECT subject, predicate, object, extraction_method FROM beliefs WHERE tenant_id = ? "
            "AND valid_until IS NULL AND invalidated_at IS NULL ORDER BY valid_from", (BENCH_TENANT,))]
        waiting = [dict(r) for r in primary.execute(
            "SELECT subject, new_value AS object FROM memory_changes WHERE tenant_id = ? AND kind = 'pending' "
            "AND state = 'open' ORDER BY created_at", (BENCH_TENANT,))]
    finally:
        primary.close()
        ops.close()
        for path in paths:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except OSError:
                    pass
    problems = []
    for expected in scenario["current"]:
        if not any(_expected_holds(expected, r) for r in rows):
            whose = " in the user's own words" if expected.get("trust") == "user" else ""
            problems.append(f"not current{whose}: {expected['subject']} {expected['object']}")
    for unwanted in scenario["not_current"]:
        problems += [f"still current: {r['subject']} {r['predicate']} {r['object']}"
                     for r in rows if _expected_holds(unwanted, r)]
    # A correction only the model read waits for the user (memory/changes.py):
    # exactly the scenario's ``pending`` values, and no other.
    expected_waiting = scenario.get("pending") or []
    for expected in expected_waiting:
        if not any(_expected_holds(expected, {**w, "extraction_method": ""}) for w in waiting):
            problems.append(f"not waiting for the user: {expected['subject']} {expected['object']}")
    problems += [f"waiting for the user, unexpected: {w['subject']} {w['object']}" for w in waiting
                 if not any(_expected_holds(e, {**w, "extraction_method": ""}) for e in expected_waiting)]
    return {"id": scenario["id"], "category": scenario["category"], "lang": scenario["lang"],
            "passed": not problems, "problems": problems,
            "current": [f"{r['subject']} {r['predicate']} {r['object']} [{r['extraction_method']}]" for r in rows],
            "waiting": [f"{w['subject']} {w['object']}" for w in waiting]}


def run_write_scenarios(
    dataset: dict[str, Any] | None = None, *, llm_replies: dict[str, Any] | None = None,
    pending: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Every write scenario through the write path; what passed, by category
    and language. *llm_replies* is the recording the ``recorded`` turns
    replay (the extraction benchmark's), and *pending* the lock's declared
    prompt change, as :func:`run_extraction_benchmark` takes them."""
    from unittest import mock

    data = dataset or json.loads(WRITES_PATH.read_text(encoding="utf-8"))
    mode = _check_recording(llm_replies, pending) if llm_replies else "current"
    recorded = (llm_replies or {}).get("replies") or {}
    client = _ScriptedClient()
    registry = type("_Registry", (), {"get_client": lambda self: client})()
    # Facts are judged by their rows: no vector is needed, and none is made.
    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry), \
            mock.patch("kazma_core.memory.embedder.get_embedder", lambda: None):
        results = [_run_scenario(s, client, recorded, llm_replies, mode) for s in data["scenarios"]]
    groups: dict[str, dict[str, int]] = {}
    for r in results:
        for key in (f"{r['category']}.{r['lang']}", "overall"):
            row = groups.setdefault(key, {"scenarios": 0, "passed": 0})
            row["scenarios"] += 1
            row["passed"] += int(r["passed"])
    report: dict[str, Any] = {
        "version": data["version"],
        "groups": {k: {**v, "share": round(v["passed"] / v["scenarios"], 4)} for k, v in sorted(groups.items())},
        "failing": [r["id"] for r in results if not r["passed"]],
        "results": results,
    }
    if llm_replies:
        report["llm_recording"] = _recording_identity(llm_replies)
        report["llm_replay"] = mode
    return report


def write_scores(report: dict[str, Any]) -> dict[str, float]:
    """The share of write scenarios that pass, per category and language, and
    overall: the scores the lock holds."""
    return {f"{k}.passed": float(v["share"]) for k, v in sorted(report["groups"].items())}


async def record_llm_extraction(dataset: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask the configured model the extraction prompt for every turn and
    return its raw replies (``scripts/memory_extraction_record.py``;
    costs one call a turn)."""
    from kazma_core.model_registry import get_model_registry

    data = dataset or json.loads(EXTRACTION_PATH.read_text(encoding="utf-8"))
    registry = get_model_registry()
    client = registry.get_client()
    replies: dict[str, str] = {}
    from kazma_core.memory.belief_extractor import _EXTRACT_SYSTEM, MESSAGE_SHAPE

    for t in data["turns"]:
        blob = _llm_blob(t["user"])
        raw = await client.chat([{"role": "system", "content": _EXTRACT_SYSTEM}, {"role": "user", "content": blob}])
        replies[_text_key(blob)] = raw.content if hasattr(raw, "content") else str(raw or "")
    # The provider client keeps its model on its config (``LLMProvider.config``).
    model = (getattr(getattr(client, "config", None), "model", None) or getattr(client, "model", None)
             or getattr(registry, "active_model", None) or "unknown")
    import datetime as _dt

    return {"model": str(model), "provider": str(getattr(registry, "_active_provider", "") or "unknown"),
            "recorded_on": _dt.date.today().isoformat(), "askings": 1,
            "prompt_sha256": _sha256(_EXTRACT_SYSTEM), "message_shape": MESSAGE_SHAPE,
            "extraction_version": data["version"],
            "replies": replies}


# ── Several members in one shared space (Spaces S1-11; the R3 study) ──────
#
# ``benchmark_data/speakers.json``: three members of one shared space, their
# turns and facts, and questions asked by each. Seeded the product's way --
# each turn names its author (``said_by``), each fact goes where
# ``speakers.Room.filed`` files it with the label ``Room.label`` gives it --
# and every question asked through the real ``recall`` as its asker. Scored
# apart from dataset v5, with its own ratchet in ``thresholds.json``
# (``speakers``). The study's vectors are replayed
# (``tests/fixtures/memory_bench/speakers_vectors.npz``).

SPEAKERS_PATH = Path(__file__).with_name("benchmark_data") / "speakers.json"
SPEAKERS_TENANT = "bench_shared"
#: The scores the speakers ratchet holds, per slice (``overall``, each
#: ``lang:<x>`` and ``category:<x>``).
SPEAKER_SCORES = ("hit_rate", "fact_hit_rate", "mrr", "precision")


def load_speakers(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    return json.loads(Path(path or SPEAKERS_PATH).read_text(encoding="utf-8"))


def speakers_room(cases: dict[str, Any]) -> Any:
    """The case set's shared space, as ``speakers.room`` would read it."""
    from types import MappingProxyType

    from kazma_core.memory.speakers import Member, Room

    members = {m["account"]: Member(account=m["account"], name=m["name"], called=tuple(m.get("aliases") or ()))
               for m in cases["members"].values()}
    return Room(space=SPEAKERS_TENANT, shared=True, members=MappingProxyType(members))


def _seed_speakers(conn: sqlite3.Connection, cases: dict[str, Any], people: Any) -> dict[str, Any]:
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import episode_row
    from kazma_core.memory.embedder import encode_text_to_blob

    members = cases["members"]
    refs: dict[str, str] = {}
    for session in cases["sessions"]:
        for i, turn in enumerate(session["turns"], 1):
            row = episode_row(
                session_id=f"sp-{session['id']}", turn_number=i, user_text=turn["user"],
                assistant_text=turn["assistant"], tenant_id=SPEAKERS_TENANT, source="benchmark",
                created_at=BASE_TIME + session["day"] * 86400 + i * 60,
                metadata={"said_by": members[turn["by"]]["account"]},
            )
            conn.execute(
                "INSERT OR IGNORE INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
                "assistant_text, summary_text, tier, structural_importance, created_at, metadata_json, "
                "embedding_model_version, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["id"], row["tenant_id"], row["session_id"], row["turn_number"], row["user_text"],
                 row["assistant_text"], row["summary_text"], row["tier"], row["importance"],
                 row["created_at"], json.dumps(row["meta"], ensure_ascii=False),
                 row["embedding_model_version"], encode_text_to_blob(row["embed_text"])),
            )
            refs[f"{session['id']}#{i}"] = row["id"]
    conn.commit()
    for fact in cases["facts"]:
        speaker = members[fact["by"]]["account"]
        about = fact["about"]
        if about.startswith("@"):
            # "I ..." when the member speaks of themself; else the name it
            # was written with ("Omar", "ليلى").
            said = "user" if about[1:] == fact["by"] else (fact.get("written_as") or members[about[1:]]["name"])
        else:
            said = about
        subject = people.filed(said, speaker)
        result = mutate_belief(
            conn, subject, fact["predicate"], fact["object"],
            predicate_type=fact["predicate_type"], confidence=0.9, importance=4,
            extraction_method=people.label(fact["method"], subject, speaker), tenant_id=SPEAKERS_TENANT,
            source_session=f"sp-{fact['session']}", source_turn=fact["turn"],
            now=BASE_TIME + fact["day"] * 86400 + fact["turn"] * 60 + 30, private=True,
            said_by=people.said_by(speaker),
        )
        if result.get("belief_id") and result.get("action") not in ("noop", "blocked", "rejected"):
            refs[fact["id"]] = str(result["belief_id"])
    current = {str(r[0]) for r in conn.execute(
        "SELECT id FROM beliefs WHERE tenant_id = ? AND valid_until IS NULL AND invalidated_at IS NULL",
        (SPEAKERS_TENANT,))}
    lost = [f["id"] for f in cases["facts"]
            if f["id"] in refs and refs[f["id"]] not in current and not f.get("later") and not f.get("guess")]
    return {"refs": refs, "lost": lost}


def _speaker_slices(results: list[dict[str, Any]]) -> dict[str, Any]:
    def part(rows: list[dict[str, Any]]) -> dict[str, Any]:
        answerable = [r for r in rows if "rr" in r]
        with_fact = [r for r in rows if "fact_hit" in r]
        return {
            "n": len(rows),
            "hit_rate": round(sum(bool(r["hit"]) for r in rows) / len(rows), 4) if rows else None,
            "fact_hit_rate": round(sum(r["fact_hit"] for r in with_fact) / len(with_fact), 4) if with_fact else None,
            "mrr": round(sum(r["rr"] for r in answerable) / len(answerable), 4) if answerable else None,
            "precision": round(sum(r["precision"] for r in answerable) / len(answerable), 4)
            if answerable else None,
            "misattributed": sum(r["misattributed"] for r in rows),
        }

    out: dict[str, Any] = {"overall": part(results)}
    for key in ("lang", "category", "first_person", "dialect"):
        for value in sorted({str(r[key]) for r in results if key in r}):
            out[f"{key}:{value}"] = part([r for r in results if str(r.get(key)) == value])
    return out


def run_speaker_benchmark(cases: dict[str, Any] | None = None, *, questions: list[dict[str, Any]] | None = None,
                          limit: int = 5, keep_results: bool = False) -> dict[str, Any]:
    """Seed a private database with the case set, ask *questions* (the case
    set's own by default) as their askers, return the scores by slice. A
    line that shows another member's fact or turn as the asker's (``user ``
    / ``User:``) counts as ``misattributed``."""
    from kazma_core.memory import speakers
    from kazma_core.memory.recall import recall
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    cases = cases or load_speakers()
    asked = questions if questions is not None else cases["questions"]
    people = speakers_room(cases)
    members = cases["members"]
    facts = {f["id"]: f for f in cases["facts"]}
    fd, path = tempfile.mkstemp(prefix="kazma-speakers-", suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        with speakers.private_room(people):
            ensure_primary_schema(conn)
            seeded = _seed_speakers(conn, cases, people)
            refs = seeded["refs"]
            owner_of = {refs[f["id"]]: f["about"][1:] for f in cases["facts"]
                        if f["id"] in refs and f["about"].startswith("@")}
            author_of = {refs[f"{s['id']}#{i}"]: t["by"]
                         for s in cases["sessions"] for i, t in enumerate(s["turns"], 1)}
            results: list[dict[str, Any]] = []
            for q in asked:
                asker = q["by"]
                res = recall(q["text"], conn=conn, tenant_id=SPEAKERS_TENANT, limit=limit, local_only=True,
                             asker=members[asker]["account"])
                beliefs, episodes = list(res.beliefs[:limit]), list(res.episodes[:limit])
                injected = [h.id for h in beliefs] + [h.id for h in episodes]
                wrong = sum(1 for h in beliefs
                            if owner_of.get(h.id, asker) != asker and h.content.startswith("user "))
                wrong += sum(1 for h in episodes
                             if author_of.get(h.id, asker) != asker
                             and str((h.metadata or {}).get("display") or "").startswith("User: "))
                out: dict[str, Any] = {"id": q["id"], "lang": q.get("lang"), "category": q.get("category"),
                                       "by": asker, "misattributed": wrong}
                for key in ("first_person", "dialect"):
                    if key in q:
                        out[key] = q[key]
                pieces = [[refs[a] for a in piece.split("|") if a in refs] for piece in q.get("gold") or []]
                answers = {a for piece in pieces for a in piece}
                gold_facts = {refs[a] for piece in q.get("gold") or [] for a in piece.split("|")
                              if a in facts and a in refs}
                rank = {item: i for i, item in reversed(list(enumerate(injected, 1)))}
                if not q.get("gold"):
                    out["hit"] = wrong == 0
                else:
                    ranks = [rank[a] for a in answers if a in rank]
                    out["hit"] = all(any(a in rank for a in piece) for piece in pieces)
                    out["rr"] = 1.0 / min(ranks) if ranks else 0.0
                    out["precision"] = sum(1 for i in injected if i in answers) / len(injected) if injected else 0.0
                    if any(a in facts for piece in q["gold"] for a in piece.split("|")):
                        out["fact_hit"] = bool(gold_facts & set(injected))
                results.append(out)
        report = {**_speaker_slices(results), "lost": seeded["lost"], "version": cases.get("version")}
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


def speaker_scores(report: dict[str, Any]) -> dict[str, float]:
    """The gated scores of a speakers run: ``<slice>.<score>``, and the
    number of misattributed lines overall (held at its locked value, 0)."""
    out: dict[str, float] = {}
    for slice_name, part in report.items():
        if not isinstance(part, dict) or "n" not in part:
            continue
        for score in SPEAKER_SCORES:
            if part.get(score) is not None:
                out[f"{slice_name}.{score}"] = float(part[score])
    out["overall.misattributed"] = float(report["overall"]["misattributed"])
    return out


class SpeakerReplay:
    """The recorded vectors of dataset v5 and of the speakers case set, as
    one embedder: a text is answered from the first file that holds it."""

    def __init__(self, *paths: str | os.PathLike[str]) -> None:
        self._replays = [ReplayEmbedder(p) for p in paths]
        self.model = self._replays[0].model
        self.dim = self._replays[0].dim

    def encode(self, text: str) -> list[float]:
        for replay in self._replays:
            try:
                return replay.encode(text)
            except KeyError:
                continue
        raise KeyError(f"no recorded vector for {text[:60]!r} in the speakers or v5 recordings")
