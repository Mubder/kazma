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

  - **RotatingFileHandler** → ``paths.log_file()`` (``<repo>/.kazma/kazma.log``)
    — 10 MB × 5 files, UTF-8. The durable record every diagnostic needs.
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
from datetime import datetime, UTC
from typing import Any

__all__ = ["StructuredJSONFormatter", "setup_logging", "is_debug", "LOG_LEVELS", "NOISY_LIBS"]

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

    effective = _resolve_level(level)
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

    use_json = (os.environ.get("KAZMA_LOG_FORMAT") or "").strip().lower() == "json"
    console_formatter = "json" if use_json else "console"

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
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(log_path),
                "maxBytes": 10 * 1024 * 1024,  # 10 MB
                "backupCount": 5,
                "encoding": "utf-8",
                "formatter": "standard",
                "level": effective,
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
        "Logging initialised: level=%s file=%s", effective, log_path,
    )
    # Mark configured AFTER the success log so subsequent calls skip — this
    # also catches the line itself being logged only once.
    _logging_configured = True
    _last_effective_level = effective
    return effective


def is_debug() -> bool:
    """True when the effective level is DEBUG (helper for conditional code)."""
    return _resolve_level(None) == "DEBUG"
