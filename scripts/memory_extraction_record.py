"""Record the configured model's replies for the memory extraction benchmark.

Memory audit M24. The extraction benchmark scores the model's deep pass from
recorded replies (``tests/fixtures/memory_bench/extraction_llm.json``), so CI
never calls a model. This asks the model of the install whose ``.env`` it
loads, once per benchmark turn, and writes the replies to ``--out``.

Usage (with the install's own Python, in that install):
    python scripts/memory_extraction_record.py --out PATH

It costs one model call per turn (the turns of
``kazma_core/memory/benchmark_data/extraction.json``). It writes nothing but
``--out``; name a checkout's fixture there, then score and lock in that
checkout (``python scripts/memory_bench.py extraction``, then ``lock``).
``--out`` is required so a run inside an install never writes into the
install's own checkout. ``scripts/memory_bench.py`` stays env-free: it builds
its benchmarks on temporary databases, and an install's ``.env`` would point
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


def record_to(out: Path, dataset: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask the configured model every turn of *dataset* (default: the shipped
    extraction dataset) and write the replies to *out*, whole or not at all."""
    from kazma_core.memory.benchmark import record_llm_extraction

    replies = asyncio.run(record_llm_extraction(dataset))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(replies, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, out)
    return replies


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="where to write the replies (JSON)")
    args = parser.parse_args(argv)

    from kazma_core.env_files import load_env_files

    load_env_files()
    replies = record_to(args.out)
    print(f"{args.out}: {len(replies['replies'])} replies from {replies['model']} "
          f"(extraction dataset {replies['extraction_version']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
