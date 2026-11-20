"""Record the configured model's replies for the memory extraction benchmark.

Memory audit M24, the benchmark audit's R2. The extraction benchmark scores
the model's deep pass from recorded replies
(``tests/fixtures/memory_bench/extraction_llm.json``), so CI never calls a
model. This asks the model of the install whose ``.env`` it loads the deep
pass's own question for every benchmark turn, ``--askings`` times (5 by
default: asked again, a model answers a little differently, and one asking
has no spread), and writes the replies to ``--out``.

Usage (with the install's own Python, in that install):
    python scripts/memory_extraction_record.py --out PATH --limit 150

It makes at most ``--limit`` model calls (turns times askings for a whole
recording; the turns of ``kazma_core/memory/benchmark_data/extraction.json``).
Every turn's first asking comes before any turn's second. The file is
written after every reply, and a file already at ``--out`` that answered the
same prompt, message form and dataset is continued: what it holds is never
asked again, so a run cut short or stopped at its limit goes on where it
stopped. It writes nothing but ``--out``; name a checkout's fixture there,
then score and lock in that checkout (``python scripts/memory_bench.py
extraction``, then ``lock --new-recording "why"``: a new recording replaces
the model pass's scores only on purpose, and only with
``benchmark.ASKINGS_STANDARD`` askings a turn). The file says what it is:
the model, the provider, the date, the askings a turn, whether it is
complete, the hash of the system prompt it answered and the form of the
message it was asked (``message_shape``); a later change to either makes the
benchmark refuse it, unless the change is declared in the lock until it is
recorded (``memory_bench.py lock --prompt-changed "why"``). ``--out`` is
required so a run inside an install never writes into the install's own
checkout. ``scripts/memory_bench.py`` stays env-free: it builds its
benchmarks on temporary databases, and an install's ``.env`` would point
them at the install.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


def _write(out: Path, recording: dict[str, Any]) -> None:
    """*recording* at *out*, whole or not at all."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(recording, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, out)


def record_to(out: Path, *, limit: int, askings: int | None = None,
              dataset: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask the configured model every turn of *dataset* (default: the shipped
    extraction dataset) *askings* times, at most *limit* calls, continuing the
    recording at *out*, and write it after every reply."""
    from kazma_core.memory.benchmark import ASKINGS_STANDARD, record_llm_extraction

    held = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None
    recording = asyncio.run(record_llm_extraction(
        dataset, limit=limit, askings=askings or ASKINGS_STANDARD, held=held,
        save=lambda now: _write(out, now)))
    _write(out, recording)
    return recording


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="where to write the replies (JSON)")
    parser.add_argument("--limit", required=True, type=int, help="the most model calls this run may make")
    parser.add_argument("--askings", type=int, help="askings a turn (default: benchmark.ASKINGS_STANDARD)")
    args = parser.parse_args(argv)
    if args.limit < 0 or (args.askings is not None and args.askings < 1):
        parser.error("--limit is 0 or more, --askings 1 or more")

    from kazma_core.env_files import load_env_files

    load_env_files()
    recording = record_to(args.out, limit=args.limit, askings=args.askings)
    replies = sum(len(v) if isinstance(v, list) else 1 for v in recording["replies"].values())
    print(f"{args.out}: {replies} replies from {recording['model']} for {len(recording['replies'])} turns "
          f"(extraction dataset {recording['extraction_version']}); "
          + ("complete" if recording.get("complete") else "not complete: run it again to go on"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
