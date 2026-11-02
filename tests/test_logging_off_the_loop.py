"""Writing a log line never holds up the event loop (FA-135).

The 2026-11 functional audit: the file and console handlers ran in the
caller's thread, so every log record the server wrote from the event loop
waited on the disk -- a slow write, a full console pipe, the midnight
rotation -- and every chat on that loop waited with it.

Now the root logger has one handler that hands the record to a queue; the
file and console handlers write on the log writer thread. A file handler
that takes 2 s a record leaves a 50 ms loop heartbeat at most 200 ms late;
the same handler on the logger directly is the negative control. And the real
``setup_logging``, in a fresh process, writes through the queue: the message,
its traceback and the turn id reach the file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from kazma_core import logging_config

REPO = Path(__file__).resolve().parents[1]


class _SlowFile(logging.Handler):
    """A file handler on a disk that takes *delay* seconds a record."""

    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay, self.lines = delay, []

    def emit(self, record: logging.LogRecord) -> None:
        time.sleep(self.delay)
        self.lines.append(self.format(record))


async def _heartbeat_lateness(logger: logging.Logger, records: int) -> float:
    """The worst lateness of a 50 ms heartbeat while the loop logs."""
    worst = 0.0
    stop = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal worst
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            before = loop.time()
            await asyncio.sleep(0.05)
            worst = max(worst, loop.time() - before - 0.05)

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.12)
    for n in range(records):
        logger.warning("record %d from the loop", n)
        await asyncio.sleep(0.06)
    stop.set()
    await beat
    return worst


def _private_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def test_a_slow_log_file_does_not_hold_up_the_loop():
    slow = _SlowFile(2.0)
    target: queue.Queue = queue.Queue(logging_config._QUEUE_MAX)
    front = logging_config._LogQueueHandler(target)
    writer = logging_config._LogWriter(target, (slow,), front)
    writer.start()
    logger = _private_logger("kazma_test.fa135.queued")
    logger.addHandler(front)
    try:
        worst = asyncio.run(_heartbeat_lateness(logger, 3))
    finally:
        logger.removeHandler(front)
        writer.enqueue_sentinel()
        writer._thread.join(10)
    assert worst < 0.2, f"the heartbeat ran {worst:.2f}s late"
    assert [line for line in slow.lines if "record" in line] == [
        f"record {n} from the loop" for n in range(3)]


def test_the_same_file_on_the_logger_held_the_loop_up():
    """Negative control: the handler in the caller's thread, as before FA-135."""
    logger = _private_logger("kazma_test.fa135.direct")
    logger.addHandler(_SlowFile(2.0))
    worst = asyncio.run(_heartbeat_lateness(logger, 1))
    assert worst >= 1.5


def test_a_full_queue_drops_and_says_so():
    target: queue.Queue = queue.Queue(1)
    front = logging_config._LogQueueHandler(target)
    logger = _private_logger("kazma_test.fa135.full")
    logger.addHandler(front)
    for n in range(4):
        logger.warning("burst %d", n)
    assert front.take_dropped() == 3 and target.qsize() == 1


def test_the_real_setup_writes_through_the_queue(tmp_path):
    """A fresh interpreter, as the server starts: one queue handler on the
    root logger, and the file gets the message, its traceback and the turn id."""
    log_path = tmp_path / "kazma.log"
    script = textwrap.dedent(f"""
        import json, logging, sys
        from kazma_core import logging_config
        from kazma_core.observability.correlation import bind_turn_id
        logging_config.setup_logging("INFO")
        root = logging.getLogger()
        print("HANDLERS=" + json.dumps([h.get_name() for h in root.handlers]))
        bind_turn_id("turn-fa135")
        try:
            raise ValueError("boom-fa135")
        except ValueError:
            logging.getLogger("kazma_core.fa135").exception("handled the boom")
        print("STOPPED=" + str(logging_config._stop_log_writer(5.0)))
    """)
    env = {**os.environ, "KAZMA_LOG_FILE": str(log_path), "KAZMA_DATA_DIR": str(tmp_path / "data"),
           "KAZMA_LOG_FORMAT": "json", "KAZMA_USER_HOME": str(tmp_path / "home")}
    done = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=env, capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    marks = dict(line.split("=", 1) for line in done.stdout.splitlines() if line.startswith(("HANDLERS=", "STOPPED=")))
    assert json.loads(marks["HANDLERS"]) == ["queue"] and marks["STOPPED"] == "True"
    text = log_path.read_text(encoding="utf-8")
    assert "handled the boom" in text and "ValueError: boom-fa135" in text
    assert "turn-fa135" in text, "the turn id is read on the caller's thread"
