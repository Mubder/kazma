"""No test leaves a link in the data folder every test of a process shares.

``test_a_bundle_names_a_database_whose_disk_is_away`` planted a link named
``swarm_tasks.db`` in that folder, leading to a disk that is away, and skipped
before its cleanup when an earlier file had made ``knowledge.db`` there: 46
tests of ``test_swarm_ui_panel.py`` could not open the swarm store (CI's first
sharded run, 2026-11-25). It failed only in that order, and only where the
account may make a symbolic link, so nothing on Windows ever saw it. The root
conftest removes such a link and fails the test that left it.

The two probes run in a child pytest, in order; isolation off is the negative
control.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HERE = "tests/test_data_folder_links.py"
PROBES = os.environ.get("KAZMA_DATA_FOLDER_LINK_PROBES") == "1"
NAME = "link_probe.db"


def _shared_folder() -> Path:
    from kazma_core import paths

    return Path(paths.data_dir())


def _link(link: Path, target: Path) -> None:
    """A link to a folder: a junction on Windows (no privilege needed)."""
    target.mkdir()
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.skipif(not PROBES, reason="a probe, run in a child pytest")
def test_probe_a_link_is_left(tmp_path) -> None:
    _link(_shared_folder() / NAME, tmp_path / "away")
    pytest.skip("before its cleanup, as the test of 2026-11-25 did")


@pytest.mark.skipif(not PROBES, reason="a probe, run in a child pytest")
def test_probe_the_next_test_finds_none() -> None:
    assert NAME not in os.listdir(_shared_folder())


def _child(**env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider",
         f"{HERE}::test_probe_a_link_is_left", f"{HERE}::test_probe_the_next_test_finds_none"],
        cwd=REPO, env={**os.environ, "KAZMA_DATA_FOLDER_LINK_PROBES": "1", **env},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)


def test_a_link_left_behind_fails_its_test_and_does_not_reach_the_next() -> None:
    out = _child()
    assert out.returncode == 1, out.stdout[-3000:]
    assert "ERROR tests/test_data_folder_links.py::test_probe_a_link_is_left" in out.stdout, out.stdout[-3000:]
    assert "left links in the data folder every test shares: link_probe.db" in out.stdout
    assert "PASSED tests/test_data_folder_links.py::test_probe_the_next_test_finds_none" in out.stdout


def test_control_without_the_guard_the_link_reaches_the_next_test() -> None:
    out = _child(KAZMA_TEST_ISOLATION="0")
    assert "FAILED tests/test_data_folder_links.py::test_probe_the_next_test_finds_none" in out.stdout, out.stdout[-3000:]
    assert "ERROR tests/test_data_folder_links.py" not in out.stdout


def test_a_link_in_a_folder_of_the_tests_own_is_fine(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    _link(_shared_folder() / NAME, tmp_path / "away")
