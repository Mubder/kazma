"""Kazma hears a Windows shutdown in time to stop cleanly (Windows only).

Found on 2026-11-20 (two restarts, the owner's and Windows Update's): the
start card said "the last run did not shut down cleanly", and it was right.
Kazma's guard runs as a scheduled task, and Task Scheduler ends every task at
the very start of a shutdown, so the guard never stopped the server. The server
heard of the shutdown only at the very end (Windows' console shutdown event,
37 s in), after the database service had stopped, logged "Shutdown signal
sent" and was ended in the same second: its stop never ran, and it never
recorded that it stopped.

The ``KazmaEngineShutdown`` service takes Windows' preshutdown notice, which
comes first, while the database still runs
(``container_engine_assets/engine-shutdown.cs``). Each Kazma server connects
to its pipe ``KazmaShutdownNotice`` and waits; at the notice the service
writes ``stop``, and the server stops the way the guard's CTRL_BREAK stops it
(:func:`_request_stop`: SIGBREAK, uvicorn's handler, then the app's own
shutdown and :func:`kazma_core.process_exit.exit_after_shutdown`). The server
keeps its end of the pipe open until its process ends: that is how the service
knows it stopped, and only then does the service stop the container engine.
Windows stops the database service after every preshutdown service is done.

No service listening (an install from before this, or a machine where setup
has not put it in place) leaves Kazma as it was, and says so once.

Started by ``serve.py`` (:func:`start`), in the server's process only.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: The pipe the ``KazmaEngineShutdown`` service serves (``engine-shutdown.cs``).
PIPE_NAME = "KazmaShutdownNotice"
#: The word the service writes at the preshutdown notice.
STOP_WORD = b"stop"
#: Seconds between tries while no service listens (or every instance is taken).
RETRY_S = 30.0
#: What to run to put the service in place (an administrator's step).
FIX = "run `kazma setup install` in an administrator terminal"

_started = threading.Event()

# What this module says before the server has set up its logging (the
# listener starts before the app is built, so a shutdown during a boot is
# heard): kept, and said once the root logger has its handlers.
_held: list[tuple[int, str, tuple]] | None = []
_held_lock = threading.Lock()
#: Seconds the held lines wait for logging, at most.
_HOLD_S = 600.0


def _flush_held() -> None:
    global _held
    waited = 0.0
    while not logging.getLogger().handlers and waited < _HOLD_S:
        threading.Event().wait(0.5)
        waited += 0.5
    with _held_lock:
        lines, _held = _held or [], None
    for level, message, args in lines:
        logger.log(level, message, *args)


#: The thread that says the held lines (kept: ``_flush_held`` ends by itself
#: once logging is set up, or after ``_HOLD_S``).
_flusher: threading.Thread | None = None


def _log(level: int, message: str, *args: object) -> None:
    global _flusher
    with _held_lock:
        if _held is not None and not logging.getLogger().handlers:
            if _flusher is None or not _flusher.is_alive():
                _flusher = threading.Thread(target=_flush_held, name="kazma-shutdown-notice-log", daemon=True)
                _flusher.start()
            _held.append((level, message, args))
            return
    logger.log(level, message, *args)


def _pipe_path(name: str = PIPE_NAME) -> str:
    """The pipe's path, ``\\\\.\\pipe\\<name>``."""
    return "\\\\.\\pipe\\" + name


def _request_stop() -> None:
    """The stop the guard's CTRL_BREAK makes: SIGBREAK, which uvicorn handles
    in the main thread (``prepare_server_exit`` maps it to KeyboardInterrupt
    before uvicorn runs)."""
    signal.raise_signal(signal.SIGBREAK)


def _read_word(pipe) -> bytes | None:
    """The service's line, or None when the pipe closed (the service stopped
    or restarted)."""
    line = b""
    while not line.endswith(b"\n"):
        try:
            chunk = pipe.read(1)
        except OSError:  # a broken pipe: the service process ended (killed, updated)
            return None
        if not chunk:
            return None
        line += chunk
        if len(line) > 64:
            return line.strip()
    return line.strip()


def _say_no_service(supervised: bool, reason: str) -> None:
    level = logging.WARNING if supervised else logging.INFO
    _log(level, "[shutdown-notice] nothing will stop Kazma cleanly when Windows restarts: the "
               "KazmaEngineShutdown service is not listening (%s); it is not installed, or older than this "
               "build. To put it in place, %s.", reason, FIX)


def listen(pipe_name: str = PIPE_NAME, *, on_stop: Callable[[], None] = _request_stop,
           stop_event: threading.Event | None = None, retry_s: float = RETRY_S,
           supervised: bool | None = None) -> None:
    """Wait on the service's pipe, and stop Kazma when it says ``stop``.

    Connects, reconnects when the service goes away, and holds the pipe open
    after the word until the process ends (or *stop_event* is set: tests).
    *on_stop* runs once."""
    stop_event = stop_event or threading.Event()
    if supervised is None:
        supervised = bool(os.environ.get("KAZMA_GUARD_STATE_FILE"))
    said_missing = False
    said_listening = False
    path = _pipe_path(pipe_name)
    while not stop_event.is_set():
        try:
            pipe = open(path, "r+b", buffering=0)  # noqa: SIM115 - held for the process's life
        except OSError as exc:
            if not said_missing:
                said_missing = True
                _say_no_service(supervised, type(exc).__name__)
            stop_event.wait(retry_s)
            continue
        try:
            if not said_listening:
                said_listening = True
                _log(logging.INFO, "[shutdown-notice] Kazma will be stopped cleanly when Windows shuts down "
                            "(the KazmaEngineShutdown service listens)")
            word = _read_word(pipe)
            if word is None:
                logger.debug("[shutdown-notice] the service closed its pipe; connecting again")
                stop_event.wait(1.0)
                continue
            if word != STOP_WORD:
                _log(logging.WARNING, "[shutdown-notice] the service said %r; only %r stops Kazma", word[:32], STOP_WORD)
                continue
            _log(logging.INFO, "[shutdown-notice] Windows is shutting down: Kazma stops now")
            try:
                on_stop()
            except (OSError, ValueError):  # what signal.raise_signal raises
                logger.warning("[shutdown-notice] the stop could not be started", exc_info=True)
            # Held open until the process ends: the service waits for this
            # end to close before it stops the container engine.
            stop_event.wait()
            return
        finally:
            # Reached on a reconnect, or once *stop_event* ends the wait after
            # the word (a test): never while the process stops, so its end of
            # the pipe stays open until the process itself has ended.
            try:
                pipe.close()
            except OSError:
                pass


def start() -> threading.Thread | None:
    """Listen in a daemon thread (Windows; once). serve.py only."""
    if sys.platform != "win32" or _started.is_set():
        return None
    _started.set()
    thread = threading.Thread(target=listen, name="kazma-shutdown-notice", daemon=True)
    thread.start()
    return thread
