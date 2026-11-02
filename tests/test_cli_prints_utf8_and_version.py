"""The CLI prints its text on any console and answers ``--version`` (2026-11, FA-225).

``kazma doctor`` died on a character cp1252 does not have (an arrow, a check
mark) on a cp1252 console or pipe (UnicodeEncodeError), and
``kazma --version`` was an unknown command. The CLI
now writes UTF-8 (a character the terminal cannot show is replaced, never a
crash) and answers ``--version``, ``-V`` and ``version``.

Run as a real child process with ``PYTHONIOENCODING=cp1252``; the same text
printed by a child without the reconfigure is the negative control.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


def _run(code: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "KAZMA_TEST_EGRESS_GUARD": "0"}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, timeout=120)


@pytest.mark.parametrize("flag", ["--version", "-V", "version"])
def test_version_answers_on_a_cp1252_console(flag):
    done = _run(f"import sys; sys.argv=['kazma', {flag!r}]; from kazma_cli.main import main; main()")
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-800:]
    assert done.stdout.decode("utf-8").startswith("kazma ")


def test_kazmas_own_text_prints_on_a_cp1252_console():
    done = _run("from kazma_cli.main import _print_utf8; _print_utf8(); print('provider → model ✓')")
    assert done.returncode == 0
    assert "→" in done.stdout.decode("utf-8")


def test_negative_control_without_it_an_arrow_crashes():
    done = _run("print('provider → model')")
    assert done.returncode != 0 and b"UnicodeEncodeError" in done.stderr
