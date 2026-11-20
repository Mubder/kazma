"""Audit evidence (docs/audits/AUDIT_MEMORY_BENCHMARKS_2026-11.md, sections 3.3
and 3.4): what ``scripts/memory_bench.py lock`` and the extraction gate do with

1. a second recording of the same model and the same messages,
2. no recording at all,
3. another extraction prompt, and
4. a new version of the extraction dataset.

It calls the lock's own functions on temporary copies. No file of the
repository is written, and no model is called.

    python extraction_lock.py [--r3 FOLDER]

``--r3`` is the folder holding the Spaces R3 study's ``deep_replies.json``
(default: its place in this repository). Writes ``extraction_lock.json``
beside this file.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "memory_bench"
#: The gate's own tolerance (tests/test_memory_benchmark.py).
SLACK = 0.0001


def gate(scores: dict[str, float], floor: dict[str, float]) -> dict[str, list[str]]:
    """``tests/test_memory_benchmark.py::_compare``: what the gate would say."""
    return {
        "got_worse": [f"{k}: {scores.get(k)} < {v}" for k, v in floor.items() if scores.get(k, -1.0) < v - SLACK],
        "improved_lock_it": [f"{k}: {v} > {floor[k]}" for k, v in scores.items()
                             if k in floor and v > floor[k] + SLACK],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--r3", type=Path, default=ROOT / "docs" / "audits" / "evidence" / "spaces_r3")
    args = parser.parse_args(argv)

    from kazma_core.memory import belief_extractor
    from kazma_core.memory import benchmark as bench

    spec = importlib.util.spec_from_file_location("_memory_bench", ROOT / "scripts" / "memory_bench.py")
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    first = json.loads((FIXTURES / "extraction_llm_2026-10-15.json").read_text(encoding="utf-8"))  # the recording measured
    locked = json.loads((FIXTURES / "thresholds.json").read_text(encoding="utf-8"))
    r3 = json.loads((args.r3 / "deep_replies.json").read_text(encoding="utf-8"))["replies"]

    def llm(scores: dict[str, float]) -> dict[str, float]:
        return {k: v for k, v in scores.items() if k.startswith("llm.")}

    report: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="kazma-audit-lock-") as tmp:
        work = Path(tmp)
        # A second recording: the six turns the R3 study asked again take
        # their second reply; the other 24 keep the reply they have.
        second = json.loads(json.dumps(first))
        again = [k for k in second["replies"] if k in r3]
        for key in again:
            second["replies"][key] = r3[key]
        second_path = work / "extraction_llm.json"
        second_path.write_text(json.dumps(second, ensure_ascii=False), encoding="utf-8")

        first_scores = bench.extraction_scores(bench.run_extraction_benchmark(llm_replies=first))
        second_scores = bench.extraction_scores(bench.run_extraction_benchmark(llm_replies=second))
        with mock.patch.object(script, "LLM_REPLIES", second_path):
            floor = script._extraction_floor(locked)["scores"]
        report["1_a_second_recording"] = {
            "turns_that_took_their_second_reply": len(again),
            "scores_first_recording": llm(first_scores),
            "scores_second_recording": llm(second_scores),
            "floor_after_lock": llm(floor),
            "gate_on_the_second_recording": gate(second_scores, floor),
            "gate_on_the_first_recording_put_back": gate(first_scores, floor),
            "floor_scores_neither_recording_reaches_together": sorted(
                k for k, v in llm(floor).items() if first_scores[k] < v - SLACK or second_scores[k] < v - SLACK),
        }

        with mock.patch.object(script, "LLM_REPLIES", work / "absent.json"):
            floor_none = script._extraction_floor(locked)["scores"]
        none_scores = bench.extraction_scores(bench.run_extraction_benchmark())
        report["2_no_recording"] = {"deep_pass_scores_left_in_the_floor": sorted(llm(floor_none)),
                                    "gate": gate(none_scores, floor_none)}

        with mock.patch.object(belief_extractor, "_EXTRACT_SYSTEM", 'Keep nothing. Reply {"beliefs": []}.'):
            other = bench.extraction_scores(bench.run_extraction_benchmark(llm_replies=first))
        report["3_another_prompt"] = {
            "scores_that_moved": {k: [first_scores[k], other[k]] for k in first_scores
                                  if abs(first_scores[k] - other[k]) > SLACK}}

        data = json.loads(bench.EXTRACTION_PATH.read_text(encoding="utf-8"))
        next_version = work / "extraction.json"
        next_version.write_text(json.dumps({"version": data["version"] + 1, "turns": data["turns"]},
                                           ensure_ascii=False), encoding="utf-8")
        with mock.patch.object(script, "EXTRACTION", next_version), \
                mock.patch.object(script, "LLM_REPLIES", second_path):
            newer = script._extraction_floor(locked)
        report["4_a_new_dataset_version"] = {
            "version_written": newer["version"],
            "floors_kept_from_the_old_version": sorted(k for k, v in newer["scores"].items()
                                                       if v > second_scores[k] + SLACK)}

    out = HERE / "extraction_lock.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=True, indent=1))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
