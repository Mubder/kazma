#!/usr/bin/env python3
"""Choose recall's evidence thresholds on half the benchmark's questions, on
every store the benchmark builds, and judge them on the other half
(2026-11-21, dataset v8).

Recall decides what is about a question by its evidence: meaning above the
question's background plus the share of its words a memory holds
(``recall._rank_by_evidence``), against a floor and a gap per kind of memory
(turns, facts, weekly summaries). Those values were swept on dataset v2-v4,
where the unrelated chat repeated a few dozen messages; on v8 (each unrelated
turn once) precision fell from 0.581 to 0.487 and no unanswerable question
came back empty. This script chooses them again without letting the choice
see half of the questions:

    python scripts/memory_threshold_sweep.py split
    python scripts/memory_threshold_sweep.py sweep --out FILE
    python scripts/memory_threshold_sweep.py judge --chosen FILE --out FILE

``split`` divides the retrieval benchmark's questions (both sets) and the
speakers case set's into a tuning half and a judging half, by category and
script (Arabic or not; the speakers' language), each group ordered by a hash
of its ids and dealt in turn. ``sweep`` asks the tuning half only, for every
configuration of a coordinate sweep, on four stores: the benchmark's store,
the same store at four times its unrelated chat (``run_scale_benchmark``'s),
each question in a small store of its own (``run_small_store_benchmark``'s),
and a shared space of several members (``run_speaker_benchmark``'s).
``judge`` asks both halves with today's thresholds and the chosen ones.

The rule:

1. No answer is lost, on any store: a configuration must show every
   tuning-half answer today's thresholds show (and every fact the speakers
   set shows).
2. More unanswerable questions answered with nothing (the three stores of
   the retrieval benchmark together); one question is a tie (a threshold
   never moves to save, or to win, one question).
3. Then precision plus MRR, averaged over the four stores; a difference
   under 0.01 is a tie.
4. Then the fewest values moved from today, then the smallest move.
5. Robust: a value moves only where its neighbours on the grid, one step
   either way, lose no answer either; a value at the grid's edge only when
   it is today's.

How it came to this, before the judging half was asked: a first sweep on the
benchmark's store alone moved the turns' floor 0.04 -> 0.06 and gap
0.15 -> 0.04, losing no answer there; at four times the store those values
lost four answers, in small stores three, and on the speakers set its hit
rate fell. Rule 5 was added after the first sweep (it had put the facts' gap
one step from a lost answer); rule 1's "on any store" after the second.
The judging half is not a sealed set: finding where the first choice lost
answers read every question of every store once
(``docs/audits/evidence/memory_thresholds_v8/attribution_every_question.json``).
The final choice moved nothing, so there was nothing for it to be fitted to.

Recorded vectors are replayed (no model is loaded). Nothing is written but
``--out``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import random
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "tests" / "fixtures" / "memory_bench" / "vectors.npz"
SPEAKER_VECTORS = ROOT / "tests" / "fixtures" / "memory_bench" / "speakers_vectors.npz"

#: Every swept value: its name here and the module value recall reads
#: (``recall._evidence_rules``, read at call time).
PARAMS = {
    "episode_floor": "_EVIDENCE_FLOOR",
    "episode_gap": "_EVIDENCE_GAP",
    "episode_coverage": "_COVERAGE_WEIGHT",
    "episode_recency": "_RECENCY_WEIGHT",
    "belief_floor": "_BELIEF_FLOOR",
    "belief_gap": "_BELIEF_GAP",
    "belief_coverage": "_BELIEF_COVERAGE_WEIGHT",
    "belief_recency": "_BELIEF_RECENCY_WEIGHT",
    "summary_floor": "_SUMMARY_FLOOR",
    "summary_gap": "_SUMMARY_GAP",
    "summary_coverage": "_SUMMARY_COVERAGE_WEIGHT",
}


def _steps(lo: float, hi: float, step: float) -> list[float]:
    n = int(round((hi - lo) / step))
    return [round(lo + i * step, 4) for i in range(n + 1)]


#: The coordinate sweep: each stage moves its values over the grid with the
#: others held where the earlier stages left them; the floors and gaps run a
#: second time once the weights have moved (a configuration already asked is
#: not asked again). Every grid holds today's value inside it.
STAGES: list[tuple[str, dict[str, list[float]]]] = [
    ("turns", {"episode_floor": _steps(0.02, 0.08, 0.01), "episode_gap": _steps(0.06, 0.20, 0.02)}),
    ("facts", {"belief_floor": _steps(0.04, 0.08, 0.01), "belief_gap": _steps(0.04, 0.12, 0.02)}),
    ("summaries", {"summary_floor": _steps(0.17, 0.25, 0.02), "summary_gap": _steps(0.06, 0.14, 0.02)}),
    ("turn words", {"episode_coverage": _steps(0.08, 0.16, 0.02)}),
    ("fact words", {"belief_coverage": _steps(0.02, 0.10, 0.02)}),
    ("summary words", {"summary_coverage": _steps(0.08, 0.16, 0.02)}),
    ("turn recency", {"episode_recency": _steps(0.0, 0.1, 0.025)}),
    ("fact recency", {"belief_recency": _steps(0.0, 0.04, 0.01)}),
    ("turns again", {"episode_floor": _steps(0.02, 0.08, 0.01), "episode_gap": _steps(0.06, 0.20, 0.02)}),
    ("facts again", {"belief_floor": _steps(0.04, 0.08, 0.01), "belief_gap": _steps(0.04, 0.12, 0.02)}),
    ("summaries again", {"summary_floor": _steps(0.17, 0.25, 0.02), "summary_gap": _steps(0.06, 0.14, 0.02)}),
]

SPLIT_SALT = "kazma-threshold-sweep-v8"
ONE_QUESTION = 1
QUALITY_TIE = 0.01
STORES = ("main", "scale", "small", "speakers")


def _arabic(text: str) -> bool:
    return bool(re.search("[؀-ۿ]", text))


def _questions(data: dict[str, Any]) -> list[dict[str, Any]]:
    return list(data["questions"]) + list(data.get("second") or [])


def _deal(groups: dict[Any, list[dict[str, Any]]]) -> dict[str, list[str]]:
    halves: dict[str, list[str]] = {"tune": [], "held": []}
    turn = 0
    for key in sorted(groups):
        ordered = sorted(groups[key], key=lambda q: hashlib.sha256(f"{SPLIT_SALT}:{q['id']}".encode()).hexdigest())
        for q in ordered:
            halves["tune" if turn % 2 == 0 else "held"].append(q["id"])
            turn += 1
    return halves


def split(data: dict[str, Any], speakers: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """Question ids of the tuning half and the judging half: the retrieval
    benchmark's (``bench``) and the speakers case set's (``speakers``)."""
    bench: dict[Any, list[dict[str, Any]]] = {}
    for q in _questions(data):
        bench.setdefault((q["category"], _arabic(q["text"])), []).append(q)
    people: dict[Any, list[dict[str, Any]]] = {}
    for q in speakers["questions"]:
        people.setdefault((q.get("category"), q.get("lang")), []).append(q)
    return {"bench": _deal(bench), "speakers": _deal(people)}


def today() -> dict[str, float]:
    from kazma_core.memory import recall

    return {name: float(getattr(recall, attr)) for name, attr in PARAMS.items()}


@contextmanager
def _thresholds(config: dict[str, float]) -> Iterator[None]:
    from kazma_core.memory import recall

    saved = {attr: getattr(recall, attr) for attr in PARAMS.values()}
    try:
        for name, value in config.items():
            setattr(recall, PARAMS[name], value)
        yield
    finally:
        for attr, value in saved.items():
            setattr(recall, attr, value)


def _replayed(embedder: Any) -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder))
    stack.enter_context(mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: embedder.model))
    return stack


class Stores:
    """The four stores, seeded once; :meth:`ask` asks one half of the
    questions with one configuration on each."""

    def __init__(self, data: dict[str, Any], speakers: dict[str, Any], halves: dict[str, dict[str, list[str]]],
                 stack: contextlib.ExitStack) -> None:
        from kazma_core.memory import benchmark

        self.data, self.speakers, self.halves = data, speakers, halves
        self.replay = benchmark.ReplayEmbedder(VECTORS)
        self.speaker_replay = benchmark.SpeakerReplay(VECTORS, SPEAKER_VECTORS)
        stack.enter_context(_replayed(self.replay))
        self.main = stack.enter_context(benchmark._seeded_store(data))
        scaled = benchmark._scaled_dataset(data)
        self.scale = stack.enter_context(benchmark._seeded_store(scaled)) if scaled else None
        self.small: dict[str, tuple[Any, ...]] = {}
        for q in _questions(data):
            sub = benchmark._small_store_dataset(data, q, rng=random.Random(q["id"]))
            if sub is not None:
                self.small[q["id"]] = (q, stack.enter_context(benchmark._seeded_store(sub)))

    def ask(self, config: dict[str, float], half: str) -> dict[str, list[dict[str, Any]]]:
        from kazma_core.memory import benchmark

        ids = set(self.halves["bench"][half])
        questions = [q for q in _questions(self.data) if q["id"] in ids]
        out: dict[str, list[dict[str, Any]]] = {}
        with _thresholds(config):
            out["main"] = benchmark._ask_questions(*self.main, questions)[0]
            out["scale"] = benchmark._ask_questions(*self.scale, questions)[0] if self.scale else []
            out["small"] = [benchmark._ask_questions(*store, [q])[0][0]
                            for qid, (q, store) in self.small.items() if qid in ids]
            asked = [q for q in self.speakers["questions"] if q["id"] in set(self.halves["speakers"][half])]
            with _replayed(self.speaker_replay):
                out["speakers"] = benchmark.run_speaker_benchmark(questions=asked, keep_results=True)["results"]
        return out


def measure(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts and means of one store's half: answers shown, unanswerable
    questions answered with nothing, MRR and precision of the answerable."""
    answerable = [r for r in results if "rr" in r]
    empty = [r for r in results if "rr" not in r]
    n = len(answerable) or 1
    return {
        "answerable": len(answerable),
        "answered": sum(1 for r in answerable if r["hit"]),
        "unanswerable": len(empty),
        "abstained": sum(1 for r in empty if r["hit"]),
        "mrr": round(sum(r["rr"] for r in answerable) / n, 4),
        "precision": round(sum(r["precision"] for r in answerable) / n, 4),
        "shown": round(sum(int(r.get("injected") or 0) for r in results) / (len(results) or 1), 2),
    }


