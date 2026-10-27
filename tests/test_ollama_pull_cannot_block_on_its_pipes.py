"""An ``ollama pull`` started from the UI cannot stall on its own output.

Audit 2026-10-26 AUD-037: ``pull_ollama_model`` started ``ollama pull`` with
``stdout=PIPE, stderr=PIPE`` that nothing read, and nothing waited on the
process. Ollama prints a progress bar for every layer: once the pipe buffer
filled, the pull blocked for good, and the Models page showed "pulling"
forever. Now the output goes to the null device and a waiter thread records
the ending in the result the pulls list shows.

Negative control: a child that writes more than a pipe holds, started the
old way, does not finish.
"""

from __future__ import annotations

import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
from kazma_core.models import discovery


def _wait_for(result: dict, status: str) -> None:
    deadline = time.monotonic() + 5
    while result["status"] != status and time.monotonic() < deadline:
        time.sleep(0.01)


@pytest.mark.asyncio
async def test_the_pull_has_no_unread_pipes_and_its_ending_is_recorded():
    proc = MagicMock(pid=4321)
    proc.wait.return_value = 0
    with patch("subprocess.Popen", return_value=proc) as popen:
        result = await discovery.pull_ollama_model("llama3.2")

    kwargs = popen.call_args.kwargs
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    _wait_for(result, "pulled")
    assert result["status"] == "pulled"
    assert 4321 not in discovery._pull_waiters


@pytest.mark.asyncio
async def test_a_failed_pull_says_so():
    proc = MagicMock(pid=4322)
    proc.wait.return_value = 1
    with patch("subprocess.Popen", return_value=proc):
        result = await discovery.pull_ollama_model("no-such-model")
    _wait_for(result, "error")
    assert result["status"] == "error"
    assert "code 1" in result["error"]


def test_negative_control_an_unread_pipe_blocks_the_child():
    # More than any pipe buffer (64 KiB on Linux, 4 KiB-1 MiB on Windows).
    writer = "import sys\nfor _ in range(64):\n    sys.stdout.write('x' * 65536)\nsys.stdout.flush()\n"
    child = subprocess.Popen([sys.executable, "-c", writer], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            child.wait(timeout=3)
    finally:
        child.kill()
        child.communicate(timeout=10)
