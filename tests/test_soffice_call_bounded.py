"""A LibreOffice call that times out returns, whatever its children hold.

2026-10-13. The full test run hung in the document certification: the
parser registry's 8 s ``soffice --version`` probe timed out under load, and
``subprocess.run(timeout=)`` then killed only the process it had started and
waited WITHOUT a limit for the output pipes -- which soffice.com's own child,
soffice.bin, still held. ``binaries.run_soffice_cli`` now kills the whole
tree and bounds the last wait. The stand-in "soffice" here is a Python
process whose child holds the pipes for 20 s. Negative control: the old call
waits for that child.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest
from kazma_core.documents import binaries

_HOLDER = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'],"
    " stdout=sys.stdout, stderr=sys.stderr)\n"
    "time.sleep(20)\n"
)


def test_a_timed_out_call_returns_while_a_child_holds_its_pipes(monkeypatch):
    monkeypatch.setattr(binaries, "find_soffice", lambda: sys.executable)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        binaries.run_soffice_cli(["-c", _HOLDER], timeout=1)
    assert time.monotonic() - started < 1 + binaries._DRAIN_SECONDS + 2


def test_a_finished_call_returns_its_output(monkeypatch):
    monkeypatch.setattr(binaries, "find_soffice", lambda: sys.executable)
    result = binaries.run_soffice_cli(["-c", "print('LibreOffice 26.2')"], timeout=30)
    assert result.returncode == 0 and result.stdout.strip() == "LibreOffice 26.2"


def test_the_old_call_waits_for_the_child():
    # Negative control: what the probe used to do.
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([sys.executable, "-c", _HOLDER], capture_output=True, timeout=1,
                       text=True, stdin=subprocess.DEVNULL)
    assert time.monotonic() - started >= 15
