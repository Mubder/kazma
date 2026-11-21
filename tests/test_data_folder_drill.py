"""The data-folder drill's judging (``scripts/data_folder_drill.py``): pure, so
it is held here; the drill itself runs on the drill VM.

``passed`` only when the move put the database in the folder, kept its data
and archives there, AND, after PostgreSQL was removed, a new setup ran the kept
database and archives there again. Each check missing on its own fails it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import data_folder_drill as drill  # noqa: E402

FOLDER = "E:\\Kazma"
DATA = "E:\\Kazma\\postgres-18\\data"


def _evidence(**over) -> dict:
    ev = {
        "move": {"exit": 0},
        "after_move": {"data_dir": DATA, "pid_in_folder": True, "archived": 3, "service": True},
        "after_move_switch": {"data_dir": DATA, "pid_in_folder": True, "archived": 4, "service": True},
        "mark_after_move": "abc",
        "after_uninstall": {"data_dir": "", "service": False, "archived": 4},
        "reinstall": {"exit": 0},
        "after_reset": {"data_dir": DATA, "pid_in_folder": True, "archived": 4, "service": True},
        "after_reset_switch": {"data_dir": DATA, "pid_in_folder": True, "archived": 5, "service": True},
        "mark_after_reset": "abc",
    }
    ev.update(over)
    return ev


def test_a_whole_run_passes():
    verdict = drill.judge(FOLDER, "abc", _evidence())
    assert verdict.verdict == "passed" and not verdict.notes


@pytest.mark.parametrize("over, failed", [
    ({"move": {"exit": 1}}, "move_exit_0"),
    ({"after_move": {"data_dir": "C:\\Program Files\\PostgreSQL\\18\\data", "pid_in_folder": True,
                     "archived": 3}}, "service_runs_from_the_folder"),
    ({"mark_after_move": "other"}, "the_data_came_along"),
    ({"after_move_switch": {"archived": 3}}, "a_new_segment_is_archived_there"),
])
def test_a_move_that_did_not_hold_fails(over, failed):
    verdict = drill.judge(FOLDER, "abc", _evidence(**over))
    assert verdict.verdict == "failed" and failed in verdict.notes


@pytest.mark.parametrize("over, failed", [
    ({"after_uninstall": {"service": True}}, "postgresql_was_removed"),
    ({"after_reset": {"data_dir": "C:\\Program Files\\PostgreSQL\\18\\data", "archived": 4}},
     "the_new_service_runs_the_kept_database"),
    ({"mark_after_reset": None}, "the_data_is_still_there"),
    ({"after_reset_switch": {"archived": 4}}, "it_archives_there_again"),
])
def test_a_reinstall_that_started_empty_is_moved_only(over, failed):
    verdict = drill.judge(FOLDER, "abc", _evidence(**over))
    assert verdict.verdict == "moved_only" and failed in verdict.notes


def test_a_folder_that_only_shares_a_prefix_is_not_the_folder():
    assert not drill._under("E:\\Kazma2\\postgres-18\\data", FOLDER)
    assert drill._under(DATA, FOLDER)
