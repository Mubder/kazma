#!/usr/bin/env python3
"""Measure document extraction fidelity on the versioned corpus.

Builds every case of ``kazma_core.documents.fidelity`` with Kazma's engines,
parses it with the real isolated parser and prints the metrics, which cases
meet the release targets, and any regression against the declared floors in
``tests/fixtures/documents/fidelity_thresholds.json``. The OCR cases are
measured once per OCR engine installed here (``fidelity.OCR_ENGINES``); an
engine that is not installed is named and skipped.

``--lock`` writes today's measurement as the floors. Within one corpus
version a floor is never lowered: a worse number is a regression to fix. The
floors of an engine not measured here are kept.

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
    cases = fidelity.corpus()
    plain = [case for case in cases if not fidelity.is_ocr_case(case.case_id)]
    ocr = [case for case in cases if fidelity.is_ocr_case(case.case_id)]
    with tempfile.TemporaryDirectory(prefix="kazma-fidelity-") as work:
        report = fidelity.run_corpus(Path(work), cases=plain)
        report["ocr_engines"] = {}
        for engine in fidelity.OCR_ENGINES:
            missing = fidelity.engine_available(engine)
            if missing:
                print(f"OCR engine {engine} not measured: {missing}", file=sys.stderr)
                continue
            report["ocr_engines"][engine] = fidelity.run_corpus(
                Path(work) / engine, cases=ocr, ocr_engine=engine)
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    if args.lock:
        locked = fidelity.lock_thresholds(report, previous)
        THRESHOLDS.write_text(json.dumps(locked, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        print(f"locked {len(locked['thresholds'])} case(s); qualified: {locked['qualified']}")
        for engine, section in locked["ocr_engines"].items():
            print(f"locked OCR engine {engine}: qualified {section['qualified']}")
        return 0
    regressions: dict[str, list[str]] = {}
    if previous:
        def check(label: str, entries: dict, floors: dict) -> None:
            for case_id, entry in entries.items():
                if "error" in entry:
                    regressions[label + case_id] = [entry["error"]]
                    continue
                misses = fidelity.meets(entry["metrics"], floors.get(case_id, {}))
                if misses:
                    regressions[label + case_id] = misses

        check("", report["cases"], previous.get("thresholds", {}))
        for engine, section in report["ocr_engines"].items():
            floors = ((previous.get("ocr_engines") or {}).get(engine) or {}).get("thresholds", {})
            check(f"{engine}:", section["cases"], floors)
    print(json.dumps({"regressions": regressions}, indent=2, ensure_ascii=False))
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
