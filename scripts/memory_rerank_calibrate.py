#!/usr/bin/env python3
"""Calibrate the reranker's scores (the memory plan's W2): a logistic fit per
kind of memory, fitted on the retrieval benchmark's first set only.

    python scripts/memory_rerank_calibrate.py [--reference] [--out FILE]

The cross-encoder (``memory/reranker.py``) answers a logit with no fixed
meaning. Recall decides on a probability that a memory answers the
question (``reranker.calibrated``: sigmoid(c0 + c_evidence * evidence +
c_score * score) per model and kind: the model's score and recall's own
evidence together, measured better than either alone), so a floor of 0.3
says the same thing for a turn, a fact and a weekly summary. This script
asks the benchmark's store the first set's questions with the reranker on
and every candidate it reads collected (no floor), each labelled by whether
it answers (the benchmark's gold, a summary standing for an answer
counting), fits (c0, c_evidence, c_score) per kind by logistic regression,
and prints the fit's reliability and ranking (AUC) on the first set and on
the second set, which it never fitted on. It writes nothing but ``--out``;
the values go into ``reranker.CALIBRATION`` by hand, with the run that made
them.

The recorded scores are replayed (``memory_bench.py rerank-scores``); no
model is loaded.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sys
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "memory_bench"
VECTORS = FIXTURES / "vectors.npz"
PASSAGE_VECTORS = FIXTURES / "passage_vectors.npz"
RERANK_SCORES = FIXTURES / "rerank_scores.npz"
RERANK_REFERENCE = FIXTURES / "rerank_reference.npz"
KINDS = ("episode", "belief", "summary")
#: Reliability bins of the calibrated probability.
BINS = (0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0001)


def collect(questions: list[dict[str, Any]], scores: Path) -> list[tuple[str, float, float, bool]]:
    """(kind, evidence, score, answers) of every candidate the reranker read
    for *questions* in the benchmark's store, its floors at zero."""
    from kazma_core.memory import benchmark, passages, recall, reranker
    from kazma_core.memory.question_shape import standalone_query

    replay = benchmark.SpeakerReplay(VECTORS, PASSAGE_VECTORS)
    scorer = benchmark.ReplayReranker(scores)
    rows: list[tuple[str, float, float, bool]] = []
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch("kazma_core.memory.embedder.get_embedder", lambda: replay))
        stack.enter_context(mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model))
        stack.enter_context(mock.patch("kazma_core.memory.reranker.get_reranker", lambda: scorer))
        stack.enter_context(mock.patch("kazma_core.memory.recall._memory_zone", lambda: UTC))
        stack.enter_context(mock.patch.dict(reranker.CALIBRATION, {}, clear=True))
        for name in ("_RERANK_EPISODE_FLOOR", "_RERANK_BELIEF_FLOOR", "_RERANK_SUMMARY_FLOOR"):
            stack.enter_context(mock.patch.object(recall, name, 0.0))
        stack.enter_context(passages.used(True))
        stack.enter_context(reranker.used(True))
        data = benchmark.load_dataset()
        conn, refs, covers = stack.enter_context(benchmark._seeded_store(data))
        for q in questions:
            query = standalone_query(q["text"], q.get("context"))
            asked_on = benchmark.BASE_TIME + float(q.get("now_day", benchmark._ASKED_ON_DAY)) * 86400
            answers = {refs.get(a, a) for piece in q["gold"] for a in piece.split("|")}
            with recall.trace_evidence() as trace:
                recall.recall(query, conn=conn, tenant_id=benchmark.BENCH_TENANT, limit=5, local_only=True,
                              now=asked_on)
            for row in trace:
                if row.get("scale") != "relevance":
                    continue
                for mid, score in row["scores"].items():
                    good = mid in answers or bool(covers.get(mid, set()) & answers)
                    rows.append((row["kind"], float(row["today"][mid]), float(score), good))
    return rows


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))


