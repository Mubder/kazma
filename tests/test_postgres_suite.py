"""The Postgres job runs what the tests themselves claim, and nothing leaks in.

Two rules, one per incident class:

1. **The marker is the list.** A test verified against a real Postgres carries
   ``@pytest.mark.postgres``; ``scripts/postgres_suite.py`` runs exactly those,
   and the CI job runs the script. The job used to hold a list of file names
   in ci.yml, which let a file be all-or-nothing and kept the claim far from
   the test that makes it.

2. **No test loads a real ``.env``.** Two Postgres tests parsed the working
   directory's ``.env`` into ``os.environ`` -- one also the operator's LIVE
   install ``.env``, by hard-coded path, with ``override=True``. On the dev box
   that flipped the backend to SQLite for every later test in the process
   (found 2026-09-25, when the marker changed the job's file order and
   ``TaskStore()`` stopped seeing Postgres); on a box whose ``.env`` names a
   live database it would have pointed the suite at it. The root conftest's
   dotenv stub is a guard, not a licence.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
CI = REPO / ".github" / "workflows" / "ci.yml"


def _suite():
    name = "_kazma_postgres_suite"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "postgres_suite.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── 1. the marker is the list ─────────────────────────────────────────────


def _postgres_job(ci_text: str) -> str:
    """The Postgres job's text, up to the next job (whichever it is)."""
    rest = ci_text.split("\n  postgres:\n", 1)[1]
    return re.split(r"\n  [a-z0-9][a-z0-9_-]*:\n", rest, maxsplit=1)[0]


def test_the_ci_job_runs_the_marked_suite():
    job = _postgres_job(CI.read_text(encoding="utf-8"))
    assert "run: python scripts/postgres_suite.py" in job
    assert "tests/test_" not in job.split("run: python scripts/postgres_suite.py", 1)[1], (
        "a hand-kept file list is back in the Postgres job -- mark the tests instead"
    )
    assert 'KAZMA_TEST_ALLOW_REAL_DB: "1"' in job, "without it conftest pins SQLite"


SUITE = "python scripts/postgres_suite.py"


#: What the armed run sets apart from the unarmed one: its database and the lock.
_ARMED_RUN_OWN = ("KAZMA_DATABASE_URL", "KAZMA_TEST_SECOND_LOCK")


def armed_run_problems(job: dict) -> list[str]:
    """Why the Postgres job does not also run the suite with Spaces' second
    lock armed (S4-01 step 5 review M2): a second run of the script with
    ``KAZMA_TEST_SECOND_LOCK=armed``, on a database of its own made by an
    earlier step, run even when the unarmed run failed, in the same
    environment but for its database and the lock (delta review N4: without
    KAZMA_DB_BACKEND the self-check skipped and the armed run went green)."""
    steps = job.get("steps") or []
    runs = [s for s in steps if str(s.get("run") or "").strip() == SUITE]
    plain = [s for s in runs if "KAZMA_TEST_SECOND_LOCK" not in (s.get("env") or {})]
    armed = [s for s in runs if str((s.get("env") or {}).get("KAZMA_TEST_SECOND_LOCK") or "").lower() == "armed"]
    problems = [] if plain else ["the suite does not run unarmed"]
    if not armed:
        return [*problems, "the suite does not run armed"]
    for step in armed:
        env = step.get("env") or {}
        url = str(env.get("KAZMA_DATABASE_URL") or "")
        if env.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
            problems.append("the armed run lacks KAZMA_TEST_ALLOW_REAL_DB: conftest pins SQLite")
        for other in plain:
            theirs = {k: v for k, v in (other.get("env") or {}).items() if k not in _ARMED_RUN_OWN}
            mine = {k: v for k, v in env.items() if k not in _ARMED_RUN_OWN}
            differ = sorted(k for k in set(mine) | set(theirs) if mine.get(k) != theirs.get(k))
            if differ:
                problems.append("the armed run's environment differs from the unarmed run's: " + ", ".join(differ))
        if url in {str((p.get("env") or {}).get("KAZMA_DATABASE_URL") or "") for p in plain}:
            problems.append("the armed run reuses the unarmed run's database")
        made = steps[:steps.index(step)]
        if not any(f"CREATE DATABASE {url.rsplit('/', 1)[-1]}" in str(s.get("run") or "") for s in made):
            problems.append("no earlier step makes the armed run's database")
        if "!cancelled()" not in str(step.get("if") or ""):
            problems.append("an unarmed failure skips the armed run")
    return problems


def _ci_postgres_job() -> dict:
    import yaml

    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["postgres"]


def test_the_ci_job_runs_the_suite_armed_too():
    assert armed_run_problems(_ci_postgres_job()) == []


