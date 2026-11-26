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
    monkeypatch.setattr(libpq_reach, "_preferred", False)
    monkeypatch.setattr(libpq_reach, "_checks_files", lambda: False)  # this PC's setting is not the test's
    monkeypatch.delenv("PSYCOPG_IMPL", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramW6432", str(tmp_path))
    # The lookup also reads C:\Program Files itself: a PostgreSQL installed on
    # the machine running the tests (18 on the owner's PC) is not this test's.
    real_bins = libpq_reach._postgres_bins
    monkeypatch.setattr(libpq_reach, "_postgres_bins",
                        lambda: [b for b in real_bins() if tmp_path in b.parents])
    monkeypatch.setenv("PATH", str(tmp_path / "elsewhere"))
    return bins


def test_a_refused_bundled_library_gives_way_to_the_newest_postgres(windows, monkeypatch, caplog):
    monkeypatch.setattr(libpq_reach, "_refused", lambda path: True)
    caplog.set_level(logging.INFO, logger=libpq_reach.__name__)
    assert libpq_reach.reach_libpq() == windows[1]  # 18, not 16
    assert os.environ["PATH"].split(os.pathsep)[-1] == str(windows[1])
    said = [r for r in caplog.records if r.name == libpq_reach.__name__]
    # A fallback that worked is said calmly, at INFO, with the operator's detail
    # (the owner's rule, 2026-11-26): what Windows checked, what Kazma uses instead.
    assert [r.levelno for r in said] == [logging.INFO]
    assert "signed library instead" in said[0].getMessage()
    assert "Application Control" in said[0].getMessage()  # the operator's detail
    before = os.environ["PATH"]
    assert libpq_reach.reach_libpq() == windows[1] and os.environ["PATH"] == before  # once


def test_while_smart_app_control_checks_files_the_signed_library_is_used_first(windows, monkeypatch, caplog):
    """Measured 2026-11-26: psycopg_binary's files carry no signature, PostgreSQL's
    libpq and its DLLs are signed. With Windows checking every file, the bundled
    library is never loaded; psycopg's Python wrapper uses the signed one."""
    loaded: list[str] = []
    monkeypatch.setattr(libpq_reach, "_checks_files", lambda: True)
    monkeypatch.setattr(libpq_reach, "load_error", lambda path: loaded.append(path.name) or None)
    monkeypatch.setattr(libpq_reach, "_refused", lambda path: pytest.fail("the bundled library was tried"))
    caplog.set_level(logging.INFO, logger=libpq_reach.__name__)
    assert libpq_reach.reach_libpq() == windows[1]
    assert os.environ["PSYCOPG_IMPL"] == "python" and libpq_reach.preferred()
    assert os.environ["PATH"].split(os.pathsep)[-1] == str(windows[1])
    assert loaded == ["libpq.dll"], "only the signed library is loaded"
    said = [r for r in caplog.records if r.name == libpq_reach.__name__]
    assert [r.levelno for r in said] == [logging.INFO] and "signed client library" in said[0].getMessage()


def test_an_operators_own_psycopg_choice_is_kept(windows, monkeypatch):
    monkeypatch.setattr(libpq_reach, "_checks_files", lambda: True)
    monkeypatch.setenv("PSYCOPG_IMPL", "binary")
    monkeypatch.setattr(libpq_reach, "_refused", lambda path: False)
    assert libpq_reach.reach_libpq() is None
    assert os.environ["PSYCOPG_IMPL"] == "binary" and not libpq_reach.preferred()


def test_control_an_allowed_bundled_library_changes_nothing(windows, monkeypatch):
    monkeypatch.setattr(libpq_reach, "_refused", lambda path: False)
    before = os.environ["PATH"]
    assert libpq_reach.reach_libpq() is None
    assert os.environ["PATH"] == before


def test_a_libpq_already_on_path_is_left_to_psycopg(windows, monkeypatch):
    monkeypatch.setattr(libpq_reach, "_refused", lambda path: path.suffix == ".pyd")
    monkeypatch.setenv("PATH", str(windows[0]))
    assert libpq_reach.reach_libpq() is None
    assert os.environ["PATH"] == str(windows[0])


def test_a_libpq_on_path_windows_refuses_too_is_passed_over(windows, monkeypatch):
    """The audit's F3: the PATH copy was taken on trust; one Windows refuses
    is passed over for an installed PostgreSQL's that it allows. The test
    above (an allowed PATH copy, left to psycopg) is the control."""
    monkeypatch.setattr(libpq_reach, "_refused", lambda path: path.suffix == ".pyd" or path.parent == windows[0])
    monkeypatch.setenv("PATH", str(windows[0]))
    assert libpq_reach.reach_libpq() == windows[1]
    assert os.environ["PATH"].split(os.pathsep) == [str(windows[0]), str(windows[1])]


def test_the_postgres_kazma_set_up_comes_first(windows, monkeypatch, tmp_path):
    """The audit's F2: the PostgreSQL ``kazma setup postgres`` recorded is
    looked for first, wherever it was installed; Program Files' after it."""
    from kazma_core import postgres_service

    recorded = tmp_path / "D-drive" / "pgsql" / "bin"
    recorded.mkdir(parents=True)
    (recorded / "libpq.dll").write_bytes(b"")
    monkeypatch.setattr(postgres_service, "installed_bin_dir", lambda record=None: recorded)
    assert libpq_reach._postgres_bins() == [recorded, windows[1], windows[0]]
    monkeypatch.setattr(postgres_service, "installed_bin_dir", lambda record=None: None)
    assert libpq_reach._postgres_bins() == [windows[1], windows[0]]  # control: none recorded


def test_no_postgres_installed_is_said(windows, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(libpq_reach, "_refused", lambda path: True)
    monkeypatch.setattr(libpq_reach, "_postgres_bins", lambda: [])
    caplog.set_level(logging.WARNING, logger=libpq_reach.__name__)
    assert libpq_reach.reach_libpq() is None
    # Only "no signed alternative here" is a WARNING, and it names what waits.
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and "reaching the database waits" in warned[0]
    assert "Nothing is wrong with the PC" in warned[0]


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
