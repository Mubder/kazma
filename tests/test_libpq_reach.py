"""psycopg reaches Postgres when Windows refuses its bundled client library.

Live 2026-11-26, 11:30 UTC: an Application Control policy began refusing
psycopg_binary's ``pq`` module; the reload after it could not load any
libpq, and Kazma started read-only with the database up. ``kazma_core``'s
import now adds an installed PostgreSQL's ``bin`` (its signed ``libpq.dll``)
to PATH when the bundled library is refused, so psycopg's pure-Python
wrapper finds a library Windows allows.
"""

from __future__ import annotations

import ast
import logging
import os
import sys
from pathlib import Path

import pytest
from kazma_core import libpq_reach

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def windows(tmp_path, monkeypatch):
    """A Windows with psycopg_binary and PostgreSQL 16 and 18 installed; nothing added yet."""
    pyd = tmp_path / "site" / "psycopg_binary" / "pq.cp314-win_amd64.pyd"
    pyd.parent.mkdir(parents=True)
    pyd.write_bytes(b"")
    bins = []
    for major in ("16", "18"):
        folder = tmp_path / "PostgreSQL" / major / "bin"
        folder.mkdir(parents=True)
        (folder / "libpq.dll").write_bytes(b"")
        bins.append(folder)
    monkeypatch.setattr(libpq_reach, "_on_windows", lambda: True)
    monkeypatch.setattr(libpq_reach, "_added", None)
    monkeypatch.setattr(libpq_reach, "_bundled", lambda: [pyd])
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramW6432", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "elsewhere"))
    return bins


def test_a_refused_bundled_library_gives_way_to_the_newest_postgres(windows, monkeypatch, caplog):
    monkeypatch.setattr(libpq_reach, "_refused", lambda path: True)
    caplog.set_level(logging.WARNING, logger=libpq_reach.__name__)
    assert libpq_reach.reach_libpq() == windows[1]  # 18, not 16
    assert os.environ["PATH"].split(os.pathsep)[-1] == str(windows[1])
    assert any("Application Control" in r.getMessage() for r in caplog.records)
    before = os.environ["PATH"]
    assert libpq_reach.reach_libpq() == windows[1] and os.environ["PATH"] == before  # once


def test_control_an_allowed_bundled_library_changes_nothing(windows, monkeypatch):
    monkeypatch.setattr(libpq_reach, "_refused", lambda path: False)
    before = os.environ["PATH"]
    assert libpq_reach.reach_libpq() is None
    assert os.environ["PATH"] == before


def test_a_libpq_already_on_path_is_left_to_psycopg(windows, monkeypatch):
    monkeypatch.setattr(libpq_reach, "_refused", lambda path: True)
    monkeypatch.setenv("PATH", str(windows[0]))
    assert libpq_reach.reach_libpq() is None
    assert os.environ["PATH"] == str(windows[0])


def test_no_postgres_installed_is_said(windows, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(libpq_reach, "_refused", lambda path: True)
    monkeypatch.setattr(libpq_reach, "_postgres_bins", lambda: [])
    caplog.set_level(logging.WARNING, logger=libpq_reach.__name__)
    assert libpq_reach.reach_libpq() is None
    assert any("cannot reach Postgres" in r.getMessage() for r in caplog.records)


def test_elsewhere_than_windows_nothing_is_probed(monkeypatch):
    monkeypatch.setattr(libpq_reach, "_on_windows", lambda: False)
    monkeypatch.setattr(libpq_reach, "_added", None)

    def no_probe():
        raise AssertionError("probed")

    monkeypatch.setattr(libpq_reach, "_bundled", no_probe)
    assert libpq_reach.reach_libpq() is None


def test_only_a_refusal_by_policy_counts(monkeypatch, tmp_path):
    import ctypes

    def load(winerror):
        def windll(path):
            exc = OSError(22, "refused")
            exc.winerror = winerror
            raise exc
        return windll

    monkeypatch.setattr(ctypes, "WinDLL", load(4551), raising=False)
    assert libpq_reach._refused(tmp_path / "pq.pyd") is True
    monkeypatch.setattr(ctypes, "WinDLL", load(193), raising=False)  # not a program: not a refusal
    assert libpq_reach._refused(tmp_path / "pq.pyd") is False


def _imports_psycopg_at_top(source: str) -> bool:
    for node in ast.parse(source).body:
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        if any(n == "psycopg" or n.startswith(("psycopg.", "psycopg_")) for n in names):
            return True
    return False


def test_kazma_core_runs_it_before_any_psycopg_import():
    """The package runs the check at import, and no Kazma module imports psycopg at its top."""
    init = (REPO / "kazma-core" / "kazma_core" / "__init__.py").read_text(encoding="utf-8")
    assert "reach_libpq()" in init
    top_level = [p.relative_to(REPO).as_posix() for p in (REPO / "kazma-core" / "kazma_core").rglob("*.py")
                 if _imports_psycopg_at_top(p.read_text(encoding="utf-8"))]
    assert top_level == []


def test_control_a_top_level_psycopg_import_is_seen():
    assert _imports_psycopg_at_top("import psycopg\n")
    assert _imports_psycopg_at_top("from psycopg_pool import ConnectionPool\n")
    assert not _imports_psycopg_at_top("def f():\n    import psycopg\n")


# ── Temporal's workflow sandbox imports kazma_core again (2026-11-26) ─────


def _sandbox(monkeypatch, inside: bool) -> None:
    import types

    unsafe = types.SimpleNamespace(in_sandbox=lambda: inside)
    monkeypatch.setitem(sys.modules, "temporalio.workflow", types.SimpleNamespace(unsafe=unsafe))


def test_inside_temporals_workflow_sandbox_nothing_is_probed(monkeypatch):
    """Every durable swarm worker on Windows failed to start: the sandbox refuses file access at import."""
    monkeypatch.setattr(libpq_reach, "_on_windows", lambda: True)
    monkeypatch.setattr(libpq_reach, "_added", None)
    _sandbox(monkeypatch, True)

    def no_probe():
        raise AssertionError("probed inside the workflow sandbox")

    monkeypatch.setattr(libpq_reach, "_bundled", no_probe)
    assert libpq_reach.reach_libpq() is None


def test_control_outside_the_sandbox_the_check_runs(windows, monkeypatch):
    _sandbox(monkeypatch, False)
    monkeypatch.setattr(libpq_reach, "_refused", lambda path: True)
    assert libpq_reach.reach_libpq() == windows[1]
