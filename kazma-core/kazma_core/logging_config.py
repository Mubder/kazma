"""Portable structured JSON logging for Kazma.

Configures logs in single-line JSON format when KAZMA_LOG_FORMAT=json is specified,
providing standard keys like timestamp, level, logger, and message.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, UTC

__all__ = ["StructuredJSONFormatter", "setup_logging"]


class StructuredJSONFormatter(logging.Formatter):
    """Zero-dependency JSON formatter for absolute portability."""

    def format(self, record: logging.LogRecord) -> str:
        # Resolve exception traceback if present
        exc_str = None
        if record.exc_info:
            exc_str = self.formatException(record.exc_info)

        # Build consistent JSON log dictionary
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "filename": record.filename,
            "lineno": record.lineno,
            "funcName": record.funcName,
        }

        # Embed formatted exception details
        if exc_str:
            log_data["exception"] = exc_str

        # Return single-line JSON representation safely
        return json.dumps(log_data, default=str)


def setup_logging() -> None:
    """Initialize structured JSON logging if requested by environment."""
    if os.environ.get("KAZMA_LOG_FORMAT") == "json":
        root = logging.getLogger()

        # Clear existing handlers to prevent duplicate or plain text output
        for handler in list(root.handlers):
            root.removeHandler(handler)

        # Configure StreamHandler targeting stdout
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJSONFormatter())
        root.addHandler(handler)

        # Align log level with KAZMA_LOG_LEVEL
        level_str = os.environ.get("KAZMA_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_str, logging.INFO)
        root.setLevel(level)

        # Standardize uvicorn access & error logs to propagate to root
        for uvicorn_logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
            ul = logging.getLogger(uvicorn_logger_name)
            ul.handlers = []
            ul.propagate = True

        root.info("[Logging] Activated portable structured JSON logging")