def fit(points: list[tuple[float, float, bool]]) -> tuple[float, float, float]:
    """(c0, c_evidence, c_score) maximising the likelihood of
    sigmoid(c0 + c_evidence * evidence + c_score * score), by Newton's
    method with a small ridge (Platt's targets: (n+ + 1)/(n+ + 2) and
    1/(n- + 2), so a kind with few answers is not fitted to certainty)."""
    pos = sum(1 for *_x, y in points if y)
    neg = len(points) - pos
    hi, lo = (pos + 1) / (pos + 2), 1 / (neg + 2)
    w = [0.0, 0.0, 0.0]
    for _ in range(200):
        g = [0.0] * 3
        h = [[1e-6 if i == j else 0.0 for j in range(3)] for i in range(3)]
        for ev, score, y in points:
            f = (1.0, ev, score)
            p = _sigmoid(sum(a * b for a, b in zip(w, f, strict=True)))
            t = hi if y else lo
            for i in range(3):
                g[i] += (p - t) * f[i]
                for j in range(3):
                    h[i][j] += p * (1 - p) * f[i] * f[j]
        step = _solve(h, g)
        w = [a - b for a, b in zip(w, step, strict=True)]
        if max(abs(x) for x in step) < 1e-9:
            break
    return round(w[0], 4), round(w[1], 4), round(w[2], 4)


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """a x = b for a small system (Gauss-Jordan with partial pivoting)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        pivot = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[pivot] = m[pivot], m[c]
        for r in range(n):
            if r != c and m[c][c]:
                factor = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= factor * m[c][k]
    return [m[i][n] / m[i][i] if m[i][i] else 0.0 for i in range(n)]


def _prob(c: tuple[float, float, float], ev: float, score: float) -> float:
    return _sigmoid(c[0] + c[1] * ev + c[2] * score)


def auc(points: list[tuple[float, bool]]) -> float | None:
    """The chance an answer ranks above a non-answer (ties count half)."""
    pos = [x for x, y in points if y]
    neg = [x for x, y in points if not y]
    if not pos or not neg:
        return None
    ordered = sorted(points)
    rank_sum, i = 0.0, 0
    while i < len(ordered):
        j = i
        while j < len(ordered) and ordered[j][0] == ordered[i][0]:
            j += 1
        rank_sum += sum((i + 1 + j) / 2 for k in range(i, j) if ordered[k][1])
        i = j
    return round((rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)), 4)


def reliability(points: list[tuple[float, float, bool]], c: tuple[float, float, float]) -> list[dict[str, Any]]:
    out = []
    for lo, hi in zip(BINS, BINS[1:], strict=False):
        inside = [(ev, s, y) for ev, s, y in points if lo <= _prob(c, ev, s) < hi]
        if inside:
            out.append({"bin": [lo, round(min(hi, 1.0), 2)], "n": len(inside),
                        "mean_p": round(sum(_prob(c, ev, s) for ev, s, _y in inside) / len(inside), 3),
                        "answers": round(sum(y for *_r, y in inside) / len(inside), 3)})
    return out


def _measures(points: list[tuple[float, float, bool]], c: tuple[float, float, float]) -> dict[str, Any]:
    return {"pairs": len(points), "answers": sum(y for *_r, y in points),
            "auc": {"evidence": auc([(ev, y) for ev, _s, y in points]),
                    "score": auc([(s, y) for _ev, s, y in points]),
                    "calibrated": auc([(_prob(c, ev, s), y) for ev, s, y in points])},
            "reliability": reliability(points, c)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--reference", action="store_true", help="the quality reference's recorded scores")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from kazma_core.memory import benchmark, reranker

    data = benchmark.load_dataset()
    scores = RERANK_REFERENCE if args.reference else RERANK_SCORES
    first = collect(list(data["questions"]), scores)
    second = collect(list(data.get("second") or []), scores)
    model = reranker.REFERENCE_MODEL if args.reference else reranker.MODEL
    report: dict[str, Any] = {"model": model, "fitted_on": "first set", "kinds": {}}
    for kind in KINDS:
        pts = [(ev, s, y) for k, ev, s, y in first if k == kind]
        if not pts:
            continue
        c = fit(pts)
        held = [(ev, s, y) for k, ev, s, y in second if k == kind]
        report["kinds"][kind] = {"calibration": list(c), "first_set": _measures(pts, c),
                                 "second_set": _measures(held, c)}
    print(json.dumps(report, indent=1))
    if args.out:
        args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
