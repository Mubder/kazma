"""Kazma logging configuration — file + stdout, with optional JSON.

Why this module exists
----------------------
Before this module wired up file logging, every Kazma module called
``logging.getLogger(__name__)`` but **nothing configured the root logger**
with a file handler. Python's default is WARNING level + emit to stderr,
which meant:

  - INFO messages from ``[MCP]``, ``[kb_ingest]``, ``[SSE]`` never reached a
    file (``paths.log_file()`` was defined but unused).
  - "Check the server logs" diagnostics were impossible — there was no log
    file to check, only terminal scrollback that vanished on restart.

This module wires up a proper two-handler setup:

  - **TimedRotatingFileHandler** → ``paths.log_file()`` (``<repo>/.kazma/kazma.log``)
    — daily rotation at local midnight with a rolling ``retention_days``-day
    window (default 7). Self-cleaning by age: each midnight rotation renames
    the current file to ``kazma.log.<yesterday>`` and auto-deletes anything
    older than the window, so the log always holds the last N days with no
    line lost. UTF-8. Retention is configurable via the Settings UI
    (``logging.retention_days`` in ConfigStore) or ``KAZMA_LOG_RETENTION_DAYS``.
  - **StreamHandler** → stdout at INFO — preserves the current "logs in the
    terminal" behaviour during interactive development.

Optional JSON mode (``KAZMA_LOG_FORMAT=json``) replaces the stdout formatter
with :class:`StructuredJSONFormatter` for log shippers (Loki, Datadog, ...).
File output stays human-readable regardless.

Levels
~~~~~~
  - Root level controlled by ``KAZMA_LOG_LEVEL`` env (default ``INFO``).
  - Debug-start mode: ``kazma serve --debug`` OR ``KAZMA_LOG_LEVEL=DEBUG``.
  - Kazma's own loggers (``kazma_core``, ``kazma_ui``, ``kazma_gateway``)
    honour the root level — they are NOT muted.
  - Noisy third-party libs (``httpx``, ``chromadb``, ``httpcore``, ``openai``,
    ``urllib3``) are pinned to WARNING so they don't drown out Kazma signal.

Usage
~~~~~
Call :func:`setup_logging` **once**, as early as possible in each entry point:

    # kazma_ui/app.py:_on_startup
    from kazma_core.logging_config import setup_logging
    setup_logging()

    # kazma_core/agent_runner.py:main
    from kazma_core.logging_config import setup_logging
    setup_logging()

The function is idempotent (subsequent calls re-apply the config, which is
safe and cheap).
"""

from __future__ import annotations

import atexit
import copy
import json
import logging
import logging.config
import logging.handlers
import os
import queue
import sys
import threading
import time as _time
from datetime import datetime, time, UTC
from typing import Any

__all__ = [
    "StructuredJSONFormatter",
    "SafeTimedRotatingFileHandler",
    "setup_logging",
    "is_debug",
    "LOG_LEVELS",
    "NOISY_LIBS",
    "resolve_retention_days",
    "resolve_log_format",
]

# Valid level names accepted from KAZMA_LOG_LEVEL / --debug.
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Third-party libraries that log chatty INFO/DEBUG noise. Pinned to WARNING
# so the Kazma signal stays readable. Kazma's own loggers are never muted.
NOISY_LIBS = (
    "httpx",
    "httpcore",
    "chromadb",
    "openai",
    "urllib3",
    "asyncio",
    "sentence_transformers",
    "transformers",
    "torch",
    "fcntl",
    # uvicorn.error emits lifecycle noise ("Waiting for application startup.");
    # uvicorn.access stays at INFO (useful for tracing request flow).
    "uvicorn.error",
)


class SafeTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """TimedRotatingFileHandler that cannot die on a Windows rotation race.

    The stock handler's ``doRollover`` does ``os.remove(dest)`` then
    ``os.rename(src, dest)`` — on Windows BOTH fail with ``PermissionError``
    when another process still holds either file open (observed 2026-08-27:
    a test run had rotated the live ``kazma.log`` at midnight while a second
    process held it; the production server's first post-midnight emit then
    raised inside ``emit`` → ``Handler.handleError`` dumped a traceback to
    the console and the file handler stopped writing for the process's
    lifetime — console-only logging).

    On rollover failure we SKIP this rotation (keep appending to the live
    file; the next boundary retries) and advance ``rolloverAt`` so the
    failure is not retried on every emit.
    """

    def doRollover(self) -> None:  # noqa: D102 — see class docstring
        try:
            super().doRollover()
        except OSError as exc:
            try:
                sys.stderr.write(
                    f"[logging] daily rotation skipped ({exc.__class__.__name__}: {exc}) — "
                    f"continuing to append to {self.baseFilename}\n"
                )
            except Exception:  # noqa: BLE001 — never raise from error handling
                pass
            # stream may have been closed before the failure — force reopen.
            self.stream = None
            # Advance the schedule past the colliding boundary, else emit()
            # would retry (and warn) on every record.
            self.rolloverAt = self.computeRollover(int(_time.time()))


class StructuredJSONFormatter(logging.Formatter):
    """Zero-dependency JSON formatter for absolute portability.

    Used for the **stdout** handler when ``KAZMA_LOG_FORMAT=json`` is set
    (e.g. when shipping to Loki/Datadog). The file handler always stays
    human-readable.
    """

    def format(self, record: logging.LogRecord) -> str:
        exc_str = None
        if record.exc_info:
            exc_str = self.formatException(record.exc_info)
        elif record.exc_text:
            # Rendered where the record was made: the writer thread gets it
            # without the traceback objects (FA-135).
            exc_str = record.exc_text
        log_data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "filename": record.filename,
            "lineno": record.lineno,
            "funcName": record.funcName,
        }
        # Turn correlation id (empty string outside a turn — set by
        # TurnIdFilter or present directly on the record).
        turn_id = getattr(record, "turn_id", "")
        if turn_id:
            log_data["turn_id"] = turn_id
        if exc_str:
            log_data["exception"] = exc_str
        return json.dumps(log_data, default=str)


def _resolve_level(explicit: str | None) -> str:
    """Return the effective log level name (uppercase, validated)."""
    if explicit:
        lvl = explicit.strip().upper()
        if lvl in LOG_LEVELS:
            return lvl
    raw = (os.environ.get("KAZMA_LOG_LEVEL") or "").strip().upper()
    if raw in LOG_LEVELS:
        return raw
    return "INFO"


def _resolve_level_with_config(explicit: str | None) -> str:
    """Resolve level with precedence: explicit arg → ConfigStore → env → INFO.

    Mirrors the overlay pattern in memory/config.py: ConfigStore (set by the
    Settings UI) overrides the env default but not an explicit call arg.
    """
    if explicit:
        return _resolve_level(explicit)
    cs_val = _store_get("logging.level")
    if isinstance(cs_val, str) and cs_val.strip().upper() in LOG_LEVELS:
        return cs_val.strip().upper()
    return _resolve_level(None)


def resolve_retention_days() -> int:
    """Log retention in days. Precedence: ConfigStore → env → default 7.

    Controls the ``backupCount`` of the daily-rotating file handler — the
    number of past days kept before the oldest is auto-deleted.
    """
    for source in (
        lambda: _store_get("logging.retention_days"),
        lambda: os.environ.get("KAZMA_LOG_RETENTION_DAYS"),
    ):
        try:
            val = source()
        except Exception:
            val = None
        if val is None:
            continue
        try:
            n = int(val)
        except (TypeError, ValueError):
            continue
        if n >= 1:
            return n
    return 7


