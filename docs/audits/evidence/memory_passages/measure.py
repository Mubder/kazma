"""W5 + W1b: Kazma's benchmark with and without passages, on one commit.

Run from a detached checkout (PYTHONPATH at it) holding the passages'
recorded vectors (``scripts/memory_bench.py passage-vectors``). Replays the
recorded vectors only: no model is loaded, nothing leaves the machine. For
each store the benchmark builds -- the store (both question sets), four
times its unrelated chat, a small store per question, the speakers' shared
space -- asks every question with passages off (today's product) and on,
today's thresholds both times, and writes ids and scores only: each store's
overall scores, and the answers shown off and not on (lost) and on and not
off (gained). Usage: ``measure.py OUT.json [CHECKOUT] [vector-background]``.

``vector-background`` measures a variant of the counting: a memory's
meaning is still max(vector, best passage), but the question's background
(dense ranks 4-15) is taken from the memories' own vectors, as today.
Evidence: ``max_today.json`` (the counting as built), and
``max_vector_background_today.json`` (the variant); product measures off and
on beside them (``scripts/memory_product_measures.py measure [--passages]``).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from kazma_core.memory import benchmark, passages

ROOT = Path(__file__).resolve().parents[4] if len(sys.argv) < 3 else Path(sys.argv[2])
FIX = ROOT / "tests" / "fixtures" / "memory_bench"
VECTORS, SPEAKERS, PASSAGES = FIX / "vectors.npz", FIX / "speakers_vectors.npz", FIX / "passage_vectors.npz"


def _patched(embedder):
    from unittest import mock

    return (mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder),
            mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: embedder.model))


def _shown(results: list[dict]) -> set[str]:
    return {r["id"] for r in results if "rr" in r and r.get("hit")}


def _empty(results: list[dict]) -> set[str]:
    return {r["id"] for r in results if "rr" not in r and r.get("hit")}


def _bench(report: dict) -> dict:
    """A run_benchmark report's overall scores and its unanswerable share."""
    o = {k: report["overall"][k] for k in ("questions", "hit_rate", "mrr", "precision")}
    o["abstain_hit_rate"] = (report.get("categories") or {}).get("abstain", {}).get("hit_rate")
    return o


def _run(on: bool) -> dict:
    replay = benchmark.SpeakerReplay(VECTORS, PASSAGES) if on else benchmark.ReplayEmbedder(VECTORS)
    speaker_replay = benchmark.SpeakerReplay(VECTORS, SPEAKERS, *([PASSAGES] if on else []))
    out: dict = {}
    a, b = _patched(replay)
    with a, b, passages.used(on):
        started = time.perf_counter()
        main = benchmark.run_benchmark(keep_results=True)
        out["main"] = {"overall": _bench(main), "results": main["results"]}
        out["second"] = {"overall": _bench(main["second"]), "results": main["second"]["results"]}
        out["seconds_main"] = round(time.perf_counter() - started, 1)
        scale = benchmark.run_scale_benchmark(keep_results=True)
        out["scale"] = {"overall": _bench(scale), "results": scale["results"]}
        out["scale_second"] = {"overall": _bench(scale["second"]), "results": scale["second"]["results"]}
        small = benchmark.run_small_store_benchmark()
        out["small"] = {"overall": small["overall"], "results": list(small["results"].values())}
    a, b = _patched(speaker_replay)
    with a, b, passages.used(on):
        sp = benchmark.run_speaker_benchmark(keep_results=True)
        out["speakers"] = {"overall": sp["overall"], "results": sp["results"]}
    return out


def _vector_background():
    """Variant: the question's background from the memories' own vectors
    (today's), the candidates' meaning still max(vector, passage)."""
    import contextlib
    from unittest import mock

    from kazma_core.memory import recall as r

    held: list = []
    real_with, real_bg = r._with_passages, r._question_background

    def with_passages(dense, best, pool):
        held.append([h.score for h in dense])
        return real_with(dense, best, pool)

    def background(scores, **kw):
        if held and kw.get("kind", "episode") == "episode":
            return real_bg(held.pop(), **kw)
        return real_bg(scores, **kw)

    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(r, "_with_passages", with_passages))
    stack.enter_context(mock.patch.object(r, "_question_background", background))
    return stack


def main() -> int:
    variant = sys.argv[3] if len(sys.argv) > 3 else ""
    off = _run(False)
    if variant == "vector-background":
        with _vector_background():
            on = _run(True)
    else:
        on = _run(True)
    record: dict = {"what": "Kazma's benchmark with passages off (today) and on, today's thresholds, "
                            "the same commit; ids and scores only.", "variant": variant or "max", "stores": {}}
    for store in ("main", "second", "scale", "scale_second", "small", "speakers"):
        was, now = off[store]["results"], on[store]["results"]
        record["stores"][store] = {
            "off": off[store]["overall"], "on": on[store]["overall"],
            "answers_lost": sorted(_shown(was) - _shown(now)),
            "answers_gained": sorted(_shown(now) - _shown(was)),
            "empty_lost": sorted(_empty(was) - _empty(now)),
            "empty_gained": sorted(_empty(now) - _empty(was)),
        }
        if store == "speakers":
            record["stores"][store]["facts_lost"] = sorted(
                {r["id"] for r in was if r.get("fact_hit")} - {r["id"] for r in now if r.get("fact_hit")})
    record["seconds_main"] = {"off": off["seconds_main"], "on": on["seconds_main"]}
    Path(sys.argv[1]).write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                 newline="\n")
    for store, row in record["stores"].items():
        print(store, json.dumps({k: row[k] for k in ("answers_lost", "answers_gained", "empty_lost",
                                                      "empty_gained")}))
        keys = ("hit_rate", "mrr", "precision", "abstain_hit_rate", "answered", "abstained", "shown",
                "fact_hit_rate", "misattributed")
        print("   off", json.dumps({k: row["off"][k] for k in keys if k in row["off"]}))
        print("   on ", json.dumps({k: row["on"][k] for k in keys if k in row["on"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
