#!/usr/bin/env python3
"""Measure how far a full store's background sits above a small store's mean
(``recall._TAIL_OFFSET``, 2026-11-21).

For each benchmark question (both sets) and each kind (turns, facts): the
full store's background -- the mean of meaning ranks 4-15 over every memory
of the kind, with the recorded vectors -- against the mean similarity of a
16-memory draw of the same store (five draws a question, seed 7). Prints the
offset's median and percentiles; recall uses the turns' 5th percentile, so a
small store drops only what even a generous full-store judgement would. The
facts' offset is printed for comparison and not used (see the comment above
``_TAIL_OFFSET``).

    .venv/Scripts/python.exe scripts/small_store_offsets.py

No model is loaded (the recorded vectors are replayed); nothing is written.
"""

from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "tests" / "fixtures" / "memory_bench" / "vectors.npz"
DRAWS = 5
DRAW_SIZE = 16
SEED = 7


def _unit(vector) -> np.ndarray:  # noqa: F821 - numpy imported in main
    import numpy as np

    v = np.asarray(vector, dtype=np.float32)
    return v / (np.linalg.norm(v) or 1.0)


def main() -> int:
    import numpy as np
    from kazma_core.memory import benchmark
    from kazma_core.memory.dual_write import episode_row
    from kazma_core.memory.speakers import fact_vector_text

    replay = benchmark.ReplayEmbedder(VECTORS)
    data = benchmark.load_dataset()
    turns = []
    for session in data["sessions"]:
        for i, turn in enumerate(session["turns"], 1):
            row = episode_row(session_id="s", turn_number=i, user_text=turn["user"],
                              assistant_text=turn["assistant"], tenant_id="t", source="b", created_at=0)
            if row["embed_text"]:
                turns.append(_unit(replay.encode(row["embed_text"])))
    facts = [_unit(replay.encode(fact_vector_text(f["subject"], f["predicate"], f["object"])))
             for f in data.get("facts", [])]
    rng = random.Random(SEED)
    for kind, rows in (("episode", np.vstack(turns)), ("belief", np.vstack(facts))):
        offsets = []
        for question in list(data["questions"]) + list(data.get("second") or []):
            sims = rows @ _unit(replay.encode(question["text"]))
            full = float(np.mean(np.sort(sims)[::-1][3:15]))
            for _ in range(DRAWS):
                draw = sims[rng.sample(range(len(sims)), min(DRAW_SIZE, len(sims)))]
                offsets.append(full - float(np.mean(draw)))
        q = statistics.quantiles(offsets, n=20)
        print(f"{kind}: {len(rows)} memories; offset median {statistics.median(offsets):.3f}, "
              f"p5 {q[0]:.3f}, p25 {q[4]:.3f}, p75 {q[14]:.3f}, p95 {q[-1]:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