def resolve_log_format(explicit: str | None = None) -> str:
    """Log output format. Precedence: explicit → ConfigStore → env → text."""
    if explicit:
        return "json" if explicit.strip().lower() == "json" else "text"
    try:
        cs_val = _store_get("logging.format")
        if isinstance(cs_val, str) and cs_val.strip().lower() == "json":
            return "json"
    except Exception:
        pass
    return "json" if (os.environ.get("KAZMA_LOG_FORMAT") or "").strip().lower() == "json" else "text"


def _store_get(key: str) -> Any:
    """A logging setting: the settings store, else the install's YAML.

    The settings store is seeded from kazma.yaml, so the YAML is where a
    setting stands when the store cannot answer. At a Windows boot Kazma
    starts before Postgres: the store could not be read when logging was set
    up, the file format fell back to plain text, and the server wrote plain
    lines until its next reload -- 31 hours on 2026-10-30/31. The daily
    digest read JSON lines only and reported "Turns completed: 0 ... No
    failures, no restarts, no alerts" over that day.
    """
    try:
        from kazma_core.config_store import get_config_store

        value = get_config_store().get(key)
    except Exception:
        value = None
    if value is not None:
        return value
    try:
        from kazma_core.config_loader import install_yaml_section

        *section, name = key.split(".")
        return install_yaml_section(*section).get(name)
    except Exception:
        return None


# Process-wide flag: once setup_logging() has configured handlers, subsequent
# calls become no-ops. Prevents the double "Logging initialised" log line that
# happens because both ``kazma serve`` CLI AND ``app.py:_setup_services`` call
# setup_logging() during a single web boot. The first call wins; later calls
# return the previously-effective level without re-running dictConfig.
_logging_configured: bool = False
_last_effective_level: str = "INFO"