def test_control_each_armed_run_rule_finds_what_it_is_for():
    job = _ci_postgres_job()
    steps = job["steps"]
    armed = next(s for s in steps if (s.get("env") or {}).get("KAZMA_TEST_SECOND_LOCK"))
    made = next(s for s in steps if "CREATE DATABASE" in str(s.get("run") or ""))
    plain_url = next(s for s in steps if str(s.get("run") or "").strip() == SUITE)["env"]["KAZMA_DATABASE_URL"]

    def _changed(step: dict, **change: Any) -> dict:
        return {**job, "steps": [{**s, **change} if s is step else s for s in steps]}

    assert armed_run_problems({**job, "steps": [s for s in steps if s is not armed]}) == [
        "the suite does not run armed"]
    assert armed_run_problems({**job, "steps": [s for s in steps if s is not made]}) == [
        "no earlier step makes the armed run's database"]
    assert armed_run_problems(_changed(armed, env={**armed["env"], "KAZMA_DATABASE_URL": plain_url})) == [
        "the armed run reuses the unarmed run's database", "no earlier step makes the armed run's database"]
    assert armed_run_problems(_changed(armed, env={**armed["env"], "KAZMA_TEST_ALLOW_REAL_DB": "0"})) == [
        "the armed run lacks KAZMA_TEST_ALLOW_REAL_DB: conftest pins SQLite",
        "the armed run's environment differs from the unarmed run's: KAZMA_TEST_ALLOW_REAL_DB"]
    # The delta review's probe (N4): the backend dropped from the armed step only.
    no_backend = {k: v for k, v in armed["env"].items() if k != "KAZMA_DB_BACKEND"}
    assert armed_run_problems(_changed(armed, env=no_backend)) == [
        "the armed run's environment differs from the unarmed run's: KAZMA_DB_BACKEND"]
    without_if = {k: v for k, v in armed.items() if k != "if"}
    assert armed_run_problems({**job, "steps": [without_if if s is armed else s for s in steps]}) == [
        "an unarmed failure skips the armed run"]


def test_the_armed_self_check_cannot_skip():
    """Delta review N4: every test of tests/test_pg_armed_is_armed.py is one
    the Postgres job exists for, so a skip fails the job (MUST_RUN); the
    tests are read from the file, so a new one cannot be left out."""
    source = (REPO / "tests" / "test_pg_armed_is_armed.py").read_text(encoding="utf-8")
    tests = [n.name for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    named = set(_suite().MUST_RUN["postgres"])
    assert len(tests) >= 5
    assert [t for t in tests if f"tests/test_pg_armed_is_armed.py::{t}" not in named] == []


def test_the_marker_is_registered():
    import tomllib

    markers = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("postgres:") for m in markers)


def test_the_marked_suite_is_not_empty_and_keeps_its_anchors():
    files = {p.name for p in _suite().marked_files()}
    assert len(files) >= 20
    for anchor in ("test_pg_backup.py", "test_pg_nul_safe.py", "test_session_spool.py",
                   "test_atomic_update_does_not_deadlock.py", "test_diagnostics_are_read_only.py"):
        assert anchor in files, f"{anchor} lost its postgres marker"


def _job(ci_text: str, name: str) -> str:
    rest = ci_text.split(f"\n  {name}:\n", 1)[1]
    return re.split(r"\n  [a-z0-9][a-z0-9_-]*:\n", rest, maxsplit=1)[0]


def test_a_second_job_runs_the_pgvector_tests():
    """The owner's decision 5: a second service and job, beside the plain
    postgres:18 live runs."""
    ci = CI.read_text(encoding="utf-8")
    plain, vector = _job(ci, "postgres"), _job(ci, "pgvector")
    assert "image: postgres:18" in plain and "pgvector" not in plain.split("steps:")[0]
    assert re.search(r"image: pgvector/pgvector:[\w.-]+@sha256:[0-9a-f]{64}\n", vector), "pin it by digest"
    # -rP: the benchmark's measurement (both search modes) is printed in the log.
    assert "run: python scripts/postgres_suite.py --marker pgvector -rP" in vector
    assert 'KAZMA_TEST_ALLOW_REAL_DB: "1"' in vector
    # numpy: without it the recorded-vector benchmarks skipped (R11).
    for job in (plain, vector):
        assert "groups: ci-light" in job


def test_the_pgvector_marker_is_registered_and_used():
    import tomllib

    markers = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("pgvector:") for m in markers)
    assert {p.name for p in _suite().marked_files(marker="pgvector")} >= {
        "test_memory_benchmark_pg.py", "test_vector_store_probe.py"}


