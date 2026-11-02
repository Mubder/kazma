"""After a fatal error the server process ends at once (FA-138).

The 2026-11 functional audit: ``serve.py`` answered a dead event loop with
``sys.exit(1)``; interpreter shutdown then joined non-daemon threads that
never finish once their loop is gone, and the server stayed up and silent
until the guard killed it (about 2.5 minutes of a 4-minute outage).

A real child process with a planted non-daemon thread that never ends runs
the error path: it exits within 10 s, with code 1 and every thread's stack on
stderr. The negative control is ``sys.exit(1)`` in the same child: it hangs.
"""

from __future__ import annotations

import subprocess
import sys
import time

_CHILD = """
import sys, threading
stuck = threading.Event()
threading.Thread(target=stuck.wait, name="never-ends").start()  # non-daemon
{exit}
"""

_NOW = "from kazma_core.process_exit import exit_after_fatal_error; exit_after_fatal_error(1)"


def _run(exit_line: str, timeout: float) -> tuple[int | None, str, float]:
    started = time.monotonic()
    proc = subprocess.Popen([sys.executable, "-c", _CHILD.format(exit=exit_line)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _out, err = proc.communicate(timeout=timeout)
        return proc.returncode, err, time.monotonic() - started
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=10)
        return None, "", time.monotonic() - started


def test_the_error_path_exits_within_ten_seconds():
    code, err, took = _run(_NOW, timeout=10)
    assert code == 1 and took < 10
    assert "never-ends" in err or "Thread" in err  # every thread's stack, for the record


def test_serve_uses_it():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "serve.py").read_text(encoding="utf-8")
    tail = src[src.index("except Exception:"):]
    assert "exit_after_fatal_error(1)" in tail and "sys.exit(1)" not in tail


def test_sys_exit_hangs_on_the_thread():
    """Negative control: the old exit waits for the thread that never ends."""
    code, _err, _took = _run("sys.exit(1)", timeout=3)
    assert code is None