def setup_logging(level: str | None = None) -> str:
    """Configure root + library logging. Idempotent.

    The FIRST call configures handlers (file + stdout) and library levels.
    Subsequent calls are no-ops — they return the previously-effective level
    without re-running ``dictConfig``. This matters because both the
    ``kazma serve`` CLI bootstrap AND ``app.py:_setup_services`` call this
    during a single web boot; without the guard, the "Logging initialised"
    line is logged twice and handlers can multiply on re-config.

    Args:
        level: Optional level override (``DEBUG``/``INFO``/...). If omitted,
            falls back to ``KAZMA_LOG_LEVEL`` env, then ``INFO``.

    Returns:
        The effective level name (for entry points to log on startup).
    """
    global _logging_configured, _last_effective_level

    # Idempotency guard: the first call configures; later calls are no-ops.
    # Both ``kazma serve`` and ``app.py:_setup_services`` invoke this during
    # a single boot — without the guard we get double "Logging initialised"
    # lines and potentially duplicated handlers.
    if _logging_configured:
        return _last_effective_level

    # Lazy import to avoid a circular: paths.py is imported pervasively.
    from kazma_core.paths import log_file, merge_legacy_hub_if_empty, migrate_legacy_user_home

    # Migrate any legacy ~/.kazma → <repo>/.kazma on first boot so the log
    # directory lands in the right place. Idempotent + safe.
    try:
        migrate_legacy_user_home()
        merge_legacy_hub_if_empty()
    except Exception:
        pass  # Logging must never fail boot.

    effective = _resolve_level_with_config(level)
    log_path = log_file()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    loggers_config: dict[str, Any] = {}
    for name in _KAZMA_LOGGERS:
        loggers_config[name] = {"level": effective, "propagate": True}
    for name in NOISY_LIBS:
        # Cap at WARNING; never let NOISY_LIBS go below WARNING even in DEBUG.
        loggers_config[name] = {"level": "WARNING", "propagate": True}

    # The `format` setting governs the DURABLE FILE output (for log shippers like
    # Loki/Datadog that want JSON). The live TERMINAL console stays human-readable
    # text by default — it's what you watch interactively, so JSON noise there is
    # counterproductive. Opt into a JSON console explicitly via KAZMA_LOG_FORMAT=json
    # (env only), restoring the original behaviour where the terminal was always text.
    file_formatter = "standard"
    use_json_file = resolve_log_format(None) == "json"
    if use_json_file:
        file_formatter = "json"
    console_formatter = "console"
    retention_days = resolve_retention_days()

    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "console": {
                "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
                "datefmt": "%H:%M:%S",
            },
            "json": {
                "()": "kazma_core.logging_config.StructuredJSONFormatter",
            },
        },
        "handlers": {
            "file": {
                # Daily rotation at local midnight with a rolling retention window.
                # Rotates once per day → kazma.log (today) + kazma.log.<yesterday>,
                # … backupCount auto-deletes the oldest daily file on each rotation,
                # so the log always holds the last `retention_days` days with no
                # line lost within the window. Self-cleaning by age.
                # Safe… variant: a Windows multi-process rotation collision
                # must skip, not kill, the handler (see class docstring).
                "()": "kazma_core.logging_config.SafeTimedRotatingFileHandler",
                "filename": str(log_path),
                "when": "midnight",
                "interval": 1,
                "backupCount": retention_days,
                "encoding": "utf-8",
                "formatter": file_formatter,
                "level": effective,
                "atTime": time(0, 0, 0),
                "utc": False,
            },
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": console_formatter,
                "level": effective,
            },
        },
        "root": {
            "level": effective,
            "handlers": ["file", "console"],
        },
        "loggers": loggers_config,
    }

    try:
        logging.config.dictConfig(config)
        # The file and console handlers write on their own thread; the
        # caller -- the event loop included -- only hands the record over
        # (FA-135).
        _write_behind_a_queue()
    except Exception:
        # Last-resort fallback: basic config so we at least get stderr.
        logging.basicConfig(level=effective)

    # uvicorn's handlers fight with root (it installs its own); force
    # propagation so our handlers see the records.
    for uvicorn_logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        ul = logging.getLogger(uvicorn_logger_name)
        ul.handlers = []  # remove uvicorn's own handlers
        ul.propagate = True  # let records flow to root

    logging.getLogger(__name__).info(
        "Logging initialised: level=%s format=%s file=%s rotation=daily retention=%dd",
        effective, "json" if use_json_file else "text", log_path, retention_days,
    )
    # Mark configured AFTER the success log so subsequent calls skip — this
    # also catches the line itself being logged only once.
    _logging_configured = True
    _last_effective_level = effective
    return effective


#: Kazma's own loggers, each given the configured level (the libraries keep
#: theirs, :data:`NOISY_LIBS` at WARNING).
_KAZMA_LOGGERS = ("kazma_core", "kazma_ui", "kazma_gateway", "kazma_skills")
#: The handlers ``setup_logging`` installs, by name: they write on the log
#: writer thread, behind the ``queue`` handler on the root logger.
_KAZMA_HANDLER_NAMES = ("file", "console")

#: Records waiting for the writer thread. Past this many a record is dropped
#: and counted -- the writer says how many -- never waited for.
_QUEUE_MAX = 100_000
_listener: _LogWriter | None = None
_written_handlers: tuple[logging.Handler, ...] = ()
_EXC_FORMATTER = logging.Formatter()


class _LogQueueHandler(logging.handlers.QueueHandler):
    """The root logger's one handler (FA-135): it hands the record to the
    log writer thread and returns. The file and console handlers ran in the
    caller's thread, so a slow disk, a full console pipe or a rotation stalled
    the event loop for every chat. The turn id is read here (``TurnIdFilter``
    on this handler): the writer thread has no turn bound."""

    def __init__(self, target: queue.Queue) -> None:
        super().__init__(target)
        self._dropped = 0
        self._dropped_lock = threading.Lock()

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        # The message and any traceback are rendered now, where the record
        # was made; each formatter downstream still formats the record its
        # own way (the default prepare() formatted it once for everyone).
        record = copy.copy(record)
        record.message = record.getMessage()
        record.msg = record.message
        record.args = None
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = _EXC_FORMATTER.formatException(record.exc_info)
            record.exc_info = None
        return record

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            with self._dropped_lock:
                self._dropped += 1

    def take_dropped(self) -> int:
        with self._dropped_lock:
            dropped, self._dropped = self._dropped, 0
        return dropped


