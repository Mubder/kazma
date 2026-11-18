"""Audit evidence (docs/audits/AUDIT_MEMORY_BENCHMARKS_2026-11.md, section
3.5): does the lock equal what the real embedder scores?

Loads bge-m3 from the local cache with the network off, at below-normal CPU
priority and four threads at most (the live install shares this machine), in
whatever data folder ``KAZMA_DATA_DIR`` names -- give it a private, empty one.
Runs the recall benchmark once on the real vectors and compares the gated
scores with the lock, every question with its replayed result, and every
real vector with its recording.

    KAZMA_DATA_DIR=<empty folder> KAZMA_USER_HOME=<empty folder> python recall_real_model.py

About two minutes. It calls no provider. Writes ``recall_real_model.json``
beside this file.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "memory_bench"
SLACK = 0.0001


def main() -> int:
    if not os.environ.get("KAZMA_DATA_DIR"):
        raise SystemExit("set KAZMA_DATA_DIR to a private, empty folder: this run must not read an install")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    import psutil

    if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    else:
        os.nice(5)

    import numpy as np
    import torch

    torch.set_num_threads(4)

    from kazma_core.memory import benchmark as bench
    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

    started = time.perf_counter()
    real = get_embedder()
    if real is None:
        raise SystemExit("no embedder here: not measured")
    model = get_embedding_model_name()
    recorder = bench.RecordingEmbedder(real)
    with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: recorder), \
            mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: model):
        with_real = bench.run_benchmark(keep_results=True)
    seconds = round(time.perf_counter() - started, 1)

    replay = bench.ReplayEmbedder(FIXTURES / "vectors.npz")
    with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: replay), \
            mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model):
        with_recorded = bench.run_benchmark(keep_results=True)

    def per_question(report: dict[str, Any]) -> dict[str, tuple[Any, ...]]:
        return {r["id"]: (bool(r["hit"]), round(r.get("rr", -1.0), 4), round(r.get("precision", -1.0), 4),
                          r.get("order"), r["injected"])
                for r in report["results"] + report["heldout"]["results"]}

    lock = json.loads((FIXTURES / "thresholds.json").read_text(encoding="utf-8"))["scores"]
    scores = bench.gated_scores(with_real)
    differ = {k: [lock[k], scores.get(k)] for k in lock if abs(lock[k] - scores.get(k, -9.0)) > SLACK}
    qa, qb = per_question(with_recorded), per_question(with_real)

    cosines = []
    for text, vec in recorder.seen.items():
        recorded = np.asarray(replay.encode(text), dtype=np.float64)
        v = np.asarray(vec, dtype=np.float64)
        cosines.append(float(v @ recorded / (np.linalg.norm(v) * np.linalg.norm(recorded))))
    texts = sorted(recorder.seen)[:200]
    real_m = np.asarray([recorder.seen[t] for t in texts], dtype=np.float64)
    rec_m = np.asarray([replay.encode(t) for t in texts], dtype=np.float64)
    real_m /= np.linalg.norm(real_m, axis=1, keepdims=True)
    rec_m /= np.linalg.norm(rec_m, axis=1, keepdims=True)
    pair_error = np.abs(real_m @ real_m.T - rec_m @ rec_m.T)

    out = {
        "model": model, "torch": torch.__version__, "seconds": seconds, "texts_embedded": len(recorder.seen),
        "gated_scores": len(scores), "gated_scores_that_differ_from_the_lock": len(differ),
        "differ_lock_then_real": differ,
        "questions_that_differ_recorded_then_real": {
            q: {"recorded": list(qa[q]), "real": list(qb[q])} for q in qa if qa[q] != qb[q]},
        "overall_real": {k: v for k, v in with_real["overall"].items() if not k.startswith("latency")},
        "heldout_overall_real": {k: v for k, v in with_real["heldout"]["overall"].items()
                                 if not k.startswith("latency")},
        "each_vector_against_its_recording": {"lowest_cosine": round(min(cosines), 6),
                                              "mean_cosine": round(sum(cosines) / len(cosines), 6)},
        "similarity_of_a_pair_real_against_recorded": {
            "largest_error": round(float(pair_error.max()), 6), "mean_error": round(float(pair_error.mean()), 6),
            "error_99_in_100": round(float(np.quantile(pair_error, 0.99)), 6)},
    }
    path = HERE / "recall_real_model.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(out, ensure_ascii=True, indent=1))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