def _markers_of(path: Path, name: str) -> set[str]:
    """The pytest marks on test *name* in *path*: its decorators and the
    module's ``pytestmark``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    marks: set[str] = set()

    def collect(node: ast.AST) -> None:
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Attribute)
                    and sub.value.attr == "mark"):
                marks.add(sub.attr)

    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pytestmark" for t in node.targets):
            collect(node.value)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            for deco in node.decorator_list:
                collect(deco)
            return marks
    raise AssertionError(f"{path.name} defines no {name}")


def test_every_named_test_exists_and_carries_its_jobs_marker():
    for marker, named in _suite().MUST_RUN.items():
        assert named, marker
        for node in named:
            rel, name = node.split("::")
            path = REPO / rel
            assert path.is_file(), node
            assert marker in _markers_of(path, name), f"{node} is named by the {marker} job but not marked"


def _junit(*cases: str) -> str:
    return f'<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite>{"".join(cases)}</testsuite></testsuites>'


def test_a_named_test_that_skips_fails_the_job():
    named = ("tests/test_memory_benchmark_pg.py::test_the_benchmark_on_postgres_primary",)
    problems = _suite().named_problems
    ran = '<testcase classname="tests.test_memory_benchmark_pg" name="test_the_benchmark_on_postgres_primary"/>'
    skipped = ('<testcase classname="tests.test_memory_benchmark_pg" name="test_the_benchmark_on_postgres_primary">'
               '<skipped message="pgvector is not available on this database"/></testcase>')
    failed = ('<testcase classname="tests.test_memory_benchmark_pg" name="test_the_benchmark_on_postgres_primary">'
              '<failure message="x"/></testcase>')
    assert problems(_junit(ran), named) == []
    assert "skipped (pgvector is not available" in problems(_junit(skipped), named)[0]
    assert problems(_junit(failed), named)[0].endswith("failed")
    assert "did not run" in problems(_junit(), named)[0]
    # A parametrized test passes only when every case does.
    param = ('<testcase classname="tests.test_memory_benchmark_pg" name="test_the_benchmark_on_postgres_primary[a]"/>'
             '<testcase classname="tests.test_memory_benchmark_pg" name="test_the_benchmark_on_postgres_primary[b]">'
             '<skipped message="b"/></testcase>')
    assert "skipped" in problems(_junit(param), named)[0]


def test_pytest_alone_calls_a_skip_green(tmp_path):
    """Negative control: the job's old pass condition, pytest's exit code, is
    0 when the only test it exists for skips; the named check is not."""
    import subprocess

    (tmp_path / "test_planted.py").write_text(
        "import pytest\n\ndef test_needs_pgvector():\n    pytest.skip('no pgvector here')\n", encoding="utf-8")
    report = tmp_path / "junit.xml"
    code = subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            f"--junitxml={report}", "test_planted.py"], cwd=str(tmp_path),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert code == 0
    problems = _suite().named_problems(report.read_text(encoding="utf-8"),
                                       ("test_planted.py::test_needs_pgvector",))
    assert problems and "skipped (no pgvector here)" in problems[0]


def test_the_marker_detector_sees_both_spellings():
    detect = _suite().carries_marker
    assert detect("import pytest\npytestmark = pytest.mark.postgres\n")
    assert detect("import pytest\n@pytest.mark.postgres\ndef test_x():\n    pass\n")
    assert detect("import pytest\npytestmark = [pytest.mark.slow, pytest.mark.postgres]\n")
    assert not detect("import pytest\n# pytest.mark.postgres in a comment\n")
    assert not detect("import pytest\npytestmark = pytest.mark.slow\n")


# ── 2. no test loads a real .env ──────────────────────────────────────────

_ROOTISH = {"REPO", "ROOT", "REPO_ROOT", "PROJECT_ROOT", "_REPO", "_ROOT", "_REPO_ROOT", "BASE_DIR"}
#: The loader's own tests plant .env files in tmp dirs and call it on purpose.
_LOADER_TESTS = {"tests/conftest.py", "tests/test_env_loading.py"}


def _real_env_reads(sources: dict[str, str]) -> list[str]:
    hits: list[str] = []
    for rel, text in sources.items():
        if rel in _LOADER_TESTS:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                and isinstance(node.right, ast.Constant) and node.right.value == ".env"
            ):
                left = ast.unparse(node.left)
                if ("Path.cwd()" in left or "getcwd()" in left or "__file__" in left
                        or (isinstance(node.left, ast.Name) and node.left.id in _ROOTISH)
                        or any(isinstance(c, ast.Constant) and isinstance(c.value, str)
                               and (c.value[1:3] == ":/" or c.value[1:3] == ":\\" or c.value.startswith("/home/"))
                               for c in ast.walk(node.left))):
                    hits.append(f"{rel}:{node.lineno} {ast.unparse(node)}")
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name in ("load_dotenv", "dotenv_values"):
                    hits.append(f"{rel}:{node.lineno} {name}(...)")
    return sorted(hits)


def _test_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for root in [REPO / "tests", *sorted(REPO.glob("kazma-*/*tests"))]:
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            out[p.relative_to(REPO).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    return out


def test_no_test_loads_a_real_env_file():
    hits = _real_env_reads(_test_sources())
    assert not hits, (
        "A test reads a real .env (the checkout's, or an install's). Tests get their\n"
        "database from the ENVIRONMENT -- the CI job, or a deliberate run against a\n"
        "throwaway container -- never from a file that may name a live one:\n  "
        + "\n  ".join(hits)
    )


def test_the_env_gate_sees_each_shape():
    planted = {
        "t.py": textwrap.dedent(
            '''
            import os
            from pathlib import Path
            from dotenv import load_dotenv
            def a():
                return (Path.cwd() / ".env").read_text()
            def b():
                load_dotenv(dotenv_path="x", override=True)
            def c():
                return Path("C:/Users/someone/kazma") / ".env"
            def fine(tmp_path):
                (tmp_path / ".env").write_text("K=V")
            '''
        )
    }
    assert _real_env_reads(planted) == [
        "t.py:10 Path('C:/Users/someone/kazma') / '.env'",
        "t.py:6 Path.cwd() / '.env'",
        "t.py:8 load_dotenv(...)",
    ]


# ── 3. a test reaches psycopg only through importorskip ───────────────────


_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError"})


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(k, ast.Name) and k.id in _IMPORT_ERRORS for k in kinds)


def _guarded_nodes(tree: ast.AST) -> set[int]:
    """The nodes inside a ``try`` whose handler catches ImportError."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(_catches_import_error(h) for h in node.handlers):
            for statement in node.body:
                inside.update(id(n) for n in ast.walk(statement))
    return inside


