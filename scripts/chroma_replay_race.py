"""Drill for the crash in Chroma's replay of a write log (KB-Q01, 2026-10-21).

The first read of a collection after a Chroma client starts applies the
collection's write log to the index Chroma loads from disk, inserting the
records on its own worker threads (rayon). That insert races in hnswlib: an
access violation in its graph search, about one client start in 5,000 with
chromadb 1.5.9 on Windows. Kazma runs Chroma with one insert thread
(``kazma_core.memory.chroma_local.serial_index_inserts``).

Each cycle builds the shape the Knowledge soak crashed on: a collection
whose index is made (and written empty) by one client, its log grown past
the index's capacity, then a NEW client loads that index and replays the
whole log on the first ``get``. ``--threads default`` is Chroma's own pool
(the crash); ``--threads 1`` is Kazma's. Run it after a chromadb upgrade:
the default should still crash (or the upgrade fixed it), one thread never.

Measured 2026-10-21 (24 logical CPUs, Windows 11, chromadb 1.5.9): default
5 crashes in about 20,600 cycles (and 1 in 7,562 fresh processes, each its
own client); one thread 0 in 28,000.

Usage::

    python scripts/chroma_replay_race.py --threads default --procs 6 --cycles 3000
    python scripts/chroma_replay_race.py --threads 1 --procs 6 --cycles 3000

Exit status 1 when any worker crashed. Each worker runs below normal
priority (Windows) and in its own scratch folder, removed at the end.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HNSW = {"space": "cosine", "max_neighbors": 48, "ef_construction": 400, "ef_search": 256}
DIM = 1024


def _worker(folder: Path, cycles: int, seed: int) -> None:
    import faulthandler

    faulthandler.enable()
    import chromadb
    from chromadb.config import Settings

    rng = random.Random(seed)
    words = [rng.randrange(1 << 30) for _ in range(300)]

    def vec() -> list[float]:
        v = [0.0] * DIM
        topic = rng.randrange(4)
        for k in range(32):
            v[k] = 1.0 if k % 4 == topic else 0.0
        for w in rng.sample(words, rng.randint(3, 25)):
            v[32 + w % (DIM - 32)] += 1.0 if (w >> 20) & 1 else -1.0
        n = sum(x * x for x in v) ** 0.5
        return [x / n for x in v]

    def client(path: Path) -> object:
        return chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))

    previous: Path | None = None
    for cycle in range(cycles):
        if previous is not None:
            shutil.rmtree(previous, ignore_errors=True)
        path = previous = folder / f"c{cycle}"
        path.mkdir(parents=True)
        first = client(path)
        col = first.get_or_create_collection(name=f"race{cycle}", configuration={"hnsw": dict(HNSW)},
                                             embedding_function=None)
        live: list[str] = []
        page = 0

        def write(pages: int) -> None:
            nonlocal page, live
            for _ in range(pages):
                ids = [f"p{page}-c{k}" for k in range(rng.randint(3, 14))]
                page += 1
                same = vec()
                col.upsert(ids=ids, embeddings=[same if rng.random() < 0.2 else vec() for _ in ids],
                           metadatas=[{"model": "m"} for _ in ids])
                live += ids
                if rng.random() < 0.4 and live:
                    gone = set(rng.sample(live, k=min(len(live), rng.randint(1, 8))))
                    col.delete(ids=sorted(gone))
                    live = [i for i in live if i not in gone]

        write(rng.randint(1, 3))
        col.get(include=["metadatas"], limit=10)  # the index is made, and written to disk empty
        write(rng.randint(8, 20))  # the log grows past the index's capacity, not applied
        first.close()
        second = client(path)  # a new client loads the empty index and replays the log
        got = second.get_collection(f"race{cycle}").get(include=["metadatas"], limit=5000)
        if len(got["ids"]) != len(set(live)):
            raise SystemExit(f"cycle {cycle}: {len(got['ids'])} records read back, {len(set(live))} written")
        second.close()
        if cycle % 250 == 0:
            print(f"cycle {cycle}", flush=True)
    print(f"done {cycles}", flush=True)


def _below_normal(pid: int) -> None:
    if os.name != "nt":
        return
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x0200 | 0x0400, False, pid)  # SET_INFORMATION | QUERY
    if handle:
        ctypes.windll.kernel32.SetPriorityClass(handle, 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS
        ctypes.windll.kernel32.CloseHandle(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--threads", choices=("default", "1"), required=True,
                        help="Chroma's insert threads: its own pool, or one (Kazma's)")
    parser.add_argument("--procs", type=int, default=6)
    parser.add_argument("--cycles", type=int, default=3000, help="client reopens per worker")
    parser.add_argument("--seed", type=int, default=20261021)
    parser.add_argument("--worker", nargs=3, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        _worker(Path(args.worker[0]), int(args.worker[1]), int(args.worker[2]))
        return 0
    env = dict(os.environ)
    env.pop("RAYON_NUM_THREADS", None)
    if args.threads == "1":
        env["RAYON_NUM_THREADS"] = "1"
    scratch = Path(tempfile.mkdtemp(prefix="chroma-replay-race-"))
    started = time.time()
    workers = []
    for i in range(args.procs):
        cmd = [sys.executable, "-I", str(Path(__file__).resolve()), "--threads", args.threads,
               "--worker", str(scratch / f"w{i}"), str(args.cycles), str(args.seed + i)]
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        _below_normal(proc.pid)
        workers.append(proc)
    crashed = 0
    for i, proc in enumerate(workers):
        out, err = proc.communicate()
        last = (out.strip().splitlines() or ["(nothing)"])[-1]
        if proc.returncode == 0:
            print(f"worker {i}: {last}")
            continue
        crashed += 1
        print(f"worker {i}: exit {proc.returncode} after {last!r}")
        print("\n".join(err.strip().splitlines()[:8]))
    shutil.rmtree(scratch, ignore_errors=True)
    print(f"threads {args.threads}: {crashed} of {args.procs} workers crashed, "
          f"{time.time() - started:.0f} s")
    return 1 if crashed else 0


if __name__ == "__main__":
    raise SystemExit(main())
