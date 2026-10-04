"""Validate recorded, human reviewed X outcomes without publishing or labeling.

Usage: python scripts/x_qualification.py --report reviewed.json [--install]
Without a report, print the current pipeline identity and required coverage.
Install is explicit; it preserves the operator's draft/auto mode.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    from kazma_core.env_files import load_env_files

    load_env_files()
    from kazma_core.x_api.qualification import evaluate_report, install_report, qualification_status

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    if args.install and not args.report:
        parser.error("--install requires --report")
    try:
        status = qualification_status()
        if args.report:
            if args.report.stat().st_size > 2_000_000:
                raise ValueError("Report exceeds two megabytes")
            report = json.loads(args.report.read_text(encoding="utf-8"))
            status = install_report(report) if args.install else evaluate_report(report, status["fingerprint"])
        print(json.dumps(status, ensure_ascii=False, indent=2))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
