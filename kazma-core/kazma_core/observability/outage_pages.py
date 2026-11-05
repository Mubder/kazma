"""Outage pages Kazma still owes a recovery note, kept across restarts.

A mechanism that pages its own outage ("Research/crawl monitoring
unavailable") promises one more message when it works again. Kept only in a
process global, that promise died with the process. Live, 2026-11-03 23:37Z
and 2026-11-04 10:35Z: the acquisition monitor paged during a Docker outage,
the guard restarted Kazma before the database came back, and the new
process -- which had paged nothing -- never said the monitor was back. The
red page stood alone in the chat, and the daily digest counted it as an
open problem.

Each paging mechanism writes a mark here when it pages and settles it after
its recovery note; a new process reads the marks at start and owes the notes
they name. The marks live in a file in the data dir, never in the settings
store: the settings store is often what is down when a page goes out.

These are synchronous file reads and writes: async code calls them through
``asyncio.to_thread``. A mark that cannot be written or read is a WARNING
and the page itself still goes out -- the worst case is the old behaviour,
a page with no note after a restart.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from kazma_core.atomic_files import atomic_write_text

logger = logging.getLogger(__name__)

__all__ = [
    "note_outage_page",
    "outage_page_owed",
    "owed_outage_pages",
    "settle_outage_page",
]

_FILE = "outage_pages.json"
#: The marks are one per paging mechanism (and one per MCP server), each
#: settled by its recovery; this bounds the file should a mechanism never
#: run again. The oldest page goes first.
_MAX_MARKS = 100
_lock = threading.Lock()


def _file() -> Path:
    from kazma_core.paths import data_dir

    return Path(data_dir()) / _FILE


def _read() -> dict[str, dict[str, Any]]:
    path = _file()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning("[outage] the owed recovery notes in %s cannot be read: %s", path, exc)
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        logger.warning("[outage] %s is not readable JSON; its owed recovery notes are lost", path)
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}


def _write(marks: dict[str, dict[str, Any]]) -> None:
    path = _file()
    if len(marks) > _MAX_MARKS:
        keep = sorted(marks, key=lambda k: float(marks[k].get("paged_at") or 0), reverse=True)[:_MAX_MARKS]
        marks = {k: marks[k] for k in keep}
    try:
        atomic_write_text(path, json.dumps(marks, sort_keys=True, ensure_ascii=False))
    except OSError as exc:
        logger.warning("[outage] the owed recovery notes in %s cannot be written: %s", path, exc)


def note_outage_page(key: str, *, since: float, cause: str = "") -> None:
    """A page for *key* went out: its recovery note is owed until settled."""
    with _lock:
        marks = _read()
        marks[key] = {"since": float(since), "cause": str(cause or "")[:300], "paged_at": time.time()}
        _write(marks)


def outage_page_owed(key: str) -> dict[str, Any] | None:
    """The mark of a page whose recovery note is still owed, or None."""
    with _lock:
        return _read().get(key)


def owed_outage_pages(prefix: str) -> dict[str, dict[str, Any]]:
    """Every owed mark whose key starts with *prefix*."""
    with _lock:
        return {k: v for k, v in _read().items() if k.startswith(prefix)}


def settle_outage_page(key: str) -> None:
    """The recovery note for *key* went out, or nothing is owed any more."""
    with _lock:
        marks = _read()
        if marks.pop(key, None) is not None:
            _write(marks)
