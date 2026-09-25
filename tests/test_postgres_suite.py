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
import sys
import textwrap
from pathlib import Path

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
    return ci_text.split("\n  postgres:\n", 1)[1].split("\n  unified-turn-lifecycle:", 1)[0]


def test_the_ci_job_runs_the_marked_suite():
    job = _postgres_job(CI.read_text(encoding="utf-8"))
    assert "run: python scripts/postgres_suite.py" in job
    assert "tests/test_" not in job.split("run: python scripts/postgres_suite.py", 1)[1], (
        "a hand-kept file list is back in the Postgres job -- mark the tests instead"
    )
    assert 'KAZMA_TEST_ALLOW_REAL_DB: "1"' in job, "without it conftest pins SQLite"


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


def _bare_psycopg_imports(sources: dict[str, str]) -> list[str]:
    """``import psycopg`` in a test: the main Tests job installs ``.[test]``,
    which does not include psycopg, so the test errors there instead of
    skipping (CI 2026-09-25, two restore-rehearsal tests)."""
    hits: list[str] = []
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n == "psycopg" or n.startswith("psycopg.") for n in names):
                hits.append(f"{rel}:{node.lineno}")
    return sorted(hits)


def test_tests_import_psycopg_only_through_importorskip():
    hits = _bare_psycopg_imports(_test_sources())
    assert not hits, (
        "A test imports psycopg directly. The main Tests job does not install it,\n"
        "so the test ERRORS there; use `psycopg = pytest.importorskip(\"psycopg\")`\n"
        "(and @pytest.mark.postgres if it should run in the Postgres job):\n  "
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
