"""The planted two-space world (``tests/_space_world.py``) leaves nothing in
the session's data folder.

It plants a word into every table of every store. Planted into the session's
folder, those rows stayed for every later test of the process: the X fire
loop stopped on a planted reply notification, the Documents API answered 503
on a planted job, a later world counted earlier worlds' rows (found by the
full suite after S1-27 and S1-28).

Each variant runs in a child process whose "session folder" is a throwaway:
the world is built and torn down, then the session folder is read for the
planted words, byte for byte, every file (``-wal`` included). The world as it
is leaves none; the world of before (no folder of its own, the session's
singletons) is the control and leaves them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_CHILD = r"""
import json, sys
from pathlib import Path
import pytest
sys.path.insert(0, sys.argv[1])
from tests import _space_world as world_module
from kazma_core import paths

session = Path(paths.data_dir())
if sys.argv[2] == "before":
    # The world of before: the session's folder and its singletons.
    world_module._own_data_folder = lambda monkeypatch, folder: Path(paths.data_dir())
    world_module._close_singletons = lambda: None
with pytest.MonkeyPatch.context() as mp:
    built = world_module.plant_world(mp, Path(sys.argv[3]))
    found = next(built)
    words = list(found["words"].values())
    built.close()
left = []
for path in session.rglob("*"):
    if path.is_file():
        data = path.read_bytes()
        left += [path.name for word in words if word.encode() in data]
print("LEFT" + json.dumps(sorted(set(left))))
"""


def _run(variant: str, tmp_path: Path) -> list[str]:
    session = tmp_path / f"session-{variant}"
    session.mkdir()
    own = tmp_path / f"own-{variant}"
    own.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env.update({
        "KAZMA_DATA_DIR": str(session),
        "KAZMA_MEMORY_STATE_DB": str(session / "memory_state.db"),
        "KAZMA_MEMORY_OPS_DB": str(session / "memory_ops.db"),
        "KAZMA_WORKSPACE": str(tmp_path / f"workspace-{variant}"),
        "KAZMA_USER_HOME": str(tmp_path / f"home-{variant}"),
        "KAZMA_SKILLS_HOME": str(tmp_path / f"skills-{variant}"),
    })
    done = subprocess.run([sys.executable, "-c", _CHILD, str(REPO), variant, str(own)], env=env,
                          cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600, check=False, stdin=subprocess.DEVNULL)
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("LEFT")), "")
    assert line, (done.stdout[-2000:], done.stderr[-4000:])
    return json.loads(line[len("LEFT"):])


def test_the_world_leaves_nothing_in_the_session_folder(tmp_path) -> None:
    assert _run("now", tmp_path) == []


def test_control_the_world_of_before_left_its_rows_in_the_session_folder(tmp_path) -> None:
    left = _run("before", tmp_path)
    assert len(left) >= 10, left
