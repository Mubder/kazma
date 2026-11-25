#!/usr/bin/env python
"""Which CI shard runs which test file.

CI's ``Tests`` job ran the whole suite on one 4-core runner (52 to 82 minutes)
and ``Playwright smoke`` every browser file one after another (34 minutes);
every other job takes 8 minutes or less. Both are now matrix shards
(``.github/workflows/ci.yml``), with an aggregate job that keeps the name
branch protection requires.

A file's shard depends only on its own path, the number of shards and the
committed table of measured durations (``scripts/ci_test_durations.json``):

* a file in the table goes where the greedy balance of the whole table puts it
  (longest first, each to the least loaded shard);
* a file that is not (a new one) goes to the shard its path's SHA-256 names.

So a shard can be computed for any file on any machine, adding or removing a
file moves no other file, and no file can be in two shards or in none. The
gate is ``tests/test_ci_shards.py``.

The table is refreshed from a CI run, whose shards upload what they measured::

    python scripts/ci_shards.py update <run id>      # needs gh
    python scripts/ci_shards.py show 6               # the balance it gives
    python scripts/ci_shards.py show 4 --kind e2e

and the browser shards are run through this file (one pytest process)::

    python scripts/ci_shards.py e2e --shard 2/4 tests/e2e/test_a.py ...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TABLE = Path(__file__).resolve().parent / "ci_test_durations.json"

#: The two kinds of shard, and what one browser test may take. The browser
#: steps had 180 to 900 s each; the longest browser test takes about 50 s.
KINDS = ("unit", "e2e")
E2E_TEST_TIMEOUT = 600


def load_table(kind: str, path: Path = TABLE) -> dict[str, float]:
    """The measured seconds of each file of *kind* (``unit`` or ``e2e``)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(name): float(seconds) for name, seconds in (data.get(kind) or {}).items()}


def balance(weights: dict[str, float], count: int) -> dict[str, int]:
    """Greedy balance: longest first, each to the least loaded of *count*
    bins; ties by path and by the lower bin, so the answer is one answer."""
    loads = [0.0] * count
    bins: dict[str, int] = {}
    for name in sorted(weights, key=lambda n: (-weights[n], n)):
        target = min(range(count), key=lambda i: (loads[i], i))
        bins[name] = target
        loads[target] += weights[name]
    return bins


def fallback_bin(name: str, count: int) -> int:
    """The bin of a file nobody has measured: a stable hash of its path."""
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big") % count


def assign(files: Iterable[str], count: int, kind: str, table: dict[str, float] | None = None) -> list[list[str]]:
    """*files* (repository paths with ``/``) dealt to *count* shards, each in
    the order given."""
    if count < 1:
        raise ValueError("at least one shard")
    table = load_table(kind) if table is None else table
    bins = balance(table, count)
    shards: list[list[str]] = [[] for _ in range(count)]
    for name in files:
        shards[bins[name] if name in bins else fallback_bin(name, count)].append(name)
    return shards


