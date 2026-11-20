#!/usr/bin/env python3
"""Run the tests a CI Postgres job claims -- and fail when one it names skips.

Usage (repo root, with KAZMA_DB_BACKEND=postgres, KAZMA_DATABASE_URL and
KAZMA_TEST_ALLOW_REAL_DB=1 set):
    python scripts/postgres_suite.py                     # the Postgres job
    python scripts/postgres_suite.py --marker pgvector   # the pgvector job
    python scripts/postgres_suite.py --list              # the files it would run

Two jobs, one marker each:

* ``postgres`` -- verified against a real Postgres; the "Tests (Postgres
  backend)" job runs these on a plain ``postgres:18``, the server live runs.
* ``pgvector`` -- needs the pgvector extension; the "Tests (pgvector)" job
  runs these on a Postgres that ships it (benchmark audit R7, the owner's
  decision 5). A test may carry both: it then runs in both jobs (a probe that
  says "no pgvector here" on one and round-trips a vector on the other).

The job used to run a list of file names kept in ci.yml, far from the tests,
with each addition justified in a comment there. The marker puts the claim
where it belongs: a test that has been verified against a real Postgres says
so itself (``pytestmark = pytest.mark.postgres`` for a whole file, or the
decorator on one test), and this script finds it. Only files that carry the
marker are handed to pytest, so an unrelated module with an optional import
cannot fail the job at collection.

**A named test that skips fails the job** (:data:`MUST_RUN`, benchmark audit
R7 and R11). A test that skips is green, so a test that skipped on every run
-- the pgvector benchmark on a server without pgvector, the Postgres-primary
benchmark in a job without numpy -- proved nothing while the docs quoted it.
Each job names the tests it exists for; one of them skipping, failing or not
running at all fails the job, saying which and why.

The bar for adding the marker is unchanged: run the test against a real,
throwaway Postgres first (docker run --rm -p 127.0.0.1:55432:5432 postgres:18;
``pgvector/pgvector:pg18`` for the pgvector marker) and see it pass. A
SQLite-shaped assertion that cannot hold on Postgres is not a Postgres
failure, and a job that is red on day one gets ignored.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MARKER = "postgres"
MARKERS = ("postgres", "pgvector")

#: The tests each job exists for: one that skips, fails or does not run fails
#: the job.
MUST_RUN: dict[str, tuple[str, ...]] = {
    "postgres": (
        # The Postgres-primary path held to local (AGENTS.md 15K quotes it).
        "tests/test_memory_pg_primary.py::test_the_benchmark_with_postgres_primary_is_within_the_stated_delta",
        # No pgvector on this server: nothing is created.
        "tests/test_vector_store_probe.py::test_a_real_postgres_is_used_only_if_it_can_hold_vectors",
    ),
    "pgvector": (
        "tests/test_memory_benchmark_pg.py::test_the_benchmark_on_pgvector_exact_and_approximate",
        # pgvector here: a vector round-trips; a table of another size is refused.
        "tests/test_vector_store_probe.py::test_a_real_postgres_is_used_only_if_it_can_hold_vectors",
        "tests/test_vector_store_probe.py::test_a_real_table_of_another_size_is_refused_before_writing",
    ),
}


def _testpaths() -> list[Path]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    paths = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths", ["tests"])
    return [ROOT / p for p in paths]


def carries_marker(source: str, marker: str = MARKER) -> bool:
    """True when the module applies ``pytest.mark.<marker>`` anywhere."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == marker
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
        ):
            return True
    return False


def marked_files(roots: list[Path] | None = None, marker: str = MARKER) -> list[Path]:
    out: set[Path] = set()
    for base in roots or _testpaths():
        if not base.is_dir():
            continue
        for path in base.rglob("test_*.py"):
            if "e2e" in path.parts or "__pycache__" in path.parts:
                continue
            if carries_marker(path.read_text(encoding="utf-8", errors="replace"), marker):
                out.add(path)
    return sorted(out)


def _node_id(case: Any) -> str:
    """``tests/test_x.py::test_name`` of a JUnit ``testcase`` (parameters
    dropped: a named test is named without them)."""
    parts = (case.get("classname") or "").split(".")
    name = (case.get("name") or "").split("[")[0]
    # pytest writes "tests.test_x" (or "tests.test_x.TestClass").
    for cut in range(len(parts), 0, -1):
        candidate = "/".join(parts[:cut]) + ".py"
        if (ROOT / candidate).is_file():
            return f"{candidate}::{name}"
    return f"{'/'.join(parts)}.py::{name}"


_RANK = {"passed": 0, "skipped": 1, "failed": 2}


def named_problems(junit_xml: str, named: tuple[str, ...]) -> list[str]:
    """Why the job failed its named tests (empty when every one ran and
    passed): skipped, failed, or not run at all."""
    from kazma_core.security.safe_xml import parse_untrusted_xml

    outcome: dict[str, str] = {}
    reason: dict[str, str] = {}
    for case in parse_untrusted_xml(junit_xml).iter("testcase"):
        node = _node_id(case)
        skipped = case.find("skipped")
        if skipped is not None:
            state = "skipped"
            reason[node] = (skipped.get("message") or "").strip()
        elif case.find("failure") is not None or case.find("error") is not None:
            state = "failed"
        else:
            state = "passed"
        # A parametrized test passes only when every one of its cases does.
        if _RANK[state] >= _RANK[outcome.get(node, "passed")]:
            outcome[node] = state
    problems = []
    for node in named:
        state = outcome.get(node)
        if state is None:
            problems.append(f"{node} did not run: this job exists to run it (renamed? unmarked?)")
        elif state == "skipped":
            problems.append(f"{node} skipped ({reason.get(node) or 'no reason'}): a named test must run here")
        elif state == "failed":
            problems.append(f"{node} failed")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--list", action="store_true", help="print the marked files and exit")
    parser.add_argument("--marker", choices=MARKERS, default=MARKER,
                        help="which job: postgres (a plain Postgres) or pgvector")
    args, passthrough = parser.parse_known_args(argv)
    files = marked_files(marker=args.marker)
    if args.list:
        for f in files:
            print(f.relative_to(ROOT).as_posix())
        return 0
    if not files:
        print(f"no test carries @pytest.mark.{args.marker} -- refusing to report an empty pass")
        return 1
    with tempfile.TemporaryDirectory(prefix="kazma-pg-suite-") as tmp:
        report = Path(tmp) / "junit.xml"
        cmd = [sys.executable, "-m", "pytest", "-q", "--timeout=300", "-m", args.marker, "-rs",
               f"--junitxml={report}", *passthrough, *(str(f.relative_to(ROOT)) for f in files)]
        print(f"[postgres-suite] {len(files)} files carry the {args.marker} marker", flush=True)
        # pytest exits 5 when the marker selects nothing: that fails the job too.
        code = subprocess.call(cmd, cwd=str(ROOT))
        problems = (named_problems(report.read_text(encoding="utf-8"), MUST_RUN[args.marker])
                    if report.is_file() else ["pytest wrote no report: the named tests cannot be checked"])
    for line in problems:
        print(f"[postgres-suite] {line}", flush=True)
    return (code or 1) if problems else code


if __name__ == "__main__":
    sys.exit(main())
