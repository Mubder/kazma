"""Atomic, non-clobbering publication of already path-checked extracts."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
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


#: Files a folder check opens before it stops and answers "yes": a folder
#: too large to check is read as holding a saved page (the answer only
#: fences the output, it never refuses).
_FOLDER_BUDGET = 5_000


def text_names_saved_page(text: str) -> bool:
    """Whether *text* holds a line a saved page begins with (``cat`` of
    several files prints each one's first line in the middle of its output)."""
    return any(
        line.lstrip("﻿").startswith(SAVED_FROM_WEB_FIRST_LINES)
        for line in (text or "").splitlines()
    )


def any_saved_from_web(paths: Iterable[Path], *, budget: int = _FOLDER_BUDGET) -> bool:
    """Whether any of *paths*, or any file under one that is a folder, is a
    file Kazma saved from the web (:func:`saved_from_web`). Blocking.

    Generated and dot folders are skipped (``project_files.GENERATED_DIRS``);
    past *budget* files the answer is True.
    """
    from kazma_core.workspace.project_files import skipped_dir

    opened = 0
    for path in paths:
        try:
            if path.is_file():
                opened += 1
                if saved_from_web(path):
                    return True
                continue
            if not path.is_dir():
                continue
        except OSError:
            continue
        for folder, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not skipped_dir(d)]
            for name in files:
                opened += 1
                if opened > budget:
                    return True
                if saved_from_web(Path(folder) / name):
                    return True
    return False


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
