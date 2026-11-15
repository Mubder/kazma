"""Atomic, non-clobbering publication of already path-checked extracts."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

#: How a file Kazma saved from the web begins: an extract's header
#: (``read_url_to_file``, a crawl's save, a research source) or an extractive
#: digest of one. Its text is the page's, whoever reads it back
#: (``file_read`` fences it; ``safety.outside_text_fetch``).
SAVED_FROM_WEB_FIRST_LINES: tuple[str, ...] = (
    "# Source: http://",
    "# Source: https://",
    "# Research digest of `",
)
#: The bytes read to find the first line.
_HEAD_BYTES = 1024


def text_saved_from_web(text: str) -> bool:
    """Whether a file's *text* (its start is enough) is one Kazma saved from
    the web: its first line is one :data:`SAVED_FROM_WEB_FIRST_LINES` names."""
    body = (text or "")[:_HEAD_BYTES].lstrip("\ufeff")
    first = body.splitlines()[0] if body else ""
    return first.startswith(SAVED_FROM_WEB_FIRST_LINES)


def saved_from_web(path: Path) -> bool:
    """Whether *path* is a file Kazma saved from the web
    (:func:`text_saved_from_web`). Blocking (a file read); False when it
    cannot be read."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(_HEAD_BYTES)
    except OSError:
        return False
    return text_saved_from_web(head.decode("utf-8", errors="replace"))


def create_extract(target: Path, text: str) -> None:
    """Publish complete bytes exactly once; existing paths are never replaced.

    Call on a worker thread after workspace/path authorization. Hard-link
    publication is atomic and exclusive on supported filesystems; unsupported
    filesystems fail visibly instead of falling back to a clobbering write.
    """
    if target.exists():
        raise FileExistsError(str(target))
    from kazma_core.web_acquire.research_llm import active_route
    if active_route.get() is None:
        from kazma_core.web_acquire.output_allowance import reserve
        reserve(target, len(text.encode('utf-8')))
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".kazma-extract-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