def shown_answers(asked: dict[str, list[dict[str, Any]]]) -> set[str]:
    """Every answer shown, as ``store:question`` (and ``speakers:question:fact``
    for a speakers question whose fact was shown)."""
    out = {f"{store}:{r['id']}" for store, rows in asked.items() for r in rows if "rr" in r and r["hit"]}
    out |= {f"speakers:{r['id']}:fact" for r in asked.get("speakers", []) if r.get("fact_hit")}
    return out


def summarise(asked: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    per = {store: measure(rows) for store, rows in asked.items() if rows}
    return {
        "stores": per,
        "abstained": sum(per[s]["abstained"] for s in ("main", "scale", "small") if s in per),
        "quality": round(sum(m["precision"] + m["mrr"] for m in per.values()) / len(per), 4),
    }


def _moves(config: dict[str, float], base: dict[str, float]) -> tuple[int, float]:
    moved = [name for name in config if abs(config[name] - base[name]) > 1e-9]
    size = sum(abs(config[name] - base[name]) / max(abs(base[name]), 0.01) for name in moved)
    return len(moved), size


def robust(row: dict[str, Any], rows: list[dict[str, Any]], axes: dict[str, list[float]],
           base: dict[str, float]) -> bool:
    """Rule 5: each of the stage's values that *row* moves from today has
    both grid neighbours measured, and neither loses an answer."""
    lost = {tuple(sorted(r["config"].items())): r["lost"] for r in rows}
    for name, values in axes.items():
        value = row["config"][name]
        if abs(value - base[name]) <= 1e-9:
            continue
        at = min(range(len(values)), key=lambda i: abs(values[i] - value))
        if at == 0 or at == len(values) - 1:
            return False
        for side in (values[at - 1], values[at + 1]):
            key = tuple(sorted({**row["config"], name: side}.items()))
            if lost.get(key, 1) != 0:
                return False
    return True


def choose(rows: list[dict[str, Any]], base: dict[str, float],
           axes: dict[str, list[float]] | None = None) -> dict[str, Any]:
    """The rule (module docstring) over evaluated configurations, each
    ``{"config", "lost", "m"}``; today's configuration is always among them."""
    feasible = [r for r in rows if r["lost"] == 0 and (axes is None or robust(r, rows, axes, base))]
    best_empty = max(r["m"]["abstained"] for r in feasible)
    band = [r for r in feasible if r["m"]["abstained"] >= best_empty - ONE_QUESTION]
    best_quality = max(r["m"]["quality"] for r in band)
    band = [r for r in band if r["m"]["quality"] >= best_quality - QUALITY_TIE]
    return min(band, key=lambda r: (_moves(r["config"], base), -r["m"]["abstained"], -r["m"]["quality"]))


def _grid(axes: dict[str, list[float]]) -> list[dict[str, float]]:
    configs: list[dict[str, float]] = [{}]
    for name, values in axes.items():
        configs = [{**c, name: v} for c in configs for v in values]
    return configs


def _load() -> tuple[dict[str, Any], dict[str, Any]]:
    from kazma_core.memory.benchmark import load_dataset, load_speakers

    return load_dataset(), load_speakers()


def _cmd_split() -> int:
    data, speakers = _load()
    halves = split(data, speakers)
    by_id = {q["id"]: q for q in _questions(data)}
    for half, ids in halves["bench"].items():
        cats: dict[str, int] = {}
        for qid in ids:
            cats[by_id[qid]["category"]] = cats.get(by_id[qid]["category"], 0) + 1
        arabic = sum(1 for qid in ids if _arabic(by_id[qid]["text"]))
        print(f"{half}: {len(ids)} questions, {arabic} in Arabic; {dict(sorted(cats.items()))}; "
              f"speakers {len(halves['speakers'][half])}")
    return 0


def _short(m: dict[str, Any]) -> str:
    return json.dumps({s: {k: v for k, v in x.items() if k in ("answered", "abstained", "precision", "mrr")}
                       for s, x in m["stores"].items()})


def _cmd_sweep(out: Path) -> int:
    data, speakers = _load()
    halves = split(data, speakers)
    base = today()
    record: dict[str, Any] = {"dataset_version": data["version"], "speakers_version": speakers.get("version"),
                              "split": halves, "today": base, "stages": []}
    with contextlib.ExitStack() as stack:
        started = time.perf_counter()
        stores = Stores(data, speakers, halves, stack)
        print(f"stores seeded in {time.perf_counter() - started:.0f} s", flush=True)
        first = stores.ask(base, "tune")
        if first != stores.ask(base, "tune"):
            print("a store answers differently when asked twice: refusing to sweep", file=sys.stderr)
            return 1
        shown_today = shown_answers(first)
        record["today_tune"] = summarise(first)
        print("today, tuning half:", _short(record["today_tune"]), flush=True)
        point = dict(base)
        asked: dict[tuple[tuple[str, float], ...], dict[str, list[dict[str, Any]]]] = {}

        def measured(config: dict[str, float]) -> dict[str, list[dict[str, Any]]]:
            key = tuple(sorted(config.items()))
            if key not in asked:
                asked[key] = stores.ask(config, "tune")
            return asked[key]

        for name, axes in STAGES:
            started = time.perf_counter()
            rows = []
            configs = [{**point, **change} for change in _grid(axes)]
            if point not in configs:
                configs.append(dict(point))
            for config in configs:
                results = measured(config)
                lost = sorted(shown_today - shown_answers(results))
                rows.append({"config": config, "lost": len(lost), "lost_ids": lost, "m": summarise(results)})
            chosen = choose(rows, base, axes)
            point = dict(chosen["config"])
            record["stages"].append({
                "stage": name,
                "axes": axes,
                "rows": [{"values": {k: r["config"][k] for k in axes}, "lost": r["lost"],
                          "lost_ids": r["lost_ids"], **r["m"]} for r in rows],
                "chosen": {k: point[k] for k in axes},
                "measured": chosen["m"],
            })
            moved = {k: (base[k], point[k]) for k in axes if abs(point[k] - base[k]) > 1e-9}
            print(f"{name}: {len(rows)} configurations in {time.perf_counter() - started:.0f} s; "
                  f"chosen {json.dumps({k: point[k] for k in axes})} (moved from today: {moved}); "
                  f"abstained {chosen['m']['abstained']}, quality {chosen['m']['quality']}", flush=True)
        record["chosen"] = point
    out.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("chosen:", json.dumps({k: v for k, v in point.items() if abs(v - base[k]) > 1e-9}))
    return 0


def _cmd_judge(chosen_path: Path, out: Path) -> int:
    data, speakers = _load()
    halves = split(data, speakers)
    chosen = json.loads(chosen_path.read_text(encoding="utf-8"))["chosen"]
    base = today()
    record: dict[str, Any] = {"dataset_version": data["version"], "today": base, "chosen": chosen}
    with contextlib.ExitStack() as stack:
        stores = Stores(data, speakers, halves, stack)
        for half in ("tune", "held"):
            was, now = stores.ask(base, half), stores.ask(chosen, half)
            lost = sorted(shown_answers(was) - shown_answers(now))
            gained = sorted(shown_answers(now) - shown_answers(was))
            record[half] = {"today": summarise(was), "chosen": summarise(now), "lost": lost, "gained": gained}
            print(f"{half}: today  {_short(summarise(was))}")
            print(f"{half}: chosen {_short(summarise(now))}")
            print(f"{half}: answers lost {lost}, gained {gained}")
    out.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("split")
    sweep = sub.add_parser("sweep")
    sweep.add_argument("--out", type=Path, required=True)
    judge = sub.add_parser("judge")
    judge.add_argument("--chosen", type=Path, required=True)
    judge.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "split":
        return _cmd_split()
    if args.cmd == "sweep":
        return _cmd_sweep(args.out)
    return _cmd_judge(args.chosen, args.out)


if __name__ == "__main__":
    sys.exit(main())
