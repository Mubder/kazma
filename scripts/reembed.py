#!/usr/bin/env python3
"""Re-embed every memory and fact after an embedding model change.

Run AFTER updating ``memory.embedding`` in ``kazma.yaml`` (model + dim) --
or after saving a new model on the Settings Embedder page and restarting the
server. It runs the same rebuild as that page's "Rebuild embeddings" button
(``kazma_core.memory.reembed.rebuild_embeddings``), so the two cannot drift:

  1. Loads the new embedder first -- a rebuild that cannot encode changes
     nothing.
  2. Copies ``memory_state.db`` to ``memory_state.db.pre_reembed`` (taken
     again at every rebuild).
  3. Re-encodes, in place, every episode and fact whose vector is missing or
     was made by another model; a row keeps its old vector until its new
     one is written.

It never touches the Knowledge Library's vectors (``vector_memory/``, or
``KAZMA_VECTOR_PATH``): that folder is the Knowledge Library's live store,
and the "knowledge vector repair" maintenance pass re-embeds another model's
vectors there itself (``KnowledgeIndex.backfill_vectors``). This script used
to end by deleting that folder, a step kept from when it held the retired V1
chat index -- every library lost its meaning search until the repair pass
had embedded it again.

Usage::

    python scripts/reembed.py

Requires ``kazma_core`` on ``sys.path`` (run from repo root).
"""

from __future__ import annotations

import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reembed")

# ---------------------------------------------------------------------------
# ensure kazma_core is importable from the repo root
# ---------------------------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
if _root not in sys.path:
    sys.path.insert(0, _root)


def main() -> int:
    from kazma_core.memory.reembed import rebuild_embeddings

    started = time.monotonic()
    last = [0.0]

    def _progress(done: int, total: int) -> None:
        now = time.monotonic()
        if done == total or now - last[0] >= 5:
            last[0] = now
            logger.info("  %d/%d", done, total)

    try:
        summary = rebuild_embeddings(_progress)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1
    except RuntimeError as exc:
        logger.error("Nothing was changed: %s", exc)
        return 1

    logger.info(
        "Re-embed complete in %.1fs: %d memories, %d facts (model %s, %d skipped).",
        time.monotonic() - started,
        summary["episodes"],
        summary["beliefs"],
        summary["model"],
        summary["skipped_rows"],
    )
    logger.info("Restart Kazma now. The copy taken before the rebuild is memory_state.db.pre_reembed.")
    return 0


if __name__ == "__main__":
    # An entry point loads the install's .env itself (since 2026-09-22
    # importing kazma_core does not): without it this ran against the
    # stale SQLite settings and no vault key.
    from kazma_core.env_files import load_env_files

    load_env_files()
    sys.exit(main())
