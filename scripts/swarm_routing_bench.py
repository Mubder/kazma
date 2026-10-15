"""Swarm semantic routing benchmark (Swarm audit SW-E10): record it, run it.

  python scripts/swarm_routing_bench.py vectors   # record the embedder's vectors for CI
  python scripts/swarm_routing_bench.py run       # score the router from the recorded vectors
  python scripts/swarm_routing_bench.py run --real  # ... from the configured embedder (loads it)

The dataset (``tests/fixtures/swarm_routing/dataset.json``) is seven worker
profiles -- the three shipped templates and four specialists -- and English
and Arabic tasks, each naming the workers it may go to; ``none`` means no
specialist (the autoscaler's templates take it, and the catch-all generalist
may). Two scenarios: every worker registered, and the specialists alone (no
catch-all instance yet). The router is
:class:`kazma_core.swarm.semantic_router.SemanticRouter` as the engine uses
it; ``tests/test_swarm_semantic_routing.py`` holds the scores from the
recorded vectors (bge-m3, int8 per vector with its scale) and the nearest
worker without a floor -- the rule before SW-E10 -- as the negative control.

A change to a profile's or a task's text, or to how either is turned into
text, needs the vectors recorded again with the real model: a text the file
does not hold fails the run, it is never guessed.

Reads no install: no ``.env``, no database (the embedder's configuration
only, from wherever ``KAZMA_DATA_DIR`` points).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for _pkg in ("kazma-core",):
    _p = str(REPO / _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)  # this checkout's package, whatever is installed

DATASET = REPO / "tests" / "fixtures" / "swarm_routing" / "dataset.json"
VECTORS = REPO / "tests" / "fixtures" / "swarm_routing" / "vectors.npz"


def load_dataset(path: Path = DATASET) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _verdict(top: str, accept: list[str], catch_all: set[str]) -> bool:
    """Whether *top* (a worker, or ``none``) is an acceptable route."""
    if top in accept:
        return True
    # The catch-all takes what no specialist does: acceptable for "none" only.
    return "none" in accept and top in catch_all


def _keyword_top(prompt: str, registered: list[dict[str, Any]]) -> str:
    """What the router's keyword fallback picks (``UnifiedRouter._score_worker``)."""
    from kazma_core.routing_engine import UnifiedRouter, _tokenize
    from kazma_core.swarm.task import WorkerCapabilities

    tokens, best, best_score = _tokenize(prompt), "none", 0
    for w in registered:
        caps = WorkerCapabilities(role=(w.get("roles") or [""])[0], expertise=list(w["expertise"]))
        overlap = UnifiedRouter._score_worker(tokens, caps)
        if overlap > best_score:
            best, best_score = w["name"], overlap
    return best


def score(dataset: dict[str, Any], embedder: Any, *, nearest_only: bool = False) -> dict[str, Any]:
    """Route every task; the hits, the misses and why.

    Scenarios: ``all`` workers registered; the ``specialists`` alone (no
    catch-all instance yet); and ``alone``, each worker the only one (one
    autoscaler instance: a task about it must reach it, no other task may).
    ``router`` is what a DISPATCH runs: the best semantic match, else the
    keyword fallback. ``nearest_only`` takes the most similar worker with no
    floor -- the rule before SW-E10 -- as the negative control."""
    from kazma_core.swarm.semantic_router import SemanticRouter, SemanticUnavailable

    router = SemanticRouter(embedder_getter=lambda: embedder)
    workers = dataset["workers"]
    catch_all = {w["name"] for w in workers if w.get("catch_all")}

    def semantic_top(prompt: str, registered: list[dict[str, Any]]) -> tuple[str, Any]:
        outcome = router.semantic_scores(prompt, registered)
        if isinstance(outcome, SemanticUnavailable):
            raise RuntimeError(f"{prompt[:60]!r}: {outcome.reason}: {outcome.detail}")
        if nearest_only:
            return max(outcome.similarities, key=outcome.similarities.__getitem__), outcome
        return (outcome.matches[0] if outcome.matches else "none"), outcome

    report: dict[str, Any] = {}
    for scenario, registered in (("all", workers),
                                 ("specialists", [w for w in workers if not w.get("catch_all")])):
        rows: list[dict[str, Any]] = []
        for task in dataset["tasks"]:
            top, outcome = semantic_top(task["prompt"], registered)
            routed = top if top != "none" else _keyword_top(task["prompt"], registered)
            rows.append({"prompt": task["prompt"], "lang": task["lang"], "accept": task["accept"],
                         "top": top, "ok": _verdict(top, task["accept"], catch_all),
                         "router": routed, "router_ok": _verdict(routed, task["accept"], catch_all),
                         "best_evidence": round(max(outcome.evidence.values()), 4),
                         "matches": outcome.matches})
        specialist = [r for r in rows if "none" not in r["accept"]]
        unrelated = [r for r in rows if "none" in r["accept"]]
        report[scenario] = {
            "tasks": len(rows),
            "correct": sum(r["ok"] for r in rows),
            "specialist_correct": sum(r["ok"] for r in specialist),
            "specialist_tasks": len(specialist),
            # A task about no specialist that was sent to one anyway.
            "misrouted_unrelated": sum(1 for r in unrelated if not r["ok"]),
            "unrelated_tasks": len(unrelated),
            "router_specialist_correct": sum(r["router_ok"] for r in specialist),
            "router_misrouted_unrelated": sum(1 for r in unrelated if not r["router_ok"]),
            "by_language": {lang: {"tasks": sum(1 for r in rows if r["lang"] == lang),
                                   "correct": sum(r["ok"] for r in rows if r["lang"] == lang)}
                            for lang in sorted({r["lang"] for r in rows})},
            "wrong": [r for r in rows if not r["ok"]],
        }
    found = taken_wrongly = about = not_about = 0
    for worker in workers:
        for task in dataset["tasks"]:
            top, _outcome = semantic_top(task["prompt"], [worker])
            if worker["name"] in task["accept"]:
                about += 1
                found += top == worker["name"]
            elif not worker.get("catch_all"):
                not_about += 1
                taken_wrongly += top == worker["name"]
    report["alone"] = {"about_it": about, "found": found, "not_about_it": not_about,
                       "taken_wrongly": taken_wrongly}
    return report


