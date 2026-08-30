"""Catch the thing that stops the event loop, with the stack that did it.

On 2026-08-30 one unacknowledged Telegram message put Kazma into a restart
loop: the message was redelivered on every reconnect, the turn it started
froze the event loop completely, the guard's health probe timed out three
times, and the child was killed before the message could ever be
acknowledged -- so it came back on the next boot. Four restarts, and the
guard's own crash-loop cooldown was one restart away from taking the service
down for thirty minutes.

The application log was no help at all. Its last line was "Enqueued from
..." and then nothing: not the consumer picking the message up, not even
uvicorn logging the health requests that were hitting it. A blocked loop
cannot log what blocked it, which is precisely when a stack is worth most.
``py-spy`` could not attach either -- the server runs at a privilege level
the operator's shell could not open.

So the loop watches itself. A daemon thread -- deliberately NOT on the loop,
because anything on the loop is frozen exactly when it is needed -- checks a
heartbeat the loop keeps refreshing. When the heartbeat goes stale the thread
writes every thread's stack, including the frozen one, to its own file and
logs a line naming it.

This does not prevent a stall. It makes the next one take minutes to diagnose
instead of a night.
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["start_stall_watchdog", "stall_dump_dir"]

#: How long the loop may be unresponsive before we consider it stalled. The
#: guard kills after roughly 90s (3 failures x 30s interval), so this has to
#: fire well inside that window or the evidence dies with the process.
DEFAULT_THRESHOLD_S = 15.0

#: How often the loop refreshes its heartbeat. Cheap: one timestamp write.
DEFAULT_INTERVAL_S = 3.0

#: Minimum gap between dumps, so a long stall leaves a few snapshots showing
#: whether it is stuck in one place or crawling, rather than thousands.
_REDUMP_EVERY_S = 30.0


def stall_dump_dir() -> Path:
    """Where stall dumps are written. Beside the logs, not inside the data dir."""
    try:
        from kazma_core.paths import user_home

        d = Path(user_home())
    except Exception:  # noqa: BLE001
        d = Path.home() / ".kazma"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_dump(lag_s: float, tag: str) -> Path | None:
    """Dump every thread's stack. Never raises -- this runs during an incident."""
    try:
        path = stall_dump_dir() / f"stall-{tag}.txt"
        with path.open("w", encoding="utf-8") as fh:
            fh.write(
                f"event loop unresponsive for {lag_s:.1f}s\n"
                f"written {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
                f"{'=' * 70}\n"
            )
            fh.flush()
            # all_threads: the blocked one is the point, and it is not this one.
            faulthandler.dump_traceback(file=fh, all_threads=True)
        return path
    except Exception:  # noqa: BLE001
        logger.debug("[loop-stall] could not write the dump", exc_info=True)
        return None


def start_stall_watchdog(
    *,
    threshold_s: float = DEFAULT_THRESHOLD_S,
    interval_s: float = DEFAULT_INTERVAL_S,
) -> Any:
    """Begin watching the running loop. Returns the heartbeat task.

    Safe to call when no loop is running: it logs and does nothing, rather
    than raising into startup.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("[loop-stall] no running loop — watchdog not started")
        return None

    state = {"last_beat": time.monotonic()}

    async def _heartbeat() -> None:
        while True:
            state["last_beat"] = time.monotonic()
            await asyncio.sleep(interval_s)

    def _watch() -> None:
        last_dump = 0.0
        while True:
            time.sleep(1.0)
            lag = time.monotonic() - state["last_beat"]
            if lag < threshold_s:
                continue
            now = time.monotonic()
            if now - last_dump < _REDUMP_EVERY_S:
                continue
            last_dump = now
            tag = time.strftime("%Y%m%d-%H%M%S")
            path = _write_dump(lag, tag)
            # CRITICAL, and emitted from a thread that is NOT blocked, so it
            # actually reaches the log the frozen loop cannot write to.
            logger.critical(
                "[loop-stall] event loop unresponsive for %.0fs — every stack "
                "written to %s. Something synchronous is running on the loop.",
                lag, path or "(dump failed)",
            )

    threading.Thread(
        target=_watch, name="kazma-loop-stall-watchdog", daemon=True
    ).start()
    task = asyncio.get_running_loop().create_task(_heartbeat())
    logger.info(
        "[loop-stall] watchdog started (dump after %.0fs unresponsive)", threshold_s
    )
    return task
