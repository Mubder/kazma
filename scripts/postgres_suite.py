#!/usr/bin/env python3
"""Run every test marked ``@pytest.mark.postgres`` -- the CI Postgres job.

Usage (repo root, with KAZMA_DB_BACKEND=postgres, KAZMA_DATABASE_URL and
KAZMA_TEST_ALLOW_REAL_DB=1 set):
    python scripts/postgres_suite.py            # run the marked tests
    python scripts/postgres_suite.py --list     # print the files it would run

The job used to run a list of file names kept in ci.yml, far from the tests,
with each addition justified in a comment there. The marker puts the claim
where it belongs: a test that has been verified against a real Postgres says
so itself (``pytestmark = pytest.mark.postgres`` for a whole file, or the
decorator on one test), and this script finds it. Only files that carry the
marker are handed to pytest, so an unrelated module with an optional import
cannot fail the job at collection.

The bar for adding the marker is unchanged: run the test against a real,
throwaway Postgres first (docker run --rm -p 55432:5432 postgres:16) and see
it pass. A SQLite-shaped assertion that cannot hold on Postgres is not a
Postgres failure, and a job that is red on day one gets ignored.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "postgres"


def _testpaths() -> list[Path]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    paths = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths", ["tests"])
    return [ROOT / p for p in paths]


def carries_marker(source: str) -> bool:
    """True when the module applies ``pytest.mark.postgres`` anywhere."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == MARKER
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
        ):
            return True
    return False


def marked_files(roots: list[Path] | None = None) -> list[Path]:
    out: set[Path] = set()
    for base in roots or _testpaths():
        if not base.is_dir():
            continue
        for path in base.rglob("test_*.py"):
            if "e2e" in path.parts or "__pycache__" in path.parts:
                continue
            if carries_marker(path.read_text(encoding="utf-8", errors="replace")):
                out.add(path)
    return sorted(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--list", action="store_true", help="print the marked files and exit")
    args, passthrough = parser.parse_known_args(argv)
    files = marked_files()
    if args.list:
        for f in files:
            print(f.relative_to(ROOT).as_posix())
        return 0
    if not files:
        print("no test carries @pytest.mark.postgres -- refusing to report an empty pass")
        return 1
    cmd = [sys.executable, "-m", "pytest", "-q", "--timeout=300", "-m", MARKER,
           *passthrough, *(str(f.relative_to(ROOT)) for f in files)]
    print(f"[postgres-suite] {len(files)} files carry the marker", flush=True)
    # pytest exits 5 when the marker selects nothing: that fails the job too.
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    sys.exit(main())