def _limit_cpu_threads() -> None:
    """Leave most of the CPU to whatever else runs here (memory_bench's rule:
    two embedding runs at torch's default once starved the live server)."""
    try:
        import torch
    except ImportError:
        return
    torch.set_num_threads(max(1, min(4, (os.cpu_count() or 2) // 4)))


def _cmd_vectors() -> int:
    from kazma_core.memory.benchmark import RecordingEmbedder
    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

    _limit_cpu_threads()
    real = get_embedder()
    if real is None:
        print("no embedder is configured: install the rag extra (bge-m3)", file=sys.stderr)
        return 2
    recorder = RecordingEmbedder(real)
    report = score(load_dataset(), recorder)
    VECTORS.parent.mkdir(parents=True, exist_ok=True)
    n = recorder.save(VECTORS, model=get_embedding_model_name())
    print(f"{VECTORS}: {n} vectors from {get_embedding_model_name()}")
    _print(report)
    return 0


def _cmd_run(real: bool, as_json: bool) -> int:
    if real:
        from kazma_core.memory.embedder import get_embedder

        _limit_cpu_threads()
        embedder = get_embedder()
    else:
        from kazma_core.memory.benchmark import ReplayEmbedder

        embedder = ReplayEmbedder(VECTORS)
    dataset = load_dataset()
    report = {"router": score(dataset, embedder), "nearest_only": score(dataset, embedder, nearest_only=True)}
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print(report["router"])
        print("nearest worker, no floor (before SW-E10):")
        _print(report["nearest_only"])
    return 0


def _print(report: dict[str, Any]) -> None:
    for scenario, row in report.items():
        if scenario == "alone":
            print(f"  alone        a task about the only worker reached it {row['found']}/{row['about_it']}; "
                  f"a task about another went to it {row['taken_wrongly']}/{row['not_about_it']}")
            continue
        print(f"  {scenario:<12} semantic: specialist {row['specialist_correct']}/{row['specialist_tasks']}, "
              f"unrelated misrouted {row['misrouted_unrelated']}/{row['unrelated_tasks']}; with the keyword "
              f"fallback: specialist {row['router_specialist_correct']}/{row['specialist_tasks']}, unrelated "
              f"misrouted {row['router_misrouted_unrelated']}/{row['unrelated_tasks']}  {row['by_language']}")
        for wrong in row["wrong"]:
            print(f"      x {wrong['top']:<11} (accept {wrong['accept']}) ev {wrong['best_evidence']:+.3f}  "
                  f"{wrong['prompt'][:70]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("vectors", help="record the configured embedder's vectors")
    run = sub.add_parser("run", help="score the router")
    run.add_argument("--real", action="store_true", help="the configured embedder, not the recording")
    run.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "vectors":
        return _cmd_vectors()
    return _cmd_run(args.real, args.json)


if __name__ == "__main__":
    sys.exit(main())
