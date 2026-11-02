"""A console that cannot encode a character writes it escaped (roadmap theme 2).

Found by the graceful reload drill (2026-11): the server, started with its
output to a file, wrote "--- Logging error ---" and a traceback to stderr for
every log line holding an arrow (cp1252 has none), and lost the line from the
console. ``setup_logging`` now sets the console's stdout to escape what it
cannot encode. A real child whose stdout is a cp1252 file logs an arrow and
Arabic text: no logging error, the text escaped on the console, whole in the
UTF-8 log file. The control is the same child with the step switched off.
"""

from __future__ import annotations

import os
import subprocess
import sys

_CHILD = r"""
import logging, sys
from kazma_core import logging_config
if sys.argv[1] == "old":
    logging_config._console_never_raises = lambda: None
logging_config.setup_logging("INFO")
logging.getLogger("kazma_core.drill").info("model kind=general → drill-model مرحبا")
logging_config.drain_logs(5)
"""


def _run(tmp_path, mode: str) -> tuple[str, str, str]:
    out = tmp_path / f"{mode}.out"
    log = tmp_path / f"{mode}.log"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8",)}
    env.update({"PYTHONIOENCODING": "cp1252", "KAZMA_LOG_FILE": str(log), "KAZMA_LOG_FORMAT": "text"})
    with out.open("wb") as stdout:
        done = subprocess.run([sys.executable, "-c", _CHILD, mode], stdout=stdout, stderr=subprocess.PIPE,
                              env=env, cwd=str(tmp_path), timeout=120)
    return (out.read_bytes().decode("cp1252", errors="replace"),
            done.stderr.decode("utf-8", errors="replace"),
            log.read_text(encoding="utf-8") if log.exists() else "")


def test_a_cp1252_console_escapes_and_never_raises(tmp_path):
    console, stderr, logfile = _run(tmp_path, "new")
    assert "Logging error" not in stderr, stderr[-1500:]
    assert "\\u2192 drill-model" in console, console[-500:]
    assert "→ drill-model مرحبا" in logfile


def test_negative_control_the_old_console_raised_per_line(tmp_path):
    _console, stderr, _logfile = _run(tmp_path, "old")
    assert "Logging error" in stderr and "UnicodeEncodeError" in stderr, stderr[-1500:]