class _LogWriter(logging.handlers.QueueListener):
    """The log writer thread: the file and console handlers, each at its own
    level, and a WARNING when records had to be dropped."""

    def __init__(self, target: queue.Queue, handlers: tuple[logging.Handler, ...],
                 front: _LogQueueHandler) -> None:
        super().__init__(target, *handlers, respect_handler_level=True)
        self._front = front

    def handle(self, record: logging.LogRecord) -> None:
        dropped = self._front.take_dropped()
        if dropped:
            super().handle(logging.LogRecord(
                __name__, logging.WARNING, __file__, 0,
                "%d log records were dropped: the log writer fell behind", (dropped,), None))
        super().handle(record)


def _write_behind_a_queue() -> None:
    """Move the file and console handlers off the root logger onto the log
    writer thread, behind one queue handler (FA-135)."""
    global _listener, _written_handlers
    root = logging.getLogger()
    written = tuple(h for h in root.handlers if h.get_name() in _KAZMA_HANDLER_NAMES)
    if not written:
        return
    target: queue.Queue = queue.Queue(_QUEUE_MAX)
    front = _LogQueueHandler(target)
    front.set_name("queue")
    try:
        from kazma_core.observability.correlation import TurnIdFilter

        # Turn correlation: the ContextVar turn id ("" outside a turn), read
        # on the caller's thread.
        front.addFilter(TurnIdFilter())
    except ImportError:
        pass
    for handler in written:
        root.removeHandler(handler)
    root.addHandler(front)
    _listener = _LogWriter(target, written, front)
    _listener.start()
    _written_handlers = written
    atexit.register(_stop_log_writer)


def drain_logs(timeout: float = 2.0) -> bool:
    """Wait, at most *timeout* seconds, until every record handed over has
    been written. True when the queue emptied in time."""
    listener = _listener
    if listener is None:
        return True
    deadline = _time.monotonic() + max(0.0, timeout)
    while listener.queue.unfinished_tasks:
        if _time.monotonic() >= deadline:
            return False
        _time.sleep(0.01)
    for handler in _written_handlers:
        try:
            handler.flush()
        except Exception:  # noqa: BLE001 - a broken handler must not stop a shutdown
            continue
    return True


def _stop_log_writer(timeout: float = 5.0) -> bool:
    """Write what is queued and stop the writer thread, bounded (at exit)."""
    global _listener
    listener = _listener
    if listener is None or listener._thread is None:
        return True
    drained = drain_logs(timeout)
    listener.enqueue_sentinel()
    thread = listener._thread
    thread.join(max(0.1, timeout))
    if not thread.is_alive():
        listener._thread = None
        _listener = None
    return drained and not thread.is_alive()


def apply_level(level: str) -> str:
    """Apply *level* now: to Kazma's loggers, the root logger and the file and
    console handlers ``setup_logging`` installed -- the four places the level
    is set at boot (FA-114: Settings set only the root logger, so the page
    said "applies immediately" and DEBUG reached the log only after a
    restart). Returns the level applied."""
    global _last_effective_level
    name = str(level or "").strip().upper()
    if name not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError(f"unknown log level {level!r}")
    root = logging.getLogger()
    root.setLevel(name)
    for logger_name in _KAZMA_LOGGERS:
        logging.getLogger(logger_name).setLevel(name)
    for handler in (*root.handlers, *_written_handlers):
        if handler.get_name() in _KAZMA_HANDLER_NAMES:
            handler.setLevel(name)
    _last_effective_level = name
    return name


def is_debug() -> bool:
    """True when the effective level is DEBUG (helper for conditional code)."""
    return _resolve_level(None) == "DEBUG"
