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

import json
import logging
import logging.config
import os
import sys
from datetime import datetime, time, UTC
from typing import Any

__all__ = [
    "StructuredJSONFormatter",
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
    try:
        from kazma_core.config_store import get_config_store

        cs_val = get_config_store().get("logging.level")
        if isinstance(cs_val, str) and cs_val.strip().upper() in LOG_LEVELS:
            return cs_val.strip().upper()
    except Exception:
        pass
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
    """Read a key from ConfigStore, returning None if unavailable/unset."""
    try:
        from kazma_core.config_store import get_config_store

        return get_config_store().get(key)
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

    kazma_loggers = ("kazma_core", "kazma_ui", "kazma_gateway", "kazma_skills")
    loggers_config: dict[str, Any] = {}
    for name in kazma_loggers:
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
                "()": "logging.handlers.TimedRotatingFileHandler",
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
        # Turn correlation: tag every record with the ContextVar turn id
        # ("" outside a turn). Attached post-dictConfig so it applies to the
        # file and console handlers regardless of formatter choice.
        try:
            from kazma_core.observability.correlation import TurnIdFilter

            _turn_filter = TurnIdFilter()
            for handler in logging.getLogger().handlers:
                handler.addFilter(_turn_filter)
        except Exception:
            pass
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
        "Logging initialised: level=%s file=%s rotation=daily retention=%dd",
        effective, log_path, retention_days,
    )
    # Mark configured AFTER the success log so subsequent calls skip — this
    # also catches the line itself being logged only once.
    _logging_configured = True
    _last_effective_level = effective
    return effective


def is_debug() -> bool:
    """True when the effective level is DEBUG (helper for conditional code)."""
    return _resolve_level(None) == "DEBUG"
