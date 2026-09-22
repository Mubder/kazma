"""Run every ``tests/js/test_*.js`` under bare node, as part of the Python suite.

CI named its node tests one by one in the unified-turn job, so a JS test file
that was not on that list was never run. Five were not (audit 2026-09-22), and
one of them — test_markdown_render.js — had been failing on a ReferenceError
for as long as nobody ran it. The list here is the directory itself, so a new
file is run the day it is added. The unified-turn job keeps its own explicit
list as well; running those twice is cheap.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = Path(__file__).resolve().parent / "js"
NODE = shutil.which("node")


def _js_tests() -> list[Path]:
    return sorted(JS_TESTS.glob("test_*.js"))


def test_js_test_enumeration_is_not_blind():
    names = {p.name for p in _js_tests()}
    assert {"test_boot.js", "test_kazma_save.js", "test_markdown_render.js"} <= names


@pytest.mark.parametrize("script", _js_tests(), ids=lambda p: p.name)
def test_js_file_passes_under_node(script: Path):
    if NODE is None:
        if os.environ.get("CI"):
            pytest.fail("node is not on PATH in CI — the JS tests would silently stop running")
        pytest.skip("node is not installed")
    result = subprocess.run(
        [NODE, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=JS_TESTS.parent.parent,
    )
    assert result.returncode == 0, (
        f"node {script.name} exited {result.returncode}\n"
        f"--- stdout (tail)\n{result.stdout[-2000:]}\n--- stderr (tail)\n{result.stderr[-2000:]}"
    )