def parse_shard(text: str) -> tuple[int, int]:
    """``K/N``, shards counted from 1."""
    try:
        which, count = (int(part) for part in text.split("/"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not K/N: {text!r}") from None
    if not 1 <= which <= count:
        raise argparse.ArgumentTypeError(f"no shard {which} of {count}")
    return which, count


def durations_of(junit: Path) -> dict[str, float]:
    """Seconds per test file in a pytest ``xunit1`` report."""
    from xml.etree import ElementTree

    seconds: dict[str, float] = {}
    # The report is the one this run's own pytest wrote.
    for case in ElementTree.parse(junit).getroot().iter("testcase"):  # nosec B314 - our own pytest's report
        name = (case.get("file") or "").replace("\\", "/")
        if name:
            seconds[name] = seconds.get(name, 0.0) + float(case.get("time") or 0.0)
    return seconds


JUNIT_OPTIONS = ("-o", "junit_family=xunit1", "-o", "junit_duration_report=total")


def write_durations(path: Path, kind: str, seconds: dict[str, float]) -> None:
    text = json.dumps({kind: {k: round(v, 2) for k, v in sorted(seconds.items())}}, indent=1)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def run_e2e(shard: tuple[int, int], files: list[str], durations: Path | None) -> int:
    """Run this shard's browser files as one pytest process; its exit code."""
    missing = [name for name in files if not (REPO / name).is_file()]
    repeated = sorted({name for name in files if files.count(name) > 1})
    if missing or repeated:
        print(f"[ci-shards] files that do not exist: {missing}; named twice: {repeated}")
        return 2
    which, count = shard
    mine = assign(files, count, "e2e")[which - 1]
    print(f"[ci-shards] shard {which} of {count}: {len(mine)} of {len(files)} browser files", flush=True)
    if not mine:
        print("[ci-shards] this shard has no file: too many shards for the list")
        return 2
    with tempfile.TemporaryDirectory(prefix="kazma-e2e-shard-") as folder:
        report = Path(folder) / "junit.xml"
        code = subprocess.call([sys.executable, "-m", "pytest", *mine, "-q", f"--timeout={E2E_TEST_TIMEOUT}",
                                f"--junitxml={report}", *JUNIT_OPTIONS], cwd=str(REPO))
        if durations is not None and report.is_file():
            write_durations(durations, "e2e", durations_of(report))
    return code


def unit_files() -> list[str]:
    """The files the Tests shards run: exactly what ``fast_test.py`` collects."""
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.append(str(Path(__file__).resolve().parent))
    import fast_test

    return [f.relative_to(REPO).as_posix() for f in fast_test.discover_test_files()]


def e2e_files() -> list[str]:
    return sorted(f.relative_to(REPO).as_posix() for f in (REPO / "tests" / "e2e").glob("test_*.py"))


def update(run_id: str) -> int:
    """Merge the durations a CI run's shards uploaded into the table."""
    with tempfile.TemporaryDirectory(prefix="kazma-durations-") as folder:
        done = subprocess.run(["gh", "run", "download", run_id, "--pattern", "test-durations-*", "--dir", folder],
                              cwd=str(REPO))
        if done.returncode:
            return done.returncode
        data = json.loads(TABLE.read_text(encoding="utf-8"))
        measured: dict[str, dict[str, float]] = {kind: {} for kind in KINDS}
        for path in sorted(Path(folder).rglob("*.json")):
            for kind, seconds in json.loads(path.read_text(encoding="utf-8")).items():
                if kind in measured:
                    measured[kind].update({str(k): float(v) for k, v in seconds.items()})
    current = {"unit": set(unit_files()), "e2e": set(e2e_files())}
    for kind in KINDS:
        merged = {**(data.get(kind) or {}), **{k: max(v, 0.1) for k, v in measured[kind].items()}}
        # A file that is gone leaves the table here, and only here.
        data[kind] = {k: round(float(v), 1) for k, v in sorted(merged.items()) if k in current[kind]}
        print(f"[ci-shards] {kind}: {len(measured[kind])} measured, {len(data[kind])} in the table")
    data["measured"] = f"CI run {run_id}"
    TABLE.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return 0


def show(count: int, kind: str) -> int:
    files = unit_files() if kind == "unit" else e2e_files()
    table = load_table(kind)
    for n, names in enumerate(assign(files, count, kind, table), 1):
        seconds = sum(table.get(name, 0.0) for name in names)
        fresh = sum(1 for name in names if name not in table)
        print(f"shard {n}/{count}: {len(names)} files, {seconds:.0f} s measured, {fresh} not measured yet")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("e2e", help="run this shard of the browser files named")
    run.add_argument("--shard", type=parse_shard, required=True)
    run.add_argument("--durations-json", type=Path, default=None)
    run.add_argument("files", nargs="+")
    up = sub.add_parser("update", help="refresh the durations table from a CI run")
    up.add_argument("run_id")
    listing = sub.add_parser("show", help="the balance the table gives")
    listing.add_argument("count", type=int)
    listing.add_argument("--kind", choices=KINDS, default="unit")
    args = ap.parse_args(argv)
    if args.command == "e2e":
        return run_e2e(args.shard, args.files, args.durations_json)
    if args.command == "update":
        return update(args.run_id)
    return show(args.count, args.kind)


if __name__ == "__main__":
    raise SystemExit(main())