def _skips_by_mark(tree: ast.Module) -> bool:
    """A top-level ``pytestmark`` holding a ``skipif``: the module is collected and skipped."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark"
                                                for t in node.targets):
            return any(isinstance(n, ast.Attribute) and n.attr == "skipif" for n in ast.walk(node.value))
    return False


def _bare_psycopg_imports(sources: dict[str, str]) -> list[str]:
    """``import psycopg`` in a test: the main Tests job installs ``.[test]``,
    which does not include psycopg, so the test errors there instead of
    skipping (CI 2026-09-25, two restore-rehearsal tests).

    An import inside ``try: ... except ImportError`` is not bare when the
    module also skips itself by a ``pytestmark`` ``skipif``: that is how a
    module is collected everywhere and skipped where psycopg is missing
    (tests/test_postgres_tests_always_collected.py, 2026-11-27). Without the
    mark its tests would fail on the missing module instead of skipping."""
    hits: list[str] = []
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        guarded = _guarded_nodes(tree) if _skips_by_mark(tree) else set()
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n == "psycopg" or n.startswith("psycopg.") for n in names) and id(node) not in guarded:
                hits.append(f"{rel}:{node.lineno}")
    return sorted(hits)


def test_tests_import_psycopg_only_through_importorskip():
    hits = _bare_psycopg_imports(_test_sources())
    assert not hits, (
        "A test imports psycopg directly. The main Tests job does not install it,\n"
        "so the test ERRORS there; use `psycopg = pytest.importorskip(\"psycopg\")` in a\n"
        "test or fixture, or at module level `try: import psycopg` / `except ImportError:\n"
        "psycopg = None` with `pytestmark = [pytest.mark.postgres, pytest.mark.skipif(psycopg\n"
        "is None, ...)]` (never a module-level importorskip: see\n"
        "test_postgres_tests_always_collected.py):\n  "
        + "\n  ".join(hits)
    )


def test_the_psycopg_gate_sees_both_import_forms():
    planted = {
        "t.py": textwrap.dedent(
            '''
            import pytest
            def a():
                import psycopg
            def b():
                from psycopg import sql
            def fine():
                psycopg = pytest.importorskip("psycopg")
            '''
        )
    }
    assert _bare_psycopg_imports(planted) == ["t.py:4", "t.py:6"]


def test_a_guarded_import_passes_only_with_a_skip_mark():
    guarded = textwrap.dedent(
        '''
        import pytest
        try:
            import psycopg
        except ImportError:
            psycopg = None
        '''
    )
    marked = guarded + 'pytestmark = [pytest.mark.postgres, pytest.mark.skipif(psycopg is None, reason="x")]\n'
    assert _bare_psycopg_imports({"t.py": marked}) == []
    # Controls: no skip mark, so its tests fail on None; a handler that is not ImportError.
    assert _bare_psycopg_imports({"t.py": guarded}) == ["t.py:4"]
    other = marked.replace("except ImportError:", "except ValueError:")
    assert _bare_psycopg_imports({"t.py": other}) == ["t.py:4"]
