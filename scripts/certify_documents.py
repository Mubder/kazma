"""Run bounded Document Intelligence certification and emit JSON."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "kazma-core"))

from kazma_core.documents.certification import run_document_certification  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic hostile-corpus and bounded resource certification. "
            "The default is a short CI smoke; --soak is explicitly opt-in."
        )
    )
    parser.add_argument("--output", type=Path, help="Atomically write the JSON report")
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Session-local work directory (default: session-artifacts/document-cert-work)",
    )
    parser.add_argument("--soak", action="store_true", help="Run the opt-in repeated parse soak")
    parser.add_argument(
        "--soak-iterations",
        type=int,
        default=int(os.getenv("KAZMA_DOCUMENT_SOAK_ITERATIONS", "100")),
    )
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="Keep generated scratch data for diagnosis",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.soak_iterations <= 0:
        raise SystemExit("--soak-iterations must be positive")
    output = args.output.resolve() if args.output else None
    work_dir = (
        args.work_dir.resolve()
        if args.work_dir
        else (_REPO_ROOT / "session-artifacts" / "document-cert-work").resolve()
    )
    try:
        report = run_document_certification(
            work_dir,
            soak=bool(args.soak),
            soak_iterations=int(args.soak_iterations),
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            pending = output.with_name(f".{output.name}.pending")
            pending.write_text(rendered, encoding="utf-8")
            pending.replace(output)
        sys.stdout.write(rendered)
        return 1 if report["overall_status"] == "FAIL" else 0
    finally:
        if not args.keep_work:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
