"""The chunk runner must isolate a hang in two runs, not a hundred and sixty.

`--timeout-method=thread` kills the process when a test hangs, so the chunk's
tally dies with it. The runner then re-ran every one of that chunk's ~160 files
in its own process to find the culprit — and on CI the culprit is usually the
FOURTH file, so ~156 of those runs were pure waste. That doubled the job's wall
clock (1,034s green versus 1,693s when a chunk died) on every run where a chunk
hung, which was most of them.

pytest -q prints a progress line per file before it dies, so the culprit is
already in the output nobody was reading. `last_file_reached` recovers it; the
caller then re-runs the chunk MINUS that file as one process, plus the file
alone.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _runner():
    spec = importlib.util.spec_from_file_location(
        "fast_test_mod", REPO / "scripts" / "fast_test.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


#: The exact shape CI produced on 2026-09-20, trimmed.
_KILLED_CHUNK_LOG = """============================= test session starts ==============================
collected 2252 items / 2 skipped
kazma-core/kazma_core_tests/integration/test_multi_platform.py ......... [  0%]
kazma-core/kazma_core_tests/unit/test_reliability.py ................... [  1%]
kazma-core/tests/test_empty_answer_recovery.py ....                      [  2%]
kazma-core/tests/test_github_app_integration.py ....+++++++++ Timeout ++++++++++
~~~~~~~~~~~ Stack of MainThread (140366038940544) ~~~~~~~~~~~
  File "/x/_pytest/runner.py", line 184, in pytest_runtest_call
"""


def test_the_culprit_is_recovered_from_a_killed_chunk() -> None:
    """The hung file is named in the output; find it there."""
    mod = _runner()
    assert (
        mod.last_file_reached(_KILLED_CHUNK_LOG)
        == "kazma-core/tests/test_github_app_integration.py"
    )


def test_no_progress_lines_is_not_a_crash() -> None:
    """A chunk that printed nothing must yield None, not raise or guess."""
    mod = _runner()
    assert mod.last_file_reached("") is None
    assert mod.last_file_reached("collected 0 items\n") is None


def test_a_clean_run_names_its_last_file() -> None:
    """On a healthy chunk the last file is simply the last one printed."""
    mod = _runner()
    log = (
        "tests/test_a.py ....   [ 30%]\n"
        "tests/test_b.py ..     [ 70%]\n"
        "tests/test_c.py .      [100%]\n"
        "===== 7 passed in 1.20s =====\n"
    )
    assert mod.last_file_reached(log) == "tests/test_c.py"


@pytest.mark.parametrize(
    "line,expected",
    [
        ("kazma-ui/kazma_ui_tests/test_x.py ..  [ 5%]", "kazma-ui/kazma_ui_tests/test_x.py"),
        ("tests/e2e/test_smoke.py s            [ 9%]", "tests/e2e/test_smoke.py"),
    ],
)
def test_progress_line_shapes(line: str, expected: str) -> None:
    """Both nested package tests and skipped-first files parse."""
    assert _runner().last_file_reached(line + "\n") == expected
