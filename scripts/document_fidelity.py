#!/usr/bin/env python3
"""Measure document extraction fidelity on the versioned corpus.

Builds every case of ``kazma_core.documents.fidelity`` with Kazma's engines,
parses it with the real isolated parser and prints the metrics, which cases
meet the release targets, and any regression against the declared floors in
``tests/fixtures/documents/fidelity_thresholds.json``.

``--lock`` writes today's measurement as the floors. Within one corpus
version a floor is never lowered: a worse number is a regression to fix.

Usage:
  python scripts/document_fidelity.py
  python scripts/document_fidelity.py --lock
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS = ROOT / "tests" / "fixtures" / "documents" / "fidelity_thresholds.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--lock", action="store_true", help="write today's measurement as the floors")
    args = parser.parse_args(argv)

    from kazma_core.env_files import load_env_files

    load_env_files()

    from kazma_core.documents import fidelity

    previous = json.loads(THRESHOLDS.read_text(encoding="utf-8")) if THRESHOLDS.is_file() else None
    with tempfile.TemporaryDirectory(prefix="kazma-fidelity-") as work:
        report = fidelity.run_corpus(Path(work))
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    if args.lock:
        locked = fidelity.lock_thresholds(report, previous)
        THRESHOLDS.write_text(json.dumps(locked, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        print(f"locked {len(locked['thresholds'])} case(s); qualified: {locked['qualified']}")
        return 0
    regressions = {}
    if previous:
        for case_id, entry in report["cases"].items():
            if "error" in entry:
                regressions[case_id] = [entry["error"]]
                continue
            misses = fidelity.meets(entry["metrics"], previous["thresholds"].get(case_id, {}))
            if misses:
                regressions[case_id] = misses
    print(json.dumps({"regressions": regressions}, indent=2, ensure_ascii=False))
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
