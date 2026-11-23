#!/usr/bin/env python3
"""Each LongMemEval question's recall block as another checkout builds it.

A memory change is answered with and without itself by one reader and the
same judges in one run (the memory plan's rule): this script builds every
question's block with the code of the checkout ``--checkout`` names -- its
own ``scripts/longmemeval_bench.py`` (``kazma_history``) and its own memory
packages, first on the path and checked -- and writes them for this
checkout's harness to answer as a condition of its own::

    python scripts/longmemeval_blocks.py --checkout OLD --data DIR --split dev \\
        --out DIR/cache/blocks-<commit>.json
    python scripts/longmemeval_bench.py answer --data DIR --split dev --blocks kazma_before=DIR/cache/blocks-<commit>.json

The file holds the dataset's text, so it is written outside both checkouts
(into ``DIR``, never committed). No model call: the questions' and rounds'
vectors come from the harness's cache in ``DIR``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PACKAGES = ("kazma-core", "kazma-gateway", "kazma-ui", "kazma-cli", "kazma-skills", "kazma-tui")


def use_checkout(checkout: Path) -> Any:
    """*checkout*'s harness, with its packages first on the path; refused
    unless the memory code and the harness that load are *checkout*'s."""
    for part in reversed(PACKAGES):
        sys.path.insert(0, str(checkout / part))
    sys.path.insert(0, str(checkout / "scripts"))
    import kazma_core
    import longmemeval_bench

    for module in (kazma_core, longmemeval_bench):
        if checkout not in Path(str(module.__file__)).resolve().parents:
            raise SystemExit(f"{module.__name__} loaded from {module.__file__}, not from {checkout}")
    return longmemeval_bench


def export_blocks(bench: Any, items: list[dict[str, Any]]) -> dict[str, str]:
    """Each item's block, as *bench* (a checkout's harness) builds it."""
    out: dict[str, str] = {}
    for n, item in enumerate(items, 1):
        block, _whole, _shown = bench.kazma_history(item, bench.history_rows(item))
        out[str(item["question_id"])] = block
        if n % 20 == 0:
            print(f"  {n} of {len(items)} blocks", flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--checkout", required=True, type=Path, help="the checkout whose code builds the blocks")
    p.add_argument("--data", required=True, type=Path, help="the folder LongMemEval was downloaded into")
    p.add_argument("--split", default="dev")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--passages", action="store_true",
                   help="passages on (the memory plan's W5): their texts embedded into the cache first")
    p.add_argument("--rerank", action="store_true",
                   help="the reranker on (the memory plan's W3), with the passages that bring its candidates: "
                        "the real model, read with no budget")
    args = p.parse_args(argv)
    checkout, out = args.checkout.resolve(), args.out.resolve()
    if any(folder == out or folder in out.parents for folder in (REPO, checkout)):
        print("the blocks hold the dataset's text: write them outside both checkouts", file=sys.stderr)
        return 2
    bench = use_checkout(checkout)
    bench._private_data_folder()
    bench._limit_cpu_threads()
    from unittest import mock

    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

    embedder = bench.CachedEmbedder(get_embedder(), args.data / "cache" / "kazma_vectors.npz",
                                    get_embedding_model_name())
    by_id = {str(x["question_id"]): x for x in bench.load(args.data)}
    items = [by_id[q] for q in bench.question_ids(args.split)[: args.limit or None]]
    switch: Any = contextlib.nullcontext()
    if args.passages or args.rerank:
        from kazma_core.memory import passages

        texts = [t for item in items for t in bench.passage_texts(bench.history_rows(item))]
        print(f"{len(texts)} passages; embedding what the cache lacks", flush=True)
        embedder.fill(texts)
        switch = bench._rerank_switch() if args.rerank else passages.used(True)
    with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder), \
            mock.patch("kazma_core.memory.recall._recall_deadline_s", lambda: 600.0), switch:
        blocks = export_blocks(bench, items)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=checkout, capture_output=True,
                            text=True, check=False).stdout.strip()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps({"commit": commit, "split": args.split, "made_on": datetime.now(UTC).isoformat(),
                               "passages": bool(args.passages or args.rerank), "rerank": bool(args.rerank),
                               "blocks": blocks}, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, out)
    print(f"wrote {len(blocks)} blocks built at {commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
