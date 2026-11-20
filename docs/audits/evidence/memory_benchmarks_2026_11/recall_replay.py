"""Audit evidence (docs/audits/AUDIT_MEMORY_BENCHMARKS_2026-11.md, section 3.5
to 3.7): can the recall benchmark's scores move without a change to recall's
code? Recorded vectors only: no model is loaded, nothing is called.

It replays ``kazma_core.memory.benchmark.run_benchmark`` and compares the 78
gated scores, and every question's result, with the first replay:

* a second replay, the questions in another order, the held-out questions
  asked first, and the NumPy search path instead of sqlite-vec;
* the recorded vectors moved inside their own rounding. The recording keeps
  one byte a number (int8), so the model's real vector is anywhere within
  half a step of the recorded one: 30 random draws inside that band, and 8
  each at a fiftieth and a thousandth of a step (the size of a float
  difference between two machines);
* how many questions each gated score rests on, and how much of the pool of
  unrelated chat is repeats;
* what decides the one question whose hit flips.

    python recall_replay.py

About ten minutes at below-normal priority. Writes ``recall_replay.json``
beside this file.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import random
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "memory_bench"
VECTORS = FIXTURES / "vectors.npz"
SLACK = 0.0001


def below_normal() -> None:
    """The live install shares this machine: leave it the CPU."""
    import psutil

    if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    else:
        os.nice(5)


def main() -> int:
    below_normal()
    import numpy as np
    from kazma_core.memory import benchmark as bench

    replay = bench.ReplayEmbedder(VECTORS)
    data = bench.load_dataset()

    class Within(bench.ReplayEmbedder):
        """The recorded vectors, each moved at random by at most *steps* of
        its own rounding step (the same move for the same text)."""

        def __init__(self, seed: int, steps: float) -> None:
            super().__init__(VECTORS)
            self._seed, self._steps = seed, steps

        def encode(self, text: str) -> list[float]:
            key = bench._text_key(text)
            i = self._index[key]
            digest = hashlib.sha256(f"{self._seed}:{key}".encode()).digest()
            noise = np.random.default_rng(int.from_bytes(digest[:8], "big")).uniform(
                -self._steps, self._steps, self._vecs.shape[1])
            return ((self._vecs[i].astype("float32") + noise.astype("float32")) * self._scale[i]).tolist()

    def patched(embedder: Any) -> Any:
        return (mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder),
                mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model))

    def run(embedder: Any, dataset: dict[str, Any] | None = None) -> dict[str, Any]:
        a, b = patched(embedder)
        with a, b:
            return bench.run_benchmark(dataset, keep_results=True)

    def per_question(report: dict[str, Any]) -> dict[str, tuple[Any, ...]]:
        return {r["id"]: (bool(r["hit"]), round(r.get("rr", -1.0), 4), round(r.get("precision", -1.0), 4),
                          r.get("order"), r["injected"])
                for r in report["results"] + report["heldout"]["results"]}

    started = time.perf_counter()
    base = run(replay)
    seconds = round(time.perf_counter() - started, 1)
    base_scores = bench.gated_scores(base)
    base_q = per_question(base)

    def against_base(report: dict[str, Any]) -> dict[str, Any]:
        scores = bench.gated_scores(report)
        moved = {k: [base_scores[k], scores.get(k)] for k in base_scores
                 if abs(base_scores[k] - scores.get(k, -9.0)) > SLACK}
        q = per_question(report)
        return {"gated_scores_moved": len(moved), "moved": moved,
                "questions_changed": sorted(x for x in base_q if base_q[x] != q.get(x)),
                "hit_flips": sorted(x for x in base_q if base_q[x][0] != q.get(x, (None,))[0])}

    locked = json.loads((FIXTURES / "thresholds.json").read_text(encoding="utf-8"))["scores"]
    out: dict[str, Any] = {
        "seconds_one_replay": seconds,
        "gated_scores": len(base_scores),
        "replay_differs_from_the_lock": {k: [base_scores.get(k), v] for k, v in locked.items()
                                         if abs(base_scores.get(k, -9.0) - v) > SLACK},
        "overall": {"tuning": base["overall"], "heldout": base["heldout"]["overall"]},
    }

    # How many questions each gated score rests on.
    sizes = []
    for prefix, part in (("", base), ("heldout.", base["heldout"])):
        for name, row in part["categories"].items():
            keys = [k for k in ("hit_rate", "mrr", "precision", "order") if k in row]
            sizes.append({"category": prefix + name, "questions": row["n"], "gated_scores": len(keys)})
    sizes.sort(key=lambda r: (r["questions"], r["category"]))
    out["questions_behind_each_score"] = {
        "categories": sizes,
        "gated_scores_on_one_question": sum(r["gated_scores"] for r in sizes if r["questions"] == 1),
        "gated_scores_on_four_or_fewer": sum(r["gated_scores"] for r in sizes if r["questions"] <= 4),
        "gated_scores_in_categories": sum(r["gated_scores"] for r in sizes),
    }

    # The same thing twice, in another order, on the other search path.
    out["second_replay"] = against_base(run(replay))
    rng = random.Random(7)
    shuffled = dict(data)
    shuffled["questions"] = rng.sample(data["questions"], len(data["questions"]))
    shuffled["heldout"] = rng.sample(data["heldout"], len(data["heldout"]))
    out["questions_in_another_order"] = against_base(run(replay, shuffled))
    swapped = dict(data)
    swapped["questions"], swapped["heldout"] = data["heldout"], data["questions"]
    first = run(replay, swapped)
    asked_first = {r["id"]: (bool(r["hit"]), round(r.get("rr", -1.0), 4), round(r.get("precision", -1.0), 4),
                             r.get("order"), r["injected"])
                   for r in first["results"] + first["heldout"]["results"]}
    out["heldout_asked_first"] = {"questions_changed": sorted(x for x in base_q if base_q[x] != asked_first.get(x))}

    import sqlite_vec

    def no_sqlite_vec(_conn: Any) -> None:
        raise RuntimeError("sqlite-vec switched off for this run")

    with mock.patch.object(sqlite_vec, "load", no_sqlite_vec):
        out["numpy_search_path"] = against_base(run(replay))

    # The recorded vectors moved inside their own rounding, and far less.
    out["recorded_vectors_moved"] = {}
    for label, steps, draws in (("inside_the_rounding_half_a_step", 0.5, 30),
                                ("a_fiftieth_of_a_step", 0.01, 8), ("a_thousandth_of_a_step", 0.001, 8)):
        moved_counts: list[int] = []
        score_counts: collections.Counter[str] = collections.Counter()
        question_counts: collections.Counter[str] = collections.Counter()
        flip_counts: collections.Counter[str] = collections.Counter()
        for seed in range(100, 100 + draws):
            d = against_base(run(Within(seed, steps)))
            moved_counts.append(d["gated_scores_moved"])
            score_counts.update(list(d["moved"]))
            question_counts.update(d["questions_changed"])
            flip_counts.update(d["hit_flips"])
        out["recorded_vectors_moved"][label] = {
            "draws": draws, "draws_with_every_score_unchanged": sum(1 for n in moved_counts if n == 0),
            "gated_scores_moved_per_draw": moved_counts,
            "scores_that_moved_in_n_draws": score_counts.most_common(),
            "questions_that_changed_in_n_draws": question_counts.most_common(),
            "hit_flips_in_n_draws": flip_counts.most_common()}
        print(label, out["recorded_vectors_moved"][label]["draws_with_every_score_unchanged"], "of", draws,
              "draws unchanged", flush=True)

    # The pool of unrelated chat.
    turn_texts = [(t["user"], t["assistant"]) for s in data["sessions"] for t in s["turns"]]
    out["pool"] = {
        "turns": len(turn_texts), "different_user_messages": len({u for u, _ in turn_texts}),
        "different_turns": len(set(turn_texts)), "recorded_vectors": int(replay._vecs.shape[0]),
        "facts": len(data["facts"]), "weekly_summaries": len(data["summaries"]),
    }

    # What decides the question whose hit flips.
    from kazma_core.memory.recall import recall
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    def look(embedder: Any, qid: str) -> dict[str, Any]:
        a, b = patched(embedder)
        fd, path = tempfile.mkstemp(prefix="kazma-audit-", suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            with a, b:
                ensure_primary_schema(conn)
                refs = bench._seed(conn, data)
                question = next(x for x in data["questions"] + data["heldout"] if x["id"] == qid)
                res = recall(question["text"], conn=conn, tenant_id=bench.BENCH_TENANT, limit=5, local_only=True)
            back = {v: k for k, v in refs.items()}

            def rows(hits: list[Any]) -> list[dict[str, Any]]:
                return [{"memory": back.get(h.id, h.id), "score": h.score,
                         **{k: h.metadata.get(k) for k in ("lift", "coverage", "evidence", "strength")}}
                        for h in hits]

            return {"question": question["text"], "category": question["category"], "answered_by": question["gold"],
                    "facts": rows(res.beliefs[:5]), "turns": rows(res.episodes[:5]), "summaries": rows(res.summaries)}
        finally:
            conn.close()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(path + suffix):
                    os.remove(path + suffix)

    out["the_question_whose_hit_flips"] = {}
    for qid, _n in out["recorded_vectors_moved"]["inside_the_rounding_half_a_step"]["hit_flips_in_n_draws"]:
        flip_seed = next((seed for seed in range(100, 130)
                          if per_question(run(Within(seed, 0.5)))[qid][0] != base_q[qid][0]), None)
        out["the_question_whose_hit_flips"][qid] = {
            "as_recorded": look(replay, qid),
            "inside_the_rounding": look(Within(flip_seed, 0.5), qid) if flip_seed is not None else None}

    path = HERE / "recall_replay.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in out.items() if k not in ("recorded_vectors_moved",)},
                     ensure_ascii=True, indent=1)[:6000])
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
