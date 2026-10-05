"""Prepare intake, freeze cases, collect human judgments and check review completeness."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kazma_core.evaluation_review import (
    apply_reviews, collection_template, freeze_collection, make_review_packet, review_readiness,
)
from live_eval import write_report


def _read(path: Path) -> dict:
    if path.stat().st_size > 20_000_000:
        raise ValueError("Input exceeds twenty megabytes.")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "freeze", "packet", "apply"):
        sub = commands.add_parser(command)
        sub.add_argument("--output", type=Path, required=True)
        if command in ("freeze",):
            sub.add_argument("--dataset", type=Path, required=True)
        if command in ("packet", "apply"):
            sub.add_argument("--report", type=Path, required=True)
        if command == "apply":
            sub.add_argument("--packet", type=Path, required=True)
    check = commands.add_parser("check")
    check.add_argument("--report", type=Path, required=True)
    check.add_argument("--dataset", type=Path, required=True)
    check.add_argument("--minimum-per-language", type=int, default=30)
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = review_readiness(_read(args.report), _read(args.dataset),
                                     minimum_per_language=args.minimum_per_language)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ready_for_human_comparison"] else 1
        if args.output.exists():
            raise ValueError("Choose a new output file; preserve intake, reports and prior reviews.")
        if args.command == "init":
            result = collection_template()
        elif args.command == "freeze":
            result = freeze_collection(_read(args.dataset))
        elif args.command == "packet":
            result = make_review_packet(_read(args.report))
        else:
            result = apply_reviews(_read(args.report), _read(args.packet))
        write_report(args.output, result)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
