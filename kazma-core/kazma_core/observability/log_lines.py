"""How Kazma reads its own logs back: one reader for every report.

The daily digest, the weekly report (``firing_ledger``), its targets
(``weekly_targets``) and the finished-turn reader (``turn_line``) count lines
Kazma writes. Each kept a parser of its own, and each matched its markers
ANYWHERE: in the message (``.search``, ``in``) or in the whole raw line. A
message carries text Kazma did not write -- a session id from a URL, a file
name, a tool's error -- so a caller could put ``[turn] Turn finished: ...
failed=yes``, ``[universal-backup] complete:`` or ``"event":
"guard.restarting"`` inside one and have it counted: a failed turn, a backup
that never ran, a restart (SonarCloud S5145, 2026-11-16). Two rules now, in
one place:

* A line is read whole into its record, never matched as raw text:
  :func:`read_app_line` for the application log (a JSON record, or the
  plain "standard" line matched from its first character to its last) and
  :func:`read_guard_line` for the guard's log (a JSON record with its
  ``ts`` and ``event``).
* A marker counts only where its writer puts it: at the START of the
  message (:func:`opens_with`), and a guard event only in the guard
  record's ``event`` field.

The writer makes every record one line (``logging_config``), so a message
cannot start a line of its own either. Gate:
tests/test_log_readers_read_markers_at_the_start.py.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = ["AppRecord", "GuardRecord", "opens_with", "read_app_line", "read_guard_line"]

#: The application log's plain line: ``logging_config``'s "standard"
#: formatter (``%Y-%m-%d %H:%M:%S [LEVEL] logger: message``), in local time.
#: ``T`` between the date and the time is read too.
_PLAIN_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2}) \[(\w+)\] (\S+): (.*)$")


@dataclass(frozen=True)
class AppRecord:
    """One record of the application log."""

    ts: float
    level: str
    logger: str
    message: str


@dataclass(frozen=True)
class GuardRecord:
    """One record of the guard's log: its event and every field it wrote."""

    ts: float
    event: str
    fields: dict[str, Any] = field(default_factory=dict)

    def get(self, name: str, default: Any = None) -> Any:
        return self.fields.get(name, default)


def _epoch(text: object) -> float | None:
    """Seconds since the epoch of an ISO time; one without a zone is local."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _json_object(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def read_app_line(line: str) -> AppRecord | None:
    """The record one application log line holds, or None.

    JSON (``StructuredJSONFormatter``): its ``timestamp`` (with its zone),
    ``level``, ``logger`` and ``message``. Plain: the whole line must be the
    "standard" format; its time is local, as the formatter writes it.
    Anything else -- a traceback's continuation, a line of another program
    -- is no record.
    """
    obj = _json_object(line)
    if obj is not None:
        ts = _epoch(obj.get("timestamp"))
        message = obj.get("message", "")
        if ts is None or not isinstance(message, str):
            return None
        return AppRecord(ts, str(obj.get("level") or ""), str(obj.get("logger") or ""), message)
    match = _PLAIN_LINE.match(line.rstrip("\r\n"))
    if match is None:
        return None
    date, clock, level, logger, message = match.groups()
    ts = _epoch(f"{date}T{clock}")
    if ts is None:
        return None
    return AppRecord(ts, level, logger, message)


def read_guard_line(line: str) -> GuardRecord | None:
    """The record one guard log line holds (``kazma_guard.GuardLog`` writes
    a JSON object with ``ts`` and ``event``), or None."""
    obj = _json_object(line)
    if obj is None:
        return None
    ts = _epoch(obj.get("ts"))
    event = obj.get("event")
    if ts is None or not isinstance(event, str) or not event:
        return None
    return GuardRecord(ts, event, obj)


@functools.lru_cache(maxsize=256)
def _literal(marker: str) -> re.Pattern[str]:
    return re.compile(re.escape(marker))


def opens_with(message: str, marker: str | re.Pattern[str]) -> re.Match[str] | None:
    """*marker* matched at the very start of *message*, or None.

    A ``str`` marker is literal text; a compiled pattern is matched with
    ``.match`` (anchored at the start, its groups readable from the match).
    """
    pattern = _literal(marker) if isinstance(marker, str) else marker
    return pattern.match(message or "")
